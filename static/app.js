/* ============================================================
   拆解海外 · 前端逻辑（无依赖）
   ============================================================ */
'use strict';

/* ---------------- 运行模式 ----------------
   本地服务（server.py）：有 /api 接口，候选池/队列的写操作可用。
   静态部署（scripts/build_static.py 产出，扔 OSS / Pages）：只有一个 data.json，
   没有后端，所以写操作必须降级为提示，而不是让按钮点了没反应。
   build_static.py 会在 index.html 里注入 window.__STATIC__ = true。 */
const IS_STATIC = !!window.__STATIC__;

/* ---------------- 常量 ---------------- */
const V_LABEL = {
  stripe:     '支付网关验证',
  official:   '官方披露',
  partial:    '口径待核',
  founder:    '创始人自报',
  disputed:   '数字有出入',
  unverified: '未核实'
};
const V_COLOR = {
  stripe:     'var(--v-stripe)',
  official:   'var(--v-official)',
  partial:    'var(--v-partial)',
  founder:    'var(--v-founder)',
  disputed:   'var(--v-disputed)',
  unverified: 'var(--v-unverified)'
};
const V_ORDER = ['stripe', 'official', 'partial', 'founder', 'disputed', 'unverified'];

const KIND_LABEL = {
  stripe: '支付验证', official: '官方', press: '报道', review: '核查', founder: '自述'
};

const REP_LABEL = {
  tech: '技术门槛', distribution: '获客门槛', capital: '资金门槛', timing: '时机依赖'
};
const REP_NOTE = '1 分＝最容易，5 分＝最难。四个维度里只要有一个是 5 分，一个人基本做不了。';

/* 国内移植可行性：打的是「能不能搬回国内做」，不是「一个人能不能做」。
   两件事经常结论相反，所以单独一套维度、单独一个视图。 */
const CHINA_DIM_LABEL = {
  demand: '付费意愿',
  payment: '支付可达',
  compliance: '合规空间',
  acquisition: '获客迁移',
  localization: '改造成本',
  competition: '竞争空位',
};
const CHINA_DIM_ORDER = ['demand', 'payment', 'compliance', 'acquisition', 'localization', 'competition'];
const CHINA_DIM_DESC = {
  demand: '国内目标客户会不会真掏钱（权重最高）',
  payment: '国内能不能顺畅收款（Stripe 用不了）',
  compliance: '越不碰监管红线分越高',
  acquisition: '海外获客渠道在国内有没有等价物',
  localization: '本地化改造量，越小分越高',
  competition: '国内是否已有强势免费替代',
};
/* 个人可做性：把 replicability 四维翻成正向 + 补一个「单人交付」维度。
   build/reach/capital/window 由 replicability 反推（build = 6 - tech，以此类推），
   保证两套数据永不打架；delivery 是原数据没有、人工判断的。
   权重：单人交付 1.4｜够得着客户 1.3｜启动轻 1.1｜造得出来 1.0｜窗口 1.0 */
const SOLO_DIM_LABEL = {
  build: '造得出来',
  delivery: '单人交付',
  reach: '够得着客户',
  capital: '启动轻',
  window: '窗口还开着'
};
const SOLO_DIM_ORDER = ['build', 'delivery', 'reach', 'capital', 'window'];
const SOLO_DIM_DESC = {
  build: '技术栈在不在一个人射程内',
  delivery: '不用团队 / 资质 / 7×24 值守',
  reach: '不靠销售团队就能触达',
  capital: '不需要先烧钱就能开张',
  window: '现在进场还有没有位置'
};

/* 四象限文案，与 scripts/score_solo_fit.py 的 QUADRANTS、server.py 的 QUAD_META 保持一致 */
const QUAD_META = {
  go: { label: '可以开干', desc: '两边都过线：一个人能做，国内也有市场。' },
  export: { label: '能做，但别在国内卖', desc: '技术完全在手，卡在国内的需求或支付土壤上。出口做更顺。' },
  partner: { label: '有市场，但一个人啃不动', desc: '需求是真的，门槛在资质、大客户销售或团队交付上。' },
  skip: { label: '别碰', desc: '两个方向都不过线。' }
};
const QUAD_ORDER = ['go', 'partner', 'export', 'skip'];
const SOLO_NOTE = '5 分＝最容易一个人做。其中「单人交付」是新增维度：要团队、要资质、要 7×24 值守的，一票否决。';

const MEDAL_LABEL = { gold: '金', silver: '银', bronze: '铜' };
const MEDAL_ICON = { gold: '🥇', silver: '🥈', bronze: '🥉' };

const READ_KEY = 'acl_read_v1';
const GOAL = 50;

/* ---------------- 状态 ---------------- */
let DATA = { cases: [], candidates: [], inbox: [], sources: {}, stats: {} };
let view = 'cases';
let q = '';
let sortBy = 'default';
let verifFilter = '';
let tagFilter = '';
let inboxSource = '';
let fitDim = '';        // '' | 'composite' | 'solo' | 'china' —— 按哪个分筛
let fitLevel = 0;       // 0 = 不限，否则为分数下限
let quadFilter = '';    // '' | go / partner / export / skip
let readSet = new Set();

/* ---------------- 工具 ---------------- */
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function num(n) {
  if (n == null || isNaN(n)) return null;
  return Number(n).toLocaleString('en-US');
}

/** 从 URL 里取出好读的域名，用作链接文字 */
function prettyHost(u) {
  try {
    return String(u).replace(/^https?:\/\//, '').replace(/\/$/, '').replace(/^www\./, '').slice(0, 60);
  } catch (e) { return String(u || '').slice(0, 60); }
}

/**
 * 卡片底部的两条链接：官网 + 来源页。
 * 官网（website）与来源（source_url）是两回事——官网是产品自己的站，
 * 来源是我们从哪儿看到的它。两者都缺时给一行灰字说明，不要整段不渲染，
 * 否则页面上看着像「这个产品没有官网」。
 */
function cardLinks(c) {
  const out = [];
  if (c.website) {
    out.push(`<a href="${esc(c.website)}" target="_blank" rel="noopener" class="cand-link" data-k="web">官网 ${esc(prettyHost(c.website))} ↗</a>`);
  }
  if (c.source_url) {
    out.push(`<a href="${esc(c.source_url)}" target="_blank" rel="noopener" class="cand-link" data-k="src">来源 ${esc(prettyHost(c.source_url))} ↗</a>`);
  }
  if (!out.length) {
    return '<div class="cand-link is-none">来源未记录</div>';
  }
  return '<div class="cand-links">' + out.join('') + '</div>';
}

/** 把收入数字压缩成好读的形式 */
function money(n) {
  if (n == null || !isFinite(n) || n <= 0) return null;
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(n >= 1e7 ? 1 : 2).replace(/\.?0+$/, '') + 'M';
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.?0+$/, '') + 'K';
  return '$' + n;
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._tm);
  t._tm = setTimeout(() => { t.hidden = true; }, 2200);
}

/** 案例的主要可比金额（用于排序与展示） */
function primaryMoney(c) {
  const m = c.metrics || {};
  return m.arr || m.mrr || m.all_time || null;
}

function correctionCount(c) {
  return (c.corrections || []).length;
}

/** 可复刻度总分：四项之和，越低越好做 */
function repTotal(c) {
  const r = c.replicability || {};
  const vals = [r.tech, r.distribution, r.capital, r.timing].filter((v) => typeof v === 'number');
  if (!vals.length) return 999;
  return vals.reduce((a, b) => a + b, 0);
}

/** 没有 solo_fit 时的兜底文案（用原始的复刻难度合计） */
function repTxtOf(c) {
  const rep = repTotal(c);
  return rep === 999 ? '—' : '复刻难度 ' + rep + '/20';
}

/** 国内移植可行性分数（0-100）；没评过分的返回 null */
function chinaScore(c) {
  const f = c.china_fit;
  return f && typeof f.score === 'number' ? f.score : null;
}

/** 移植分档位：>=70 高，55-70 中，<55 低 */
function chinaTier(score) {
  if (score === null || score === undefined) return 'na';
  if (score >= 70) return 'hi';
  if (score >= 55) return 'mid';
  return 'lo';
}

/** 已评分的案例，按名次排好 */
function chinaRanked() {
  return DATA.cases
    .filter((c) => c.china_fit && typeof c.china_fit.rank === 'number')
    .sort((a, b) => a.china_fit.rank - b.china_fit.rank);
}

