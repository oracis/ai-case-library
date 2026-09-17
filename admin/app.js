/* ============================================================
   拆解海外 · 管理后台（独立站点）
   ============================================================
   这是和公开阅读站完全分开的第二套界面：

   - 公开站（/ ，static/）：只读，谁都能看，可以整个扔到 OSS 上。
   - 后台    （/ ，admin/）：要登录，能核实与入库，只绑 127.0.0.1。

   两边共用同一份 data/*.json，但不共用任何界面代码 —— 后台的
   「核实工作台」在公开站里一行都没有，公开站的阅读/排行/筛选在这里
   也一行都没有。这样公开站就算被人翻个底朝天，也翻不出后台的存在。
*/
'use strict';

/* ---------------- 工具 ---------------- */
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(msg) {
  const t = $('toast');
  if (!t) return;
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._tm);
  t._tm = setTimeout(() => { t.hidden = true; }, 2200);
}

const V_LABEL = {
  stripe: '支付网关验证', official: '官方披露', partial: '口径待核',
  founder: '创始人自报', disputed: '数字有出入', unverified: '未核实'
};

/* ---------------- 状态 ---------------- */
let DATA = null;            // /api/data 的完整响应（含核实草稿）
let VSCHEMA = null;         // 核实规则表
let ADMIN = false;          // 是否已登录
let VIEW = 'candidates';    // 当前后台视图

/* ---------------- 会话 ---------------- */
async function checkSession() {
  try {
    const r = await fetch('/api/session');
    if (!r.ok) return false;
    const j = await r.json();
    return !!j.admin;
  } catch (e) {
    return false;
  }
}

function showLogin() {
  $('login').hidden = false;
  $('app').hidden = true;
  const pw = $('login-pw');
  if (pw) { pw.value = ''; pw.focus(); }
}

function showApp() {
  $('login').hidden = true;
  $('app').hidden = false;
}

async function doLogin() {
  const pw = $('login-pw');
  const err = $('login-err');
  const btn = $('login-go');
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = '登录中…';
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw.value }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '登录失败');
    ADMIN = true;
    showApp();
    await loadAdmin();
  } catch (e) {
    err.hidden = false;
    err.textContent = e.message || '登录失败';
  } finally {
    btn.disabled = false;
    btn.textContent = '登录';
  }
}

async function doLogout() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { /* 按已登出处理 */ }
  ADMIN = false;
  DATA = null;
  showLogin();
}

/* ---------------- 数据 ---------------- */
async function loadAdmin() {
  const r = await fetch('/api/data');
  if (r.status === 401) {           // 登录态过期了，退回登录框
    ADMIN = false;
    showLogin();
    return;
  }
  if (!r.ok) throw new Error('接口返回 ' + r.status);
  DATA = await r.json();
  renderAll();
}

/* ---------------- 渲染：统计条 ---------------- */
function renderStats() {
  const s = DATA.stats || {};
  $('n-cands').textContent = s.candidates || 0;
  $('n-inbox').textContent = s.inbox || 0;
  $('n-cases').textContent = s.curated || 0;
  $('adm-stat').textContent =
    '候选 ' + (s.candidates || 0) +
    ' · 精品 ' + (s.premium || 0) +
    ' · 备选 ' + (s.backup || 0);
}

/* ---------------- 渲染：候选池 ---------------- */
function renderCandidates() {
  const list = DATA.candidates || [];
  if (!list.length) {
    $('grid-cands').innerHTML = '<div class="adm-empty">候选池空了。到采集队列转入几条。</div>';
    return;
  }
  $('grid-cands').innerHTML = list.map((c) => {
    const hl = (c.metrics || {}).headline;
    const p = draftProgress(c.id);
    return `
      <article class="cand">
        <div class="cand-h">
          <div class="cand-name">${esc(c.name)}</div>
          <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未核实')}</span>
        </div>
        <div class="card-liner">${esc(c.one_liner || '')}</div>
        ${hl ? `<div class="card-metric is-null">${esc(hl)}</div>` : ''}
        ${c.note ? `<div class="cand-note">${esc(c.note)}</div>` : ''}
        ${c.blocking ? `<div class="cand-block"><b>卡在哪：</b><span>${esc(c.blocking)}</span></div>` : ''}
        ${cardLinks(c)}
        <div class="card-foot">
          <span>${esc(c.added_at || '')}</span>
          <span class="cand-draft ${p ? 'on' : ''}">${p ? '草稿 ' + Math.round(p) + '%' : '未核实'}</span>
          <button class="src-link" data-verify="${esc(c.id)}">核实 →</button>
        </div>
      </article>`;
  }).join('');

  $('grid-cands').querySelectorAll('[data-verify]').forEach((btn) => {
    btn.onclick = (e) => { e.stopPropagation(); openVerify(btn.dataset.verify); };
  });
}

