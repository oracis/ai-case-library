/**
 * 全局配置 —— 部署前必须修改的地方集中在这一个文件
 *
 * ⚠️ 这里只能放**公开**信息。AppKey / AppSecret 是支付密钥，
 * 一旦写进小程序端代码就等于公开（用户可反编译包体），
 * 它们只允许出现在 cloudfunctions/ 各函数的 config 里。
 */

// 云开发环境 ID：CloudBase 控制台 → 你的环境 → 环境 ID（形如 case-lib-1a2b3c）
const ENV_ID = 'YOUR-CLOUDBASE-ENV-ID';

// 云函数名（部署后若改名，改这里）
const FUNC = {
  cases: 'get-cases',        // 拉取案例全量（精简字段）
  order: 'pay-order',        // 下单：返回 payData（signData/paySig/signature）
  query: 'query-order',      // 查单兜底 + 权益刷新
  export: 'export-cases',    // 全量导出（付费功能，云函数内校验权益）
  notify: 'pay-notify'       // 发货推送（HTTP 触发，不走 callFunction）
};

/**
 * 道具清单。
 * ⚠️ 小程序端这份只用于**展示价格与名称**；真正的权威在云函数
 * pay-order 的 config 里（服务端以它为准，绝不信任前端传来的价格）。
 * ⚠️ 后台「道具管理」里的道具 ID、价格必须与云函数 config 一致，
 * 否则下单会被平台拒绝（goodsPrice 需与后台道具价格一致）。
 */
const GOODS = {
  report_single: { name: '深度报告 · 单篇', price: 660,  priceText: '¥6.60',  unit: '篇' },
  export_all:    { name: '全站案例导出',     price: 1990, priceText: '¥19.90', unit: '次' },
  member_month:  { name: '月度会员',         price: 1200, priceText: '¥12.00', unit: '月' }
};

// 会员时长（毫秒）—— 云函数按同一常量发放权益，两边要一致
const MEMBER_DAYS = 30;

module.exports = { ENV_ID, FUNC, GOODS, MEMBER_DAYS };
