/**
 * 金额 / 口径 / 评分格式化。
 *
 * 🔴 库规红线 1：收入口径不许混用。
 *   mrr              订阅制月经常性收入；非订阅制恒 0
 *   last_30d_revenue 近 30 天总收入（含一次性买卖）；与 MRR 不可比，可差数十倍
 *   arr              年经常性收入
 *   all_time         累计
 * 所以：
 *   - 每个金额必须带口径标签，不允许裸奔；
 *   - 各口径永不相加、不互相替代、不放进同一个排序榜；
 *   - 订阅制优先展示 MRR（更能反映持续性），非订阅制看近 30 天。
 *
 * 字段说明：小程序端用的是**扁平化**结构（metrics 已由同步脚本拆平，
 * 评分已取 .score）。云函数返回的文档结构见 scripts/sync_cases_cloudbase.js
 * 的 mapCase() —— 那是唯一的数据契约，两边都别绕开它。
 */

const CALIBER_LABEL = {
  mrr: 'MRR',
  d30: '近30天',
  arr: 'ARR',
  lifetime: '累计'
};

/** 案例自带的口径标记（metrics.caliber / top-level caliber） */
const CASE_CALIBER = {
  mrr: 'MRR 口径',
  run_rate: '年化口径',
  lifetime: '累计口径',
  unknown: '口径未标注'
};

function caliberText(c) {
  return CASE_CALIBER[c.caliber] || CASE_CALIBER.unknown;
}

/** $12,000 / $1.2亿 —— 大数压缩成中文量级，小数位随量级收紧 */
function money(v) {
  if (v == null || v === '' || isNaN(v)) return '未披露';
  const n = Number(v);
  if (n <= 0) return '未披露';
  if (n >= 1e8) return '$' + trim(n / 1e8) + '亿';
  if (n >= 1e4) return '$' + trim(n / 1e4) + '万';
  return '$' + fmtInt(n);
}

function trim(n) {
  return n >= 100 ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
}

function fmtInt(n) {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * 案例的收入展示行（0-2 行）。MRR 与近 30 天同时存在时两行都出、
 * 各自带口径 —— 读者有权看到两个数，但不允许看到它们被加在一起。
 */
function revenueLines(c) {
  const out = [];
  const mrr = Number(c.mrr) || 0;
  const d30 = Number(c.last_30d_revenue) || 0;
  const arr = Number(c.arr) || 0;
  if (mrr > 0) out.push({ caliber: 'mrr', text: money(mrr) + '/月' });
  if (d30 > 0) out.push({ caliber: 'd30', text: money(d30) });
  else if (arr > 0 && mrr <= 0) out.push({ caliber: 'arr', text: money(arr) + '/年' });
  return out;
}

/** 主展示口径（列表卡片用）：订阅制看 MRR，其余看近 30 天 */
function mainRevenue(c) {
  return revenueLines(c)[0] || null;
}

const VERIF_LABEL = {
  stripe: '网关验证',
  official: '官方口径',
  partial: '第三方',
  founder: '自报',
  disputed: '来源矛盾',
  unverified: '未验证'
};

function verifText(v) {
  return VERIF_LABEL[v] || '未验证';
}

/** 双轴评分（0-100 浮点，展示取整） */
function quadOf(c) {
  return {
    solo: c.solo_score == null ? null : Math.round(c.solo_score),
    china: c.china_score == null ? null : Math.round(c.china_score)
  };
}

function scoreOf(c) {
  return c.composite_score == null ? null : Math.round(c.composite_score);
}

const TIER_LABEL = { premium: '精品', standard: '实核', backup: '备选' };
function tierText(t) {
  return TIER_LABEL[t] || '';
}

module.exports = {
  CALIBER_LABEL,
  CASE_CALIBER,
  caliberText,
  money,
  revenueLines,
  mainRevenue,
  verifText,
  quadOf,
  scoreOf,
  tierText
};
