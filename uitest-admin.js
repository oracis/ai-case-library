/* 管理后台的前端冒烟测试：用最小 DOM 桩跑一遍 admin/ 的两个脚本。

   和 uitest.js 分开，是因为它们测的是两个站：
     uitest.js        公开站（static/）—— 只读，谁都能看
     uitest-admin.js  后台站（admin/）—— 要登录，能核实与入库
   两边不该有任何共享的界面代码，测试也就各写各的。

   用法：node uitest-admin.js
   依赖：data/ 下的 json（直接读文件，不起服务）与 scripts/verify_rules.py
*/

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = __dirname;
let pass = 0, fail = 0;
const errors = [];

function ck(label, ok, extra) {
  console.log('  [' + (ok ? 'PASS' : 'FAIL') + '] ' + label + (extra ? '  ' + extra : ''));
  ok ? pass++ : fail++;
}

/* ---------------- 最小 DOM 桩 ----------------
   比 uitest.js 那份多两样东西：
     - classList 真做记录，因为 switchView 要靠 .adm-nav 的 active 态切换
     - querySelectorAll 能按选择器返回刚渲染出来的按钮，
       否则「点核实打开工作台」这条路径根本测不到 */
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
    _cls: new Set(),
    get className() { return [...this._cls].join(' '); },
    set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
    classList: {
      add(c) { el._cls.add(c); },
      remove(c) { el._cls.delete(c); },
      toggle(c, on) { on ? el._cls.add(c) : el._cls.delete(c); },
      contains(c) { return el._cls.has(c); },
    },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    /* 属性读写：真实 DOM 自带，桩里必须补上 —— aria-expanded 这类
       折叠状态就是靠它记的，缺了会让被测函数抛 TypeError，
       而异常在 async 上下文里被吞掉，表现成「测试跑一半没输出了」。 */
    _attrs: {},
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this._attrs, name)
        ? this._attrs[name] : null;
    },
    setAttribute(name, v) { this._attrs[name] = String(v); },
    removeAttribute(name) { delete this._attrs[name]; },
    querySelectorAll(sel) {
      // 只支持 admin/app.js 实际用到的两种：[data-xxx] 与 .class
      const out = [];
      if (sel.startsWith('[') && sel.endsWith(']')) {
        const attr = sel.slice(1, -1);
        const re = new RegExp(attr + '="([^"]*)"', 'g');
        let m;
        while ((m = re.exec(this._html))) {
          const b = mkEl('btn-' + attr + '-' + m[1]);
          b.dataset[attr] = m[1];
          out.push(b);
        }
      } else if (sel.startsWith('.')) {
        const cls = sel.slice(1);
        if (this._cls.has(cls)) out.push(this);
      }
      return out;
    },
    insertAdjacentHTML(_p, html) { this._html += html; },
    appendChild() {},
    focus() {},
    onclick: null, oninput: null, onchange: null, onkeydown: null,
  };
  return el;
}

function get(id) {
  if (!nodes.has(id)) nodes.set(id, mkEl(id));
  return nodes.get(id);
}

// 侧栏三个导航按钮：switchView 要按 data-view 找它们
const NAVS = ['candidates', 'inbox', 'published'].map((v) => {
  const b = mkEl('nav-' + v);
  b.className = 'adm-nav' + (v === 'candidates' ? ' active' : '');
  b.dataset.view = v;
  return b;
});

const document = {
  getElementById: get,
  querySelector: () => null,
  querySelectorAll: (sel) => (sel === '.adm-nav' ? NAVS : []),
  addEventListener: () => {},
  createElement: mkEl,
  head: { appendChild: () => {} },
  body: { appendChild: () => {} },
};

/* ---------------- 数据 ---------------- */
function rd(n, dflt) {
  try {
    return JSON.parse(fs.readFileSync(path.join(ROOT, 'data', n + '.json'), 'utf8'));
  } catch (e) {
    return dflt;
  }
}

const PAYLOAD = {
  cases: rd('cases', []),
  candidates: rd('candidates', []),
  inbox: rd('inbox', []),
  sources: rd('sources', {}),
  verifications: rd('verifications', {}),
  stats: {
    curated: rd('cases', []).length,
    candidates: rd('candidates', []).length,
    inbox: rd('inbox', []).length,
    premium: rd('cases', []).filter((c) => c.tier === 'premium').length,
    backup: rd('cases', []).filter((c) => c.tier !== 'premium').length,
  },
  generated_at: new Date().toISOString(),
};

