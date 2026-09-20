/* ============================================================
   核实工作台（只属于管理后台）
   ============================================================
   这段代码在公开阅读站里一行都没有 —— 公开站是纯只读快照，
   连「核实」两个字都不该出现，免得读者拿着一堆点不动的按钮
   以为网站坏了，也免得有人照着公开站的代码去猜后台长什么样。

   它依赖 app.js 提供的：$ / esc / toast / switchView / closeDrawer /
   loadAdmin / DATA。跨文件共享靠的是同一份全局词法环境，
   所以 index.html 里必须先加载 verify.js，再加载 app.js。
   ============================================================ */

let VDRAFT = {};               // 草稿：candidate_id -> 已填条件
let VRESULT = null;            // 最近一次判定结果

/** 拉规则表。拉过一次就缓存 —— 规则不可能在一轮会话里变。 */
async function ensureSchema() {
  if (VSCHEMA) return VSCHEMA;
  try {
    const r = await fetch('/api/verify-schema');
    if (!r.ok) throw new Error('接口返回 ' + r.status);
    VSCHEMA = await r.json();
    return VSCHEMA;
  } catch (err) {
    toast('取核实规则失败：' + err.message);
    return null;
  }
}

function emptyDraft() {
  return {
    gates: [], musts: [], bonus: [],
    caliber: '', verification: '', source_kinds: [],
    sources: [], corrections: [], note: '',
  };
}

/** 草稿完成度（%）：把已勾的必填项除以总项数。没草稿返回 0。 */
function draftProgress(id) {
  const d = (DATA.verifications || {})[id];
  if (!d) return 0;
  // 必填项数从规则表里取 —— 规则调整（比如放宽掉一手来源）这里自动跟着走，
  // 不再写死一个数字，免得规则改了进度条还按老算法算。
  // 只数**拦发布**的必填项。非阻塞的标记（人工核读）算进分母会让进度永远
  // 到不了 100% —— 而它本来就不拦发布，那样是在骗自己。
  const nMust = (VSCHEMA && VSCHEMA.musts)
    ? VSCHEMA.musts.filter((m) => m.blocking !== false).length : 2;
  const total = nMust + 2 + 1;   // 必填 + 口径/等级 + 至少一级来源
  let got = 0;
  got += Math.min((d.musts || []).length, nMust);
  if (d.caliber) got += 1;
  if (d.verification) got += 1;
  if ((d.source_kinds || []).length) got += 1;
  return Math.min(100, (got / total) * 100);
}

/** 打开工作台。后台站必然已登录，这里直接拉规则表开抽屉。 */
async function openVerify(id) {
  const c = (DATA.candidates || []).find((x) => x.id === id);
  if (!c) return;
  const sch = await ensureSchema();
  if (!sch) return;

  VDRAFT = (DATA.verifications && DATA.verifications[id]) || emptyDraft();
  VRESULT = null;
  $('drawer-body').innerHTML = verifyHTML(c, sch, VDRAFT, null);
  $('drawer').hidden = false;
  $('drawer-mask').hidden = false;
  $('drawer').scrollTop = 0;
  wireVerify(c);
  refreshVerdict(c);
}

/** 收集当前界面上的选择。 */
function collectDraft() {
  const pick = (name) => Array.from(document.querySelectorAll(`[data-vg="${name}"]:checked`))
    .map((el) => el.value);
  return {
    gates: pick('gates'),
    musts: pick('musts'),
    bonus: pick('bonus'),
    caliber: ($('v-caliber') || {}).value || '',
    verification: ($('v-verif') || {}).value || '',
    source_kinds: pick('source_kinds'),
    sources: collectSources(),
    corrections: collectCorrections(),
    note: ($('v-note') || {}).value || '',
  };
}

/** 来源表：每行 label + url + kind。 */
function collectSources() {
  return Array.from(document.querySelectorAll('.v-src-row')).map((row) => ({
    label: (row.querySelector('.v-src-label') || {}).value || '',
    url: (row.querySelector('.v-src-url') || {}).value || '',
    kind: (row.querySelector('.v-src-kind') || {}).value || 'press',
  })).filter((s) => s.url || s.label);
}

/** 修正表：记录「别人抄错了什么」。库里最有价值的字段之一。 */
function collectCorrections() {
  return Array.from(document.querySelectorAll('.v-cor-row')).map((row) => ({
    claim: (row.querySelector('.v-cor-claim') || {}).value || '',
    truth: (row.querySelector('.v-cor-truth') || {}).value || '',
    source: (row.querySelector('.v-cor-source') || {}).value || '',
  })).filter((x) => x.claim);
}

