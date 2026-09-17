/* 前端渲染冒烟测试：用最小 DOM 桩跑一遍 app.js 的所有渲染函数。
   目的：在没有浏览器的情况下，确认渲染逻辑不抛异常、且真的产出了 HTML。
   用法：node uitest.js                        （文件系统读 data/，服务端模式）
        node uitest.js http://127.0.0.1:5052  （走真实接口）
        node uitest.js --static               （测 scripts/build_static.py 的产物）
        node uitest.js --static=public        （测别的输出目录） */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = __dirname;

const ARGS = process.argv.slice(2);
const STATIC = ARGS.some((a) => a === '--static' || a.startsWith('--static='));
const STATIC_DIR = ((ARGS.find((a) => a.startsWith('--static=')) || '').split('=')[1]) || 'dist';
const API = ARGS.find((a) => /^https?:\/\//.test(a)) || '';

/* 静态产物里，数据在 dist/data.js（window.__CASE_LIB_DATA__ = {...}）。
   这里用 vm 执行它，既拿到了数据，也顺便验证了那个文件本身是可执行的。 */
function readStaticData() {
  const p = path.join(ROOT, STATIC_DIR, 'data.js');
  if (!fs.existsSync(p)) {
    throw new Error('找不到 ' + STATIC_DIR + '/data.js —— 先跑 python scripts/build_static.py');
  }
  const box = { window: {} };
  vm.createContext(box);
  vm.runInContext(fs.readFileSync(p, 'utf8'), box, { filename: 'data.js' });
  const d = box.window.__CASE_LIB_DATA__;
  if (!d) throw new Error(STATIC_DIR + '/data.js 里没有 window.__CASE_LIB_DATA__');
  return d;
}

/* ---------------- 最小 DOM 桩 ---------------- */
const nodes = new Map();

function mkEl(id) {
  const el = {
    id,
    _html: '',
    textContent: '',
    value: '',
    hidden: false,
    dataset: {},
    style: {},
    scrollTop: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    querySelectorAll() { return []; },
    insertAdjacentHTML(_pos, html) { this._html += html; },
    appendChild() {},
    focus() {},
    onclick: null,
    oninput: null,
    onchange: null,
  };
  return el;
}

function get(id) {
  if (!nodes.has(id)) nodes.set(id, mkEl(id));
  return nodes.get(id);
}

const document = {
  getElementById: get,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  activeElement: null,
  createElement: mkEl,
  head: { appendChild: () => {} },
  body: { appendChild: () => {} },
};

const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

function loadData() {
  if (STATIC) return Promise.resolve(readStaticData());
  if (!API) {
    const rd = (n) => JSON.parse(fs.readFileSync(path.join(ROOT, 'data', n + '.json'), 'utf8'));
    const cases = rd('cases'), candidates = rd('candidates');
    let inbox = [];
    try { inbox = rd('inbox'); } catch (e) { inbox = []; }
    const sources = rd('sources');
    // 站点元信息（公众号/社群/仓库）也读进来：app.js 的引流位靠它渲染，
    // 少了这一项，测试就永远看不到引流位，等于没测。
    let site = {};
    try { site = rd('site'); } catch (e) { site = {}; }
    // 核实草稿：候选卡上的「草稿 xx%」和工作台回填都靠它
    let verifications = {};
    try { verifications = rd('verifications'); } catch (e) { verifications = {}; }
    const byV = {}, byM = {}, byC = {};
    cases.forEach((c) => {
      byV[c.verification] = (byV[c.verification] || 0) + 1;
      byC[c.category || '未分类'] = (byC[c.category || '未分类'] || 0) + 1;
      (c.models || []).forEach((m) => { byM[m] = (byM[m] || 0) + 1; });
    });
    const verified = (byV.stripe || 0) + (byV.official || 0);
    return Promise.resolve({
      cases, candidates, inbox, sources, site, verifications,
      stats: {
        curated: cases.length, candidates: candidates.length, inbox: inbox.length,
        verified, flagged: cases.reduce((a, c) => a + (c.corrections || []).length, 0)
          + cases.filter((c) => c.verification === 'disputed').length,
        categories: Object.keys(byC).length,
        by_verification: byV, by_category: byC, by_model: byM,
        // 三套评分的存在数量（真实接口由 server.py 计算，这里对齐一下）
        china_scored: cases.filter((c) => c.china_fit).length,
        solo_scored: cases.filter((c) => c.solo_fit).length,
        dual_scored: cases.filter((c) => c.composite).length,
      },
      generated_at: new Date().toISOString(),
    });
  }
  return fetch(API + '/api/data').then((r) => r.json());
}

/* 核实规则表：跑 python 拿真规则，而不是在测试里抄一份。
   抄一份的话，规则改了测试还是绿的，等于没测。 */
let _schemaCache = null;
function readVerifySchema() {
  if (_schemaCache) return _schemaCache;
  const { execFileSync } = require('child_process');
  const out = execFileSync('python', ['scripts/verify_rules.py', '--schema'],
    { cwd: ROOT, encoding: 'utf8' });
  _schemaCache = JSON.parse(out);
  return _schemaCache;
}

const errors = [];const sandbox = {
  document, localStorage, console,
  // 静态模式下把 fetch 做成「一调就炸」：万一 app.js 在静态模式里还去请 /api，
  // 测试会立刻暴露，而不是静默拿到 undefined
  fetch: STATIC
    ? () => { throw new Error('静态模式不该调用 fetch(/api)'); }
    : (u, opt) => {
      // 核实规则表：真实实现里由 server.py 从 scripts/verify_rules.py 取。
      // 测试里跑一次那个模块拿真规则，保证界面测的是真规则而不是抄来的副本。
      if (String(u).includes('/api/verify-schema')) {
        const schema = readVerifySchema();
        return Promise.resolve({ ok: true, json: () => Promise.resolve(schema) });
      }
      return loadData().then((d) => ({ ok: true, json: () => Promise.resolve(d) }));
    },
  CSS: { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') },
  setTimeout, clearTimeout, confirm: () => false, alert: () => {},
  window: {},
};
sandbox.globalThis = sandbox;

/* app.js 靠 window.__STATIC__ 判断运行模式；静态模式下再把数据预置进 window，
   这样 loadStaticData() 走「已存在」那条快路径（真实浏览器里是先 script 加载 data.js）。 */
if (STATIC) {
  sandbox.window.__STATIC__ = true;
  try {
    sandbox.window.__CASE_LIB_DATA__ = readStaticData();
  } catch (e) {
    console.log('[FAIL] 读取静态产物失败: ' + e.message);
    process.exit(1);
  }
}

/* ---------------- 跑起来 ---------------- */
// 静态模式直接测构建出来的那份 app.js，而不是源文件——否则测的不是要部署的东西
const APP_SRC = STATIC
  ? path.join(ROOT, STATIC_DIR, 'app.js')
  : path.join(ROOT, 'static', 'app.js');
const src = fs.readFileSync(APP_SRC, 'utf8');
vm.createContext(sandbox);

// 捕获未处理异常。
// 关键：异步段里抛的同步异常会被 unhandledRejection 吞掉，而进程退出码仍是 0 ——
// 表现是「测试跑一半没输出了，却算通过」。所以两种都要接，且都要计入 fail。
process.on('unhandledRejection', (e) => {
  console.log('\n  [FAIL] 未处理的 Promise 异常：' + (e && e.message ? e.message : e));
  if (e && e.stack) console.log('        ' + String(e.stack).split('\n')[1]);
  fail += 1;
});
process.on('uncaughtException', (e) => {
  console.log('\n  [FAIL] 未捕获异常：' + (e && e.message ? e.message : e));
  if (e && e.stack) console.log('        ' + String(e.stack).split('\n')[1]);
  fail += 1;
});

/** 每个检查段落都包一层，任何一段炸了也要把结果打出来、别静默中止 */
function section(title, fn) {
  console.log(title);
  try {
    fn();
  } catch (e) {
    console.log('  [FAIL] 这一段抛异常中止了：' + (e && e.message ? e.message : e));
    if (e && e.stack) console.log('        ' + String(e.stack).split('\n')[1]);
    fail += 1;
  }
}

(async () => {
  console.log('='.repeat(60));
  console.log('  前端渲染冒烟测试' + (STATIC
    ? '（静态产物 ' + STATIC_DIR + '/，只读模式）'
    : API ? '（走真实接口 ' + API + '）' : '（读本地 data/）'));
  console.log('='.repeat(60));

  try {
    vm.runInContext(src, sandbox, { filename: 'app.js' });
  } catch (e) {
    console.log('[FAIL] app.js 求值抛异常: ' + e.message);
    process.exit(1);
  }

  // 等 main() 里的 await load() 完成
  await new Promise((r) => setTimeout(r, 600));

  const checks = [
    ['stat-strip', '统计条', '精写案例'],
    ['funnel', '三级漏斗', '原始素材'],
    ['chips', '模式芯片', 'chip'],
    ['grid-cases', '案例卡片网格', 'class="card"'],
    ['grid-cands', '候选池网格', 'class="cand"'],
    // 精简构建（--no-inbox）里队列是故意不带的，此时应显示「不含采集队列」的说明，
    // 而不是一排卡片 —— 两种都算通过
    ['grid-inbox', '采集队列网格', /class="cand"|不含采集队列/],
    ['src-list', '信息源卡片', 'src-card'],
    ['filterbox', '采集过滤规则', 'rule-tag'],
    ['method', '方法论', 'm-step'],
    ['side-meta', '侧栏元信息', '案例'],
    ['vigilance', '数据健康度横幅', '核实'],
    ['china-key', '移植评分维度说明', 'ck-item'],
    ['china-board', '移植排行榜', 'cboard-row'],
    ['china-podium', '金银铜领奖台', 'pod-medal'],
  ];

  let pass = 0, fail = 0;
  console.log('\n[A] 各区块是否产出 HTML');
  for (const [id, label, kw] of checks) {
    const html = get(id)._html || '';
    // kw 可以是字符串，也可以是正则（同一个区块在不同构建里有不同正当形态时用正则）
    const ok = html.length > 40 && (kw instanceof RegExp ? kw.test(html) : html.includes(kw));
    console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label.padEnd(14) +
      ' ' + String(html.length).padStart(6) + ' 字符');
    ok ? pass++ : fail++;
  }

  // 已读进度不是 innerHTML，而是几个元素的文本/宽度
  const readN = get('read-n').textContent;
  const readBarW = get('read-bar').style.width;
  const rTrackOk = /^\d+$/.test(String(readN)) && /%$/.test(String(readBarW));
  console.log('  [' + (rTrackOk ? 'PASS' : 'FAIL') + '] 已读进度          已读=' +
    readN + ' 目标=' + get('read-goal').textContent + ' 进度条=' + readBarW);
  rTrackOk ? pass++ : fail++;

  console.log('\n[B] 卡片数量是否与数据一致');
  // 注意：card 元素的 class 是 "card" 或 "card read"，不能匹配 .card-top 这类
  const countCards = (html) => (html.match(/class="card(?:\s+read)?"/g) || []).length;
  const caseCards = countCards(get('grid-cases')._html || '');
  const candCards = (get('grid-cands')._html.match(/class="cand"/g) || []).length;
  const inboxCards = (get('grid-inbox')._html.match(/class="cand"/g) || []).length;
  const expect = API ? null : {
    cases: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'cases.json'), 'utf8')).length,
    cands: JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'candidates.json'), 'utf8')).length,
  };
  const nC = (get('nav-count-cases').textContent);
  const nK = (get('nav-count-cands').textContent);
  console.log('  案例卡片 ' + caseCards + ' 张，导航计数 ' + nC + (expect ? '（数据 ' + expect.cases + '）' : ''));
  console.log('  候选卡片 ' + candCards + ' 张，导航计数 ' + nK + (expect ? '（数据 ' + expect.cands + '）' : ''));
  console.log('  队列卡片 ' + inboxCards + ' 张（上限 200）');
  const cntOk = expect ? (caseCards === expect.cases && candCards === expect.cands) : true;
  console.log('  [' + (cntOk ? 'PASS' : 'FAIL') + '] 数量与数据源一致');
  cntOk ? pass++ : fail++;

  console.log('\n[C] 详情抽屉能否渲染每条案例');
  let drawerFails = [];
  const cases = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'cases.json'), 'utf8'));
  // 通过 openCase 走一遍（它调用了 detailHTML）
  for (const c of cases) {
    try {
      sandbox.openCase(c.id);
      const html = get('drawer-body')._html || '';
      if (html.length < 300) drawerFails.push(c.id + ' (仅 ' + html.length + ' 字符)');
    } catch (e) {
      drawerFails.push(c.id + ' -> ' + e.message);
    }
  }
  const dOk = drawerFails.length === 0;
  console.log('  [' + (dOk ? 'PASS' : 'FAIL') + '] ' + cases.length + ' 条案例详情全部渲染成功');
  if (!dOk) drawerFails.forEach((f) => console.log('      ✗ ' + f));
  dOk ? pass++ : fail++;

  console.log('\n[D] 已读进度是否写入 localStorage');
  const readRaw = localStorage.getItem('acl_read_v1');
  let readArr = [];
  try { readArr = JSON.parse(readRaw || '[]'); } catch (e) {}
  const rOk = readArr.length === cases.length;
  console.log('  [' + (rOk ? 'PASS' : 'FAIL') + '] 已读 ' + readArr.length + ' / ' + cases.length);
  rOk ? pass++ : fail++;

  console.log('\n[E] 特殊字符与空值健壮性');
  // vm 里的 let 绑定不会挂到 sandbox 上，所以要在 context 内部执行
  try {
    vm.runInContext(`
      DATA.cases.push({
        id: 'dirty-test', name: '<script>alert(1)</script>', name_en: 'X & "Y"',
        one_liner: '引号 " 和 & 和 <b>标签</b>', category: "O'Brien",
        verification: 'stripe', metrics: {}, models: [], tags: [],
        why_it_works: ['<img onerror=x>'], playbook: [], signals: [],
        corrections: [{ claim: '<b>c</b>', truth: 'a & b', source: 'https://x/?a=1&b=2' }],
        sources: [{ label: 'L & M', url: 'https://y/?q=1&r=2', kind: 'press' }],
        replicability: {}, verdict: '<em>v</em>'
      });
      openCase('dirty-test');
    `, sandbox);
    vm.runInContext('var __H__ = document.getElementById("drawer-body").innerHTML;', sandbox);
    const h = sandbox.__H__ || '';
    const noRawScript = !h.includes('<script>alert');
    const noRawImg = !h.includes('<img onerror');
    const escapedAmp = h.includes('&amp;');
    const ok = noRawScript && noRawImg && escapedAmp;
    console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] HTML 转义正确' +
      '（裸 <script> ' + (noRawScript ? '无' : '有') +
      ' / 裸 <img onerror> ' + (noRawImg ? '无' : '有') +
      ' / & 已转义 ' + (escapedAmp ? '是' : '否') + '）');
    ok ? pass++ : fail++;
  } catch (e) {
    console.log('  [FAIL] 脏数据渲染抛异常: ' + e.message);
    fail++;
  }

  console.log('\n[F] 国内移植排行');
  try {
    const ranked = JSON.parse(vm.runInContext(
      'JSON.stringify(chinaRanked().map((c) => ({ id: c.id, score: c.china_fit.score, medal: c.china_fit.medal })))',
      sandbox));
    if (!ranked.length) {
      console.log('  [SKIP] 数据里没有 china_fit，跳过（先跑 score_china_fit.py）');
    } else {
      const medals = ranked.slice(0, 3).map((r) => r.medal).join(',');
      const okMedal = medals === 'gold,silver,bronze';
      console.log('  [' + (okMedal ? 'PASS' : 'FAIL') + '] 前三名奖牌 = ' + medals);
      okMedal ? pass++ : fail++;

      const desc = ranked.every((r, i) => i === 0 || ranked[i - 1].score >= r.score);
      console.log('  [' + (desc ? 'PASS' : 'FAIL') + '] 分数降序排列（' + ranked.length + ' 条已评分）');
      desc ? pass++ : fail++;

      const dimsOk = vm.runInContext(
        'DATA.cases.filter((c) => c.china_fit).every((c) => Object.keys(c.china_fit.dims || {}).length === 6)',
        sandbox);
      console.log('  [' + (dimsOk ? 'PASS' : 'FAIL') + '] 每条都有完整 6 个维度');
      dimsOk ? pass++ : fail++;

      const rows = (get('china-board')._html.match(/class="cboard-row/g) || []).length;
      const okRows = rows === ranked.length;
      console.log('  [' + (okRows ? 'PASS' : 'FAIL') + '] 排行榜渲染 ' + rows + ' 行 / 应 ' + ranked.length + ' 行');
      okRows ? pass++ : fail++;

      vm.runInContext('sortBy = "china"; renderCases();', sandbox);
      const firstId = (get('grid-cases')._html.match(/data-id="([^"]+)"/) || [])[1];
      const okSort = firstId === ranked[0].id;
      console.log('  [' + (okSort ? 'PASS' : 'FAIL') + '] 按移植分排序首位 = ' + firstId + '（应 ' + ranked[0].id + '）');
      okSort ? pass++ : fail++;
      vm.runInContext('sortBy = "default"; renderCases();', sandbox);

      const okBadge = (get('grid-cases')._html.match(/class="cf-china/g) || []).length === ranked.length;
      console.log('  [' + (okBadge ? 'PASS' : 'FAIL') + '] 每张卡片都带移植分徽章');
      okBadge ? pass++ : fail++;
    }
  } catch (e) {
    console.log('  [FAIL] 排行渲染抛异常: ' + e.message);
    fail++;
  }

  console.log('\n[G] 综合排行（solo_fit / composite）与按分筛选');
  const ck = (ok, label) => {
    console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label);
    ok ? pass++ : fail++;
  };
  try {
    const dualN = vm.runInContext('dualRanked().length', sandbox);
    const soloN = vm.runInContext('soloRanked().length', sandbox);
    ck(dualN > 0 && soloN > 0, '已评分：综合 ' + dualN + ' 条 / 个人可做性 ' + soloN + ' 条');

    // 四象限
    const quadCount = (get('dual-quads')._html.match(/class="quad q-/g) || []).length;
    ck(quadCount === 4, '四象限渲染出 4 个格子（实际 ' + quadCount + '）');
    const qSum = vm.runInContext('QUAD_ORDER.reduce((a,k)=>a+quadCases(k).length,0)', sandbox);
    ck(qSum === dualN, '四象限分组不重不漏：合计 ' + qSum + ' / 应 ' + dualN);

    // 榜单行数
    const dbRows = (get('dual-board')._html.match(/class="dboard-row/g) || []).length;
    const sbRows = (get('solo-board')._html.match(/class="dboard-row/g) || []).length;
    ck(dbRows === dualN, '综合榜渲染 ' + dbRows + ' 行 / 应 ' + dualN);
    ck(sbRows === soloN, '个人可做性榜渲染 ' + sbRows + ' 行 / 应 ' + soloN);

    // 综合分公式：0.6 × 短板 + 0.4 × 均值
    const formulaOk = vm.runInContext(`DATA.cases.filter(c=>c.composite).every(c=>{
      const f = c.composite;
      const lo = Math.min(f.solo, f.china), mean = (f.solo + f.china) / 2;
      const want = Math.round((0.6 * lo + 0.4 * mean) * 10) / 10;
      return Math.abs(f.score - want) < 0.051;
    })`, sandbox);
    ck(formulaOk, '综合分 = 0.6×短板 ＋ 0.4×均值');

    // 奖牌与降序
    const dMedal = vm.runInContext('dualRanked().slice(0,3).map(c=>c.composite.medal).join(",")', sandbox);
    ck(dMedal === 'gold,silver,bronze', '综合前三奖牌 = ' + dMedal);
    const sMedal = vm.runInContext('soloRanked().slice(0,3).map(c=>c.solo_fit.medal).join(",")', sandbox);
    ck(sMedal === 'gold,silver,bronze', '个人可做性前三奖牌 = ' + sMedal);
    ck(vm.runInContext('dualRanked().every((c,i,a)=>i===0||a[i-1].composite.score>=c.composite.score)', sandbox),
      '综合分降序排列');
    ck(vm.runInContext('soloRanked().every((c,i,a)=>i===0||a[i-1].solo_fit.score>=c.solo_fit.score)', sandbox),
      '个人可做性降序排列');

    // 五维完整性（原来的 replicability 只有四维）
    ck(vm.runInContext('DATA.cases.filter(c=>c.solo_fit).every(c=>Object.keys(c.solo_fit.dims||{}).length===5)', sandbox),
      '每条个人可做性都有完整 5 维（含「单人交付」）');

    // 排序下拉新增的两个分支
    const soloTopId = vm.runInContext('soloRanked()[0].id', sandbox);
    vm.runInContext('sortBy="solo"; renderCases();', sandbox);
    let firstId = (get('grid-cases')._html.match(/data-id="([^"]+)"/) || [])[1];
    ck(firstId === soloTopId, '按个人可做性排序首位 = ' + firstId);
    const dualTopId = vm.runInContext('dualRanked()[0].id', sandbox);
    vm.runInContext('sortBy="composite"; renderCases();', sandbox);
    firstId = (get('grid-cases')._html.match(/data-id="([^"]+)"/) || [])[1];
    ck(firstId === dualTopId, '按综合分排序首位 = ' + firstId);
    vm.runInContext('sortBy="default"; renderCases();', sandbox);

    // 卡片徽章
    const sBadge = (get('grid-cases')._html.match(/class="cf-solo/g) || []).length;
    const dBadge = (get('grid-cases')._html.match(/class="cf-dual/g) || []).length;
    ck(sBadge === soloN, '卡片带「个」徽章 ' + sBadge + ' 张 / 应 ' + soloN);
    ck(dBadge === dualN, '卡片带「综」徽章 ' + dBadge + ' 张 / 应 ' + dualN);

    // 筛选：象限
    const goN = vm.runInContext('quadCases("go").length', sandbox);
    vm.runInContext('quadFilter="go"; renderCases();', sandbox);
    const afterQuad = vm.runInContext('filtered().length', sandbox);
    ck(afterQuad === goN, '象限筛选「可以开干」→ ' + afterQuad + ' 条');
    ck(((get('grid-cases')._html.match(/class="card(?:\s+read)?"/g) || []).length) === goN,
      '筛选后卡片数与命中数一致');

    // 筛选：维度 × 下限
    vm.runInContext('quadFilter=""; fitDim="solo"; fitLevel=70; renderCases();', sandbox);
    const n70 = vm.runInContext('filtered().length', sandbox);
    const allHigh = vm.runInContext('filtered().every(c=>c.solo_fit && c.solo_fit.score>=70)', sandbox);
    ck(allHigh && n70 > 0, '个人可做性 ≥70 → ' + n70 + ' 条且全部达标');

    // 筛选叠加：象限 × 分数
    vm.runInContext('quadFilter="go"; fitDim="solo"; fitLevel=70; renderCases();', sandbox);
    const stacked = vm.runInContext('filtered().length', sandbox);
    ck(stacked <= n70 && stacked <= goN, '叠加筛选（象限 ＋ 分数）命中 ' + stacked + ' 条');

    // 命中提示
    vm.runInContext('renderFilterBar();', sandbox);
    const tip = get('fb-count').textContent || '';
    ck(tip.indexOf('筛选中') === 0, '筛选栏提示：' + tip);

    // 重置
    const totalCases = vm.runInContext('DATA.cases.length', sandbox);
    vm.runInContext('resetFitFilter();', sandbox);
    const backN = vm.runInContext('filtered().length', sandbox);
    ck(backN === totalCases, '清空筛选恢复全量 ' + backN + ' / 应 ' + totalCases);

    // 抽屉里的新区块
    vm.runInContext('openCase("' + dualTopId + '")', sandbox);
    const dh = get('drawer-body')._html || '';
    ck(dh.indexOf('个人可做性') !== -1, '抽屉含「个人可做性」区块');
    ck(dh.indexOf('综合分') !== -1, '抽屉含「综合分」区块');
    ck(dh.indexOf('quad-badge') !== -1, '抽屉含象限徽章');
    ck(dh.indexOf('axis-row') !== -1, '抽屉含双轴对比条');
    ck(dh.indexOf('rep-5') !== -1, '抽屉里个人可做性用五列网格');
  } catch (e) {
    console.log('  [FAIL] 综合排行 / 筛选测试抛异常: ' + e.message);
    fail++;
  }

  console.log('\n[H] 空数据 / 缺字段健壮性');
  try {
    vm.runInContext(`
      DATA.cases.push({ id: 'empty-test', name: '空字段测试', verification: 'unverified' });
      openCase('empty-test');
      DATA.cases.pop();
      DATA.cases.push({ id: 'null-metrics', name: '空指标', verification: 'stripe',
                        metrics: { headline: '', arr: null } });
      openCase('null-metrics');
      DATA.cases.length = DATA.cases.length - 1;
    `, sandbox);
    console.log('  [PASS] 缺 metrics / 全空字段不抛异常');
    pass++;
  } catch (e) {
    console.log('  [FAIL] 缺字段渲染抛异常: ' + e.message);
    fail++;
  }

  /* ---------------- 运行模式 ----------------
     这个站是纯只读的阅读站 —— 不管有没有后端，界面上都不该有任何写按钮。
     核实与入库搬到了另一个站点（admin/，另一个端口），所以这里连
     「未登录」这种提示都不需要：读者根本不知道有后台存在。 */
  console.log('\n[I] 运行模式（' + (STATIC ? '静态部署' : '本地服务') + '）');
  {
    const candHTML = get('grid-cands')._html || '';
    const inboxHTML = get('grid-inbox')._html || '';
    const metaHTML = get('side-meta')._html || '';
    const metaRo = metaHTML.includes('只读快照');

    const ck = (label, ok, extra) => {
      console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label + (extra ? '  ' + extra : ''));
      ok ? pass++ : fail++;
    };

    ck('不渲染「核实」按钮', !candHTML.includes('data-verify="'));
    ck('不渲染「转入候选池」按钮', !inboxHTML.includes('data-tocand="'));
    ck('不出现「未登录」字样（这里没有登录这回事）',
      !candHTML.includes('未登录') && !inboxHTML.includes('未登录'));

    if (STATIC) {
      ck('侧栏标注只读快照', metaRo);
      ck('数据来自 data.js（没有走 /api）', !!sandbox.window.__CASE_LIB_DATA__);
      ck('静态标记已生效', vm.runInContext('IS_STATIC === true', sandbox));
    } else {
      ck('本地服务不显示只读标记', !metaRo);
      ck('本地模式 IS_STATIC 为假', vm.runInContext('IS_STATIC === false', sandbox));
    }
  }

  section('\n[I2] 卡片来源链接（官网 / 来源 / 缺省提示）', () => {
    const ck = (label, ok, extra) => {
      console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label + (extra ? '  ' + extra : ''));
      ok ? pass++ : fail++;
    };

    /* cardLinks 是纯函数，直接在沙箱里喂各种形状的记录，验证四种输出。
       之前这段逻辑是「有 source_url 才渲染，否则整段消失」，
       导致字段为空时页面上看着像「这个产品没有官网」。 */
    const hasFn = vm.runInContext('typeof cardLinks === "function"', sandbox);
    ck('app.js 导出 cardLinks', hasFn);
    if (!hasFn) return;                  // 构建产物没重新生成时，别把后面的断言带崩

    const run = (obj) => vm.runInContext(
      'cardLinks(' + JSON.stringify(obj) + ')', sandbox);

    const both = run({ website: 'https://stan.store/', source_url: 'https://trustmrr.com/startup/stan' });
    ck('两条都有时渲染两个链接', both.includes('官网') && both.includes('来源'));
    ck('官网链接指向 website', both.includes('href="https://stan.store/"'));
    ck('来源链接指向 source_url', both.includes('href="https://trustmrr.com/startup/stan"'));
    ck('链接文字用域名缩写', both.includes('stan.store') && both.includes('trustmrr.com/startup/stan'));
    ck('外链都带 noopener', (both.match(/rel="noopener"/g) || []).length === 2);

    const onlyWeb = run({ website: 'https://a.io/' });
    ck('只有官网时只渲染官网', onlyWeb.includes('官网') && !onlyWeb.includes('来源'));

    const onlySrc = run({ source_url: 'https://x.com/a' });
    ck('只有来源时只渲染来源', onlySrc.includes('来源') && !onlySrc.includes('官网'));

    const none = run({});
    ck('两个都没有时给灰字说明（不是整段消失）', none.includes('来源未记录'));
    ck('缺省提示不带链接', !none.includes('<a '));

    /* 反向：卡片 HTML 里不该再出现旧的「字段空就整段不渲染」写法 */
    const inboxHTML = get('grid-inbox')._html || '';
    const candHTML = get('grid-cands')._html || '';
    ck('卡片底部渲染了来源行', inboxHTML.includes('cand-link') || candHTML.includes('cand-link')
      || inboxHTML.includes('来源未记录') || candHTML.includes('来源未记录'));
  });

  console.log('\n[J] 引流位（顶部条 / 页脚 / 详情抽屉底）');
  (function checkPromo() {
    const ck = (label, ok, extra) => {
      console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label + (extra ? '  ' + extra : ''));
      ok ? pass++ : fail++;
    };
    let site = {};
    try { site = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'site.json'), 'utf8')); }
    catch (e) { site = {}; }
    const wx = site.wechat || {};
    const cm = site.community || {};
    if (!wx.name) {
      console.log('  [SKIP] data/site.json 里没配公众号，跳过');
      return;
    }
    const bar = get('promo-bar')._html || '';
    const foot = get('site-foot')._html || '';
    ck('顶部条渲染出公众号名', bar.includes(wx.name));
    ck('顶部条给出可执行动作（微信搜索）', bar.includes('微信搜索关注'));
    ck('页脚渲染出公众号与仓库',
      foot.includes('sf-in') && foot.includes(wx.name)
      && (!site.repo || foot.includes(site.repo)));

    // 社群还没开通时绝不能做成能点的链接——点了没反应比不放更伤信任
    if (cm.name && !cm.url) {
      ck('社群未开通时不渲染成链接', foot.includes('sf-a ghost'));
    } else if (cm.name && cm.url) {
      ck('社群已配 url 时渲染成外链', foot.includes('href="' + cm.url + '"'));
    }

    // 抽屉底部：读者刚读完一条，转化意愿最高的位置
    sandbox.openCase(cases[0].id);
    const dh = get('drawer-body')._html || '';
    ck('详情抽屉底部有引流块', dh.includes('class="d-promo"'));
    ck('抽屉引流块文案来自配置', dh.includes(wx.name));
  })();

  /* ---------------- 公开站上不该有后台的痕迹 ----------------
     核实与入库现在是另一个站点（admin/，另一个端口）。
     所以这里要验的是「搬干净了」：公开站的代码里一个后台函数都不剩，
     界面上也没有任何点了没反应的按钮。

     这不是洁癖 —— 留着 openVerify / verifyHTML 在公开站的 app.js 里，
     等于把后台的 DOM 结构、字段名、接口路径白送给任何「查看源代码」的人；
     留着一颗点不动的按钮，则是在告诉读者这个站本来是能编辑的。

     工作台本身（门槛 / 必填 / 加分那套断言）在 uitest-admin.js 里测，
     那里加载的是 admin/verify.js。 */
  console.log('\n[K] 公开站没有后台代码');
  (function checkNoAdminCode() {
    const ck = (label, ok, extra) => {
      console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label + (extra ? '  ' + extra : ''));
      ok ? pass++ : fail++;
    };

    // src 是这份测试实际求值的那一份（静态模式下是构建产物）
    for (const name of ['openVerify', 'verifyHTML', 'draftProgress', 'publishVerified',
                        'ensureSchema', 'VSCHEMA', 'VDRAFT', 'VRESULT',
                        'loadSession', 'renderAdminChip', 'openLogin', 'doLogin',
                        'doLogout', 'IS_ADMIN', '/api/login', '/api/logout',
                        'verify-schema']) {
      ck('app.js 里没有 ' + name, !src.includes(name));
    }
    ck('app.js 里没有 toCandidate（写操作已搬走）', !src.includes('async function toCandidate'));
    ck('页面里没有后台入口按钮', !get('admin-btn')._html);
    ck('抽屉里没被塞过登录框', !(get('drawer-body')._html || '').includes('login-pw'));
    ck('统计条正常渲染（说明页面本身是好的）',
      (get('stat-strip')._html || '').includes('精写案例'));
  })();

  if (errors.length) {
    console.log('\n[!] 捕获到未处理异常：');
    errors.forEach((e) => console.log('    ' + e));
    fail += errors.length;
  }

  console.log('\n' + '='.repeat(60));
  console.log('  结果：' + pass + ' 通过 / ' + fail + ' 失败');
  console.log('='.repeat(60));
  process.exit(fail ? 1 : 0);
})();
