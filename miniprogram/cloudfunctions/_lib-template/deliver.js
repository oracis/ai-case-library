/**
 * 发货核心（pay-notify 与 query-order 共用，各带一份）。
 *
 * ⚠ 2026-10-15：原先只有 pay-notify / query-order 各有一份，get-cases 与
 *    export-cases 都 require 了本文件却**没有**这个文件 —— 部署即崩。
 *    「云函数上传不含上级目录」，所以每个需要它的云函数都必须各带一份。
 *    改本文件要同步到这四处（test_paywall_lib.py 会查）。
 *
 * 幂等（官方硬要求）：以平台单号 wx_order_id 判重 —— 同一订单重复通知只发一次货。
 * 订单表 orders 是事实来源：case_id 等下单时写好的信息从库里读，
 * 不依赖推送是否透传 attach（官方字段表未承诺 attach 必达）。
 */

const cfg = require('./config.js');

/** pid（后台道具 ID）→ 本地 key。发货只认 pid，防止前端伪造 key。 */
function goodsByPid(pid) {
  for (const key of Object.keys(cfg.GOODS)) {
    if (cfg.GOODS[key].pid === pid) return Object.assign({ key }, cfg.GOODS[key]);
  }
  return null;
}

/**
 * 发货。
 * @returns {ok, skipped, errmsg} skipped=true 表示已发货过（幂等命中）
 */
async function deliver(db, params) {
  const openid = params.openid;
  const outTradeNo = params.outTradeNo;
  const wxOrderId = params.wxOrderId || '';
  const pid = params.pid;
  const quantity = Math.max(1, parseInt(params.quantity || 1, 10) || 1);

  if (!openid || !pid) return { ok: false, errmsg: 'missing openid/pid' };
  const goods = goodsByPid(pid);
  if (!goods) return { ok: false, errmsg: 'unknown pid ' + pid };

  // ---- 1. 幂等：订单已发货直接返回成功 ----
  const hit = await db.collection('orders')
    .where({ out_trade_no: outTradeNo })
    .limit(1)
    .get();
  if (hit.data && hit.data.length) {
    const o = hit.data[0];
    if (o.status === 2) return { ok: true, skipped: true };
    // 补记平台单号（查单兜底路径下下单时没有）
    if (wxOrderId && o.wx_order_id !== wxOrderId) {
      await db.collection('orders').doc(o._id).update({
        data: { wx_order_id: wxOrderId }
      });
    }
  }

  // ---- 2. 按 kind 发货（users 集合） ----
  const u = await db.collection('users').where({ _openid: openid }).limit(1).get();
  const now = Date.now();
  const _ = db.command;

  if (goods.kind === 'report') {
    // 单篇报告：case_id 从订单里读（下单时写入，最可靠）
    const order = hit.data && hit.data[0];
    const caseId = (order && order.case_id) || '';
    if (!caseId) return { ok: false, errmsg: 'order has no case_id' };
    const patch = u.data && u.data.length
      ? { reports: _.addToSet(caseId), updated_at: now }
      : { _openid: openid, reports: [caseId], export_all: false, member_until: 0, created_at: now, updated_at: now };
    if (u.data && u.data.length) {
      await db.collection('users').doc(u.data[0]._id).update({ data: patch });
    } else {
      await db.collection('users').add({ data: patch });
    }
  } else if (goods.kind === 'export') {
    if (u.data && u.data.length) {
      await db.collection('users').doc(u.data[0]._id).update({
        data: { export_all: true, updated_at: now }
      });
    } else {
      await db.collection('users').add({
        data: { _openid: openid, reports: [], export_all: true, member_until: 0, created_at: now, updated_at: now }
      });
    }
  } else if (goods.kind === 'member') {
    // 会员：在「现有到期时间」与「现在」之间取晚者续期，买多久加多久
    const base = (u.data && u.data[0] && u.data[0].member_until > now)
      ? u.data[0].member_until : now;
    const until = base + (goods.days || 30) * 86400000;
    if (u.data && u.data.length) {
      await db.collection('users').doc(u.data[0]._id).update({
        data: { member_until: until, updated_at: now }
      });
    } else {
      await db.collection('users').add({
        data: { _openid: openid, reports: [], export_all: false, member_until: until, created_at: now, updated_at: now }
      });
    }
  } else {
    return { ok: false, errmsg: 'unknown kind ' + goods.kind };
  }

  // ---- 3. 订单状态推进 ----
  if (hit.data && hit.data.length) {
    await db.collection('orders').doc(hit.data[0]._id).update({
      data: { status: 2, wx_order_id: wxOrderId || (hit.data[0].wx_order_id || ''), deliver_at: now }
    });
  }
  return { ok: true, skipped: false };
}

/** 读权益（get-cases / query-order / export-cases 共用） */
async function getEntitlements(db, openid) {
  if (!openid) return { reports: [], export_all: false, member_until: 0 };
  const u = await db.collection('users').where({ _openid: openid }).limit(1).get();
  const d = u.data && u.data[0];
  if (!d) return { reports: [], export_all: false, member_until: 0 };
  return {
    reports: Array.isArray(d.reports) ? d.reports : [],
    export_all: !!d.export_all,
    member_until: d.member_until || 0
  };
}

/**
 * 能不能看某条案例的完整内容（含收入数据与深度报告正文）。
 *
 * 2026-10-15 商业模式改为**内容收费**（原来「内容免费」被证明收不到钱 ——
 * 免费接口把 17 个付费导出列全给了，用户三行代码自己拼 CSV，导出门形同虚设，
 * 见 scripts/verify_paywall.py）。
 *
 * 判定只看服务端 users 表：买了单篇报告、或有导出权、或会员未过期。
 * 前端不参与判定 —— 前端只是展示门，抓包改按钮没有任何意义。
 */
function canReadFull(ent, caseId, now) {
  if (!ent) return false;
  if (ent.export_all) return true;
  if (ent.member_until && ent.member_until > (now || Date.now())) return true;
  if (Array.isArray(ent.reports) && caseId && ent.reports.indexOf(caseId) >= 0) return true;
  return false;
}

module.exports = { deliver, getEntitlements, goodsByPid, canReadFull };
