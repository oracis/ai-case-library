/**
 * 查单兜底 + 权益刷新。
 *
 * 两个用途：
 *   1. {outTradeNo} —— 支付成功后前端主动查（官方：success 回调可能丢失，
 *      不能作为发货依据；推送丢失时也要靠查单补发货）
 *   2. {} —— 只返回当前权益（购买后刷新用）
 *
 * 官方规范：query_order **不支持云调用**，必须 HTTPS 外网调用，
 * pay_sig 拼在 URL query 上，uri 签 '/xpay/query_order'（不带 query），
 * body 必须与实际发出的原始 JSON 逐字节一致。
 */

const cloud = require('wx-server-sdk');
const { paySig, postJson } = require('./lib/sign.js');
const { deliver, getEntitlements, resolveCaller, identOf } = require('./lib/deliver.js');
const cfg = require('./lib/config.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

/** access_token 实例级缓存：云函数实例复用，避免每次查单都换 token */
let TOKEN = { v: '', exp: 0 };

async function accessToken() {
  const now = Date.now();
  if (TOKEN.v && TOKEN.exp > now + 60000) return TOKEN.v;
  // stable_token：grant_type=client_credential
  const body = JSON.stringify({
    appid: cfg.APPID,
    secret: cfg.APP_SECRET,
    grant_type: 'client_credential'
  });
  const r = await postJson('https://api.weixin.qq.com/cgi-bin/stable_token', body);
  if (r.body && r.body.access_token) {
    TOKEN = { v: r.body.access_token, exp: now + (r.body.expires_in || 7200) * 1000 };
    return TOKEN.v;
  }
  throw new Error('stable_token 失败: ' + JSON.stringify(r.body));
}

/** 本地 key（report_single）→ 后台 pid（deliver 只认 pid） */
function pidOf(key) {
  return (cfg.GOODS[key] && cfg.GOODS[key].pid) || '';
}

exports.main = async (event) => {
  // M2 双身份：小程序语境 = OPENID；Web SDK 语境 = CloudBase uid
  const caller = await resolveCaller(cloud);
  const ident = identOf(caller);

  // ---- 只取权益 ----
  if (!event.outTradeNo) {
    const ent = await getEntitlements(db, ident);
    return { errcode: 0, entitlements: ent };
  }

  // ---- 查单 ----
  const hit = await db.collection('orders')
    .where({ out_trade_no: event.outTradeNo })
    .limit(1)
    .get();
  if (!hit.data || !hit.data.length) {
    return { errcode: 40401, errmsg: '订单不存在' };
  }
  const order = hit.data[0];

  // 已发货：不必再调平台，直接返权益
  if (order.status === 2) {
    const ent = await getEntitlements(db, ident);
    return { errcode: 0, entitlements: ent, delivered: true };
  }

  const token = await accessToken();
  // ⚠️ 键序即字节序：这个字符串签完名后原样发出，不能再 sort/format
  const qbody = JSON.stringify({
    openid: order.openid,
    env: cfg.ENV,
    order_id: order.out_trade_no
  });
  const sig = paySig('/xpay/query_order', qbody, cfg.APP_KEY);
  const url = 'https://api.weixin.qq.com/xpay/query_order'
    + '?access_token=' + encodeURIComponent(token)
    + '&pay_sig=' + sig;

  const r = await postJson(url, qbody);
  if (r.body && r.body.errcode === 0 && r.body.order) {
    const st = r.body.order.status;
    // status: 2=已支付待发货，4=已发货，5/7/8=退款相关
    if (st === 2) {
      await deliver(db, {
        openid: order.openid,
        outTradeNo: order.out_trade_no,
        wxOrderId: r.body.order.wx_order_id || order.wx_order_id || '',
        pid: pidOf(order.product_id),
        quantity: 1
      });
    }
    const ent = await getEntitlements(db, ident);
    return { errcode: 0, entitlements: ent, order_status: st };
  }
  return {
    errcode: (r.body && r.body.errcode) || 50001,
    errmsg: (r.body && r.body.errmsg) || '查单失败'
  };
};