/** 高分在前；null（没评过）一律排最后 */
function hiFirst(a, b) {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

/** 个人可做性分数（0-100）；没评过返回 null */
function soloScore(c) {
  const f = c.solo_fit;
  return f && typeof f.score === 'number' ? f.score : null;
}

/** 个人可做性分档：>=70 高，55-70 中，<55 低 */
function soloTier(score) {
  if (score === null || score === undefined) return 'na';
  if (score >= 70) return 'hi';
  if (score >= 55) return 'mid';
  return 'lo';
}

/** 双轴综合分；没有 china_fit 或 solo_fit 的返回 null */
function dualScore(c) {
  const f = c.composite;
  return f && typeof f.score === 'number' ? f.score : null;
}

function soloRanked() {
  return DATA.cases
    .filter((c) => c.solo_fit && typeof c.solo_fit.rank === 'number')
    .sort((a, b) => a.solo_fit.rank - b.solo_fit.rank);
}

function dualRanked() {
  return DATA.cases
    .filter((c) => c.composite && typeof c.composite.rank === 'number')
    .sort((a, b) => a.composite.rank - b.composite.rank);
}

/** 取某个象限里的案例（已按综合分排好） */
function quadCases(key) {
  return dualRanked().filter((c) => c.composite.quadrant === key);
}

/* ---------------- 已读进度 ---------------- */
function loadRead() {
  try {
    readSet = new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]'));
  } catch (e) { readSet = new Set(); }
}
function saveRead() {
  try { localStorage.setItem(READ_KEY, JSON.stringify([...readSet])); } catch (e) { /* 忽略 */ }
}
function renderRead() {
  const n = [...readSet].filter((id) => DATA.cases.some((c) => c.id === id)).length;
  $('read-n').textContent = n;
  $('read-goal').textContent = GOAL;
  $('read-bar').style.width = Math.min(100, (n / GOAL) * 100) + '%';
  const track = $('readtrack');
  const hint = $('read-hint');
  if (n >= GOAL) {
    track.classList.add('done');
    hint.textContent = '目标达成。接下来该做的是：把其中 3 个模式套到自己想做的方向上。';
  } else if (n === 0) {
    track.classList.remove('done');
    hint.textContent = '点开卡片就算读过一个。看够 50 个，判断力才会长出来。';
  } else {
    track.classList.remove('done');
    hint.textContent = '还差 ' + (GOAL - n) + ' 个。别只刷首页，点进去看「怎么赚钱」那一段。';
  }
}

/* ---------------- 渲染：顶部统计 ---------------- */
function renderStats() {
  const s = DATA.stats || {};
  const items = [
    { n: s.curated || 0, k: '精写案例', cls: '' },
    { n: s.candidates || 0, k: '候选池', cls: '' },
    { n: s.inbox || 0, k: '采集队列', cls: '' },
    { n: s.verified || 0, k: '已核实（支付/官方）', cls: 'g' },
    { n: s.flagged || 0, k: '修正标记', cls: 'a' },
    { n: s.categories || 0, k: '覆盖分类', cls: 'b' }
  ];
  $('stat-strip').innerHTML = items.map((it) =>
    `<div class="stat"><div class="stat-n ${it.cls}">${esc(it.n)}</div><div class="stat-k">${esc(it.k)}</div></div>`
  ).join('');

  $('nav-count-cases').textContent = s.curated || 0;
  $('nav-count-cands').textContent = s.candidates || 0;
  $('nav-count-inbox').textContent = s.inbox || 0;
  $('nav-count-china').textContent = s.china_scored || 0;
  $('nav-count-dual').textContent = s.dual_scored || 0;

  $('side-meta').innerHTML =
    `<div>案例 <b>${s.curated || 0}</b> · 候选 <b>${s.candidates || 0}</b></div>` +
    `<div>已核实 <b>${s.verified || 0}</b> · 修正 <b>${s.flagged || 0}</b></div>` +
    `<div style="margin-top:6px">生成 <b>${esc(DATA.generated_at || '')}</b></div>` +
    (IS_STATIC ? '<div class="ro-mode">只读快照 · 静态部署</div>' : '');

  renderFunnel();
}

function renderFunnel() {
  const s = DATA.stats || {};
  const raw = s.inbox || 0;
  const cand = s.candidates || 0;
  const cur = s.curated || 0;
  const max = Math.max(raw, cand, cur, 1);

  const row = (lab, n, cls) => `
    <div class="fn-row">
      <span class="fn-lab">${lab}</span>
      <span class="fn-track"><span class="fn-bar ${cls}" style="width:${Math.max(2, (n / max) * 100)}%"></span></span>
      <span class="fn-n">${n}</span>
    </div>`;

  $('funnel').innerHTML =
    '<div class="funnel-title">三级漏斗</div>' +
    row('原始素材', raw, 'fn-raw') +
    row('候选池', cand, 'fn-cand') +
    row('精写案例', cur, 'fn-case') +
    `<div class="fn-note">素材多、精写少，是正常的。这个库不是一次做完的，是边看边长。</div>`;
}

function renderVigilance() {
  const s = DATA.stats || {};
  const partial = (s.by_verification || {}).partial || 0;
  const founder = (s.by_verification || {}).founder || 0;
  const disputed = (s.by_verification || {}).disputed || 0;
  const unver = (s.by_verification || {}).unverified || 0;
  const unresolved = partial + founder + disputed + unver;

  $('vigilance').innerHTML =
    '<span class="v-ico">▲</span><div>' +
    `本库共 <b>${s.curated || 0}</b> 条案例，其中 <b>${s.verified || 0}</b> 条的收入已被支付网关或官方口径核实；` +
    `另有 <em>${unresolved}</em> 条只到「创始人自报 / 口径待核」，` +
    `以及 <em>${s.flagged || 0}</em> 处已发现的数字出入。` +
    '<b>凡标注待核的数字，引用前请自己再搜一遍。</b>' +
    '</div>';
}

/* ---------------- 渲染：模式芯片 ---------------- */
function renderChips() {
  const byModel = (DATA.stats || {}).by_model || {};
  const models = Object.keys(byModel);
  let html = `<button class="chip plain${tagFilter === '' ? ' on' : ''}" data-tag="">全部模式</button>`;
  html += models.map((m) =>
    `<button class="chip${tagFilter === m ? ' on' : ''}" data-tag="${esc(m)}">${esc(m)}` +
    `<span class="c-n">${byModel[m]}</span></button>`
  ).join('');
  $('chips').innerHTML = html;

  $('chips').querySelectorAll('.chip').forEach((el) => {
    el.onclick = () => {
      tagFilter = el.dataset.tag || '';
      renderChips();
      renderCases();
    };
  });
}

/* ---------------- 过滤 & 排序 ---------------- */
function filtered() {
  let list = DATA.cases.slice();

  if (q) {
    const s = q.toLowerCase();
    list = list.filter((c) => {
      const hay = [
        c.name, c.name_en, c.one_liner, c.category, c.industry, c.origin,
        c.verdict, c.what_it_does, c.how_it_makes_money,
        (c.tags || []).join(' '), (c.models || []).join(' '),
        ((c.metrics || {}).headline) || ''
      ].filter(Boolean).join(' ').toLowerCase();
      return hay.indexOf(s) !== -1;
    });
  }

  if (verifFilter) list = list.filter((c) => c.verification === verifFilter);
  if (tagFilter) list = list.filter((c) => (c.models || []).indexOf(tagFilter) !== -1);

  // 按分筛：象限（只在评过综合分的案例上有效）
  if (quadFilter) {
    list = list.filter((c) => c.composite && c.composite.quadrant === quadFilter);
  }
  // 按分筛：维度 × 分数下限
  if (fitDim && fitLevel > 0) {
    const pick = fitDim === 'composite' ? dualScore
      : fitDim === 'solo' ? soloScore : chinaScore;
    list = list.filter((c) => {
      const v = pick(c);
      return v !== null && v >= fitLevel;
    });
  }

  if (sortBy === 'revenue') {
    list.sort((a, b) => (primaryMoney(b) || -1) - (primaryMoney(a) || -1));
  } else if (sortBy === 'corrections') {
    list.sort((a, b) => correctionCount(b) - correctionCount(a));
  } else if (sortBy === 'replicable') {
    list.sort((a, b) => repTotal(a) - repTotal(b));
  } else if (sortBy === 'solo') {
    list.sort((a, b) => hiFirst(soloScore(a), soloScore(b)));
  } else if (sortBy === 'composite') {
    list.sort((a, b) => hiFirst(dualScore(a), dualScore(b)));
  } else if (sortBy === 'china') {
    // 国内移植可行性：高分在前，没评过分的排最后
    list.sort((a, b) => {
      const sa = chinaScore(a);
      const sb = chinaScore(b);
      if (sa === null && sb === null) return 0;
      if (sa === null) return 1;
      if (sb === null) return -1;
      return sb - sa;
    });
  } else if (sortBy === 'recent') {
    list.sort((a, b) => String(b.verified_at || '').localeCompare(String(a.verified_at || '')));
  }
  return list;
}