/** 候选卡片底部：官网 + 来源两条链接（与公开站同一套口径） */
function cardLinks(c) {
  const pretty = (u) => String(u || '').replace(/^https?:\/\//, '').replace(/\/$/, '').replace(/^www\./, '').slice(0, 60);
  const out = [];
  if (c.website) {
    out.push(`<a href="${esc(c.website)}" target="_blank" rel="noopener" class="cand-link" data-k="web">官网 ${esc(pretty(c.website))} ↗</a>`);
  }
  if (c.source_url) {
    out.push(`<a href="${esc(c.source_url)}" target="_blank" rel="noopener" class="cand-link" data-k="src">来源 ${esc(pretty(c.source_url))} ↗</a>`);
  }
  return out.length
    ? '<div class="cand-links">' + out.join('') + '</div>'
    : '<div class="cand-link is-none">来源未记录</div>';
}

/* ---------------- 渲染：采集队列 ---------------- */
function renderInbox() {
  const all = DATA.inbox || [];
  const limit = 60;
  const shown = all.slice(0, limit);
  const srcLabel = { hn: 'Hacker News', trustmrr: 'TrustMRR', ph: 'Product Hunt', unknown: '未标注' };

  if (!shown.length) {
    $('grid-inbox').innerHTML = '<div class="adm-empty">采集队列是空的。跑一次 scripts/harvest.py 采集。</div>';
    return;
  }
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
          <button class="src-link" data-tocand="${esc(c.id)}">转入候选池</button>
        </div>
      </article>`;
  }).join('') + (all.length > limit
    ? `<div class="adm-empty" style="grid-column:1/-1">还有 ${all.length - limit} 条未显示。</div>` : '');

  $('grid-inbox').querySelectorAll('[data-tocand]').forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); toCandidate(b.dataset.tocand); };
  });
}

async function toCandidate(id) {
  const c = (DATA.inbox || []).find((x) => x.id === id);
  if (!c) return;
  try {
    const r = await fetch('/api/inbox/' + encodeURIComponent(id) + '/to-candidates', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '操作失败');
    toast('已转入候选池：' + c.name);
    await loadAdmin();
  } catch (err) {
    toast('转入失败：' + err.message);
  }
}

/* ---------------- AI 自动核实 ---------------- */
let AI_TIMER = null;        // 轮询句柄（任务跑完要清掉）

/* 每条核实结果该怎么说人话。
   判定结果有好几种形态，界面上必须一眼分得清，因为它们对应完全不同的
   下一步动作：已发布的不用管、草稿够格只差人背书、缺材料要人去补。 */
function aiOutcome(it) {
  if (it.error) {
    return { key: 'err', label: '出错', hint: '任务抛异常，看原始输出' };
  }
  if (it.published) {
    return { key: 'pub', label: '已发布', hint: '已进案例库' + (it.case_id ? '（' + it.case_id + '）' : '') };
  }
  if (it.ok === false) {
    return { key: 'skip', label: '跳过', hint: it.why || '没能负责地填出草稿' };
  }
  if (it.held) {
    return { key: 'held', label: '够格但分不够', hint: '质量分 ' + (it.score == null ? '-' : it.score) +
      ' 低于阈值，草稿已存' };
  }
  if (it.remain && it.remain.length) {
    return { key: 'draft', label: '草稿待补', hint: '还差必填项' };
  }
  return { key: 'draft', label: '草稿已存', hint: '等人工过一眼来源后发布' };
}

/* 结果表：一行一条，说清「谁会什么样、还差什么、点哪儿去接着做」。
   没有这张表，日志里那句「处理 1 条｜已发布 0」对人完全无用。 */
function renderAIResult(job) {
  const card = $('ai-result-card');
  const box = $('ai-result');
  if (!card || !box) return;
  const items = (job && job.items) || [];
  if (!items.length) { card.hidden = true; box.innerHTML = ''; return; }
  card.hidden = false;

  const s = (job && job.summary) || {};
  const note = $('ai-res-note');
  if (note) {
    note.textContent = s.error ? ''
      : '共 ' + items.length + ' 条｜已发布 ' + (s.published || 0) +
        '｜草稿 ' + ((s.held || 0) + (s.done || 0) - (s.published || 0) - (s.failed || 0) > 0
          ? (s.done || 0) - (s.published || 0) - (s.failed || 0) : 0) +
        '｜未成 ' + (s.failed || 0);
  }

  box.innerHTML = items.map((it) => {
    const o = aiOutcome(it);
    // 「还差什么 / 门槛未确认 / 提醒」都要摆出来 —— 这几条就是人的待办
    const bits = [];
    if (it.remain && it.remain.length) {
      bits.push('<span class="ai-tag wait">还差 ' + esc(it.remain.join('、')) + '</span>');
    }
    if (it.unverified && it.unverified.length) {
      bits.push('<span class="ai-tag wait">未确认 ' + esc(it.unverified.join('、')) + '</span>');
    }
    if (it.denied && it.denied.length) {
      bits.push('<span class="ai-tag bad">反证 ' + esc(it.denied.join('、')) + '</span>');
    }
    if (it.warnings && it.warnings.length) {
      bits.push('<span class="ai-tag note">提醒 ' + esc(it.warnings.join('、')) + '</span>');
    }
    const meta = [];
    if (it.score != null) meta.push('质量分 ' + it.score);
    if (it.tier_label) meta.push(esc(it.tier_label));
    if (it.caliber) meta.push('口径 ' + esc(it.caliber));
    if (it.source_count != null) meta.push('来源 ' + it.source_count);
    if (it.verdict) meta.push(esc(it.verdict));

    return `
      <div class="ai-res">
        <div class="ai-res-h">
          <span class="ai-res-name">${esc(it.name || it.id || '（未知）')}</span>
          <span class="ai-res-out ${o.key}">${esc(o.label)}</span>
          <button class="src-link ai-res-go" data-goto="${esc(it.id || '')}">去核实 →</button>
        </div>
        ${meta.length ? `<div class="ai-res-meta">${meta.join(' · ')}</div>` : ''}
        ${bits.length ? `<div class="ai-res-bits">${bits.join('')}</div>`
                      : `<div class="ai-res-bits"><span class="ai-tag ok">${esc(o.hint)}</span></div>`}
      </div>`;
  }).join('');

  box.querySelectorAll('[data-goto]').forEach((btn) => {
    btn.onclick = () => {
      const id = btn.dataset.goto;
      if (!id) return;
      switchView('candidates');            // 先回候选池，工作台是叠在它上面的
      openVerify(id);                      // 直接开这条的核实工作台
    };
  });
}

function showAILog(j) {
  const el = $('ai-log');
  if (!el) return;
  // /api/ai/status 把任务包在 job 字段里（state/has_key 是面板级字段）；
  // 兼容直接传任务对象（裸 {log, summary}）的调用方式。
  const job = j.job || j;
  // 别在这里把日志强行展开 —— 折叠开关归用户管。
  // 判据和开关保持一致：aria-expanded 记的是「用户此刻是否把日志折起来了」。
  const folded = $('ai-log-toggle') &&
                 $('ai-log-toggle').getAttribute('aria-expanded') === 'false';
  if (!folded) el.hidden = false;
  let text = (job.log || []).join('\n') || '（等待输出…）';
  const s = job.summary;
  if (s) {
    text += '\n\n—— ' + (s.error ? '任务出错：' + s.error
      : '处理 ' + (s.done || 0) + ' 条｜已发布 ' + (s.published || 0) +
        '｜分不够只存草稿 ' + (s.held || 0) + '｜未成 ' + (s.failed || 0));
  }
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

function scheduleAIPoll() {
  if (AI_TIMER) return;                          // 已在轮询
  const btn = $('ai-go');
  if (btn) { btn.disabled = true; btn.textContent = '任务运行中…'; }
  AI_TIMER = setInterval(async () => {
    try {
      const r = await fetch('/api/ai/status');
      if (!r.ok) return;
      const j = await r.json();
      renderAIResult(j.job);
      showAILog(j);
      if (!j.job || j.job.state !== 'running') {
        clearInterval(AI_TIMER);
        AI_TIMER = null;
        if (btn) { btn.disabled = false; btn.textContent = '开始跑'; }
        if (j.job && j.job.state === 'done') {
          toast('AI 核实完成');
          loadAdmin();                            // 刷新候选池草稿进度
        }
      }
    } catch (e) { /* 下一轮再试 */ }
  }, 1500);
}

async function loadAIStatus() {
  try {
    const r = await fetch('/api/ai/status');
    if (!r.ok) return;
    const j = await r.json();
    const st = $('ai-set-state');
    if (st) {
      st.textContent = j.has_key
        ? '✓ 已配置（' + (j.model || 'deepseek-chat') + '）—— 可直接跑'
        : '未配置 Key —— 在上面保存一份即可启用';
    }
    const key = $('ai-key');
    if (key && j.has_key && !key.value) {
      key.placeholder = '已保存（输入新值可更换）';
    }
    // 打开页面时如果服务端还有没跑完的任务，接着看日志
    if (j.job && j.job.state === 'running') {
      renderAIResult(j.job);
      showAILog(j);
      scheduleAIPoll();
    } else if (j.job && j.job.state !== 'idle' && (j.job.log || []).length) {
      renderAIResult(j.job);
      showAILog(j);
    }
  } catch (e) { /* 面板静默失败，不影响其他视图 */ }
}

async function saveAISettings() {
  const body = {};
  const key = $('ai-key'), base = $('ai-base'), model = $('ai-model');
  if (key && key.value.trim()) body.api_key = key.value.trim();
  if (base && base.value.trim()) body.ai_base = base.value.trim();
  if (model && model.value.trim()) body.ai_model = model.value.trim();
  if (!Object.keys(body).length) { toast('没有要保存的内容'); return; }
  try {
    const r = await fetch('/api/ai/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '保存失败');
    toast(j.has_key ? '已保存，Key 生效' : '已清除 Key');
    if (key) { key.value = ''; key.placeholder = '已保存（输入新值可更换）'; }
    loadAIStatus();
  } catch (e) {
    toast('保存失败：' + e.message);
  }
}

async function runAI() {
  const btn = $('ai-go');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/ai/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        limit: Number(($('ai-limit') || {}).value) || 5,
        min_score: Number(($('ai-minscore') || {}).value) || 0,
        publish: !!($('ai-publish') || {}).checked,
        include_small: !!($('ai-small') || {}).checked,
      }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '启动失败');
    toast('任务已启动');
    scheduleAIPoll();
  } catch (e) {
    toast(e.message);
    if (btn) btn.disabled = false;
  }
}

async function showAIPlan() {
  const out = $('ai-plan-out');
  try {
    const r = await fetch('/api/ai/plan');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    if (!out) return;
    out.innerHTML = (j.items || []).map((it) => {
      const g = it.gate_keys || [], m = it.must_keys || [];
      let b;
      if (g.length) b = '门槛 ' + g.length + ' 项' + (m.length ? ' + 必填缺 ' + m.length + ' 项' : '');
      else if (m.length) b = '必填缺 ' + m.length + ' 项';
      else b = '草稿已齐，可直接发布';
      return '<div class="ai-plan-item">' +
        '<b>' + esc(it.name || it.id) + '</b>' +
        '<span class="ai-plan-m">' + esc(it.headline || '未获取') + '</span>' +
        '<span class="ai-plan-b">' + esc(b) + '</span></div>';
    }).join('') || '<div class="adm-empty">没有可选候选（占位条目已跳过）。</div>';
  } catch (e) {
    toast('卡点加载失败：' + e.message);
  }
}

/* ---------------- 渲染：已发布（含备选→精品拔档） ---------------- */
function renderCases() {
  const list = DATA.cases || [];
  if (!list.length) {
    $('grid-cases').innerHTML = '<div class="adm-empty">还没有已发布的案例。</div>';
    return;
  }
  $('grid-cases').innerHTML = list.map((c) => {
    const tier = c.tier || 'backup';
    const tierLabel = tier === 'premium' ? '精品' : '备选';
    const qs = c.quality_score == null ? '–' : c.quality_score;
    // 人工核读记录：只有明确的 true/false 才画。undefined 是本次改动之前的老案例
    // —— 它们压根没这条记录，标成「未核读」等于替它们认了一个没做过的判断。
    let hr = '';
    if (c.human_read === true) {
      const at = (c.human_read_at || '').slice(0, 10);
      hr = '<span class="adm-hr ok">已核读' + (at ? ' ' + esc(at) : '') + '</span>';
    } else if (c.human_read === false) {
      hr = '<span class="adm-hr no">未经人工核读</span>';
    }
    return `
      <article class="cand">
        <div class="cand-h">
          <div class="cand-name">${esc(c.name)}</div>
          <span class="badge" data-v="${esc(c.verification || 'unverified')}">${esc(V_LABEL[c.verification] || '未核实')}</span>
        </div>
        <div class="card-liner">${esc(c.one_liner || '')}</div>
        <div class="card-foot">
          <span class="adm-tier ${tier === 'premium' ? 't-premium' : 't-backup'}">${tierLabel} · ${qs} 分</span>
          ${hr}
          <span>${esc(c.verified_at || c.updated_at || '')}</span>
        </div>
      </article>`;
  }).join('');
}

function renderAll() {
  renderStats();
  renderCandidates();
  renderInbox();
  renderCases();
}

/* ---------------- 视图切换 ---------------- */
function switchView(v) {
  VIEW = v;
  ['candidates', 'inbox', 'published', 'ai'].forEach((name) => {
    const el = $('view-' + name);
    if (el) el.hidden = (name !== v);
  });
  document.querySelectorAll('.adm-nav').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === v);
  });
}

/* ---------------- 抽屉 ---------------- */
function closeDrawer() {
  const d = $('drawer');
  if (d) d.hidden = true;
  const m = $('drawer-mask');
  if (m) m.hidden = true;
}

/* ---------------- 启动 ---------------- */
function bind() {
  const go = $('login-go');
  const pw = $('login-pw');
  if (go) go.onclick = doLogin;
  if (pw) pw.onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  const out = $('adm-logout');
  if (out) out.onclick = doLogout;

  document.querySelectorAll('.adm-nav').forEach((b) => {
    b.onclick = () => switchView(b.dataset.view);
  });

  const dc = $('drawer-close');
  if (dc) dc.onclick = closeDrawer;
  const dm = $('drawer-mask');
  if (dm) dm.onclick = closeDrawer;

  // AI 核实面板（元素都在 index.html 里，桩测试环境缺了就跳过）
  const aiSave = $('ai-save');
  if (aiSave) aiSave.onclick = saveAISettings;
  const aiGo = $('ai-go');
  if (aiGo) aiGo.onclick = runAI;
  const aiPlan = $('ai-plan');
  if (aiPlan) aiPlan.onclick = showAIPlan;
  // 原始日志默认折起来：它是给排查用的，日常该看的是上面那张结果表
  const aiLogT = $('ai-log-toggle');
  if (aiLogT) {
    aiLogT.onclick = () => {
      const log = $('ai-log');
      if (!log) return;
      // 以「日志当前是否真的显示着」为准，而不是读属性再猜 ——
      // 属性缺省时会和真实可见性不一致，导致第一次点没反应。
      const showing = !log.hidden;
      log.hidden = showing;
      aiLogT.classList.toggle('on', showing);
      aiLogT.setAttribute('aria-expanded', showing ? 'false' : 'true');
    };
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
}

(async function main() {
  bind();
  ADMIN = await checkSession();
  if (ADMIN) {
    showApp();
    try {
      await loadAdmin();
    } catch (e) {
      toast('数据加载失败：' + e.message);
    }
    loadAIStatus();          // 异步刷新 AI 面板状态，失败静默
  } else {
    showLogin();
  }
})();

