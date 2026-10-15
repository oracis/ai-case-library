/**
 * 下单云函数（虚拟支付 · 个人主体）。
 *
 * 职责（官方 5.1 流程的①）：
 *   1. 校验道具 key（不信任前端传的价格，价格以本函数配置为准）
 *   2. code2Session 换 openid + session_key
 *   3. 生成唯一 outTradeNo（8-32 位、不能下划线开头）
 *   4. 落订单（status=0 待支付）
 *   5. 组 signData → 算 paySig（AppKey 签 requestVirtualPayment）+ signature（sessionKey 签）
 *   6. 返回 payData 给前端，前端原样调 wx.requestVirtualPayment
 */

const cloud = require('wx-server-sdk');
const { paySig, signature, postJson, getJson } = require('./lib/sign.js');
const cfg = require('./lib/config.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

exports.main = async (event) => {
  const productId = event.productId;
  const code = event.code;
  const goods = cfg.GOODS[productId];
  if (!goods) return { errcode: 40010, errmsg: '未知道具：' + productId };
  if (!code) return { errcode: 40001, errmsg: '缺少登录 code' };

  // ---- 1. code2Session：openid 用来记账，session_key 是用户态签名密钥 ----
  const sess = await jscode2Session(code);
  if (!sess || !sess.openid) {
    return { errcode: 40002, errmsg: '登录态失效，请重新进入小程序' };
  }

  // ---- 2. outTradeNo：T + base36 时间戳 + 6 位随机大写字母数字 ----
  // 官方约束：每次下单重新生成、唯一、8-32 位、不能以下划线开头
  const outTradeNo = 'T' + Date.now().toString(36).toUpperCase()
    + Math.random().toString(36).slice(2, 8).toUpperCase();

  // ---- 3. 落订单 ----
  await db.collection('orders').add({
    data: {
      out_trade_no: outTradeNo,
      openid: sess.openid,
      product_id: productId,
      case_id: event.caseId || '',
      price: goods.price,
      status: 0, // 0 待支付 / 2 已发货
      created_at: Date.now()
    }
  });

  // ---- 4. signData：这个字符串本身就是验签对象，一次成型后只传递、不再改动 ----
  const signData = JSON.stringify({
    offerId: cfg.OFFER_ID,
    buyQuantity: 1,
    env: cfg.ENV,
    currencyType: 'CNY',
    productId: goods.pid,
    goodsPrice: goods.price,
    outTradeNo: outTradeNo,
    attach: JSON.stringify({ k: productId, c: event.caseId || '' })
  });

  // ---- 5. 两套签名 ----
  return {
    errcode: 0,
    outTradeNo: outTradeNo,
    payData: {
      signData: signData,
      mode: 'short_series_goods',
      paySig: paySig('requestVirtualPayment', signData, cfg.APP_KEY),
      signature: signature(signData, sess.session_key)
    }
  };
};

/** GET https://api.weixin.qq.com/sns/jscode2session */
async function jscode2Session(code) {
  const url = 'https://api.weixin.qq.com/sns/jscode2session'
    + '?appid=' + encodeURIComponent(cfg.APPID)
    + '&secret=' + encodeURIComponent(cfg.APP_SECRET)
    + '&js_code=' + encodeURIComponent(code)
    + '&grant_type=authorization_code';
  try {
    const r = await getJson(url);
    return (r.body && r.body.openid) ? r.body : null;
  } catch (e) {
    console.error('jscode2Session 失败', e);
    return null;
  }
}
