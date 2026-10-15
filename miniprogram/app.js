/**
 * 应用入口。
 *
 * 设计原则（与 Web 站一致，别在小程序里丢掉）：
 * 1. 免费内容不设登录墙 —— 未登录用户可浏览全部案例摘要与评分速查，
 *    只有「功能」（深度报告/导出/会员）才需要购买。
 * 2. 登录失败不阻断使用：wx.login 失败时静默降级为游客，
 *    购买时再重新走登录流程。
 */

const { ENV_ID } = require('./utils/config.js');

App({
  onLaunch() {
    if (!wx.cloud) {
      console.error('当前基础库版本过低（需要 2.2.3+），请升级微信客户端');
      return;
    }
    wx.cloud.init({ env: ENV_ID, traceUser: false });
    // 静默登录：拿 code 换 openid 与权益。失败不影响免费内容浏览。
    wx.login({
      success: () => this.loadMe(),
      fail: () => { this.globalData.guest = true; }
    });
  },

  globalData: {
    openid: '',
    entitlements: null,   // { reports: [], export_all: bool, member_until: ts }
    guest: false
  },

  /**
   * 拉取「我是谁 + 我有什么权益」。
   * openid 与权益都从服务端取（前端拿不到 openid，只有 code），
   * 顺带保证权益判定只有一份实现（云函数 get-cases/query-order 共用）。
   */
  loadMe() {
    const app = this;
    return wx.cloud.callFunction({ name: 'get-cases', data: { me: true } })
      .then((res) => {
        const r = (res.result || {});
        app.globalData.openid = r.openid || '';
        app.globalData.entitlements = r.entitlements || null;
        app.globalData.guest = !r.openid;
        return r;
      })
      .catch((err) => {
        // 云函数未部署/环境 ID 没改时走这里：游客模式，功能页会提示配置
        console.warn('loadMe 失败（游客模式）:', err && err.errMsg);
        app.globalData.guest = true;
        return null;
      });
  },

  /** 当前是否会员（member_until 是毫秒时间戳） */
  isMember() {
    const e = this.globalData.entitlements;
    return !!(e && e.member_until && e.member_until > Date.now());
  },

  /** 是否已购买某案例的深度报告 */
  hasReport(caseId) {
    const e = this.globalData.entitlements;
    return !!(e && Array.isArray(e.reports) && e.reports.indexOf(caseId) >= 0);
  },

  hasExport() {
    const e = this.globalData.entitlements;
    return !!(e && e.export_all);
  }
});
