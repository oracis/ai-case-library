/* 拆解海外 · Service Worker（2026-10-14 上线）
 * ---------------------------------------------------------------
 * 缓存策略按「内容更新频率」分三类，不搞一刀切：
 *
 *   1) 数据与页面（data.js / data.json / index.html / case/*.html）
 *      → network-first。这个库每天新增案例，读者离线时看旧数据可以，
 *        在线时绝不允许拿到旧数据 —— 否则「已核实」徽章会和线上对不上。
 *
 *   2) 外壳资源（style.css / app.js / icons / manifest）
 *      → stale-while-revalidate。这些文件变化少，先给缓存保证秒开，
 *        后台再拉新版替换。
 *
 *   3) 其余同源 GET（404.html、sitemap 等）→ 直接走网络，不进缓存。
 *
 * ⚠ install 阶段只预缓存「壳」，刻意不缓存 data.js：装上旧数据后，
 *   读者会在离线状态看到一份和线上不一致的案例集，比没有离线更糟。
 *
 * ⚠ 版本号 CACHE 只应在「需要作废所有旧缓存」时手工 +1
 *   （比如改了缓存策略）。日常发内容不用动它。
 */
var CACHE = 'case-lib-v1';
var SHELL = [
  './index.html',
  './manifest.webmanifest',
  './style.css',
  './app.js',
  './icons/icon-192.png',
  './icons/icon-512.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      // addAll 有一个失败就整体回滚，逐个 put 更耐单点故障
      return Promise.all(SHELL.map(function (url) {
        return c.add(new Request(url, { cache: 'reload' }))
          .catch(function () { /* 单个资源失败不阻塞安装 */ });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) return caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isDataLike(url) {
  return /[./]data\.(js|json)$/.test(url.pathname) ||
         url.pathname === '/' ||
         /(^|\/)index\.html$/.test(url.pathname) ||
         /(^|\/)case\/.+\.html$/.test(url.pathname);
}

function isShellAsset(url) {
  return /\.(css|js|png|svg|webmanifest)$/.test(url.pathname) &&
         !/[./]data\.(js|json)$/.test(url.pathname);
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;

  var url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;

  // 页面导航（含刷新）：network-first，失败回退到壳
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put('./index.html', copy); });
        return res;
      }).catch(function () {
        return caches.match('./index.html').then(function (r) {
          return r || new Response('离线且没有可用缓存', {
            status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' }
          });
        });
      })
    );
    return;
  }

  if (isDataLike(url)) {
    e.respondWith(
      fetch(req).then(function (res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (r) { return r || Response.error(); });
      })
    );
    return;
  }

  if (isShellAsset(url)) {
    e.respondWith(
      caches.match(req).then(function (hit) {
        var net = fetch(req).then(function (res) {
          if (res && res.ok) {
            var copy = res.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
          }
          return res;
        }).catch(function () { return hit || Response.error(); });
        return hit || net;  // 有缓存先给缓存，后台静默更新
      })
    );
  }
});

/* 页面里「有新内容，刷新」按钮通过 postMessage 触发，让新 SW 立即接管 */
self.addEventListener('message', function (e) {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
