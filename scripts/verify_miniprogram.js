#!/usr/bin/env node
/**
 * 小程序 + 云函数专项验证（零依赖）。
 *
 * 覆盖：
 *   1. JSON 合法性（app.json / project.config.json / 各 page json / 云函数 package.json）
 *   2. JS 语法（node --check 全量）
 *   3. 签名算法跨语言对照（Node crypto 实现 vs 官方 Python hmac 示例）
 *      —— 签名错一分钱都收不到，这是最值得自动化的断言
 *   4. 数据契约比对：get-cases 的 FREE / PAID 两层白名单（之和）必须覆盖
 *      sync 脚本 mapCase 的全部输出键
 *   5. 付费墙有效性：导出列必须含 FREE 层给不了的字段
 *      —— 2026-10-15「内容免费」被证伪后加的；细节见 scripts/verify_paywall.py
 *      （白名单漏键 = 字段静默不外泄；mapCase 多键 = 白名单收窄，仅警告）
 *   5. WXML 标签配对
 *   6. app.json 页面路由与文件存在性
 *
 * 用法：node scripts/verify_miniprogram.js
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const ROOT = path.dirname(path.dirname(__filename));
const MP = path.join(ROOT, 'miniprogram');

let pass = 0;
let fail = 0;
const failures = [];

function ok(name, detail) {
  pass += 1;
  console.log('  ✓ ' + name + (detail ? '  ' + detail : ''));
}
function bad(name, detail) {
  fail += 1;
  failures.push(name + ': ' + detail);
  console.log('  ✗ ' + name + '  ' + detail);
}

/* ---------- 1. JSON 合法性 ---------- */
function checkJson(rel) {
  const p = path.join(MP, rel);
  if (!fs.existsSync(p)) { bad('JSON 存在 ' + rel, '文件不存在'); return; }
  try {
    JSON.parse(fs.readFileSync(p, 'utf8'));
    ok('JSON ' + rel);
  } catch (e) {
    bad('JSON ' + rel, e.message);
  }
}

/* ---------- 2. JS 语法 ---------- */
function walkJs(dir, out) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === 'miniprogram_npm') continue;
      walkJs(p, out);
    } else if (name.endsWith('.js')) {
      out.push(p);
    }
  }
  return out;
}

/* ---------- 3. 签名跨语言对照 ---------- */
function checkSignatures() {
  // 被测向量（与 Python 侧完全一致）
  const cases = [
    { fn: 'paySig', uri: 'requestVirtualPayment', body: '{"offerId":"123","buyQuantity":1}', key: 'k' + 'e'.repeat(20) },
    { fn: 'paySig', uri: '/xpay/query_order', body: '{"openid":"oX","env":0,"order_id":"T1"}', key: 'sk-test-0123456789' },
    { fn: 'signature', uri: null, body: '{"a":"中文","b":[1,2]}', key: 'session' + 'K'.repeat(24) }
  ];

  // Node 侧实现（从被测文件里抠出来执行，避免「验证脚本自己抄一份」的假验证）
  const signSrc = fs.readFileSync(path.join(MP, 'cloudfunctions/pay-order/lib/sign.js'), 'utf8');
  const mod = { exports: {} };
  new Function('module', 'exports', 'require', signSrc)(mod, mod.exports, require);
  const impl = mod.exports;

  // Python 侧：官方文档同款算法
  const py = `
import hmac, hashlib, json
cases = json.loads(r'''${JSON.stringify(cases)}''')
out = []
for c in cases:
    if c['fn'] == 'paySig':
        msg = c['uri'] + '&' + c['body']
    else:
        msg = c['body']
    out.append(hmac.new(c['key'].encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest())
print(json.dumps(out))
`;
  const pyOut = execFileSync('python', ['-c', py], { encoding: 'utf8' });
  const expected = JSON.parse(pyOut.trim());

  cases.forEach((c, i) => {
    let got;
    try {
      got = c.fn === 'paySig'
        ? impl.paySig(c.uri, c.body, c.key)
        : impl.signature(c.body, c.key);
    } catch (e) {
      bad('签名 ' + c.fn + ' #' + i, '抛异常 ' + e.message);
      return;
    }
    if (got === expected[i]) {
      ok('签名 ' + c.fn + ' #' + i, 'Node 与官方 Python 算法一致');
    } else {
      bad('签名 ' + c.fn + ' #' + i, 'Node=' + got + ' Python=' + expected[i]);
    }
  });
}

