/**
 * 签名与 HTTP 工具（官方《虚拟支付：个人》5.5 的 Node 实现）。
 *
 * 两套签名，别混：
 *   paySig     密钥是 AppKey。C 端 uri 固定 'requestVirtualPayment'；
 *              B 端（/xpay/* 服务端接口）uri 是实际接口路径、不带 query。
 *              msg = uri + '&' + post_body
 *   signature  密钥是 sessionKey（code2Session 换来）。
 *              msg = post_body
 *
 * 🔴 post_body 必须与实际发出的请求体**逐字节一致**：不格式化、不改键顺序。
 *    所以 signData 一律 JSON.stringify 一次成型，之后只传这个字符串。
 */

const crypto = require('crypto');
const https = require('https');

function paySig(uri, postBody, appKey) {
  return crypto.createHmac('sha256', appKey)
    .update(uri + '&' + postBody, 'utf8')
    .digest('hex');
}

function signature(postBody, sessionKey) {
  return crypto.createHmac('sha256', sessionKey)
    .update(postBody, 'utf8')
    .digest('hex');
}

/** 通用 JSON POST。CloudBase 云函数外网请求走这里。 */
function postJson(url, body, timeout) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname + u.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body)
      },
      timeout: timeout || 10000
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let parsed;
        try { parsed = JSON.parse(buf); } catch (e) { parsed = { raw: buf }; }
        resolve({ status: res.statusCode, body: parsed });
      });
    });
    req.on('timeout', () => req.destroy(new Error('request timeout')));
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

function getJson(url, timeout) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname + u.search,
      method: 'GET',
      timeout: timeout || 10000
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let parsed;
        try { parsed = JSON.parse(buf); } catch (e) { parsed = { raw: buf }; }
        resolve({ status: res.statusCode, body: parsed });
      });
    });
    req.on('timeout', () => req.destroy(new Error('request timeout')));
    req.on('error', reject);
    req.end();
  });
}

module.exports = { paySig, signature, postJson, getJson };