/** 问后端「现在这个状态算不算过」。判定逻辑只在后端一处。 */
async function refreshVerdict(c) {
  const draft = collectDraft();
  VDRAFT = draft;
  const p = $('v-verdict');
  if (p) p.innerHTML = '<span class="v-dim">判定中…</span>';
  try {
    const r = await fetch('/api/candidates/' + encodeURIComponent(c.id) + '/verification', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '判定失败');
    VRESULT = j.result;
    if (p) p.innerHTML = verdictHTML(VRESULT);
    const btn = $('v-publish');
    if (btn) {
      btn.disabled = !VRESULT.publishable;
      btn.textContent = VRESULT.publishable
        ? ('发布到' + (TIER_POOL[VRESULT.tier] || '备选池'))
        : '还差条件，不能发布';
    }
  } catch (err) {
    if (p) p.innerHTML = `<span class="v-bad">判定失败：${esc(err.message)}</span>`;
  }
}

function verdictHTML(r) {
  if (!r) return '';
  const rows = [];
  if (r.gates_failed.length) {
    rows.push(`<div class="v-row v-bad">门槛未过 ${r.gates_failed.length} 项（一道都没成立）：` +
      r.gates_failed.map((g) => esc(g.label)).join('、') + '</div>');
  }
  // 已放行的反证：不拦，但必须单独列出来 —— 一眼能看出这条有哪些地方存疑
  const running = (r.denied_gates || []).filter(
    (g) => !r.gates_failed.some((f) => f.key === g.key));
  if (running.length) {
    rows.push(`<div class="v-row v-warn">已放行的 AI 反证 ${running.length} 项（另有门槛成立）：` +
      running.map((g) => esc(g.label)).join('、') + '</div>');
  }
  if ((r.unverified_gates || []).length) {
    rows.push(`<div class="v-row v-warn">门槛待你确认 ${r.unverified_gates.length} 项：` +
      r.unverified_gates.map((g) => esc(g.label)).join('、') + '</div>');
  }
  if (r.missing.length) {
    rows.push(`<div class="v-row v-warn">还差 ${r.missing.length} 项必填：` +
      r.missing.map((m) => esc(m.label)).join('、') + '</div>');
  }
  if ((r.warnings || []).length) {
    rows.push(`<div class="v-row v-warn">提醒 ${r.warnings.length} 项：` +
      r.warnings.map((w) => esc(w.label)).join('、') + '</div>');
  }
  // 三道门槛全成立的加成不是人勾的 —— 单列一行，别让人以为是勾多了
  if (r.gate_bonus) {
    rows.push(`<div class="v-row v-dim">自动加成：${esc(r.gate_bonus_label)} +${r.gate_bonus}</div>`);
  }
  const pct = Math.round((r.bonus_score / r.bonus_max) * 100);
  rows.push(`<div class="v-row v-dim">质量分 ${r.bonus_score} / ${r.bonus_max}` +
    `（阈值 ${r.threshold}）· ${pct}%</div>`);
  return `<div class="v-verdict-in ${r.publishable ? 'ok' : 'no'}">
    <div class="v-verdict-line">${esc(r.verdict)}</div>
    ${rows.join('')}
  </div>`;
}