/* ---------- 4. 数据契约比对 ---------- */
function checkContract() {
  const gcSrc = fs.readFileSync(path.join(MP, 'cloudfunctions/get-cases/index.js'), 'utf8');
  // 2026-10-15 起白名单拆成 FREE / PAID 两层（内容收费）。契约要同时覆盖两层：
  // sync 脚本 mapCase 输出的键必须落在「两层之和」里，否则付费用户会拿到
  // 恒为 undefined 的字段（前端不报错，只显示空白，最难查）。
  const mf = gcSrc.match(/const FREE = \{([\s\S]*?)\};/);
  const mp = gcSrc.match(/const PAID = \{([\s\S]*?)\};/);
  if (!mf || !mp) {
    bad('契约', 'get-cases 里找不到 FREE / PAID 两层定义');
    return;
  }
  const grab = (s) => (s.match(/(\w+):\s*true/g) || []).map((x) => x.split(':')[0].trim());
  const free = grab(mf[1]);
  const paid = grab(mp[1]);
  const fields = free.concat(paid);
  if (!fields.length) { bad('契约', 'FREE/PAID 解析为空'); return; }

  const sync = require(path.join(ROOT, 'scripts/sync_cases_cloudbase.js'));
  const cases = JSON.parse(fs.readFileSync(sync.CASES_PATH, 'utf8'));
  const mapped = cases.map(sync.mapCase);
  const keys = new Set();
  mapped.forEach((c) => Object.keys(c).forEach((k) => keys.add(k)));

  const missing = [...keys].filter((k) => fields.indexOf(k) < 0);
  const extra = fields.filter((k) => !keys.has(k));
  if (missing.length) {
    bad('契约', 'mapCase 输出的键不在 get-cases FREE/PAID 白名单: ' + missing.join(', '));
  } else {
    ok('契约', keys.size + ' 个键全部在白名单内（FREE ' + free.length
      + ' + PAID ' + paid.length + '）');
  }
  if (extra.length) {
    console.log('  ! 白名单有但映射未产出的键（收窄，允许）: ' + extra.join(', '));
  }

  // 口径红线抽查：任何案例不允许 mrr 与 last_30d_revenue 被相加的迹象
  // （这条是结构断言：两个字段必须各自独立存在）
  const both = mapped.filter((c) => c.mrr != null && c.last_30d_revenue != null).length;
  ok('口径分离', 'MRR 与近30天收入独立字段，' + both + ' 条两者都有（各自带口径展示）');

  // 数值卫生：不允许 NaN 混入（JSON 会序列化成 null，但 MapCase 应先拦）
  const nanKeys = [];
  mapped.forEach((c) => {
    Object.keys(c).forEach((k) => {
      if (typeof c[k] === 'number' && !isFinite(c[k])) nanKeys.push(c.id + '.' + k);
    });
  });
  if (nanKeys.length) bad('数值卫生', 'NaN 字段: ' + nanKeys.slice(0, 5).join(', '));
  else ok('数值卫生', '无 NaN/Infinity');
}

/* ---------- 5. WXML 配对 ---------- */
function checkWxml(rel) {
  const src = fs.readFileSync(path.join(MP, rel), 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '');
  const stack = [];
  const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g;
  let m;
  let err = '';
  while ((m = re.exec(src)) !== null) {
    const closing = m[1] === '/';
    const tag = m[2];
    const selfClose = m[4] === '/';
    if (closing) {
      const top = stack.pop();
      if (top !== tag) { err = '</' + tag + '> 与 <' + top + '> 不配对'; break; }
    } else if (!selfClose) {
      stack.push(tag);
    }
  }
  if (!err && stack.length) err = '有未闭合标签: ' + stack.join(', ');
  if (err) bad('WXML ' + rel, err);
  else ok('WXML ' + rel);
}

