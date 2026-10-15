/**
 * 深度报告（付费功能）。
 * 内容 = playbook（可迁移打法）+ founder（创始人）+ traffic_sources（流量来源）
 *      + 口径明细。权益不在前端判定真假 —— 前端只做展示门，
 *      export 类硬功能在云函数侧校验权益（见 cloudfunctions/export-cases）。
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
      const q = fmt.quadOf(c);
      this.setData({
        c: Object.assign({}, c, {
          verifText: fmt.verifText(c.verification),
          tierText: fmt.tierText(c.tier),
          caliberText: fmt.caliberText(c),
          revLines: fmt.revenueLines(c).map((l) => ({
            text: l.text, caliberLabel: fmt.CALIBER_LABEL[l.caliber]
          })),
          playbook: Array.isArray(c.playbook) ? c.playbook : [],
          traffic: Array.isArray(c.traffic_sources) ? c.traffic_sources : [],
          founderText: c.founder || ''
        }),
        hasReport: getApp().hasReport(this.id)
      });
    }).catch(() => this.setData({ missing: true }));
  },

  buy() {
    if (this.data.buying) return;
    this.setData({ buying: true });
    pay.buy('report_single', this.id).then((r) => {
      this.setData({ buying: false });
      if (r.ok) {
        getApp().loadMe().then(() => {
          this.setData({ hasReport: getApp().hasReport(this.id) });
          wx.showToast({ title: '已解锁', icon: 'none' });
        });
      } else if (r.errmsg) {
        wx.showToast({ title: r.errmsg, icon: 'none', duration: 3000 });
      }
    });
  }
});
