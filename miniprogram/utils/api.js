/**
 * 云函数调用封装 + 案例数据缓存。
 *
 * 为什么不全量直连数据库：小程序端直连 CloudBase 数据库要靠安全规则放权，
 * 且无法收敛字段（replicability 评分明细等内部字段不该外泄）。
 * 一律走云函数，字段白名单在云函数侧控制。
 */

const { FUNC } = require('./config.js');

const CACHE_KEY = 'cases_cache_v1';
const CACHE_VER_KEY = 'cases_ver_v1';

function call(name, data) {
  return wx.cloud.callFunction({ name, data: data || {} })
    .then((res) => {
      const r = res.result || {};
      if (r.errcode && r.errcode !== 0) {
        const err = new Error(r.errmsg || ('云函数 ' + name + ' 返回错误 ' + r.errcode));
        err.errcode = r.errcode;
        throw err;
      }
      return r;
    });
}

/**
 * 拉取案例列表。
 * 策略：storage 缓存 + 版本号比对。60 条精简数据约 190KB，
 * 每次进页面重拉对首屏不友好；缓存后只在版本变化时重拉。
 */
function getCases(force) {
  if (!force) {
    const cached = wx.getStorageSync(CACHE_KEY);
    if (cached && cached.length) return Promise.resolve(cached);
  }
  return call(FUNC.cases, {})
    .then((r) => {
      const cases = r.cases || [];
      try {
        wx.setStorageSync(CACHE_KEY, cases);
        wx.setStorageSync(CACHE_VER_KEY, r.ver || '');
      } catch (e) { /* storage 满/禁用时静默降级为每次拉取 */ }
      return cases;
    });
}

/** 静默刷新缓存（后台比对版本，不打断当前阅读） */
function refreshCache() {
  return call(FUNC.cases, { ver: wx.getStorageSync(CACHE_VER_KEY) || '' })
    .then((r) => {
      if (r.changed && r.cases) {
        try { wx.setStorageSync(CACHE_KEY, r.cases); wx.setStorageSync(CACHE_VER_KEY, r.ver || ''); } catch (e) {}
        return true;
      }
      return false;
    })
    .catch(() => false);
}

/** 单案例详情（含付费内容标记，不含需购买才可见的字段） */
function getCase(id) {
  return call(FUNC.cases, { id });
}

module.exports = { call, getCases, refreshCache, getCase };
