/**
 * 案例数据出口。
 *
 * 三种用法：
 *   { me: true }               返回 openid + entitlements（登录态与权益）
 *   { id: 'xxx' }              返回单条（FREE 字段；付费字段看权益）
 *   { id: 'xxx', full: true }  请求完整内容（付费字段；**服务端校验权益**）
 *   { ver: '123' } / {}        版本比对：相同返回 {changed:false}，
 *                              不同返回全量 FREE 字段（约 40KB，客户端缓存）
 *   { subscribe: { email } }   M3 周报订阅（Web 落地页；格式校验 + 去重后入库）
 *
 * 🔴🔴 商业模式 2026-10-15 改为**内容收费**（原「内容免费」被证伪）：
 *   原来 34 个字段全在免费白名单里，其中包含 export-cases 要导出的
 *   **全部 17 列** ⇒ 用户一次 get-cases 调�� + 三行代码就能自己拼出 CSV，
 *   ¥19.90 的导出门形同虚设（scripts/verify_paywall.py 会持续守这条）。
 *   现在拆成两层：FREE 层给列表与摘要，PAID 层（收入明细 + 深度报告正文）
 *   必须在服务端校验权益后才返回。前端不是边界，users 表才是。
 *
 * 🔴 字段白名单在这里收敛：内部字段（needs_review / corrections /
 *    human_read / status 等作业态字段）不外泄。
 *    契约与 scripts/sync_cases_cloudbase.js 的 mapCase() 一致，两边必须同步改。
 */

const cloud = require('wx-server-sdk');
const { getEntitlements, canReadFull, resolveCaller, identOf } = require('./lib/deliver.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

/** FREE 层：免费可得。列表页、搜索、评分速查只靠这些就能跑。 */
const FREE = {
  id: true, name: true, name_en: true, one_liner: true,
  origin: true, industry: true, category: true, tags: true,
  verification: true, tier: true, quality_score: true,
  solo_score: true, china_score: true, composite_score: true,
  quadrant: true, quadrant_label: true,
  updated_at: true,
  // ⚠ updated_ts 必须在 FREE 层：ver 版本号靠它算，而下面全量查询只取
  //   FREE 层 —— 一度把它放进 PAID，导致 list 里没有它、ver 恒为 "0"，
  //   版本比对永远判「没变化」，客户端再也拉不到新案例（且不报错，最难查）。
  //   它的值只是个时间戳，不构成付费内容泄漏。
  updated_ts: true
};

/**
 * PAID 层：**必须校验权益**才返回。
 *
 * 收入五件套（mrr / last_30d_revenue / arr / all_time）+ 口径三件套
 * （caliber / metric_note / customers）是导出的核心，深度报告六字段是
 * 报告页的正文 —— 两组都必须锁，否则两道门同时失效。
 */
const PAID = {
  mrr: true, last_30d_revenue: true, arr: true, all_time: true,
  caliber: true, metric_note: true, customers: true,
  founder: true, traffic_sources: true, signals: true,
  what_it_does: true, how_it_makes_money: true, why_it_works: true,
  verdict: true, judgment: true, playbook: true
};

exports.main = async (event) => {
  // ---- M3 周报订阅（不要求登录态；Web 落地页匿名可调） ----
  if (event.subscribe && event.subscribe.email) {
    const email = String(event.subscribe.email).trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || email.length > 200) {
      return { errcode: 40002, errmsg: '邮箱格式不对' };
    }
    const coll = db.collection('subscribers');
    const dup = await coll.where({ email: email }).count();
    if (dup.total > 0) return { errcode: 0, subscribed: true, dedup: true };
    await coll.add({ data: {
      email: email,
      source: 'web',            // 来源标记（web / miniprogram / import）
      created_at: new Date().toISOString()
    } });
    return { errcode: 0, subscribed: true };
  }

  // M2 双身份：小程序语境 = OPENID；Web SDK 语境 = CloudBase uid
  const caller = await resolveCaller(cloud);
  const ident = identOf(caller);
  const openid = caller.openid;

  // ---- 登录态 + 权益 ----
  if (event.me) {
    const ent = await getEntitlements(db, ident);
    return {
      errcode: 0,
      openid: openid,
      uid: caller.uid,
      identity: ident ? 'bound' : 'anonymous',
      entitlements: ent,
      canReadFull: canReadFull(ent, '')
    };
  }

  // ---- 绑定 Web 会话（M2）----
  // 仅小程序语境发起：把 Web 端匿名会话的 uid 绑到当前 openid 的 users
  // 文档。之后 Web 端以 uid 读权益，与小程序共享同一份 reports/export/会员。
  if (event.bind_uid) {
    if (!openid) {
      return { errcode: 40301, errmsg: '绑定仅支持小程序内发起（需要微信登录态）' };
    }
    const uid = String(event.bind_uid);
    if (!/^[\w-]{8,64}$/.test(uid)) {
      return { errcode: 40011, errmsg: '绑定码格式不合法' };
    }
    const now = Date.now();
    const u = await db.collection('users').where({ _openid: openid }).limit(1).get();
    if (u.data && u.data.length) {
      await db.collection('users').doc(u.data[0]._id).update({
        data: { uid: uid, updated_at: now }
      });
    } else {
      await db.collection('users').add({
        data: { _openid: openid, uid: uid, reports: [], export_all: false, member_until: 0, created_at: now, updated_at: now }
      });
    }
    return { errcode: 0, bound: true, uid: uid };
  }

  // ---- 单条 ----
  if (event.id) {
    const ent = await getEntitlements(db, ident);
    const unlocked = canReadFull(ent, event.id);
    // full:true 只是「我要看付费层」的请求，能不能给看权益 —— 顺序不能反
    const field = unlocked ? Object.assign({}, FREE, PAID) : FREE;
    const r = await db.collection('cases')
      .where({ id: event.id })
      .limit(1)
      .field(field)
      .get();
    const c = (r.data && r.data[0]) || null;
    return {
      errcode: 0,
      case: c,
      // 前端据此决定显示付费墙还是正文；它只是展示提示，不是安全边界
      locked: !!c && !unlocked
    };
  }

  // ---- 全量 + 版本比对（只给 FREE 层）----
  const r = await db.collection('cases')
    .field(FREE)
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