/* ---------------- 渲染：案例卡片 ---------------- */
function renderCases() {
  const list = filtered();
  const grid = $('grid-cases');
  grid.innerHTML = list.map(cardHTML).join('');
  $('empty-cases').hidden = list.length > 0;

  grid.querySelectorAll('.card').forEach((el) => {
    el.onclick = () => openCase(el.dataset.id);
  });
}

function cardHTML(c) {
  const m = c.metrics || {};
  const pm = primaryMoney(c);
  const cc = correctionCount(c);
  const cs = chinaScore(c);
  const ss = soloScore(c);
  const ds = dualScore(c);
  const cf = c.china_fit || {};
  const df = c.composite || {};
  const medal = cf.medal ? `<b class="medal-inline m-${esc(cf.medal)}">${MEDAL_ICON[cf.medal]}</b>` : '';
  const chinaHTML = cs === null ? '' :
    `${medal}<span class="cf-china t-${chinaTier(cs)}" title="国内移植可行性 ${cs}/100">国 ${cs}</span>` +
    (cf.blocker ? `<span class="cf-block" title="硬伤：${esc(cf.blocker)}">⚠</span>` : '');
  // 个人可做性：有 solo_fit 就用它，否则退回原始的复刻难度合计
  const soloHTML = ss === null
    ? `<span>${esc(repTxtOf(c))}</span>`
    : `<span class="cf-solo t-${soloTier(ss)}" title="个人可做性 ${ss}/100">个 ${ss}</span>`;
  const dualHTML = ds === null ? ''
    : `<span class="cf-dual q-${esc(df.quadrant || 'na')}" title="综合分 ${ds}/100 · ${esc(df.quadrant_label || '')}">综 ${ds}</span>`;

  const metricHTML = m.headline
    ? `<div class="card-metric${pm ? '' : ' is-null'}">${esc(pm ? money(pm) : m.headline)}` +
      (pm ? `<small>${esc(m.headline)}</small>` : '') + '</div>'
    : '';

  const tags = (c.tags || []).slice(0, 4).map((t) => `<span class="tg">${esc(t)}</span>`).join('');

  return `
    <article class="card${readSet.has(c.id) ? ' read' : ''}" data-id="${esc(c.id)}"
             style="--vc:${V_COLOR[c.verification] || 'var(--border-2)'}">
      <div class="card-top">
        <div class="card-name">${esc(c.name)}
          ${c.name_en && c.name_en !== c.name ? `<small>${esc(c.name_en)}</small>` : ''}
        </div>
        <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未标注')}</span>
      </div>
      <div class="card-liner">${esc(c.one_liner || '')}</div>
      ${metricHTML}
      <div class="card-tags">${tags}</div>
      <div class="card-foot">
        <span>${esc(c.category || '')}${c.industry ? ' · ' + esc(c.industry) : ''}</span>
        <span class="cf-r">${cc ? `<span class="flag">修正 ${cc}</span>` : ''}${soloHTML}${chinaHTML}${dualHTML}</span>
      </div>
    </article>`;
}

/* ---------------- 渲染：候选池 ---------------- */
function renderCandidates() {
  const list = DATA.candidates.slice();
  $('grid-cands').innerHTML = list.map((c) => {
    const hl = (c.metrics || {}).headline;
    return `
      <article class="cand">
        <div class="cand-h">
          <div>
            <div class="cand-name">${esc(c.name)}</div>
            ${c.name_en && c.name_en !== c.name ? `<div style="font-size:11px;color:var(--text-3);font-family:var(--mono)">${esc(c.name_en)}</div>` : ''}
          </div>
          <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未核实')}</span>
        </div>
        <div class="card-liner">${esc(c.one_liner || '')}</div>
        ${hl ? `<div class="card-metric is-null">${esc(hl)}</div>` : ''}
        ${c.note ? `<div class="cand-note">${esc(c.note)}</div>` : ''}
        ${c.blocking ? `<div class="cand-block"><b>卡在哪：</b><span>${esc(c.blocking)}</span></div>` : ''}
        ${cardLinks(c)}
        <div class="card-foot">
          <span>${esc(c.added_at || '')}</span>
        </div>
      </article>`;
  }).join('');
}

/* ---------------- 渲染：采集队列 ---------------- */
function renderInbox() {
  const all = DATA.inbox || [];

  // 按来源统计，做成可点的筛选
  const bySrc = {};
  all.forEach((c) => {
    const k = c.harvest_source || 'unknown';
    bySrc[k] = (bySrc[k] || 0) + 1;
  });
  const srcLabel = { hn: 'Hacker News', trustmrr: 'TrustMRR', ph: 'Product Hunt',
                     all: '全部源', unknown: '未标注' };

  let list = inboxSource ? all.filter((c) => (c.harvest_source || 'unknown') === inboxSource) : all;

  $('inbox-bar').innerHTML =
    `<span>队列共 <b style="color:var(--text)">${all.length}</b> 条` +
    (inboxSource ? `，当前筛选 <b style="color:var(--text)">${list.length}</b> 条` : '') + '</span>' +
    '<button class="src-link" data-isrc="">全部</button>' +
    Object.keys(bySrc).sort((a, b) => bySrc[b] - bySrc[a]).map((k) =>
      `<button class="src-link" data-isrc="${esc(k)}"${inboxSource === k ? ' style="border-color:var(--border-2);color:var(--text)"' : ''}>` +
      `${esc(srcLabel[k] || k)} ${bySrc[k]}</button>`
    ).join('');

  $('inbox-bar').querySelectorAll('[data-isrc]').forEach((b) => {
    b.onclick = () => { inboxSource = b.dataset.isrc || ''; renderInbox(); };
  });

  const limit = 200;
  const shown = list.slice(0, limit);

  $('grid-inbox').innerHTML = shown.map((c) => {
    const hl = (c.metrics || {}).headline;
    return `
      <article class="cand">
        <div class="cand-h">
          <div class="cand-name">${esc(c.name)}</div>
          <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未核实')}</span>
        </div>
        ${c.one_liner ? `<div class="card-liner">${esc(c.one_liner).slice(0, 200)}</div>` : ''}
        ${hl ? `<div class="card-metric is-null">${esc(hl)}</div>` : ''}
        ${cardLinks(c)}
        <div class="card-foot">
          <span>${esc(srcLabel[c.harvest_source] || c.harvest_source || '')} · ${esc(c.added_at || '')}</span>
        </div>
      </article>`;
  }).join('') + (list.length > limit
    ? `<div class="empty" style="grid-column:1/-1">还有 ${list.length - limit} 条未显示。用上面的来源筛选缩小范围，或直接跑一次 scripts/harvest.py 覆盖旧数据。</div>`
    : '');

  if (!shown.length) {
    /* 队列为空有两种完全不同的原因，文案不能说同一句：
       ① 精简构建（--no-inbox）—— 队列是**故意不带**的，它只对本地有用；
       ② 真没采到 —— 那才是需要跑一次 harvest.py 的情况。
       混在一起说，读者会以为这个站的采集坏了。 */
    const excluded = (DATA.stats || {}).inbox_included === false;
    $('grid-inbox').innerHTML = excluded
      ? '<div class="empty" style="grid-column:1/-1">这一版是<b>精简构建</b>，不含采集队列' +
        '—— 队列只在本机跑采集时有用，案例与候选池都在上面。<br><br>' +
        '想看队列，在本机构建时去掉 <code style="font-family:var(--mono);color:var(--money)">--no-inbox</code>。</div>'
      : '<div class="empty" style="grid-column:1/-1">采集队列是空的。<br><br>' +
        '跑一次采集：<code style="font-family:var(--mono);color:var(--money)">python scripts/harvest.py --source all</code><br>' +
        '（Hacker News 免 key 可直接跑；Product Hunt 需要 token）</div>';
  }

}

