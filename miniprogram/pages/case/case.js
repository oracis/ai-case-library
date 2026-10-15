/**
 * 案例详情（摘要层 · 免费）。
 *
 * 🔴 商业模式 2026-10-15 改为**内容收费**（原「内容免费」被证伪）：
 *   原来列表接口把收入数字和报告正文全给了，于是「付 ¥19.90 导出」和
 *   「付 ¥6.60 看报告」两道门同时形同虚设 —— 用户一次调用 + 三行代码
 *   就能拿到全部数据。scripts/verify_paywall.py 现在持续守这条。
 *
 * 免费/付费边界（当前）：
 *   免费：名称 / 一句话定位 / 国家地区 / 分类 / 收入验证等级 / 双轴评分 / 象限
 *   付费：收入数字（mrr / 近30天 / arr / 累计）+ 口径说明 + 深度报告正文
 *         （打法清单 / 创始人 / 流量来源 / 证据链）
 *
 * 边界由云函数 get-cases 的 FREE / PAID 两层强制：未购买时那些字段**压根不返回**，
 * 不是前端藏起来。本页用 api.getCase(id) 取（列表缓存只有 FREE 层，
 * 拿不到收入数字）。前端只负责把 locked 画成付费墙 —— 改按钮没用。
 */

const api = require('../../utils/api.js');
const fmt = require('../../utils/fmt.js');
const pay = require('../../utils/pay.js');
const { GOODS } = require('../../utils/config.js');

Page({
  data: {
    c: null,
    missing: false,
    locked: true,
    loading: true,
    hasReport: false,
    buying: false,
    goods: GOODS.report_single
  },

  onLoad(opts) {
    this.id = decodeURIComponent(opts.id || '');
  },

  onShow() {
    this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getCase(this.id).then((r) => {
      const c = r.case;
      if (!c) { this.setData({ missing: true, loading: false }); return; }
      const locked = !!r.locked;
      this.setData({
        loading: false,
        locked: locked,
        hasReport: !locked,
        c: this.decorate(c, locked)
      });
    }).catch(() => this.setData({ missing: true, loading: false }));
  },

  decorate(c, locked) {
    const q = fmt.quadOf(c);
    return Object.assign({}, c, {
      verifText: fmt.verifText(c.verification),
      tierText: fmt.tierText(c.tier),
      // 未解锁时收入字段没返回：留空数组，绝不能让 WXML 渲染成「¥0」
      // —— 那会被读成「这个产品没有收入」，是事实错误。
      revLines: locked ? [] : fmt.revenueLines(c).map((l) => ({
        text: l.text,
        caliberLabel: fmt.CALIBER_LABEL[l.caliber]
      })),
      solo: q.solo, china: q.china,
      score: fmt.scoreOf(c)
    });
  },

  buyReport() {
    if (this.data.buying) return;
    this.setData({ buying: true });
    pay.buy('report_single', this.id).then((r) => {
      this.setData({ buying: false });
      if (r.ok) {
        // 权益以服务端为准：刷新全局后重读
        getApp().loadMe().then(() => {
          this.load();
          wx.showToast({ title: '已解锁深度报告', icon: 'none' });
        });
      } else if (r.errmsg) {
        wx.showToast({ title: r.errmsg, icon: 'none', duration: 3000 });
      }
      // canceled：用户自己取消，不打扰
    });
  },

  goReport() {
    wx.navigateTo({ url: '/pages/report/report?id=' + encodeURIComponent(this.id) });
  }
});
