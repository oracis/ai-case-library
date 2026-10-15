/**
 * 深度报告（付费内容）。
 *
 * 2026-10-15 商业模式改为**内容收费**：报告正文 + 收入明细都在云函数
 * get-cases 的 PAID 层，必须服务端校验权益才返回（见 cloudfunctions/get-cases）。
 * 所以这里**不再从本地缓存的列表数据里取**——列表缓存只有 FREE 层，
 * 里面根本没有 playbook / founder / traffic_sources。
 * 必须每次用 getCase(id) 单独取：那一次调用里云函数已经判过权益，
 * 返回 locked=true 就是没买。
 *
 * ⚠ 前端仍要处理 locked 分支，但那是「展示门」不是安全边界：
 *   真正的边界在云函数，前端拿不到就是拿不到，改按钮没用。
 */

const api = require('../../utils/api.js');
const fmt = require('../../utils/fmt.js');
const pay = require('../../utils/pay.js');
const { GOODS } = require('../../utils/config.js');

Page({
  data: {
    c: null,
    missing: false,
    locked: true,        // 未解锁（云函数判定）
    loading: true,
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
        c: Object.assign({}, c, {
          verifText: fmt.verifText(c.verification),
          tierText: fmt.tierText(c.tier),
          // 未解锁时收入字段压根没返回，别让页面显示成 0（会误导成「这个产品没收入」）
          caliberText: locked ? '' : fmt.caliberText(c),
          revLines: locked ? [] : fmt.revenueLines(c).map((l) => ({
            text: l.text, caliberLabel: fmt.CALIBER_LABEL[l.caliber]
          })),
          playbook: (!locked && Array.isArray(c.playbook)) ? c.playbook : [],
          traffic: (!locked && Array.isArray(c.traffic_sources)) ? c.traffic_sources : [],
          founderText: locked ? '' : (c.founder || '')
        })
      });
    }).catch(() => this.setData({ missing: true, loading: false }));
  },

  buy() {
    if (this.data.buying) return;
    this.setData({ buying: true });
    pay.buy('report_single', this.id).then((r) => {
      this.setData({ buying: false });
      if (r.ok) {
        getApp().loadMe().then(() => {
          // 权益已到账，重新拉一次拿全文
          this.load();
          wx.showToast({ title: '已解锁', icon: 'none' });
        });
      } else if (r.errmsg) {
        wx.showToast({ title: r.errmsg, icon: 'none', duration: 3000 });
      }
    });
  }
});