/* ---------------- 渲染：国内移植排行 ---------------- */

function renderChinaKey() {
  const items = CHINA_DIM_ORDER.map((k) =>
    `<span class="ck-item"><b>${esc(CHINA_DIM_LABEL[k])}</b>${esc(CHINA_DIM_DESC[k])}</span>`
  ).join('');
  $('china-key').innerHTML =
    `<div class="ck-title">六个维度 · 各 1–5 分（5 = 在国内最好做）</div>` +
    `<div class="ck-list">${items}</div>` +
    `<div class="ck-note">权重：付费意愿 ×1.5，支付可达 ×1.2，合规空间 ×1.2，其余 ×1.0；满分 34.5 归一化到 100。</div>`;
}

function chinaBars(dims) {
  return CHINA_DIM_ORDER.map((k) => {
    const v = (dims || {})[k];
    if (typeof v !== 'number') return '';
    return `<span class="cbar l${v}" title="${esc(CHINA_DIM_LABEL[k])}：${v}/5">
              <i>${esc(CHINA_DIM_LABEL[k])}</i><b>${v}</b>
            </span>`;
  }).join('');
}

function renderChina() {
  renderChinaKey();
  const list = chinaRanked();
  const board = $('china-board');
  if (!board) return;
  if (!list.length) {
    board.innerHTML = '<div class="empty">还没有评分数据。先跑 <code>python scripts/score_china_fit.py</code>。</div>';
    return;
  }
  board.innerHTML = list.map((c) => {
    const f = c.china_fit;
    const tier = chinaTier(f.score);
    const medal = f.medal
      ? `<div class="cb-medal m-${esc(f.medal)}">${MEDAL_ICON[f.medal]}<span>${esc(MEDAL_LABEL[f.medal])}牌</span></div>`
      : `<div class="cb-rank">${f.rank}</div>`;
    return `
      <article class="cboard-row t-${tier}${f.medal ? ' is-medal m-' + esc(f.medal) : ''}" data-id="${esc(c.id)}">
        ${medal}
        <div class="cb-main">
          <div class="cb-head">
            <span class="cb-name">${esc(c.name)}</span>
            <span class="cb-cat">${esc(c.category || '')}</span>
            ${f.blocker ? `<span class="cb-block">硬伤：${esc(f.blocker)}</span>` : ''}
          </div>
          <div class="cb-liner">${esc(c.one_liner || '')}</div>
          <div class="cb-bars">${chinaBars(f.dims)}</div>
          <div class="cb-note">${esc(f.note || '')}</div>
        </div>
        <div class="cb-score t-${tier}"><b>${f.score}</b><small>/ 100</small></div>
      </article>`;
  }).join('');

  board.querySelectorAll('.cboard-row').forEach((el) => {
    el.onclick = () => openCase(el.dataset.id);
  });
}

/** 案例列表顶部的迷你领奖台；有筛选条件时自动隐藏 */
function renderPodium() {
  const box = $('china-podium');
  if (!box) return;
  const top3 = chinaRanked().slice(0, 3);
  const busy = q || verifFilter || tagFilter || quadFilter || (fitDim && fitLevel > 0)
    || sortBy !== 'default' || view !== 'cases';
  if (!top3.length || busy) {
    box.innerHTML = '';
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.innerHTML =
    `<div class="podium-title">国内移植可行性 · 金银铜（点开看依据）</div>` +
    `<div class="podium-grid">` +
    top3.map((c) => {
      const f = c.china_fit;
      return `<button class="pod m-${esc(f.medal)}" data-id="${esc(c.id)}">
        <span class="pod-medal">${MEDAL_ICON[f.medal]}</span>
        <span class="pod-name">${esc(c.name)}</span>
        <span class="pod-score">${f.score}</span>
      </button>`;
    }).join('') +
    `</div>`;
  box.querySelectorAll('.pod').forEach((el) => {
    el.onclick = () => openCase(el.dataset.id);
  });
}

/* ---------------- 渲染：综合排行（个人可做性 × 国内移植性） ---------------- */

const FIT_DIM_NAME = { composite: '综合分', solo: '个人可做性', china: '国内移植性' };

function renderDualFormula() {
  const box = $('dual-formula');
  if (!box) return;
  box.innerHTML =
    `<div class="df-line">综合分 = <b>0.6 × 短板</b> ＋ <b>0.4 × 均值</b>` +
    `&nbsp;·&nbsp;及格线 <b>70</b>（脚本里用 <code>--threshold</code> 可改）</div>` +
    `<div class="df-eg">90 × 40 → <b>50</b>&nbsp;&nbsp;｜&nbsp;&nbsp;60 × 60 → <b>60</b>` +
    `&nbsp;&nbsp;<span class="df-hint">双及格 &gt; 单点突出</span></div>`;
}

/** 双轴对比条：一眼看出短板在哪一边 */
function dualAxisHTML(solo, china) {
  const row = (label, v, cls) => {
    const w = Math.max(0, Math.min(100, typeof v === 'number' ? v : 0));
    return `<div class="axis-row ${cls}">` +
      `<span class="axis-k">${label}</span>` +
      `<span class="axis-track"><i style="width:${w}%"></i></span>` +
      `<b class="axis-v">${typeof v === 'number' ? v : '—'}</b></div>`;
  };
  return `<div class="db-axis">${row('个人可做', solo, 'solo')}${row('国内移植', china, 'china')}</div>`;
}

/** 个人可做性五维小条（复用移植排行那套 cbar 样式） */
function soloBars(dims) {
  return SOLO_DIM_ORDER.map((k) => {
    const v = (dims || {})[k];
    if (typeof v !== 'number') return '';
    return `<span class="cbar l${v}" title="${esc(SOLO_DIM_LABEL[k])}：${v}/5">
              <i>${esc(SOLO_DIM_LABEL[k])}</i><b>${v}</b>
            </span>`;
  }).join('');
}

/** 榜单行；kind = 'composite' 或 'solo' */
function dualBoardHTML(list, kind) {
  return list.map((c) => {
    const f = kind === 'composite' ? c.composite : c.solo_fit;
    if (!f) return '';
    const score = f.score;
    const tier = kind === 'composite'
      ? (score >= 70 ? 'hi' : score >= 55 ? 'mid' : 'lo')
      : soloTier(score);
    const medal = f.medal
      ? `<div class="db-rank is-medal m-${esc(f.medal)}">${MEDAL_ICON[f.medal]}</div>`
      : `<div class="db-rank">${f.rank}</div>`;
    const q = c.composite;
    const qtag = (kind === 'composite' && q && q.quadrant && QUAD_META[q.quadrant])
      ? `<span class="db-q q-${esc(q.quadrant)}">${esc(QUAD_META[q.quadrant].label)}</span>`
      : '';
    const body = kind === 'composite'
      ? dualAxisHTML(f.solo, f.china)
      : `<div class="db-bars">${soloBars(f.dims)}</div>`;
    const note = kind === 'solo' && f.delivery_note
      ? `<div class="db-note">交付：${esc(f.delivery_note)}</div>` : '';
    return `
      <article class="dboard-row t-${tier}${f.medal ? ' is-medal m-' + esc(f.medal) : ''}" data-id="${esc(c.id)}">
        ${medal}
        <div class="db-main">
          <div class="db-head">
            <span class="db-name">${esc(c.name)}</span>
            <span class="db-cat">${esc(c.category || '')}</span>
            ${qtag}
          </div>
          ${body}
          ${note}
        </div>
        <div class="db-score t-${tier}"><b>${score}</b><small>/ 100</small></div>
      </article>`;
  }).join('');
}

function renderQuads() {
  const box = $('dual-quads');
  if (!box) return;
  if (!dualRanked().length) {
    box.innerHTML = '<div class="empty">还没有综合分数据。依次跑 '
      + '<code>python scripts/score_china_fit.py</code> 和 '
      + '<code>python scripts/score_solo_fit.py</code>。</div>';
    return;
  }
  box.innerHTML = QUAD_ORDER.map((key) => {
    const meta = QUAD_META[key];
    const grp = quadCases(key);
    const items = grp.map((c) => {
      const f = c.composite;
      return `<button class="q-item" data-id="${esc(c.id)}" title="${esc(c.one_liner || '')}">
        <span class="q-name">${esc(c.name)}</span>
        <span class="q-scores">
          <i title="个人可做性">个 ${f.solo}</i>
          <i title="国内移植性">国 ${f.china}</i>
          <i class="q-total" title="综合分">综 ${f.score}</i>
        </span>
      </button>`;
    }).join('');
    return `
      <div class="quad q-${esc(key)}">
        <div class="q-head">
          <span class="q-title">${esc(meta.label)}</span>
          <span class="q-n">${grp.length}</span>
        </div>
        <div class="q-desc">${esc(meta.desc)}</div>
        <div class="q-list">${items || '<div class="q-none">（空）</div>'}</div>
      </div>`;
  }).join('');

  box.querySelectorAll('.q-item').forEach((el) => {
    el.onclick = () => openCase(el.dataset.id);
  });
}

function renderDual() {
  renderDualFormula();
  renderQuads();
  const dual = dualRanked();
  const solo = soloRanked();
  const b1 = $('dual-board');
  const b2 = $('solo-board');
  if (b1) {
    b1.innerHTML = dual.length
      ? dualBoardHTML(dual, 'composite')
      : '<div class="empty">还没有综合分数据。跑 <code>python scripts/score_solo_fit.py</code>。</div>';
  }
  if (b2) {
    b2.innerHTML = solo.length
      ? dualBoardHTML(solo, 'solo')
      : '<div class="empty">还没有个人可做性数据。跑 <code>python scripts/score_solo_fit.py</code>。</div>';
  }
  [b1, b2].forEach((b) => {
    if (!b) return;
    b.querySelectorAll('.dboard-row').forEach((el) => {
      el.onclick = () => openCase(el.dataset.id);
    });
  });
}

/* ---------------- 渲染：按分筛选栏 ---------------- */

function renderFilterBar() {
  const btn = $('fb-reset');
  const cnt = $('fb-count');
  if (!cnt) return;
  const parts = [];
  if (quadFilter && QUAD_META[quadFilter]) parts.push(QUAD_META[quadFilter].label);
  if (fitDim && fitLevel > 0) parts.push(`${FIT_DIM_NAME[fitDim] || fitDim} ≥ ${fitLevel}`);
  if (btn) btn.hidden = !parts.length;
  if (!parts.length) {
    cnt.textContent = '';
    cnt.className = 'fb-count';
    return;
  }
  const n = filtered().length;
  cnt.textContent = `筛选中：${parts.join(' ＋ ')} · 命中 ${n} 条`;
  cnt.className = 'fb-count on' + (n === 0 ? ' empty' : '');
}

function resetFitFilter() {
  quadFilter = '';
  fitDim = '';
  fitLevel = 0;
  const a = $('fitdim'); if (a) a.value = '';
  const b = $('fitlevel'); if (b) b.value = '0';
  const c = $('quad'); if (c) c.value = '';
  renderCases();
  renderPodium();
  renderFilterBar();
}

/* ---------------- 渲染：信息源 ---------------- */
function renderSources() {
  const s = DATA.sources || {};
  const fr = s.filter_rules || {};

  $('filterbox').innerHTML =
    '<h3>采集过滤器（去掉广告与新闻稿）</h3>' +
    '<div class="rule-row"><div class="rule-lab drop">丢弃关键词</div><div class="rule-tags">' +
      (fr.drop_keywords || []).map((k) => `<span class="rule-tag">${esc(k)}</span>`).join('') +
    '</div></div>' +
    '<div class="rule-row"><div class="rule-lab keep">保留信号</div><div class="rule-tags">' +
      (fr.keep_signals || []).map((k) => `<span class="rule-tag">${esc(k)}</span>`).join('') +
    '</div></div>' +
    (fr.note ? `<div class="rule-note">${esc(fr.note)}</div>` : '');

  $('src-list').innerHTML = (s.sources || []).map((src) => `
    <article class="src-card">
      <div class="src-head">
        <span class="src-name">${esc(src.name)}</span>
        <span class="src-tier" data-t="${esc(src.tier)}">可信度 ${esc(src.tier)}</span>
        ${src.enabled === false ? '<span class="badge" data-v="unverified">未启用</span>' : ''}
        <span class="src-url">${esc(src.url || '')}</span>
      </div>
      <div class="src-desc">${esc(src.how || '')}</div>
      <dl class="src-grid">
        <dt>鉴权</dt><dd>${esc(src.auth || '—')}</dd>
        <dt>能拿到</dt><dd>${esc((src.fields || []).join(' · ')) || '—'}</dd>
        <dt>价值</dt><dd>${esc(src.why || '—')}</dd>
        <dt>坑</dt><dd class="warn">${esc(src.caveat || '—')}</dd>
      </dl>
      ${(src.endpoints || []).length ? '<div class="src-links">' +
        src.endpoints.map((e) => `<a class="src-link" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.label)} ↗</a>`).join('') +
        '</div>' : ''}
    </article>`).join('');

  if (s.principle) {
    $('src-list').insertAdjacentHTML('beforeend',
      `<div class="m-quote">${esc(s.principle)}</div>`);
  }
}

