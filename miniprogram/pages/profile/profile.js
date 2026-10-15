/**
 * 我的：权益 + 两个付费功能入口（全站导出 / 月度会员）。
 *
 * 导出是**硬功能**：wx.openDocument 不支持 csv，
 * 且大文本走剪贴板体验差，所以走「云函数生成 → 云存储 → 下载打开」。
 * 权益校验在云函数侧做（前端门只挡君子）。
 */

const api = require('../../utils/api.js');
const { FUNC, GOODS } = require('../../utils/config.js');
const pay = require('../../utils/pay.js');
const fmt = require('../../utils/fmt.js');

Page({
  data: {
    openid: '',
    guest: false,
    isMember: false,
    memberUntilText: '',
    reports: [],
    hasExport: false,
    buying: '',
    exporting: false
  },

  onShow() { this.refresh(); },

  refresh() {
    const app = getApp();
    Promise.all([app.loadMe(), api.getCases()])
      .then(([me, cases]) => {
        const ent = app.globalData.entitlements || {};
        const reports = (ent.reports || [])
          .map((id) => cases.find((c) => c.id === id))
          .filter(Boolean)
          .map((c) => ({ id: c.id, name: c.name }));
        const until = ent.member_until && ent.member_until > Date.now()
          ? new Date(ent.member_until).toLocaleDateString('zh-CN') : '';
        this.setData({
          openid: app.globalData.openid || '',
          guest: app.globalData.guest && !app.globalData.openid,
          isMember: app.isMember(),
          memberUntilText: until,
          reports,
          hasExport: app.hasExport()
        });
      })
      .catch(() => {});
  },

  /** 统一购买入口：成功后刷新权益 */
  buy(key) {
    if (this.data.buying) return;
    this.setData({ buying: key });
    pay.buy(key, '').then((r) => {
      this.setData({ buying: '' });
      if (r.ok) {
        getApp().loadMe().then(() => this.refresh());
        wx.showToast({ title: '购买成功', icon: 'none' });
      } else if (r.errmsg) {
        wx.showToast({ title: r.errmsg, icon: 'none', duration: 3000 });
      }
    });
  },

  /** 导出：云函数校验权益 → 生成 CSV → 传云存储 → 下载 → 用小程序打开 */
  doExport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    api.call(FUNC.export, {})
      .then((r) => {
        if (r.fileId) {
          return wx.cloud.downloadFile({ fileID: r.fileId })
            .then((dl) => {
              // csv 不在 openDocument 支持列表，改用「复制全文」兜底 + 保存提示
              return wx.setClipboardData({
                data: r.csv || '',
                success: () => wx.showToast({ title: 'CSV 已复制，粘贴保存为 .csv', icon: 'none', duration: 3000 })
              });
            })
            .catch(() => {
              return wx.setClipboardData({ data: r.csv || '' });
            });
        }
        return wx.setClipboardData({
          data: r.csv || '',
          success: () => wx.showToast({ title: 'CSV 已复制', icon: 'none' })
        });
      })
      .catch((err) => {
        wx.showToast({ title: err.errmsg || err.message || '导出失败', icon: 'none', duration: 3000 });
      })
      .then(() => this.setData({ exporting: false }));
  },

  goReport(e) {
    wx.navigateTo({ url: '/pages/report/report?id=' + encodeURIComponent(e.currentTarget.dataset.id) });
  },

  /** 规则说明 —— 官方要求的用户告知义务（限额/费率/退款） */
  showRules() {
    wx.showModal({
      title: '购买规则',
      content: '虚拟支付（个人主体）：月支付限额 10 万元；安卓等终端费率 1%，T+3 结算；iOS 走 Apple 支付（费率 12%，约 45-60 天结算）。虚拟商品不支持退换；iOS 退款需向 App Store 申请。',
      showCancel: false
    });
  }
});