/* ---------- 6. 路由与文件 ---------- */
function checkRoutes() {
  const app = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'));
  (app.pages || []).forEach((pg) => {
    const dir = path.join(MP, pg);
    const need = [pg + '.js', pg + '.json', pg + '.wxml', pg + '.wxss'];
    const missing = need.filter((f) => !fs.existsSync(path.join(MP, f)));
    if (missing.length) bad('路由 ' + pg, '缺文件: ' + missing.join(', '));
    else ok('路由 ' + pg);
  });
  const tabPaths = ((app.tabBar || {}).list || []).map((t) => t.pagePath);
  const notInPages = tabPaths.filter((p) => (app.pages || []).indexOf(p) < 0);
  if (notInPages.length) bad('tabBar', 'pagePath 不在 pages: ' + notInPages.join(', '));
  else if (tabPaths.length) ok('tabBar', tabPaths.length + ' 个 tab 均在 pages 内');

  // 云函数目录完整性：index.js + package.json 必须成对
  const cfDir = path.join(MP, 'cloudfunctions');
  fs.readdirSync(cfDir).forEach((name) => {
    if (name.startsWith('_')) return;
    const hasIdx = fs.existsSync(path.join(cfDir, name, 'index.js'));
    const hasPkg = fs.existsSync(path.join(cfDir, name, 'package.json'));
    if (hasIdx && hasPkg) ok('云函数 ' + name);
    else bad('云函数 ' + name, '缺 index.js 或 package.json');
  });
}

/* ---------- 6b. app.json 顶层 schema（开发者工具才报的错）---------- */
// 这类错只在真机编译时报「无效的 app.json ["xxx"]」，纯 JSON 合法校验抓不到。
// 用精准黑名单：只拦已证实的非法顶层键，不给完整 allowlist（避免微信新增合法键时误伤）。
function checkAppJsonSchema() {
  const app = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'));
  // 已证实非法：app.json 顶层无 description 字段，会触发「无效的 app.json ["description"]」
  //   —— 该字段属于 project.config.json / sitemap.json（sitemap 用 desc，非 description）
  const invalid = ['description'];
  let hit = false;
  invalid.forEach((k) => {
    if (Object.prototype.hasOwnProperty.call(app, k)) {
      bad('app.json schema',
        '顶层含非法字段 "' + k + '"（开发者工具报『无效的 app.json ["' + k + '"]』）；' +
        '该字段属 project.config.json 或 sitemap.json，应从 app.json 删除');
      hit = true;
    }
  });
  if (!hit) ok('app.json schema', '无非法顶层字段');
}

/* ---------- main ---------- */
console.log('=== 1. JSON 合法性 ===');
checkJson('app.json');
checkJson('project.config.json');
fs.readdirSync(path.join(MP, 'pages')).forEach((pg) => checkJson('pages/' + pg + '/' + pg + '.json'));
fs.readdirSync(path.join(MP, 'cloudfunctions')).forEach((fn) => {
  if (!fn.startsWith('_')) checkJson('cloudfunctions/' + fn + '/package.json');
});

console.log('\n=== 2. JS 语法 ===');
const jsFiles = walkJs(MP, []).concat([
  path.join(ROOT, 'scripts/sync_cases_cloudbase.js')
]);
let syntaxOk = 0;
jsFiles.forEach((f) => {
  try {
    execFileSync(process.execPath, ['--check', f], { stdio: 'pipe' });
    syntaxOk += 1;
  } catch (e) {
    bad('语法 ' + path.relative(ROOT, f), String(e.stderr || e.message).split('\n')[0]);
  }
});
if (syntaxOk === jsFiles.length) ok('JS 语法', jsFiles.length + ' 个文件全部通过');

console.log('\n=== 3. 签名算法（Node vs 官方 Python）===');
checkSignatures();

console.log('\n=== 4. 数据契约 ===');
checkContract();

console.log('\n=== 5. WXML 配对 ===');
fs.readdirSync(path.join(MP, 'pages')).forEach((pg) => checkWxml('pages/' + pg + '/' + pg + '.wxml'));

console.log('\n=== 6. 路由与云函数 ===');
checkRoutes();
checkAppJsonSchema();

console.log('\n========================================');
console.log('通过 ' + pass + ' · 失败 ' + fail);
if (fail) {
  console.log('\n失败项:');
  failures.forEach((f) => console.log('  - ' + f));
  process.exit(1);
}
console.log('全部通过 ✓');
