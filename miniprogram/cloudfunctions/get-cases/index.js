/**
 * 案例数据出口。
 *
 * 三种用法：
 *   { me: true }               返回 openid + entitlements（登录态与权益）
 *   { id: 'xxx' }              返回单条（免费字段）
 *   { ver: '123' } / {}        版本比对：相同返回 {changed:false}，
 *                              不同返回全量（约 190KB，客户端缓存）
 *
 * 🔴 字段白名单在这里收敛：内部字段（needs_review / corrections /
 *    human_read / status 等作业态字段）不外泄。
 *    契约与 scripts/sync_cases_cloudbase.js 的 mapCase() 一致，
 *    两边必须同步改。
 */

const cloud = require('wx-server-sdk');
const { getEntitlements } = require('./lib/deliver.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

const FIELDS = {
  id: true, name: true, name_en: true, one_liner: true,
  origin: true, industry: true, category: true, tags: true,
  mrr: true, last_30d_revenue: true, arr: true, all_time: true,
  caliber: true, metric_note: true, customers: true,
  solo_score: true, china_score: true, composite_score: true,
  quadrant: true, quadrant_label: true,
  verification: true, tier: true, quality_score: true,
  what_it_does: true, how_it_makes_money: true, why_it_works: true,
  verdict: true, judgment: true, playbook: true,
  founder: true, traffic_sources: true, signals: true,
  updated_at: true, updated_ts: true
};

exports.main = async (event) => {
  const openid = cloud.getWXContext().OPENID;

  // ---- 登录态 + 权益 ----
  if (event.me) {
    const ent = await getEntitlements(db, openid);
    return { errcode: 0, openid: openid || '', entitlements: ent };
  }

  // ---- 单条 ----
  if (event.id) {
    const r = await db.collection('cases')
      .where({ id: event.id })
      .limit(1)
      .field(FIELDS)
      .get();
    return { errcode: 0, case: (r.data && r.data[0]) || null };
  }

  // ---- 全量 + 版本比对 ----
  const r = await db.collection('cases')
    .field(FIELDS)
    .limit(1000)
    .get();
  const list = r.data || [];
  // ver = 全库最大 updated_ts（同步脚本保证每条都有）
  const ver = String(list.reduce((m, c) => Math.max(m, Number(c.updated_ts) || 0), 0));

  if (event.ver && event.ver === ver) {
    return { errcode: 0, changed: false, ver: ver };
  }
  return { errcode: 0, changed: true, ver: ver, cases: list };
};
