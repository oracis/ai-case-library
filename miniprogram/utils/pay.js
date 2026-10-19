/**
 * 购买流程封装（虚拟支付 · 个人主体）。
 *
 * 官方流程（《虚拟支付：个人》5.1）：
 *   前端请求服务器下单 → 服务器生成 outTradeNo + 签名 payData
 *   → wx.requestVirtualPayment 拉起支付
 *   → 发货以「平台推送」为准，前端 success 回调可能丢失，只做提示
 *
 * 硬规则（官方 Agent 注意事项）：
 *   - mode 固定 short_series_goods（道具直购），不做代币/预付余额
 *   - 金额单位「分」，全程不换算
 *   - env 固定 0（正式环境）
 *   - 价格以服务端配置为准，前端只传「买哪个」，绝不传价格
 */

const { FUNC } = require('./config.js');
const api = require('./api.js');

/**
 * iOS 版本校验：官方要求微信 ≥ 8.0.68（Apple IAP 依赖）。
 * 校验代码逐字来自官方文档示例。
 */
function checkIosVersion() {
  const sys = wx.getSystemInfoSync();
  if (sys.platform !== 'ios') return true;
  const cur = sys.version.split('.').map(Number);
  const base = [8, 0, 68];
  for (let i = 0; i < 3; i++) {
    if ((cur[i] || 0) > base[i]) return true;
    if ((cur[i] || 0) < base[i]) break;
  }
  wx.showModal({ title: '提示', content: '请将微信更新至最新版后再进行支付', showCancel: false });
  return false;
}

/**
 * 购买入口。
 * @param goodsKey 道具 key（report_single / export_all / member_month）
 * @param caseId   单篇报告时传案例 id，其余传 ''
 * @returns Promise<{ok, canceled, errmsg}>
 */
function buy(goodsKey, caseId) {
  if (!checkIosVersion()) return Promise.resolve({ ok: false, canceled: true });
  let outTradeNo = '';

  // 1. 登录拿 code（code 一次性；每次下单重新登录拿新的）
  return new Promise((resolve, reject) => {
    wx.login({ success: resolve, fail: reject });
  }).then((lr) => {
    // 2. 服务端下单：服务端定价格、生成 outTradeNo、算两套签名
    return api.call(FUNC.order, { productId: goodsKey, caseId: caseId || '', code: lr.code });
  }).then((order) => {
    outTradeNo = order.outTradeNo || '';
    const d = order.payData || {};
    if (!d.signData) {
      const err = new Error(order.errmsg || '下单失败：服务端未返回支付数据');
      err.errcode = order.errcode;
      throw err;
    }
    // 3. 拉起支付。⚠️ payData 由服务端整体给出，前端只补 mode 与回调，
    //    不手工拼任何签名字段（拼错即验签失败）。
    return new Promise((resolve, reject) => {
      wx.requestVirtualPayment({
        signData: d.signData,
        mode: d.mode || 'short_series_goods',
        paySig: d.paySig,
        signature: d.signature,
        success: () => resolve(),
        fail: (err) => {
          // errCode 2000 附近为用户取消；其余为真实失败
          const canceled = err && (err.errCode === 2000 || /取消|cancel/i.test(err.errMsg || ''));
          if (canceled) resolve({ canceled: true });
          else reject(err);
        }
      });
    });
  }).then((maybe) => {
    // 4. success 回调只做提示。权益以服务端发货为准 —— 用刚拿到的 outTradeNo
    //    主动查一次单兜底刷新（官方：success 回调可能丢失，不能作为发货依据）
    if (maybe && maybe.canceled) return { ok: false, canceled: true };
    return api.call(FUNC.query, { outTradeNo })
      .then((r) => ({ ok: true, entitlements: r.entitlements }))
      .catch(() => ({ ok: true, entitlements: null }));
  }).catch((err) => {
    // 云函数未部署 / 环境 ID 未配置：给可读提示而不是裸 errMsg
    const msg = err && err.errMsg ? String(err.errMsg) : '';
    if (/FunctionName.*not found|找不到|not exists/i.test(msg) || (err && err.errcode === 'SYSTEM_ERROR')) {
      return { ok: false, errmsg: '云函数未部署或环境 ID 未配置（见 miniprogram/README.md）' };
    }
    return { ok: false, errmsg: (err && err.errmsg) || msg || '支付失败，请稍后再试' };
  });
}

module.exports = { buy, checkIosVersion };
