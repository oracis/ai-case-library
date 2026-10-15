/**
 * 案例详情。
 *
 * 免费/付费边界（产品原则：卖功能，不卖内容）：
 *   免费：是什么 / 怎么赚钱 / 为什么成立 / 结论 / 收入数字 / 双轴评分
 *   付费：深度报告（打法清单 + 创始人 + 流量来源 + 证据链结构化呈现）
 * 注意：同样的内容在 Web 站是全文公开的。小程序卖的是「单篇结构化报告的
 * 便捷形态」，不是排他性内容 —— 这也是个人主体工具类目能过审的形态
 * （付费墙锁「内容」会撞知识付费红线，锁「功能」不会）。
 */

const api = require('../../utils/api.js');
const fmt = require('../../utils/fmt.js');
const pay = require('../../utils/pay.js');
const { GOODS } = require('../../utils/config.js');

Page({
  data: {
    c: null,
    missing: false,
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
    api.getCases().then((cases) => {
      const c = cases.find((x) => x.id === this.id);
      if (!c) { this.setData({ missing: true }); return; }
      this.setData({
        c: this.decorate(c),
        hasReport: getApp().hasReport(this.id)
      });
    }).catch(() => this.setData({ missing: true }));
  },

  decorate(c) {
    const q = fmt.quadOf(c);
    const lines = fmt.revenueLines(c).map((l) => ({
      text: l.text,
      caliberLabel: fmt.CALIBER_LABEL[l.caliber]
    }));
    return Object.assign({}, c, {
      verifText: fmt.verifText(c.verification),
      tierText: fmt.tierText(c.tier),
      revLines: lines,
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
          this.setData({ hasReport: getApp().hasReport(this.id) });
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
