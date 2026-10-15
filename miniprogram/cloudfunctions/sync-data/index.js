/**
 * 数据同步入口（HTTP 触发型）。
 *
 * 本地脚本 scripts/sync_cases_cloudbase.js 把 data/cases.json 扁平化后
 * 分片 POST 到这里，由本函数写进 cases 集合。
 *
 * 为什么不让小程序端直连数据库写入：
 *   1. 写入必须收权（安全规则只能「所有用户可读」）；
 *   2. 字段契约需要一处收敛（白名单映射只在同步脚本做一次）。
 */

const cloud = require('wx-server-sdk');
const cfg = require('./lib/config.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();
const _ = db.command;

exports.main = async (event) => {
  // ---- 解出 JSON body（HTTP 触发时 body 是字符串） ----
  let payload = event;
  if (event && typeof event.body === 'string') {
    try {
      payload = JSON.parse(
        event.isBase64Encoded
          ? Buffer.from(event.body, 'base64').toString('utf8')
          : event.body
      );
    } catch (e) {
      return http(400, { ok: false, errmsg: 'bad json' });
    }
  }

  if (payload.secret !== cfg.SYNC_SECRET) {
    return http(401, { ok: false, errmsg: 'unauthorized' });
  }

  const cases = payload.cases;
  if (!Array.isArray(cases) || !cases.length) {
    return http(400, { ok: false, errmsg: 'empty cases' });
  }

  // ---- upsert by id：20 条一批，已存在的 update、缺失的 add ----
  let upserted = 0;
  for (let i = 0; i < cases.length; i += 20) {
    const batch = cases.slice(i, i + 20);
    const ids = batch.map((c) => c.id).filter(Boolean);
    if (!ids.length) continue;
    const exist = await db.collection('cases')
      .where({ id: _.in(ids) })
      .field({ id: true })
      .get();
    const existIds = new Set((exist.data || []).map((d) => d.id));
    for (const c of batch) {
      if (!c.id) continue;
      if (existIds.has(c.id)) {
        await db.collection('cases').where({ id: c.id }).update({ data: c });
      } else {
        await db.collection('cases').add({ data: c });
      }
      upserted += 1;
    }
  }

  return http(200, { ok: true, upserted: upserted });
};

function http(status, obj) {
  return {
    statusCode: status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj)
  };
}