/* ---------------- 渲染：方法论 ---------------- */
function renderMethod() {
  const steps = [
    {
      h: '先定死规矩：写之前，先搜一遍',
      p: '每一个数字，在写进案例之前必须至少搜一次。这一步不能交给工具，因为工具不会因为「数字看起来合理」而警觉，而你会。',
      extra: '本库里 Nitra 的客户数被中文转述写成「7000 多家」，公开披露是 <code>700+</code>；Chatbase 的 <code>$20M</code> 被写成「年入」，实际是累计收入。这两个错误都不是查不到，是没查。'
    },
    {
      h: '六个口径，每次都问一遍',
      p: 'ARR / MRR / run-rate / 累计收入 / 平台流水 / 毛利 —— 这六个词经常被混着用，混一个就差十倍。',
      extra: '<code>ARR</code> 是年化经常性收入，不等于现金；<code>run-rate</code> 是按某月乘出来的估计值，不等于已发生；<code>累计收入</code> ≠ 年收入；<code>平台流水</code> 不是收入（Nitra 的「$10 亿」是处理量，不是营收）。'
    },
    {
      h: '给每条数字标注核实等级',
      p: '不看「有没有数字」，看「这个数字是谁说的」。等级从高到低：支付网关直连 → 公司官方披露 → 口径待核 → 创始人自报 → 数字有出入。',
      extra: '凡是标成「口径待核」和「数字有出入」的，引用时都要写清不确定在哪。这比写一个漂亮的假数字有价值得多。'
    },
    {
      h: '主动收录「翻车」案例',
      p: '库里专门保留了两类反例：一类是数字核不到（Meerkats.ai）、一类是增长停了（Speel.co 增长 0%）。它们比成功案例更能防止你踩坑。',
      extra: 'Speel.co 的 $65K MRR 是真的，MoM 增长 0% 也是真的。收入真实 ≠ 还在增长——这两件事必须分开看。'
    },
    {
      h: '按「模式」读，不要按「行业」读',
      p: '不要记「美容店 SaaS 赚了 2 亿」。要记「找一个没人愿意干的脏活，把它做成订阅制」。模式可以搬到完全不同的行业，行业不能。',
      extra: '用左上角的模式芯片筛选。看完同一模式下的 4–5 个案例，你就能提炼出这个模式的成立条件。'
    },
    {
      h: '看到 50 个再谈判断',
      p: '左侧进度条就是干这个用的。看到 20 个你会觉得「都是运气」，看到 50 个你会开始问「他的钱从哪来、流量从哪来、我能不能做这一段」。',
      extra: '这是这个库唯一的目标：不是收藏，是让你对「这事能不能成」长出直觉。'
    },
    {
      h: '三级漏斗：素材 → 候选 → 精写',
      p: '左边那条漏斗就是这个库的骨架。素材多、精写少是正常的，不是没做完，是这一层本来就要慢。',
      extra: '<strong>采集队列</strong>＝脚本捞的（机器，几百条，全没看过）→ <strong>候选池</strong>＝人挑出来的（几十条，还没核数字）→ <strong>精写案例</strong>＝核过数字、能拿出去说的（当前 24 条）。一个人一天能精写 2–3 条，一周就是十几条。别急。'
    },
    {
      h: '候选池不是垃圾堆，是待办清单',
      p: '候选池里有些项目数字更亮，但还没核。别因为好看就去看——核准了再进精写库。点「提升为精写案例」会带一个待核实标记进去。',
      extra: '当前候选池里最值得先动手的三个：<code>Stan</code>（TrustMRR 榜单第一，量级异常需复核）、<code>PROSP</code>（MRR $128K 但产品信息缺失）、<code>Voklit</code>（月入 $1.6K 挂 $60K，小体量定价样本）。'
    },
    {
      h: '最后问一句：这东西能搬回国内吗',
      p: '「一个人能不能做」和「能不能在国内做」是两个问题，答案经常相反。翻到「移植排行」，先看国内有没有人真会付钱，再看有没有踩合规红线。',
      extra: '两个反例，低分原因完全不同：<code>CheckVibe</code> 本地化成本几乎为零、一个人完全能做，但国内开发者不为安全工具付费——需求端是空的；<code>Nitra</code> 产品也不难做，但医疗合规和资质拿不到。前者是没人买，后者是卖不了。'
    }
  ];

  $('method').innerHTML =
    '<div class="m-quote">别自己想，去看已经跑通的人在干什么。但看之前，先确认那些数字是真的。</div>' +
    steps.map((s, i) => `
      <div class="m-step">
        <div class="m-num">${i + 1}</div>
        <div class="m-body">
          <h3>${s.h}</h3>
          <p>${s.p}</p>
          ${s.extra ? `<p style="color:var(--text-3);font-size:12.5px">${s.extra}</p>` : ''}
        </div>
      </div>`).join('') +
    '<div class="d-h">核实等级图例</div>' +
    '<div class="legend">' +
      V_ORDER.map((v) => `<span class="legend-item"><span class="badge" data-v="${v}">${V_LABEL[v]}</span></span>`).join('') +
    '</div>' +
    '<div class="d-h">可复刻度怎么读</div>' +
    '<p class="d-p">每条案例详情页里有四个分数：' +
      Object.keys(REP_LABEL).map((k) => `<strong>${REP_LABEL[k]}</strong>`).join(' / ') +
      '。' + esc(REP_NOTE) + '</p>' +
    '<div class="d-h">数据与命令</div>' +
    (IS_STATIC
      ? '<p class="d-p">线上这份是<b>只读快照</b>——只有一个 <code style="font-family:var(--mono);color:var(--money)">data.json</code>，没有后端，所以候选池和采集队列里不出现写按钮。数据由 GitHub Actions 每天定时采集、自校验、提交，再构建成静态文件发布到这里。</p>' +
        '<p class="d-p">要做核实和写操作（提升候选、转入候选池），克隆仓库后在本地跑 <code style="font-family:var(--mono);color:var(--money)">python server.py</code>。源码与数据：<a href="https://github.com/oracis/ai-case-library" target="_blank" rel="noopener" style="color:var(--money)">github.com/oracis/ai-case-library</a></p>'
      : '<p class="d-p">三级漏斗对应三个文件：<code style="font-family:var(--mono);color:var(--money)">data/inbox.json</code>（采集队列）、<code style="font-family:var(--mono);color:var(--money)">data/candidates.json</code>（候选池）、<code style="font-family:var(--mono);color:var(--money)">data/cases.json</code>（精写案例）。信息源配置在 <code style="font-family:var(--mono);color:var(--money)">data/sources.json</code>。直接改 JSON 刷新页面即可生效，不需要重启服务。</p>' +
        '<p class="d-p"><b>采集</b>：<code style="font-family:var(--mono);color:var(--money)">python scripts/harvest.py --source hn</code>（Hacker News，免 key）／<code style="font-family:var(--mono);color:var(--money)">--source trustmrr</code>／<code style="font-family:var(--mono);color:var(--money)">--source all</code>。加 <code style="font-family:var(--mono);color:var(--money)">--dry-run</code> 可先看结果不写入。</p>' +
        '<p class="d-p"><b>核实</b>：<code style="font-family:var(--mono);color:var(--money)">python scripts/verify.py "Nitra" --domain nitra.com</code> 会把六个口径和搜索入口摊在你面前；加 <code style="font-family:var(--mono);color:var(--money)">--open</code> 直接开浏览器，加 <code style="font-family:var(--mono);color:var(--money)">--log</code> 记一条核实日志。</p>' +
        '<p class="d-p"><b>自测</b>：<code style="font-family:var(--mono);color:var(--money)">python selftest.py</code> 会起一个临时服务跑完接口测试（用 5087 端口，不影响正在跑的 5052）。</p>');
}