let _schema = null;

/* 找 Python 解释器。
   原来这里直接 execFileSync('python', ...)，在 Windows 上会翻车：
   Node 的 execFileSync 不走 shell，PATH 里那个 `python` 可能是
   Microsoft Store 的 App Execution Alias 或 WSL stub，
   结果是一个 EBUSY（不是「找不到」，所以错误信息看不出怎么回事）。

   所以按优先级试探，并把最后一个失败原因带进报错里。
   可用 CASE_LIB_PYTHON 环境变量直接指定，CI 与本地都能救急。 */
function findPython() {
  if (process.env.CASE_LIB_PYTHON) return process.env.CASE_LIB_PYTHON;
  const { execFileSync } = require('child_process');
  const candidates = process.platform === 'win32'
    ? ['python', 'python3', 'py']
    : ['python3', 'python'];
  const errors = [];
  for (const exe of candidates) {
    try {
      execFileSync(exe, ['-c', 'print(1)'], { encoding: 'utf8', stdio: 'pipe' });
      return exe;
    } catch (e) {
      errors.push(exe + ' → ' + (e.code || String(e.message).slice(0, 60)));
    }
  }
  throw new Error(
    '找不到可用的 Python 解释器（试过 ' + candidates.join(' / ') + '）。\n'
    + '  失败详情：' + errors.join('；') + '\n'
    + '  指定一个：set CASE_LIB_PYTHON=C:\\path\\to\\python.exe');
}

function readVerifySchema() {
  if (_schema) return _schema;
  const { execFileSync } = require('child_process');
  const out = execFileSync(findPython(), ['scripts/verify_rules.py', '--schema'],
    { cwd: ROOT, encoding: 'utf8' });
  _schema = JSON.parse(out);
  return _schema;
}

/* ---------------- 会话状态 ----------------
   后台全靠这个开关在「登录框」和「后台主体」之间切换，
   所以测试要能把它拨来拨去 —— 顺带也验证了登出是真的清掉了状态。 */
let LOGGED_IN = false;
let LAST_PUT = null;      // 记录存草稿的请求体，用来断言「真的发了」

const sandbox = {
  document,
  console,
  fetch: (u, opt) => {
    const url = String(u);
    const method = (opt && opt.method) || 'GET';
    if (url.includes('/api/session')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ admin: LOGGED_IN }) });
    }
    if (url.includes('/api/verify-schema')) {
      if (!LOGGED_IN) {
        return Promise.resolve({ ok: false, status: 401,
          json: () => Promise.resolve({ error: '需要管理员登录' }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(readVerifySchema()) });
    }
    if (url.includes('/api/login')) {
      const body = JSON.parse((opt && opt.body) || '{}');
      if (body.password === 'correct horse') {
        LOGGED_IN = true;
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, admin: true }) });
      }
      return Promise.resolve({ ok: false, status: 401,
        json: () => Promise.resolve({ error: '密码不对' }) });
    }
    if (url.includes('/api/logout')) {
      LOGGED_IN = false;
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    if (url.includes('/api/data')) {
      if (!LOGGED_IN) {
        return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(PAYLOAD) });
    }
    if (url.includes('/verification') && method === 'PUT') {
      LAST_PUT = JSON.parse((opt && opt.body) || '{}');
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  },
  setTimeout, clearTimeout,
  confirm: () => false,
  alert: () => {},
  window: {},
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

process.on('unhandledRejection', (e) => {
  // 别只是记一笔就走：这种异常会把后面的断言整段跳过，
  // 表现成「测试跑一半没输出了」——必须当场打出来，否则极难定位。
  console.log('  [FAIL] 未处理的 Promise 异常：' + (e && e.message ? e.message : e));
  if (e && e.stack) {
    console.log('        ' + String(e.stack).split('\n')[1]);
  }
  fail += 1;
});

