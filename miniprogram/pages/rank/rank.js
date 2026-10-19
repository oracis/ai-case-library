/**
 * 双轴评分速查（免费功能）。
 * X 轴：一个人做得来（solo_fit）；Y 轴：搬回国内（china_fit）。
 * 这是本库相对纯案例列表的差异点 —— 帮读者判断「能不能搬」，
 * 所以把它放在 tab 上做引流，而不是藏在筛选项里。
 */

const api = require('../../utils/api.js');
const fmt = require('../../utils/fmt.js');

const THRESHOLD = 60;

const QUADS = [
  { key: 'star',  title: '★ 黄金象限',   sub: '一个人做得来，也适合搬回国', solo: true,  china: true  },
  { key: 'solo',  title: '◆ 适合单干',   sub: '国内移植有难度，但能独立跑', solo: true,  china: false },
  { key: 'team',  title: '▲ 需要搭伙',   sub: '国内市场好，但一个人啃不动', solo: false, china: true  },
  { key: 'hard',  title: '× 两头不占',   sub: '要么难做，要么难搬',         solo: false, china: false }
];

Page({
  data: { quads: [], loading: true, total: 0 },

  onLoad() { this.load(); },
  onShow() { if (!this.data.loading) this.load(); },

  load() {
    api.getCases().then((cases) => {
      const groups = {};
      QUADS.forEach((q) => { groups[q.key] = []; });
      cases.forEach((c) => {
        const q = fmt.quadOf(c);
        if (q.solo == null || q.china == null) return;
        const realKey = q.solo >= THRESHOLD
          ? (q.china >= THRESHOLD ? 'star' : 'solo')
          : (q.china >= THRESHOLD ? 'team' : 'hard');
        groups[realKey].push(Object.assign({}, c, {
          solo: q.solo, china: q.china,
          mainRev: fmt.mainRevenue(c)
        }));
      });
      // 组内按综合分降序
      Object.keys(groups).forEach((k) => {
        groups[k].sort((a, b) => (fmt.scoreOf(b) || 0) - (fmt.scoreOf(a) || 0));
      });
      this.setData({
        quads: QUADS.map((q) => ({
          key: q.key, title: q.title, sub: q.sub,
          items: groups[q.key],
          count: groups[q.key].length
        })),
        total: cases.length,
        loading: false
      });
    }).catch(() => this.setData({ loading: false }));
  },

  goCase(e) {
    wx.navigateTo({ url: '/pages/case/case?id=' + encodeURIComponent(e.currentTarget.dataset.id) });
  }
});