/* ---------------- 渲染：抽屉详情 ---------------- */
function openCase(id) {
  const c = DATA.cases.find((x) => x.id === id);
  if (!c) return;

  if (!readSet.has(id)) {
    readSet.add(id);
    saveRead();
    renderRead();
    const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
    if (card) card.classList.add('read');
  }

  $('drawer-body').innerHTML = detailHTML(c);
  $('drawer').hidden = false;
  $('drawer-mask').hidden = false;
  $('drawer').scrollTop = 0;
}

function closeDrawer() {
  $('drawer').hidden = true;
  $('drawer-mask').hidden = true;
}

function detailHTML(c) {
  const m = c.metrics || {};
  const out = [];

  /* 头部 */
  out.push(`<div class="d-head">
    <div class="d-title">${esc(c.name)}
      ${c.name_en && c.name_en !== c.name ? `<small>${esc(c.name_en)}</small>` : ''}
    </div>
    <div class="d-liner">${esc(c.one_liner || '')}</div>
    <div class="d-badges">
      <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未核实')}</span>
      ${(c.models || []).map((x) => `<span class="tg">${esc(x)}</span>`).join('')}
      ${correctionCount(c) ? `<span class="flag">发现 ${correctionCount(c)} 处数字出入</span>` : ''}
    </div>
  </div>`);

  /* 一句话判断 */
  if (c.verdict) {
    out.push('<div class="d-h">一句话判断</div>');
    out.push(`<div class="d-verdict">${esc(c.verdict)}</div>`);
  }

  /* 关键数字 */
  out.push('<div class="d-h">关键数字</div>');
  const rows = [];
  const push = (k, v, mono) => { if (v != null && v !== '') rows.push([k, v, mono]); };

  push('主指标', m.headline, true);
  if (m.arr) push('ARR', '$' + num(m.arr), true);
  if (m.mrr) push('MRR', '$' + num(m.mrr), true);
  if (m.all_time) push('累计收入', '$' + num(m.all_time), true);
  push('客户/规模', m.customers);
  push('团队', m.team);
  push('融资', m.funding);
  push('估值', m.valuation);
  push('增长', m.growth);
  push('价格', m.price_point);
  push('地区', c.origin);

  out.push('<div class="metrics">' + rows.map((r) =>
    `<div class="k">${esc(r[0])}</div><div class="v${r[2] ? ' mono' : ''}">${esc(r[1])}</div>`
  ).join('') + '</div>');

  if (m.metric_note) {
    out.push(`<div class="metric-warn"><b>口径说明：</b>${esc(m.metric_note)}</div>`);
  }

  /* 业务 */
  if (c.what_it_does) {
    out.push('<div class="d-h">它到底做什么</div>');
    out.push(`<p class="d-p">${esc(c.what_it_does)}</p>`);
  }
  if (c.how_it_makes_money) {
    out.push('<div class="d-h">钱从哪来</div>');
    out.push(`<p class="d-p">${esc(c.how_it_makes_money)}</p>`);
  }

  /* 为什么成立 */
  if ((c.why_it_works || []).length) {
    out.push('<div class="d-h">为什么这事能成</div>');
    out.push('<ul class="d-list why">' + c.why_it_works.map((x) => `<li>${esc(x)}</li>`).join('') + '</ul>');
  }

  /* 关键证据 */
  if ((c.signals || []).length) {
    out.push('<div class="d-h">支撑证据</div>');
    out.push('<ul class="d-list sig">' + c.signals.map((x) => `<li>${esc(x)}</li>`).join('') + '</ul>');
  }

  /* 可复刻动作 */
  if ((c.playbook || []).length) {
    out.push('<div class="d-h">可以搬走什么</div>');
    out.push('<ul class="d-list play">' + c.playbook.map((x) => `<li>${esc(x)}</li>`).join('') + '</ul>');
  }

  /* 修正 —— 招牌区块 */
  if (correctionCount(c)) {
    out.push('<div class="d-h bad">核实修正 · 别抄错</div>');
    out.push(c.corrections.map((co) => `
      <div class="corr">
        <div class="corr-claim">${esc(co.claim || '')}</div>
        <div class="corr-truth">${esc(co.truth || '')}</div>
        ${co.source ? `<div class="corr-src"><a href="${esc(co.source)}" target="_blank" rel="noopener">${esc(co.source)}</a></div>` : ''}
      </div>`).join(''));
  }

  /* 可复刻度 */
  const rep = c.replicability || {};
  if (Object.keys(rep).length) {
    out.push('<div class="d-h">一个人能不能做</div>');
    out.push('<div class="rep">' + Object.keys(REP_LABEL).map((k) => {
      const v = rep[k];
      if (typeof v !== 'number') return '';
      return `<div class="rep-cell" data-lv="${v}">
        <div class="rep-k">${REP_LABEL[k]}</div>
        <div class="rep-v">${v}/5</div>
        <div class="rep-bar">${'<i></i>'.repeat(5)}</div>
      </div>`;
    }).join('') + '</div>');
    out.push(`<div class="rep-note">${esc(REP_NOTE)} 本条合计 ${repTotal(c)}/20。</div>`);
  }

  /* 个人可做性（solo_fit）：把上面四维翻成正向 + 补一个「单人交付」维度，归一化到 100。
     和原始四维的关系：build/reach/capital/window 由上面反推，delivery 是新增的人工判断。 */
  const sf = c.solo_fit;
  if (sf && typeof sf.score === 'number') {
    const tier = soloTier(sf.score);
    const medal = sf.medal
      ? `<span class="d-medal m-${esc(sf.medal)}">${MEDAL_ICON[sf.medal]} 第 ${sf.rank} 名 · ${esc(MEDAL_LABEL[sf.medal])}牌</span>`
      : `<span class="d-medal plain">第 ${sf.rank} 名</span>`;
    out.push('<div class="d-h">个人可做性 · 方向统一后的总分</div>');
    out.push(`<div class="china-hero t-${tier}">
      <div class="ch-score"><b>${sf.score}</b><small>/ 100</small></div>
      ${medal}
    </div>`);
    out.push('<div class="rep rep-5">' + SOLO_DIM_ORDER.map((k) => {
      const v = (sf.dims || {})[k];
      if (typeof v !== 'number') return '';
      return `<div class="rep-cell" data-lv="${v}">
        <div class="rep-k">${esc(SOLO_DIM_LABEL[k])}</div>
        <div class="rep-v">${v}/5</div>
        <div class="rep-bar">${'<i></i>'.repeat(5)}</div>
      </div>`;
    }).join('') + '</div>');
    if (sf.delivery_note) {
      out.push(`<div class="rep-note">单人交付 ${(sf.dims || {}).delivery || '—'}/5：${esc(sf.delivery_note)}</div>`);
    }
    out.push(`<div class="rep-note">${esc(SOLO_NOTE)}</div>`);
  }

  /* 国内移植可行性 —— 与上面的「复刻度」是两个不同的问题：
     一个问「一个人能不能做」，一个问「能不能搬回国内做」。*/
  const cf = c.china_fit;
  if (cf && typeof cf.score === 'number') {
    const tier = chinaTier(cf.score);
    const medal = cf.medal
      ? `<span class="d-medal m-${esc(cf.medal)}">${MEDAL_ICON[cf.medal]} 第 ${cf.rank} 名 · ${esc(MEDAL_LABEL[cf.medal])}牌</span>`
      : `<span class="d-medal plain">第 ${cf.rank} 名</span>`;
    out.push('<div class="d-h">能不能搬回国内做</div>');
    out.push(`<div class="china-hero t-${tier}">
      <div class="ch-score"><b>${cf.score}</b><small>/ 100</small></div>
      ${medal}
    </div>`);
    out.push('<div class="rep">' + CHINA_DIM_ORDER.map((k) => {
      const v = (cf.dims || {})[k];
      if (typeof v !== 'number') return '';
      return `<div class="rep-cell" data-lv="${v}">
        <div class="rep-k">${esc(CHINA_DIM_LABEL[k])}</div>
        <div class="rep-v">${v}/5</div>
        <div class="rep-bar">${'<i></i>'.repeat(5)}</div>
      </div>`;
    }).join('') + '</div>');
    if (cf.note) out.push(`<div class="rep-note">${esc(cf.note)}</div>`);
    if (cf.blocker) {
      out.push(`<div class="china-blocker">硬伤：${esc(cf.blocker)} —— 这一项没解决，分数再高也别立项。</div>`);
    }
    out.push('<div class="rep-note">评分维度与权重见「移植排行」页。注意它和上面的「一个人能不能做」是两个问题，结论经常相反。</div>');
  }

  /* 综合分 —— 两个维度合起来看：短板占 6 成权重 */
  const df = c.composite;
  if (df && typeof df.score === 'number') {
    const meta = QUAD_META[df.quadrant] || { label: '未分象限', desc: '' };
    const tier = df.score >= 70 ? 'hi' : df.score >= 55 ? 'mid' : 'lo';
    const medal = df.medal
      ? `<span class="d-medal m-${esc(df.medal)}">${MEDAL_ICON[df.medal]} 综合第 ${df.rank} 名 · ${esc(MEDAL_LABEL[df.medal])}牌</span>`
      : `<span class="d-medal plain">综合第 ${df.rank} 名</span>`;
    out.push('<div class="d-h">综合分 · 两个维度合起来看</div>');
    out.push(`<div class="china-hero t-${tier}">
      <div class="ch-score"><b>${df.score}</b><small>/ 100</small></div>
      ${medal}
    </div>`);
    out.push(dualAxisHTML(df.solo, df.china));
    out.push(`<div class="quad-badge q-${esc(df.quadrant || 'na')}">
      <b>${esc(meta.label)}</b><span>${esc(meta.desc)}</span>
    </div>`);
    out.push(`<div class="rep-note">综合分 = 0.6 × 短板 ＋ 0.4 × 均值（及格线 ${df.threshold || 70}）。`
      + '短板占 6 成，所以「能做但没市场」和「有市场但做不了」都会被压下去。'
      + '公式、维度与分值都在 <code>scripts/score_solo_fit.py</code>，改完重跑即可。</div>');
  }

  /* 来源 */
  if ((c.sources || []).length) {
    out.push('<div class="d-h">来源</div>');
    out.push(c.sources.map((s) => `
      <div class="src-item">
        <span class="src-kind" data-k="${esc(s.kind || '')}">${esc(KIND_LABEL[s.kind] || '来源')}</span>
        <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label || s.url)}</a>
      </div>`).join(''));
  }

  /* 页脚 */
  out.push('<div class="d-h">记录</div>');
  out.push(`<div class="metrics">
    <div class="k">核实日期</div><div class="v">${esc(c.verified_at || '—')}</div>
    <div class="k">最近更新</div><div class="v">${esc(c.updated_at || '—')}</div>
    <div class="k">归类</div><div class="v">${esc(c.category || '—')}${c.industry ? ' / ' + esc(c.industry) : ''}</div>
    <div class="k">标签</div><div class="v">${esc((c.tags || []).join(' · ')) || '—'}</div>
  </div>`);

  /* 引流位放最底部：读完一条才有转化意愿，塞在前面只会打断阅读 */
  out.push(drawerPromoHTML());

  return out.join('');
}