(async () => {
  console.log('='.repeat(60));
  console.log('  管理后台前端冒烟测试（admin/）');
  console.log('='.repeat(60));

  // 顺序和 index.html 一致：先工作台，再主体
  for (const f of ['admin/verify.js', 'admin/app.js']) {
    try {
      vm.runInContext(fs.readFileSync(path.join(ROOT, f), 'utf8'), sandbox, { filename: f });
    } catch (e) {
      console.log('[FAIL] ' + f + ' 求值抛异常: ' + e.message);
      process.exit(1);
    }
  }
  console.log('\n[A] 脚本能加载');
  ck('verify.js 与 app.js 都不抛异常', true);
  ck('工作台函数已就位', typeof vm.runInContext('typeof openVerify', sandbox) === 'string'
    && vm.runInContext('typeof openVerify', sandbox) === 'function');
  ck('主体函数已就位', vm.runInContext('typeof loadAdmin', sandbox) === 'function');

  await new Promise((r) => setTimeout(r, 300));

  /* ---------------- 未登录 ---------------- */
  console.log('\n[B] 未登录（默认就该停在登录框）');
  ck('登录框可见', get('login').hidden === false);
  ck('后台主体隐藏', get('app').hidden === true);
  ck('ADMIN 状态为假', vm.runInContext('ADMIN', sandbox) === false);

  console.log('\n[C] 登录框本身');
  {
    // index.html 的结构由这里兜底检查：真实页面里这些 id 必须存在
    const html = fs.readFileSync(path.join(ROOT, 'admin', 'index.html'), 'utf8');
    ck('有密码输入框', html.includes('id="login-pw"') && html.includes('type="password"'));
    ck('有登录按钮', html.includes('id="login-go"'));
    ck('有错误提示位', html.includes('id="login-err"'));
    ck('说明了怎么重设密码', html.includes('scripts/auth.py --reset'));
    ck('说明了有效期', html.includes('12 小时'));
    ck('说明了失败锁定', html.includes('输错 5 次'));
    ck('声明不被搜索引擎收录', html.includes('noindex'));
    ck('可以回公开站', html.includes('回到公开站'));
  }

  console.log('\n[D] 登录流程');
  {
    // 错密码：应留在登录框并给出提示
    get('login-pw').value = 'wrong';
    await vm.runInContext('doLogin()', sandbox);
    ck('错密码仍停在登录框', get('app').hidden === true);
    ck('错密码给出提示', (get('login-err').textContent || '').length > 0,
      get('login-err').textContent);

    // 对密码：应进入后台并拉数据
    get('login-pw').value = 'correct horse';
    await vm.runInContext('doLogin()', sandbox);
    ck('对密码进入后台', get('app').hidden === false && get('login').hidden === true);
    ck('ADMIN 状态为真', vm.runInContext('ADMIN', sandbox) === true);
    ck('数据已加载', vm.runInContext('!!DATA', sandbox) === true);
  }

  console.log('\n[E] 三块视图');
  {
    ck('侧栏计数已填', get('n-cands').textContent !== '' && get('n-cases').textContent !== '',
      '候选 ' + get('n-cands').textContent + ' / 已发布 ' + get('n-cases').textContent);
    ck('顶栏统计有内容', (get('adm-stat').textContent || '').includes('候选'),
      get('adm-stat').textContent);

    const candHTML = get('grid-cands')._html || '';
    ck('候选池渲染出「核实 →」', candHTML.includes('data-verify="'));
    ck('候选池显示草稿进度', candHTML.includes('cand-draft'));

    const caseHTML = get('grid-cases')._html || '';
    ck('已发布显示档位标签', caseHTML.includes('adm-tier'));
    ck('已发布区分精品 / 备选',
      caseHTML.includes('t-premium') || caseHTML.includes('t-backup'));

    /* 人工核读记录。三种输入要画出三种结果：
       true → 已核读 + 日期；false → 未经人工核读；缺字段 → 什么都不画。
       第三种最关键：老案例压根没这条记录，画成「未核读」等于替它们认了
       一个它们没做过的判断 —— 那是在造数据。 */
    vm.runInContext('DATA.cases = [' +
      JSON.stringify({ id: 'hr1', name: 'A', tier: 'premium', quality_score: 70,
                       human_read: true, human_read_at: '2026-09-17 14:57' }) + ',' +
      JSON.stringify({ id: 'hr2', name: 'B', tier: 'backup', quality_score: 0,
                       human_read: false, human_read_at: '' }) + ',' +
      JSON.stringify({ id: 'hr3', name: 'C', tier: 'backup', quality_score: 0 }) +
      ']; renderCases();', sandbox);
    const hrHTML = get('grid-cases')._html || '';
    ck('已核读的画「已核读」+ 日期',
      hrHTML.includes('已核读') && hrHTML.includes('2026-09-17'));
    ck('未核读的画「未经人工核读」', hrHTML.includes('未经人工核读'));
    ck('没这条记录的老案例不画核读标记（不替它认判断）',
      (hrHTML.match(/adm-hr/g) || []).length === 2,
      '画了 ' + (hrHTML.match(/adm-hr/g) || []).length + ' 个');
    vm.runInContext('DATA.cases = ' + JSON.stringify(PAYLOAD.cases || []) +
                    '; renderCases();', sandbox);

    const inboxHTML = get('grid-inbox')._html || '';
    ck('采集队列渲染「转入候选池」',
      PAYLOAD.inbox.length === 0 || inboxHTML.includes('data-tocand="'));

    /* 卡片来源行：官网 / 来源两条链接。
       旧行为是「字段空就整段不渲染」，看着像产品没有官网，所以补这一组。 */
    const links = vm.runInContext(
      'cardLinks(' + JSON.stringify({
        website: 'https://stan.store/',
        source_url: 'https://trustmrr.com/startup/stan',
      }) + ')', sandbox);
    ck('卡片渲染官网链接', links.includes('官网') && links.includes('href="https://stan.store/"'));
    ck('卡片渲染来源链接', links.includes('来源') && links.includes('trustmrr.com/startup/stan'));
    ck('外链带 noopener', (links.match(/rel="noopener"/g) || []).length === 2);

    const emptyLinks = vm.runInContext('cardLinks({})', sandbox);
    ck('两个字段都空时给灰字说明', emptyLinks.includes('来源未记录') && !emptyLinks.includes('<a '));

    ck('候选池卡片底部有来源行',
      candHTML.includes('cand-link') || candHTML.includes('来源未记录'));

    // 视图切换
    vm.runInContext("switchView('inbox')", sandbox);
    ck("切到采集队列", get('view-inbox').hidden === false && get('view-candidates').hidden === true);
    vm.runInContext("switchView('published')", sandbox);
    ck("切到已发布", get('view-published').hidden === false && get('view-inbox').hidden === true);
    vm.runInContext("switchView('candidates')", sandbox);
    ck("切回候选池", get('view-candidates').hidden === false);
  }

  console.log('\n[F] 核实工作台（门槛 / 必填 / 加分）');
  {
    const cands = vm.runInContext('DATA.candidates', sandbox) || [];
    if (!cands.length) {
      ck('候选池非空（工作台需要素材）', false);
    } else {
      let sch = null;
      let schErr = '';
      try { sch = readVerifySchema(); } catch (e) { schErr = e.message; }
      /* 失败时把真实原因带出来。以前这里吞掉异常只说「读不到」，
         于是在 Windows 上看到的是一个没有线索的红叉 —— 真正的原因是
         Node 起不了子进程（EBUSY / 找不到解释器），跟规则表本身无关。 */
      ck('规则表可读取（跑 verify_rules.py 真实现）', !!sch,
        sch ? ('gates=' + sch.gates.length + ' musts=' + sch.musts.length)
            : ('原因：' + String(schErr).split('\n')[0]));

      if (sch) {
        ck('门槛 3 项', sch.gates.length === 3, String(sch.gates.length));
        ck('必填 3 项', sch.musts.length === 3, String(sch.musts.length));

        // 草稿进度
        const pid = cands[0].id;
        // 这条不能依赖真实数据：跑过 AI 核实之后 cands[0] 很可能已经有草稿了。
        // 先确保它的草稿不存在，再测「没草稿 → 0%」这条不变量。
        vm.runInContext('DATA.verifications = DATA.verifications || {}', sandbox);
        vm.runInContext('delete DATA.verifications[' + JSON.stringify(pid) + ']', sandbox);
        const p0 = vm.runInContext('draftProgress(' + JSON.stringify(pid) + ')', sandbox);
        ck('没草稿时进度 0', p0 === 0, String(p0));
        const full = {
          gates: sch.gates.map((g) => g.key), musts: sch.musts.map((m) => m.key),
          caliber: 'arr', verification: 'official', source_kinds: ['official'],
        };
        vm.runInContext('DATA.verifications = DATA.verifications || {}', sandbox);
        vm.runInContext('DATA.verifications[' + JSON.stringify(pid) + '] = ' +
          JSON.stringify(full), sandbox);
        const p1 = vm.runInContext('draftProgress(' + JSON.stringify(pid) + ')', sandbox);
        ck('填齐后进度 100%', p1 >= 100, Math.round(p1) + '%');
        vm.runInContext('delete DATA.verifications[' + JSON.stringify(pid) + ']', sandbox);

        // 工作台 HTML：六段齐全，发布键默认禁用
        const vhtml = vm.runInContext(
          'verifyHTML(DATA.candidates[0], ' + JSON.stringify(sch) + ', emptyDraft(), null)',
          sandbox);
        ck('渲染出门槛段', vhtml.includes('门槛'));
        ck('渲染出必填段', vhtml.includes('必填'));
        ck('渲染出加分项段', vhtml.includes('加分项'));
        ck('渲染出来源段', vhtml.includes('来源'));
        ck('渲染出修正段', vhtml.includes('修正'));
        ck('渲染出发布按钮', vhtml.includes('id="v-publish"'));
        ck('发布按钮默认禁用（条件未齐）', vhtml.includes('disabled'));
        ck('门槛给出「为什么」', vhtml.includes(sch.gates[0].why.slice(0, 12)));

        // 判定条：不过要说清差什么，过要说清进哪一档
        sandbox.window.__R = {
          ok: false, publishable: false,
          gates_failed: [{ key: 'g1', label: '产品仍在运营', why: '死了的数字没意义' }],
          denied_gates: [{ key: 'g1', label: '产品仍在运营', why: '死了的数字没意义' }],
          unverified_gates: [], warnings: [],
          missing: [{ key: 'm1', label: '未选收入口径', why: '差十倍' }],
          missing_count: 1, bonus_score: 20, bonus_max: 100, bonus_hits: [],
          threshold: 60, tier: null, tier_label: '', verdict: '还差 1 项必填',
        };
        const bad = vm.runInContext('verdictHTML(window.__R)', sandbox);
        ck('判定条列出被否门槛', bad.includes('产品仍在运营'));
        ck('判定条列出缺的必填', bad.includes('未选收入口径'));
        ck('不过时用 no 样式', bad.includes('v-verdict-in no'));

        // 已放行的反证要单独列出来 —— 放行不等于没争议，界面必须看得见
        sandbox.window.__R3 = {
          ok: true, publishable: true, gates_failed: [], missing: [], missing_count: 0,
          denied_gates: [{ key: 'g1', label: '产品仍在运营', why: '死了的数字没意义' }],
          unverified_gates: [], warnings: [],
          bonus_score: 40, bonus_max: 100, bonus_hits: [],
          threshold: 60, tier: 'backup', tier_label: '备选池',
          verdict: '可发布 · 备选（质量分 40，差 20 到精品，1 项待人工复核，含 1 项 AI 反证）',
        };
        const flagged = vm.runInContext('verdictHTML(window.__R3)', sandbox);
        ck('已放行的反证仍点名出来', flagged.includes('已放行的 AI 反证'));
        ck('判定写清含几项 AI 反证', flagged.includes('含 1 项 AI 反证'));

        // 三道门槛全成立的自动加成要单列一行 —— 它不是人勾出来的
        sandbox.window.__R4 = {
          ok: true, publishable: true, gates_failed: [], denied_gates: [],
          missing: [], missing_count: 0, unverified_gates: [], warnings: [],
          bonus_score: 60, bonus_max: 110, bonus_hits: [],
          gate_bonus: 10, gate_bonus_label: '三道门槛全部成立',
          threshold: 60, tier: 'premium', tier_label: '精品池',
          verdict: '可发布 · 精品（质量分 60）',
        };
        const boosted = vm.runInContext('verdictHTML(window.__R4)', sandbox);
        ck('自动加成单列一行', boosted.includes('自动加成'));
        ck('加成写清加了多少', boosted.includes('三道门槛全部成立 +10'));
        ck('满分含加成上限', boosted.includes('110'));

        sandbox.window.__R2 = {
          ok: true, publishable: true, gates_failed: [], missing: [], missing_count: 0,
          bonus_score: 100, bonus_max: 100, bonus_hits: [],
          threshold: 60, tier: 'premium', tier_label: '精品池',
          verdict: '可发布 · 精品（质量分 100）',
        };
        const ok = vm.runInContext('verdictHTML(window.__R2)', sandbox);
        ck('通过时用 ok 样式', ok.includes('v-verdict-in ok'));
        ck('通过时显示质量分与阈值', ok.includes('质量分 100') && ok.includes('阈值 60'));

        const src0 = vm.runInContext('srcRowHTML({})', sandbox);
        ck('来源行带三个输入与删除键',
          src0.includes('v-src-label') && src0.includes('v-src-url') &&
          src0.includes('v-src-kind') && src0.includes('v-del'));
        const cor0 = vm.runInContext('corRowHTML({})', sandbox);
        ck('修正行带 claim / truth / source',
          cor0.includes('v-cor-claim') && cor0.includes('v-cor-truth') &&
          cor0.includes('v-cor-source'));

        // 打开抽屉：这是后台站真正的入口动作
        await vm.runInContext('openVerify(' + JSON.stringify(cands[0].id) + ')', sandbox);
        ck('点核实打开抽屉', get('drawer').hidden === false);
        ck('抽屉里是工作台', (get('drawer-body')._html || '').includes('id="v-publish"'));
        vm.runInContext('closeDrawer()', sandbox);
        ck('能关掉抽屉', get('drawer').hidden === true);
      }
    }
  }

  console.log('\n[G] 登出');
  {
    await vm.runInContext('doLogout()', sandbox);
    ck('退回登录框', get('login').hidden === false && get('app').hidden === true);
    ck('清空了内存里的数据', vm.runInContext('DATA', sandbox) === null);
    ck('ADMIN 复位', vm.runInContext('ADMIN', sandbox) === false);
  }

  console.log('\n[H] 后台站与公开站互不牵连');
  {
    const pubApp = fs.readFileSync(path.join(ROOT, 'static', 'app.js'), 'utf8');
    const admApp = fs.readFileSync(path.join(ROOT, 'admin', 'app.js'), 'utf8');
    const admVfy = fs.readFileSync(path.join(ROOT, 'admin', 'verify.js'), 'utf8');
    ck('公开站没有 loadAdmin', !pubApp.includes('loadAdmin'));
    ck('公开站没有 switchView(‘published’) 这套后台视图',
      !pubApp.includes("'published'"));
    ck('后台站没有公开站的阅读视图', !admApp.includes("renderChina") &&
      !admApp.includes('renderDual'));
    ck('后台工作台不在公开站里', !pubApp.includes('verifyHTML'));
    ck('后台工作台只在 admin/verify.js 里', admVfy.includes('verifyHTML'));
  }

  console.log('\n[I] hidden 属性必须真的能藏住元素');
  {
    /* 登录框 / 后台主体 / 三块视图 / 抽屉全靠 el.hidden 切换。
       浏览器给 [hidden] 的 display:none 来自 UA 样式表，优先级极低，
       样式表里任何一条 #app{display:flex} 都能把它顶掉 —— 顶掉之后
       showApp() 改了属性、界面纹丝不动，症状就是「点登录没反应」。
       所以这里查两件事：兜底规则在不在、还有没有元素正被顶着。 */
    const css = fs.readFileSync(path.join(ROOT, 'admin', 'style.css'), 'utf8');
    const html = fs.readFileSync(path.join(ROOT, 'admin', 'index.html'), 'utf8');

    const rules = [];
    css.replace(/\/\*[\s\S]*?\*\//g, '').split('}').forEach((chunk) => {
      const i = chunk.indexOf('{');
      if (i < 0) return;
      rules.push({ sel: chunk.slice(0, i).trim(), body: chunk.slice(i + 1) });
    });

    // 只有 !important 才压得过 #app 这种 ID 选择器
    const guard = rules.find((r) => /\[hidden\]/.test(r.sel)
      && /display\s*:\s*none/.test(r.body) && /!important/.test(r.body));
    ck('样式表里有 [hidden] 兜底（display:none !important）', !!guard);

    // HTML 里带 hidden 的元素，它们的 id / class 有没有被 display 规则顶掉
    const keys = [];
    html.replace(/<[a-zA-Z][^>]*>/g, (tag) => {
      if (!/\shidden(?=[\s>=]|$)/.test(tag)) return tag;
      const id = (tag.match(/id="([^"]+)"/) || [])[1];
      const cls = (tag.match(/class="([^"]+)"/) || [])[1];
      if (id) keys.push('#' + id);
      if (cls) cls.split(/\s+/).forEach((c) => { if (c) keys.push('.' + c); });
      return tag;
    });
    ck('页面里确实有用 hidden 切显示的元素', keys.length > 0, keys.length + ' 个选择器');

    const hit = [];
    keys.forEach((k) => {
      rules.forEach((r) => {
        if (r.sel.split(',').some((s) => s.trim() === k) && /display\s*:/.test(r.body)) hit.push(k);
      });
    });
    const uniq = [...new Set(hit)];
    ck('带 hidden 的元素没有被 display 规则顶掉',
      uniq.length === 0 || !!guard,
      uniq.length ? '被顶掉的是 ' + uniq.join(' ') + '，靠兜底规则压住' : '无冲突');
  }

  console.log('\n[I] AI 核实面板（结构 + 接线）');
  {
    const html = fs.readFileSync(path.join(ROOT, 'admin', 'index.html'), 'utf8');
    const appJs = fs.readFileSync(path.join(ROOT, 'admin', 'app.js'), 'utf8');
    const css = fs.readFileSync(path.join(ROOT, 'admin', 'style.css'), 'utf8');

    ck('侧栏有「AI 核实」导航', html.includes('data-view="ai"'));
    ck('有 AI 视图区块', html.includes('id="view-ai"') && html.includes('hidden'));
    ['ai-key', 'ai-base', 'ai-model', 'ai-save', 'ai-limit',
     'ai-minscore', 'ai-publish', 'ai-small', 'ai-plan', 'ai-go', 'ai-log']
      .forEach((id) => ck('元素 #' + id + ' 存在', html.includes('id="' + id + '"')));

    ck('app.js 接了 status 轮询', appJs.includes('/api/ai/status'));
    ck('app.js 接了 plan', appJs.includes('/api/ai/plan'));
    ck('app.js 接了 run', appJs.includes('/api/ai/run'));
    ck('app.js 接了 settings 保存', appJs.includes('/api/ai/settings'));
    ck('Key 输入框不回显明文（type=password）',
      /id="ai-key"[^>]*type="password"/.test(html));
    ck('界面说明了 human_read 留给人工',
      html.includes('我亲自看过原文') && html.includes('留给人工'));
    // 2026-09-17：人工核读从「拦发布」降级成标记。断言打在代码行为上而不是
    // 说明文案上 —— 文案会改，但「界面按 blocking 分档呈现」这件事必须成立。
    const verifyJs = fs.readFileSync(path.join(ROOT, 'admin', 'verify.js'), 'utf8');
    ck('必填项按 blocking 区分呈现（非阻塞的标「不拦发布」）',
      verifyJs.includes('m.blocking === false') && verifyJs.includes('不拦发布'));
    ck('完成度只数拦发布的必填项',
      verifyJs.includes('m.blocking !== false'));
    ck('样式表里有「不拦发布」标记样式', css.includes('.v-tag'));
    ck('样式表里有 AI 面板样式',
      css.includes('.ai-card') && css.includes('.ai-log') && css.includes('.ai-plan-item'));
    // 2026-09-20：核实顺序改由初筛分决定（scripts/triage.py）。后台要看得到
    // 「这条为什么排在前面」，否则顺序一变就成了黑箱。
    ck('plan 条目显示初筛档位', appJs.includes('ai-plan-g') && appJs.includes('grade_label'));
    ck('样式表里有初筛档位徽章样式', css.includes('.ai-plan-g'));
    ck('switchView 覆盖 ai 视图', appJs.includes("'candidates', 'inbox', 'published', 'ai'"));

    // 回归：/api/ai/status 返回 {job:{log,summary,...}}，showAILog 曾只读
    // j.log（外层不存在）导致日志框永远显示「等待输出」。函数级断言防复发。
    vm.runInContext(
      "showAILog({job:{log:['14:00  测试日志行'], summary:{done:1,published:0,held:0,failed:0}}})",
      sandbox);
    const logEl = get('ai-log');
    ck('showAILog 渲染 job.log（不是永远「等待输出」）',
      logEl.textContent.includes('14:00  测试日志行')
      && !logEl.textContent.includes('等待输出'));
    ck('showAILog 渲染 summary 汇总行',
      logEl.textContent.includes('处理 1 条｜已发布 0｜分不够只存草稿 0｜未成 0'));

    /* ---- 结果表：这一轮审了哪些 ----
       起因：AI 跑完只留下一句「处理 1 条｜已发布 0」，页面上看不出审的是
       哪条、结论如何、该点哪儿，等于审了个寂寞。这几条把结果表钉住。 */
    ['ai-result', 'ai-result-card', 'ai-log-toggle'].forEach((id) =>
      ck('元素 #' + id + ' 存在', html.includes('id="' + id + '"')));
    ck('原始日志默认折起来（日常该看的是结果表）',
      /id="ai-log"[^>]*hidden/.test(html));

    vm.runInContext(
      "renderAIResult({state:'done'," +
      " summary:{done:2,published:1,held:1,failed:0}," +
      " items:[" +
      "  {id:'stan',name:'Stan',ok:true,published:true,case_id:'stan',verdict:'可发布',tier_label:'精品',score:70,caliber:'mrr',source_count:3}," +
      "  {id:'getdavid',name:'David',ok:false,why:'没抓到任何原文'}" +
      "]})", sandbox);
    const resEl = get('ai-result');
    const resHtml = resEl._html || '';
    ck('结果表逐条渲染（两条结果就有两行）',
      (resHtml.match(/class="ai-res"/g) || []).length === 2);
    ck('结果表点名是哪条案例（不是只有总数）',
      resHtml.includes('Stan') && resHtml.includes('David'));
    ck('结果表给出结论文案（已发布 / 跳过）',
      resHtml.includes('已发布') && resHtml.includes('跳过'));
    ck('结果表给出未审成的原因', resHtml.includes('没抓到任何原文'));
    ck('结果表带质量分与来源数', resHtml.includes('质量分 70') && resHtml.includes('来源 3'));
    ck('每行都有「去核实」按钮（能接着手动操作）',
      (resHtml.match(/data-goto=/g) || []).length === 2);
    ck('草稿待补的行列出「还差什么」',
      (function () {
        vm.runInContext(
          "renderAIResult({state:'done',summary:{done:1,held:0,failed:0},items:[" +
          "{id:'x',name:'X',ok:true,remain:['口径选定'],unverified:['还在运营'],warnings:['仅有二手来源']}]})",
          sandbox);
        const h = get('ai-result')._html || '';
        return h.includes('还差 口径选定') && h.includes('未确认 还在运营')
            && h.includes('提醒 仅有二手来源');
      })());
    ck('没有结果时结果表整块收起（不占位）',
      (function () {
        vm.runInContext("renderAIResult({state:'idle',items:[]})", sandbox);
        return get('ai-result-card').hidden === true;
      })());

    // 折叠开关得管用：showAILog 不许把用户折起来的日志再弹开
    // 约定：aria-expanded="false" = 用户已把日志折起来了
    vm.runInContext(
      "$('ai-log-toggle').setAttribute('aria-expanded','false');" +
      "$('ai-log').hidden = true;" +          // 用户已经折起来了
      "showAILog({job:{log:['x'], summary:{done:1}}})", sandbox);
    ck('用户折起日志后，轮询不会把它强行展开',
      get('ai-log').hidden === true);
    ck('折叠后正文仍在更新（只是不显示）',
      get('ai-log').textContent.includes('x'));

    // 开关本身：点一下切状态（判据是 aria-expanded）
    vm.runInContext("$('ai-log').hidden = false;" +
                    "$('ai-log-toggle').setAttribute('aria-expanded','true')", sandbox);
    vm.runInContext("$('ai-log-toggle').onclick()", sandbox);
    ck('点折叠开关能把日志折起来',
      get('ai-log').hidden === true &&
      get('ai-log-toggle').getAttribute('aria-expanded') === 'false');
    vm.runInContext("$('ai-log-toggle').onclick()", sandbox);
    ck('再点一下能展开回来',
      get('ai-log').hidden === false &&
      get('ai-log-toggle').getAttribute('aria-expanded') === 'true');
  }

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
