/**
 * 发货推送接收（HTTP 触发型云函数）。
 *
 * 部署注意（README 有详细步骤）：
 *   - 必须用「HTTP 触发」部署，触发地址填到 MP 后台
 *     【开发与服务】-【开发管理】-【消息推送】的 URL
 *   - 微信云开发网关已做验签，**业务代码不要再验签**（CloudBase 议题明确）
 *   - 返回必须是 XML：<xml><ErrCode>0</ErrCode><ErrMsg><![CDATA[success]]></ErrMsg></xml>
 *     非 0 平台会重试，最多 15 次
 *
 * 兼容两种 event 形态：HTTP 触发（body 是 XML 字符串）与
 * 消息推送直传对象（字段直接在 event 上）。
 */

const cloud = require('wx-server-sdk');
const { xmlValue } = require('./lib/xml.js');
const { deliver } = require('./lib/deliver.js');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

const OK_XML = '<xml><ErrCode>0</ErrCode><ErrMsg><![CDATA[success]]></ErrMsg></xml>';

function resp(body) {
  return { statusCode: 200, headers: { 'Content-Type': 'application/xml' }, body: body };
}

exports.main = async (event) => {
  let xml = '';
  if (typeof event === 'string') {
    xml = event;
  } else if (event && typeof event.body === 'string') {
    xml = event.isBase64Encoded
      ? Buffer.from(event.body, 'base64').toString('utf8')
      : event.body;
  } else if (event && event.xml) {
    xml = typeof event.xml === 'string' ? event.xml : JSON.stringify(event.xml);
  }

  // 推送对象直传形态（字段直接在 event 上）
  const isDirect = !xml && event && event.Event;

  const eventName = isDirect ? event.Event : xmlValue(xml, 'Event');
  if (eventName && eventName !== 'xpay_goods_deliver_notify') {
    // 非本业务事件：直接确认收到，避免平台重试轰炸
    return resp(OK_XML);
  }

  const openid = isDirect ? (event.OpenId || '') : xmlValue(xml, 'OpenId');
  const outTradeNo = isDirect ? (event.OutTradeNo || '') : xmlValue(xml, 'OutTradeNo');
  const wxOrderId = isDirect
    ? ((event.WeChatPayInfo && event.WeChatPayInfo.MchOrderNo) || '')
    : xmlValue(xml, 'MchOrderNo');
  const pid = isDirect
    ? ((event.GoodsInfo && event.GoodsInfo.ProductId) || '')
    : xmlValue(xml, 'ProductId');
  const quantity = isDirect
    ? ((event.GoodsInfo && event.GoodsInfo.Quantity) || 1)
    : (xmlValue(xml, 'Quantity') || 1);

  if (!openid || !pid) {
    // 字段缺失：返回非 0 让平台重试（可能是推送内容异常）
    return resp('<xml><ErrCode>1</ErrCode><ErrMsg><![CDATA[bad payload]]></ErrMsg></xml>');
  }

  const r = await deliver(db, { openid, outTradeNo, wxOrderId, pid, quantity });
  if (!r.ok) {
    console.error('deliver 失败', r.errmsg);
    return resp('<xml><ErrCode>2</ErrCode><ErrMsg><![CDATA[deliver failed]]></ErrMsg></xml>');
  }
  if (r.skipped) {
    console.log('幂等命中，重复通知不重复发货:', wxOrderId || outTradeNo);
  }
  return resp(OK_XML);
};