/* ---------------- 引流位 ----------------
   这个站是获客入口，收入在公众号和社群里，所以引流位是正经组件，不是装饰：
   只出现在「顶部条 / 详情抽屉底 / 页脚」三处，不插进卡片流、不打断阅读。
   文案与链接全部来自 data/site.json（经 /api/data 或 data.js 带下来），
   改公众号名、以后接入知识星球，都只改那一个文件，页面代码不用动。

   一个刻意的取舍：社群没有 url 时不渲染成可点的按钮，而是标注「即将开通」。
   放一个点了没反应的名字，比不放更伤信任。 */
const SITE_DEFAULT = { title: '案例库', repo: '', wechat: {}, community: {} };

function siteMeta() {
  const s = (DATA && DATA.site) || {};
  return {
    title: s.title || SITE_DEFAULT.title,
    repo: s.repo || SITE_DEFAULT.repo,
    wechat: s.wechat || {},
    community: s.community || {},
  };
}

/* 可引导的渠道列表。顺序就是优先级：公众号 → 社群。 */
function promoChannels(m) {
  const out = [];
  if (m.wechat.name) {
    out.push({
      kind: 'wechat',
      label: m.wechat.name,
      text: m.wechat.hint || '同步更新拆解长文',
      action: '微信搜索关注',
      url: '',
    });
  }
  if (m.community.name) {
    // 说明文字用 hint（讲清将来给什么），没开通时右侧标「即将开通」而不是做成链接
    out.push({
      kind: 'community',
      label: m.community.name,
      text: m.community.hint || '',
      action: m.community.url ? '进入社群' : '即将开通',
      url: m.community.url || '',
    });
  }
  return out;
}

