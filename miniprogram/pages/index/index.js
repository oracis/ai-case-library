/**
 * 案例列表（免费）。
 * 信息密度参照 Web 站案例卡：名称 + 一句话定位 + 收入（带口径）+ 验证等级 + 双轴评分。
 */

const api = require('../../utils/api.js');
const fmt = require('../../utils/fmt.js');

Page({
  data: {
    cases: [],
    filtered: [],
    kw: '',
    loading: true,
    notConfigured: false
  },

  onLoad() {
    this.load();
  },

  onShow() {
    // 后台静默刷新：版本没变就什么都不做，不打断阅读
    if (!this.data.loading) {
      api.refreshCache().then((changed) => {
        if (changed) this.decorate(api && this._raw || []);
      }).catch(() => {});
    }
  },

  load() {
    this.setData({ loading: true, notConfigured: false });
    api.getCases()
      .then((cases) => this.decorate(cases))
      .catch((err) => {
        // ENV_NOT_CONFIGURED 是开发期未配云环境的预期状态，静默进空态，不打警告
        if (!(err && err.message === 'ENV_NOT_CONFIGURED')) {
          console.warn('加载失败', err);
        }
        this.setData({ loading: false, notConfigured: true });
      });
  },

  /** 给每条案例预计算展示字段，模板保持干净 */
  decorate(cases) {
    this._raw = cases;
    const decorated = cases.map((c) => {
      const mr = fmt.mainRevenue(c);
      const q = fmt.quadOf(c);
      return Object.assign({}, c, {
        verifText: fmt.verifText(c.verification),
        tierText: fmt.tierText(c.tier),
        mainRev: mr ? { text: mr.text, caliberLabel: fmt.CALIBER_LABEL[mr.caliber] } : null,
        solo: q.solo == null ? '–' : q.solo,
        china: q.china == null ? '–' : q.china,
        score: fmt.scoreOf(c)
      });
    });
    this.setData({ cases: decorated, loading: false });
    this.applyFilter();
  },

  onSearch(e) {
    this.setData({ kw: e.detail.value.trim() });
    this.applyFilter();
  },

  clearKw() {
    this.setData({ kw: '' });
    this.applyFilter();
  },

  applyFilter() {
    const kw = this.data.kw.toLowerCase();
    const all = this.data.cases;
    if (!kw) { this.setData({ filtered: all }); return; }
    this.setData({
      filtered: all.filter((c) =>
        (c.name || '').toLowerCase().indexOf(kw) >= 0 ||
        (c.one_liner || '').toLowerCase().indexOf(kw) >= 0 ||
        (c.category || '').toLowerCase().indexOf(kw) >= 0)
    });
  },

  goCase(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/case/case?id=' + encodeURIComponent(id) });
  },

  retry() { this.load(); }
});
