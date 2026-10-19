/**
 * 云函数配置模板。
 *
 * 🔴 使用方式：把本文件复制为各云函数目录下的 lib/config.js 并填写真实值。
 *    （各云函数部署时上传的是自己的目录，共享目录不会被打包，
 *      所以每个云函数各带一份 —— 改这里记得同步到所有云函数。）
 *
 * 🔴 安全红线：AppKey / AppSecret 只能出现在云函数里。
 *    写进小程序端代码 = 公开（包体可被反编译）。
 *    更稳的做法是在 CloudBase 控制台 → 云函数 → 配置 → 环境变量里配，
 *    然后用 process.env.APP_KEY 读（本文件留空即可）。
 */

module.exports = {
  APPID: process.env.APPID || 'YOUR-APPID',
  OFFER_ID: process.env.OFFER_ID || 'YOUR-OFFER-ID',       // 虚拟支付 → 基本配置
  APP_KEY: process.env.APP_KEY || 'YOUR-APPKEY',           // 现网 AppKey，签 paySig 用
  APP_SECRET: process.env.APP_SECRET || 'YOUR-APPSECRET',  // 换 access_token / session_key 用

  ENV: 0,  // 0 正式环境（官方要求固定 0）；沙箱联调时才改 1

  /**
   * 道具配置 —— pid 是 MP 后台「道具管理」里创建的商品 ID（不是这里的 key）。
   * price 单位「分」，必须与后台道具价格一致，否则下单被平台拒绝。
   * kind 决定发货动作：report=解锁单篇 / export=开通导出 / member=开通会员。
   */
  GOODS: {
    report_single: { pid: process.env.PID_REPORT || 'YOUR-PID-REPORT', price: 660,  kind: 'report' },
    export_all:    { pid: process.env.PID_EXPORT || 'YOUR-PID-EXPORT', price: 1990, kind: 'export' },
    member_month:  { pid: process.env.PID_MEMBER || 'YOUR-PID-MEMBER', price: 1200, kind: 'member', days: 30 }
  },

  // sync-data 云函数用：防陌生人往你的库里写数据
  SYNC_SECRET: process.env.SYNC_SECRET || 'CHANGE-ME-TO-RANDOM'
};