/* 没有可引导的东西就整体不渲染——别留一个空框。 */
function promoInfo() {
  const m = siteMeta();
  const chans = promoChannels(m);
  if (!chans.length && !m.repo) return null;
  return { m, chans };
}

function promoAction(c, cls) {
  return c.url
    ? `<a class="${cls}" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.action)}</a>`
    : `<span class="${cls} ghost">${esc(c.action)}</span>`;
}

/* 顶部条：全站一条，放在最顶。优先推公众号——那是唯一「说得出就能到位」的入口。 */
function renderPromoBar() {
  const el = $('promo-bar');
  if (!el) return;
  const info = promoInfo();
  if (!info) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const c = info.chans[0];
  const body = c
    ? `<span class="pb-txt">深度拆解发在公众号 <b>${esc(c.label)}</b>` +
      (c.text ? `<em>${esc(c.text)}</em>` : '') + '</span>' +
      `<span class="pb-act">${esc(c.action)}</span>`
    : `<span class="pb-txt">全部案例与数据 <b>开源在 GitHub</b></span>`;
  el.className = 'promo-bar' + (c ? ' k-' + c.kind : ' k-repo');
  el.innerHTML = `<div class="pb-in"><span class="pb-dot"></span>${body}</div>`;
  el.hidden = false;
}

/* 页脚：放在 .scroll 内容末尾，所有视图共用。 */
function renderSiteFoot() {
  const el = $('site-foot');
  if (!el) return;
  const info = promoInfo();
  if (!info) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const { m, chans } = info;
  const rows = chans.map((c) => `<div class="sf-row">
      <span class="sf-k">${esc(c.label)}</span>
      <span class="sf-t">${esc(c.text)}</span>
      ${promoAction(c, 'sf-a')}
    </div>`).join('');
  el.innerHTML = `<div class="sf-in">
    <div class="sf-head">
      <h4>这个库会一直更新</h4>
      <p>案例都来自海外公开渠道，每个数字核过才进库。新的拆解先发在公众号；
        每日线索、本周最值得看的 3 条、以及<b>我为什么把其余的都否掉了</b>，放在社群里。</p>
    </div>
    <div class="sf-rows">${rows}</div>
    <div class="sf-tail">
      ${m.repo ? `<a href="${esc(m.repo)}" target="_blank" rel="noopener">源码与数据（GitHub）</a>` : ''}
      <span>内容仅作商业研究参考，不构成投资或创业建议</span>
    </div>
  </div>`;
  el.hidden = false;
}

/* 详情抽屉底部：读者刚读完一条，是转化意愿最高的位置。 */
function drawerPromoHTML() {
  const info = promoInfo();
  if (!info) return '';
  const { chans } = info;
  const rows = chans.map((c) => `<div class="dp-row">
      <span class="dp-k">${esc(c.label)}</span>
      <span class="dp-t">${esc(c.text)}</span>
      ${promoAction(c, 'dp-a')}
    </div>`).join('');
  return `<div class="d-promo">
    <div class="dp-head">看完这条，想看更多</div>
    <div class="dp-sub">海外每天都有新跑通的小项目。我每天筛一批：哪些值得看、哪些直接否掉了、为什么。</div>
    ${rows}
  </div>`;
}

/* ---------------- 视图切换 ---------------- */
function switchView(v) {
  view = v;
  ['cases', 'dual', 'china', 'candidates', 'inbox', 'sources', 'method'].forEach((name) => {
    const el = $('view-' + name);
    if (el) el.hidden = (name !== v);
  });
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === v);
  });
  // 搜索 / 排序 / 模式芯片 / 按分筛选：只在案例视图显示（榜单视图本身已是全量排行）
  const showControls = (v === 'cases');
  const ctrl = document.querySelector('.controls');
  if (ctrl) ctrl.style.display = showControls ? 'flex' : 'none';
  const fb = $('filterbar');
  if (fb) fb.style.display = showControls ? 'flex' : 'none';
  $('chips').style.display = showControls ? 'flex' : 'none';
  $('scroll').scrollTop = 0;
  renderPodium();
}

/* ---------------- 数据加载 ---------------- */
/* 静态部署用 <script> 加载 data.js，而不是 fetch('data.json')。
   原因：直接双击 index.html 时是 file:// 协议，fetch 本地文件会被 CORS 拦掉，
   而 <script> 不受这个限制。这样 dist/ 整个目录拷到哪儿都能双击打开。 */
function loadStaticData() {
  return new Promise((resolve, reject) => {
    if (window.__CASE_LIB_DATA__) return resolve(window.__CASE_LIB_DATA__);
    const el = document.createElement('script');
    el.src = 'data.js';
    el.onload = () => window.__CASE_LIB_DATA__
      ? resolve(window.__CASE_LIB_DATA__)
      : reject(new Error('data.js 存在，但里面没有 window.__CASE_LIB_DATA__'));
    el.onerror = () => reject(new Error('读不到 data.js —— 先跑 python scripts/build_static.py'));
    document.head.appendChild(el);
  });
}

async function load() {
  if (IS_STATIC) {
    DATA = await loadStaticData();
  } else {
    const r = await fetch('/api/data');
    if (!r.ok) throw new Error('接口返回 ' + r.status);
    DATA = await r.json();
  }

  renderStats();
  renderVigilance();
  renderChips();
  renderCases();
  renderChina();
  renderDual();
  renderFilterBar();
  renderPodium();
  renderCandidates();
  renderInbox();
  renderSources();
  renderMethod();
  renderRead();
  // 引流位：顶部条与页脚都从同一份 site 配置渲染；配置为空则整体不出现
  renderPromoBar();
  renderSiteFoot();
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  document.querySelectorAll('.nav-item').forEach((b) => {
    b.onclick = () => switchView(b.dataset.view);
  });

  let tm = null;
  $('search').oninput = (e) => {
    clearTimeout(tm);
    tm = setTimeout(() => {
      q = e.target.value.trim();
      renderCases();
      renderPodium();
    }, 120);
  };

  $('sort').onchange = (e) => { sortBy = e.target.value; renderCases(); renderPodium(); };
  $('verif').onchange = (e) => { verifFilter = e.target.value; renderCases(); renderPodium(); };

  // 按分筛选：维度 × 下限 × 象限，三个条件叠加
  const fd = $('fitdim');
  const fl = $('fitlevel');
  const qd = $('quad');
  const after = () => { renderCases(); renderPodium(); renderFilterBar(); };
  if (fd) fd.onchange = (e) => {
    fitDim = e.target.value;
    // 只选了维度却没设下限时自动补 70，免得点了「没反应」
    if (fitDim && !fitLevel) { fitLevel = 70; if (fl) fl.value = '70'; }
    if (!fitDim) { fitLevel = 0; if (fl) fl.value = '0'; }
    after();
  };
  if (fl) fl.onchange = (e) => { fitLevel = parseInt(e.target.value, 10) || 0; after(); };
  if (qd) qd.onchange = (e) => { quadFilter = e.target.value; after(); };
  const fr = $('fb-reset');
  if (fr) fr.onclick = resetFitFilter;

  $('drawer-close').onclick = closeDrawer;
  $('drawer-mask').onclick = closeDrawer;

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
    // / 聚焦搜索
    if (e.key === '/' && document.activeElement !== $('search')) {
      e.preventDefault();
      $('search').focus();
    }
  });
}

/* ---------------- 启动 ---------------- */
(async function main() {
  loadRead();
  bind();
  try {
    await load();
  } catch (err) {
    document.getElementById('scroll').innerHTML =
      '<div class="empty">加载失败：' + esc(err.message) +
      '<br><br>请确认服务已启动（python server.py）。</div>';
  }
})();