/** 工作台主体。每层条件渲染成可点的一行，右侧显示它的理由。 */
function verifyHTML(c, sch, draft, result) {
  const has = (arr, k) => (arr || []).includes(k);

  const gateRows = sch.gates.map((g) => `
    <label class="v-opt ${has(draft.gates, g.key) ? 'on' : ''}">
      <input type="checkbox" data-vg="gates" value="${esc(g.key)}" ${has(draft.gates, g.key) ? 'checked' : ''}>
      <span class="v-opt-main">
        <b>${esc(g.label)}</b>
        <i>${esc(g.why)}</i>
      </span>
    </label>`).join('');

  // 必填项分两种：blocking=true 的拦住发布；blocking=false 的（人工核读标记）
  // 不勾也能发，只会挂一条提醒。标记由规则表带下来，界面不写死哪一项是哪种。
  const mustRows = sch.musts.map((m) => `
    <label class="v-opt ${has(draft.musts, m.key) ? 'on' : ''}">
      <input type="checkbox" data-vg="musts" value="${esc(m.key)}" ${has(draft.musts, m.key) ? 'checked' : ''}>
      <span class="v-opt-main">
        <b>${esc(m.label)}${m.blocking === false ? ' <span class="v-tag">不拦发布</span>' : ''}</b>
        <i>${esc(m.why)}</i>
      </span>
    </label>`).join('');

  const bonusRows = sch.bonus.map((b) => `
    <label class="v-opt ${has(draft.bonus, b.key) ? 'on' : ''}">
      <input type="checkbox" data-vg="bonus" value="${esc(b.key)}" ${has(draft.bonus, b.key) ? 'checked' : ''}>
      <span class="v-opt-main">
        <b>${esc(b.label)}</b>
      </span>
      <span class="v-pts">+${b.points}</span>
    </label>`).join('');

  const caliberOpts = sch.calibers.map((x) =>
    `<option value="${esc(x.key)}" ${draft.caliber === x.key ? 'selected' : ''}>${esc(x.label)} · ${esc(x.desc)}</option>`
  ).join('');

  const verifOpts = sch.verifications.map((x) =>
    `<option value="${esc(x.key)}" ${draft.verification === x.key ? 'selected' : ''}>${esc(x.label)} · ${esc(x.desc)}</option>`
  ).join('');

  // 来源构成：勾选「属于哪几级」，决定有没有一手来源
  const srcKindRows = sch.source_tiers.map((t) => `
    <label class="v-src ${has(draft.source_kinds, t.key) ? 'on' : ''}">
      <input type="checkbox" data-vg="source_kinds" value="${esc(t.key)}" ${has(draft.source_kinds, t.key) ? 'checked' : ''}>
      <span class="v-tier t-${esc(t.tier)}">${esc(t.tier)}</span>
      <span class="v-opt-main"><b>${esc(t.label)}</b></span>
    </label>`).join('');

  const srcRows = (draft.sources || []).map((s) => srcRowHTML(s)).join('');

  // AI 对口径的判断理由。以前这个字段根本不存，界面上只有一句「还差 N 项必填」——
  // 完全看不出 AI 是「没找到证据」还是「找到了反证」，而这两件事的处理方式相反。
  const reasonBox = draft.caliber_reason ? `
    <details class="v-reason">
      <summary>AI 的判断理由${draft.caliber_consistent_ai === false
        ? '（它认为口径与数字对不上）' : ''}</summary>
      <p>${esc(draft.caliber_reason)}</p>
    </details>` : '';

  const corRows = (draft.corrections || []).map((x) => corRowHTML(x)).join('');

  return `
  <div class="v-head">
    <div class="v-title">核实 · ${esc(c.name)}</div>
    <div class="v-sub">${esc(c.one_liner || '')}</div>
    ${c.blocking ? `<div class="v-blocking"><b>原本卡在哪：</b>${esc(c.blocking)}</div>` : ''}
    <div class="v-verdict" id="v-verdict"></div>
  </div>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">1</span>门槛 <em>一道成立就进库；一道都没成立且有反证才不进</em></h3>
    <div class="v-list">${gateRows}</div>
  </section>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">2</span>必填 <em>不带「不拦发布」的必须齐全；口径 / 等级 / 来源另算</em></h3>
    <div class="v-list">${mustRows}</div>
    <div class="v-fields">
      <label class="v-field">
        <span>收入是哪个口径</span>
        <select id="v-caliber"><option value="">请选择…</option>${caliberOpts}</select>
      </label>
      <label class="v-field">
        <span>核实等级</span>
        <select id="v-verif"><option value="">请选择…</option>${verifOpts}</select>
      </label>
    </div>
    ${reasonBox}
    <div class="v-sublabel">来源属于哪几级（没有一手来源也能先存，会标黄提醒）</div>
    <div class="v-list v-list-src">${srcKindRows}</div>
  </section>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">3</span>加分项 <em>凑够 ${sch.threshold} 分进精品池，否则进备选池</em></h3>
    <div class="v-list">${bonusRows}</div>
  </section>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">4</span>来源 <em>每条都要能点回原文</em></h3>
    <div class="v-rows" id="v-srcs">${srcRows}</div>
    <button class="v-add" id="v-add-src" type="button">+ 加一条来源</button>
  </section>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">5</span>修正 <em>发现别人抄错了什么，记在这里</em></h3>
    <div class="v-rows" id="v-cors">${corRows}</div>
    <button class="v-add" id="v-add-cor" type="button">+ 记一条修正</button>
  </section>

  <section class="v-sec">
    <h3 class="v-h"><span class="v-n">6</span>备注</h3>
    <textarea id="v-note" class="v-note" rows="3" placeholder="核完之后想让自己记住的判断…">${esc(draft.note || '')}</textarea>
  </section>

  <div class="v-actions">
    <button class="v-btn" id="v-save" type="button">存草稿</button>
    <button class="v-btn primary" id="v-publish" type="button" disabled>还差条件，不能发布</button>
  </div>`;
}

