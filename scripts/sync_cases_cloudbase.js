#!/usr/bin/env node
/**
 * 把 data/cases.json 同步到微信云开发（CloudBase）cases 集合。
 *
 * 用法：
 *   node scripts/sync_cases_cloudbase.js --url <sync-data 云函数 HTTP 触发地址> \
 *        --secret <SYNC_SECRET> [--dry] [--batch 15]
 *
 *   --dry   只做映射与统计，不发起任何请求（先用它验证数据契约）
 *
 * 设计：
 *   - 零第三方依赖（Node 18+ 内置 fetch），不装 SDK —— 云开发凭据
 *     只存在于云函数内，本地到云函数之间用 secret 单向鉴权。
 *   - 本文件的 mapCase() 是**唯一的数据契约**：扁平化（metrics 拆平、
 *     评分取 .score）只在这一次完成，云函数 get-cases 的白名单与
 *     小程序前端都按这个结构读。改契约必须三处同步。
 *   - 分片 15 条/批，避免单次请求体过大。
 *
 * 路径约定遵循项目铁律：ROOT = dirname(dirname(abspath(__file__)))，零绝对路径。
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(path.dirname(__filename));
const CASES_PATH = path.join(ROOT, 'data', 'cases.json');

/* ---------------- 数据契约（改这里 = 改全链路的字段结构） ---------------- */

/** 数值字段清洗：非有限数字一律 null（0 保留 —— 0 与「没有」是两回事） */
function num(v) {
  const n = Number(v);
  return (v === '' || v == null || !isFinite(n)) ? null : n;
}

/** '2026-10-13' / 完整时间串 → 毫秒时间戳（版本比对用） */
function tsOf(s) {
  if (!s) return 0;
  const t = Date.parse(s);
  if (!isNaN(t)) return t;
  const t2 = Date.parse(s + 'T00:00:00+08:00');
  return isNaN(t2) ? 0 : t2;
}

/**
 * cases.json 条目 → CloudBase cases 集合文档。
 * 🔴 与 cloudfunctions/get-cases/index.js 的 FIELDS 白名单保持一致。
 */
function mapCase(c) {  const m = c.metrics || {};
  const sf = c.solo_fit || {};
  const cf = c.china_fit || {};
  const comp = c.composite || {};
  return {
    id: c.id,
    name: c.name || c.name_en || c.id,
    name_en: c.name_en || '',
    one_liner: c.one_liner || '',
    origin: c.origin || '',
    industry: c.industry || '',
    category: c.category || '',
    tags: Array.isArray(c.tags) ? c.tags : [],

    // 收入（口径分离：四个字段各自独立，不相加 —— 库规红线 1）
    mrr: num(m.mrr),
    last_30d_revenue: num(m.last_30d_revenue),
    arr: num(m.arr),
    all_time: num(m.all_time != null ? m.all_time : m.all_time_revenue),
    caliber: m.caliber || c.caliber || '',
    metric_note: m.metric_note || '',
    customers: m.customers != null ? num(m.customers) : null,

    // 双轴评分（score_*.py 写入，取 .score 扁平化）
    solo_score: num(sf.score),
    china_score: num(cf.score),
    composite_score: num(comp.score),
    quadrant: comp.quadrant || '',
    quadrant_label: comp.quadrant_label || '',

    // 验证与分档
    verification: c.verification || '',
    tier: c.tier || '',
    quality_score: num(c.quality_score),

    // 正文（深度报告内容源）
    what_it_does: c.what_it_does || '',
    how_it_makes_money: c.how_it_makes_money || '',
    why_it_works: Array.isArray(c.why_it_works) ? c.why_it_works : [],
    verdict: c.verdict || '',
    judgment: c.judgment || '',
    playbook: Array.isArray(c.playbook) ? c.playbook : [],
    founder: c.founder || '',
    traffic_sources: Array.isArray(c.traffic_sources) ? c.traffic_sources : [],
    signals: Array.isArray(c.signals) ? c.signals : [],

    updated_at: c.updated_at || '',
    updated_ts: tsOf(c.updated_at)
  };
}

/* ---------------- 主流程 ---------------- */

async function main() {
  const args = process.argv.slice(2);
  const argOf = (flag) => {
    const i = args.indexOf(flag);
    return i >= 0 && i + 1 < args.length ? args[i + 1] : '';
  };
  const url = argOf('--url');
  const secret = argOf('--secret');
  const dry = args.indexOf('--dry') >= 0;
  const batchSize = Math.max(1, parseInt(argOf('--batch') || '15', 10));

  if (!fs.existsSync(CASES_PATH)) {
    console.error('[!] 找不到 ' + CASES_PATH);
    process.exit(1);
  }
  const raw = JSON.parse(fs.readFileSync(CASES_PATH, 'utf8'));
  if (!Array.isArray(raw) || !raw.length) {
    console.error('[!] cases.json 为空或不是数组');
    process.exit(1);
  }

  const mapped = raw.map(mapCase);
  const ver = String(mapped.reduce((mx, c) => Math.max(mx, c.updated_ts || 0), 0));

  // ---- 契约自检：缺 id 的条目会在云函数侧被静默跳过，必须先拦 ----
  const bad = mapped.filter((c) => !c.id);
  if (bad.length) {
    console.error('[!] ' + bad.length + ' 条缺 id，终止（云函数侧会跳过它们，造成静默漏数据）');
    process.exit(1);
  }

  console.log('案例总数: ' + mapped.length);
  console.log('版本号 ver = ' + ver + '（' + new Date(Number(ver)).toISOString() + '）');
  const withMrr = mapped.filter((c) => c.mrr != null).length;
  const withD30 = mapped.filter((c) => c.last_30d_revenue != null).length;
  const withScore = mapped.filter((c) => c.composite_score != null).length;
  console.log('有 MRR: ' + withMrr + ' · 有近30天收入: ' + withD30 + ' · 有综合分: ' + withScore);

  if (dry) {
    console.log('\n[dry] 不发起请求。映射样例（第一条）：');
    console.log(JSON.stringify(mapped[0], null, 2).slice(0, 1200) + '\n…');
    return;
  }

  if (!url || !secret) {
    console.error('[!] 缺少 --url 或 --secret（先跑 --dry 验证映射，再查 README 拿云函数地址）');
    process.exit(1);
  }

  let upserted = 0;
  const batches = Math.ceil(mapped.length / batchSize);
  for (let i = 0; i < mapped.length; i += batchSize) {
    const slice = mapped.slice(i, i + batchSize);
    const n = i / batchSize + 1;
    process.stdout.write('[' + n + '/' + batches + '] POST ' + slice.length + ' 条… ');
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret: secret, cases: slice })
    });
    if (!res.ok) {
      console.error('HTTP ' + res.status);
      process.exit(1);
    }
    const r = await res.json();
    if (!r.ok) {
      console.error('失败: ' + (r.errmsg || JSON.stringify(r)));
      process.exit(1);
    }
    upserted += r.upserted || 0;
    console.log('upserted=' + (r.upserted || 0));
  }
  console.log('\n完成：' + upserted + '/' + mapped.length + ' 条已写入 CloudBase cases 集合');
}

// 直接运行时才执行；被 require（验证脚本）时不产生副作用
if (require.main === module) {
  main().catch((e) => {
    console.error('[!] 异常:', e);
    process.exit(1);
  });
}

// 导出供验证脚本（scripts/verify_miniprogram.js）做契约比对
module.exports = { mapCase, num, tsOf, CASES_PATH };