function srcRowHTML(s) {
  const kinds = [['stripe', '支付验证'], ['official', '官方'], ['press', '报道'],
                 ['review', '核查'], ['founder', '自述'], ['secondary', '二手转述']];
  const opts = kinds.map(([k, lab]) =>
    `<option value="${k}" ${(s.kind || 'press') === k ? 'selected' : ''}>${lab}</option>`).join('');
  return `<div class="v-src-row">
    <input class="v-src-label" placeholder="来源名称" value="${esc(s.label || '')}">
    <input class="v-src-url" placeholder="https://" value="${esc(s.url || '')}">
    <select class="v-src-kind">${opts}</select>
    <button class="v-del" type="button" title="删掉">×</button>
  </div>`;
}

function corRowHTML(x) {
  return `<div class="v-cor-row">
    <input class="v-cor-claim" placeholder="别人说的是什么" value="${esc(x.claim || '')}">
    <input class="v-cor-truth" placeholder="实际是什么" value="${esc(x.truth || '')}">
    <input class="v-cor-source" placeholder="依据链接" value="${esc(x.source || '')}">
    <button class="v-del" type="button" title="删掉">×</button>
  </div>`;
}

/** 绑事件：勾选变化就重判，增删行也重判。 */
function wireVerify(c) {
  document.querySelectorAll('[data-vg]').forEach((el) => {
    el.onchange = () => {
      el.closest('.v-opt, .v-src').classList.toggle('on', el.checked);
      refreshVerdict(c);
    };
  });
  ['v-caliber', 'v-verif', 'v-note'].forEach((id) => {
    const el = $(id);
    if (el) el.onchange = () => refreshVerdict(c);
  });

  const addSrc = $('v-add-src');
  if (addSrc) addSrc.onclick = () => {
    $('v-srcs').insertAdjacentHTML('beforeend', srcRowHTML({}));
    wireRowDeletes(() => refreshVerdict(c));
    refreshVerdict(c);
  };
  const addCor = $('v-add-cor');
  if (addCor) addCor.onclick = () => {
    $('v-cors').insertAdjacentHTML('beforeend', corRowHTML({}));
    wireRowDeletes(() => refreshVerdict(c));
    refreshVerdict(c);
  };
  wireRowDeletes(() => refreshVerdict(c));

  const save = $('v-save');
  if (save) save.onclick = async () => {
    try {
      const r = await fetch('/api/candidates/' + encodeURIComponent(c.id) + '/verification', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectDraft()),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      toast('草稿已存，下次打开接着填');
      await loadAdmin();
    } catch (err) { toast('存草稿失败：' + err.message); }
  };

  const pub = $('v-publish');
  if (pub) pub.onclick = () => publishVerified(c);
}

function wireRowDeletes(again) {
  document.querySelectorAll('.v-del').forEach((b) => {
    if (b._wired) return;
    b._wired = true;
    b.onclick = () => { b.closest('.v-src-row, .v-cor-row').remove(); again(); };
  });
}

/** 发布。后端会再验一次 —— 前端置灰只是提示，闸门在后端。 */
async function publishVerified(c) {
  const draft = collectDraft();
  const tierHint = VRESULT && VRESULT.publishable
    ? (TIER_POOL[VRESULT.tier] || '备选池') : '';
  if (!confirm(
    '把「' + c.name + '」发布到' + tierHint + '？\n\n' +
    '发布后它会从候选池移到精写案例，并带上你填的核实等级与来源。'
  )) return;

  const btn = $('v-publish');
  btn.disabled = true;
  btn.textContent = '发布中…';
  try {
    const r = await fetch('/api/candidates/' + encodeURIComponent(c.id) + '/promote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verification_config: draft }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || '发布失败');
    toast('已发布到' + (TIER_POOL[j.case.tier] || '备选池') +
          '：' + j.case.name);
    closeDrawer();
    await loadAdmin();
    switchView('published');
  } catch (err) {
    toast('发布失败：' + err.message);
    btn.disabled = false;
    btn.textContent = '重新发布';
  }
}