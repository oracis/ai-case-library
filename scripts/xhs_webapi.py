#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书创作平台**页面内 fetch** 层：不点 DOM，直接在已登录页面里发请求。

## 为什么有这个模块

小红书没有对外 API，但不是「没有服务端接口」。2026-10-18 实测把两件事分清了：

| 能力 | 真相 | 依据 |
|---|---|---|
| **草稿** | 真的只在本地 IndexedDB | 页面原文：「草稿存储于当前使用的浏览器本地，在清除浏览器数据时会被删除」 |
| **已发布列表 / 用户信息 / 图片上传许可** | **有服务端接口** | `/api/galaxy/v2/creator/note/user/posted` 实测 200 |

以前读已发布列表靠 DOM 滚动抓（`xhs_publish` 里那套：进 note-manager →
反复滚 `div.content` 容器 → 等懒加载 → 解析 `span.note-card__title`），
**几十秒**且随改版失效。走接口是**秒级 + 结构化**。

## 签名怎么过的（这是本模块的核心）

小红书所有 `/api/` 请求带 `x-s` / `x-t` 两个签名头，网页版把签名函数挂在
**全局 `window._webmsxyw`** 上。所以根本不用逆 JS：

```js
window._webmsxyw(<签名对象>, <data>)   // → {X-s, X-t, X-s-Common}
```

⚠⚠ **签名对象随 method 变**（2026-10-18 踩了一轮）：

| method | 签名对象 | data 参数 | 错误后果 |
|---|---|---|---|
| GET  | `pathname + search`（**query 要一起签**） | `'{}'` | 只签 pathname → **406 `{"code":-1}`** |
| POST / PUT | `pathname` | **body 解析后的对象**（不是字符串！） | 传字符串 → 稳定 406；传对象才过（2026-10-18 矩阵实测） |

406 那个坑很隐蔽：返回的是 JSON 而不是报错页，看着像「参数不对」，
实际是签名对象漏了 query。POST 那条同理 —— 同一个 406，两种错法。

⚠ 因为签名器内部会 `JSON.stringify(对象)`，所以**实际发出去的 body 必须和它
序列化的结果逐字节一致**：紧凑 JSON（`separators=(',',':')`）、不允许有空格、
非 ASCII 不转义。`fetch_json()` 传 dict 就会自动按这个口径压。

## 边界（别踩）

- **必须在已登录的 creator 页面上跑**（9223 那个 `chrome-debug-profile`），
  签名依赖页面里的 `_webmsxyw` 和 cookie 里的 `a1`。
- **9223 是 headless 起的**，`_launch_9223.py` 不预开页面 ⇒ 刚拉起来的实例
  `/json/list` 是**空列表**（曾经误判成「连不上/没登录」）。`ensure_page()`
  会自己 `PUT /json/new` 开一页再等签名函数就位。
- **跨域到 `edith.xiaohongshu.com` 会 `TypeError: Failed to fetch`**：
  CORS 预检其实是放行的（`Access-Control-Allow-Headers: content-type,x-s,x-t,x-s-common`），
  失败发生在**签名不匹配**时服务端返回的响应不带 CORS 头。⇒ 优先用
  `creator.xiaohongshu.com/api/...` 同源接口。
- 这是**读**为主的能力。**创建**笔记（`web_api/sns/v2/note`）仍被 461 拦死；
  但**删除**已打通（`capa/postgw` 另一个网关），用 `scripts/xhs_delete.py`，
  别在这里直接拼写请求。

## 用法

    python -X utf8 scripts/xhs_webapi.py posted            # 列已发布笔记（标题/时间/赞）
    python -X utf8 scripts/xhs_webapi.py posted --pages 5  # 多翻几页
    python -X utf8 scripts/xhs_webapi.py posted --json     # 原始结构
    python -X utf8 scripts/xhs_webapi.py call --url <完整URL> [--method POST --body '{}']
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_XHS_PORT", 9223))
HOST = "https://creator.xiaohongshu.com"
POSTED_TPL = HOST + "/api/galaxy/v2/creator/note/user/posted?page=%d&page_size=%d"
USER_INFO = HOST + "/api/galaxy/user/info"


def pick_page(port=PORT):
    """挑一个 creator 的真实页面（排除 service_worker 壳，那个 eval 全是 None）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    targets = json.loads(op.open("http://127.0.0.1:%d/json/list" % port, timeout=5).read())
    pages = [t for t in targets if t.get("type") == "page"
             and "creator.xiaohongshu.com" in (t.get("url") or "")]
    if not pages:
        return None
    for p in pages:
        if "/publish/publish" in (p.get("url") or ""):
            return p
    return pages[0]


def _http_json(port, path, method="GET", timeout=8):
    """裸 HTTP 打 CDP 的 REST 端点（/json/list、/json/new…）。

    ⚠ `ProxyHandler({})` 是必须的：本机开着系统代理时 urllib 会把
       127.0.0.1 也代进去，直接超时。
    """
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    raw = op.open(req, timeout=timeout).read()
    return json.loads(raw) if raw.strip() else None


def ensure_page(port=PORT, url=HOST + "/publish/publish", wait=20):
    """保证 port 上有一页 creator，返回该 target。

    为什么要这个（2026-10-18）：9223 的 Chrome 是 **headless** 起的，启动器
    不预开页面，所以新拉起的实例 `/json/list` 是空数组 —— 旧代码在这里
    直接报「连不上/先登录」，实际登录态好得很。
    """
    page = pick_page(port)
    if page:
        return page
    try:
        # Chrome 111+ 要求 PUT；url 走 query（body 形式在部分版本被忽略）
        _http_json(port, "/json/new?" + urllib.parse.quote(url, safe=":"), method="PUT")
    except Exception:
        pass
    deadline = time.time() + max(5, wait)
    while time.time() < deadline:
        page = pick_page(port)
        if page:
            return page
        time.sleep(1)
    return None


def connect(port=PORT):
    """返回 (cdp, page) 或 (None, None)。调用方负责 close。"""
    page = ensure_page(port)
    if not page:
        return None, None
    cdp = wp.CDP(port)
    if not cdp.connect_target(page["id"]):
        return None, None
    return cdp, page


_FETCH_JS = r"""(function(cfg){
  var url = cfg.url, method = cfg.method, body = cfg.body || '', signed = cfg.signed;
  var headers = {'Content-Type':'application/json;charset=UTF-8'};
  if (signed && typeof window._webmsxyw === 'function') {
    try {
      var o = new URL(url, location.origin);
      // GET 必须把 query 一起签，否则 406（见模块 docstring）
      var obj = (method === 'GET') ? (o.pathname + o.search) : o.pathname;
      // ⚠ POST/ PUT 的 data 参数要传**对象**，不能传字符串：
      //   传字符串 → 稳定 406；传对象 → 服务端才认（2026-10-18 矩阵实测）
      var data = body;
      if (method !== 'GET') {
        try { data = JSON.parse(body || '{}'); } catch (e) { data = body; }
      }
      var sig = window._webmsxyw(obj, data);
      if (sig && sig['X-s']) headers['x-s'] = sig['X-s'];
      if (sig && sig['X-t']) headers['x-t'] = sig['X-t'];
      if (sig && sig['X-s-Common']) headers['x-s-common'] = sig['X-s-Common'];
    } catch(e) { headers['x-sign-err'] = String(e).slice(0, 90); }
  }
  var init = {method:method, headers:headers, credentials:'include'};
  if (method !== 'GET') init.body = body || '{}';
  return fetch(url, init)
    .then(function(r){
      var hs = {};
      if (cfg.wantHeaders) { try { r.headers.forEach(function(v,k){ hs[k] = v; }); } catch(e) {} }
      return r.text().then(function(t){
        return JSON.stringify({status:r.status, len:t.length, text:t,
                               headers: cfg.wantHeaders ? hs : undefined});
    });})
    .catch(function(e){ return JSON.stringify({status:0, err:String(e).slice(0,140)}); });
})(%s)"""


def _compact(body):
    """dict → 紧凑 JSON 字符串（与 JS JSON.stringify 同序同格式）。

    ⚠ 必须 `separators=(',', ':')`：签名是对「对象再序列化」的结果算的，
       发了带空格的 body 就等于发了另一个串 → 406。
    """
    if body is None:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return body


def fetch_json(cdp, url, method="GET", body=None, signed=True, timeout_note=""):
    """在页面上下文里发一次请求，返回 (status, parsed_or_None, raw_text)。

    解析失败时把原文一并返回 —— 小红书出错时可能返 HTML（风控页），
    光看状态码会误判成「接口挂了」。

    `body` 建议直接传 dict（本函数会按 JS 口径压成紧凑 JSON）。
    """
    st, js, raw, _ = fetch_full(cdp, url, method, body, signed)
    return st, js, raw


def fetch_full(cdp, url, method="GET", body=None, signed=True):
    """同 fetch_json，但连**响应头**一起返回：`(status, json, raw, headers)`。

    响应头不是可有可无的：小红书风控拒绝（HTTP 461）时，body 里是
    `{"code":0,"success":true}` 这种「看起来一切正常」的假象，真正的信息
    全在 `verifytype` / `verifyuuid` 两个响应头里（见 probe_xhs_publish_api.py）。
    """
    cfg = json.dumps({"url": url, "method": method.upper(),
                      "body": _compact(body), "signed": bool(signed),
                      "wantHeaders": True},
                     ensure_ascii=False)
    out = cdp.eval(_FETCH_JS % cfg)
    if not isinstance(out, str):
        return 0, None, "eval 返回非字符串：%r" % (out,), {}
    try:
        r = json.loads(out)
    except Exception:
        return 0, None, "回包不是 JSON：%s" % str(out)[:200], {}
    if r.get("status") in (0, None):
        return 0, None, r.get("err") or "unknown", {}
    text = r.get("text") or ""
    headers = r.get("headers") or {}
    try:
        return int(r["status"]), json.loads(text), text, headers
    except Exception:
        return int(r["status"]), None, text, headers


def posted_notes(pages=2, page_size=30, cdp=None, port=PORT):
    """拉已发布笔记列表（按发布时间倒序）。

    返回 (notes, raw_first_page)。notes 里每条保留接口原字段，另附
    `title`（= display_title 优先）与 `note_id` 便于对账。
    """
    own = cdp is None
    if own:
        cdp, _ = connect(port)
        if cdp is None:
            raise RuntimeError(
                "连不上 %d 上的小红书页面：Chrome 没起（python scripts/_launch_9223.py --hold 28800）"
                % port)
        href = ""
        try:
            href = str(cdp.eval("location.href", refresh_context=True) or "")
        except Exception:
            pass
        if "/login" in href:
            raise RuntimeError("小红书登录态已失效（落在 %s），需人工扫码" % href[:80])
    notes, first_raw = [], None
    try:
        for p in range(1, max(1, pages) + 1):
            st, js, raw = fetch_json(cdp, POSTED_TPL % (p, page_size))
            if p == 1:
                first_raw = js
            if st != 200 or not isinstance(js, dict) or js.get("code") != 0:
                if p == 1:
                    raise RuntimeError("接口返回异常：HTTP %s / %s" % (st, raw[:200]))
                break
            batch = ((js.get("data") or {}).get("notes") or [])
            if not batch:
                break
            for n in batch:
                n["note_id"] = n.get("id")
                n["title"] = n.get("display_title") or n.get("title") or ""
                notes.append(n)
            if len(batch) < page_size:
                break
    finally:
        if own and cdp is not None:
            try:
                cdp.close_target(cdp.tid)
            except Exception:
                pass
    return notes, first_raw


def cmd_posted(args):
    notes, _ = posted_notes(pages=args.pages, page_size=args.size)
    if args.json:
        print(json.dumps(notes, ensure_ascii=False, indent=1)[:20000])
        return 0
    print("已发布笔记 %d 条（接口直读，非 DOM 抓取）" % len(notes))
    for n in notes:
        print("  %s  %-4s 赞%-4s 看%-5s  %s"
              % (n.get("time", "?"), n.get("note_id", "?")[:8],
                 n.get("likes", "-"), n.get("view_count", "-"),
                 (n.get("title") or "")[:40]))
    return 0


def cmd_call(args):
    cdp, page = connect(args.port)
    if cdp is None:
        print("✗ 连不上 %d 上的小红书页面" % args.port)
        return 1
    print("页面：%s" % (page.get("url") or "")[:100])
    st, js, raw = fetch_json(cdp, args.url, args.method, args.body, signed=not args.no_sign)
    print("HTTP %s" % st)
    if js is not None:
        print(json.dumps(js, ensure_ascii=False)[:1500])
    else:
        print(raw[:800])
    return 0 if st == 200 else 2


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("posted", help="列已发布笔记")
    p1.add_argument("--pages", type=int, default=1)
    p1.add_argument("--size", type=int, default=30)
    p1.add_argument("--json", action="store_true")
    p1.set_defaults(fn=cmd_posted)

    p2 = sub.add_parser("call", help="调任意接口（只读用）")
    p2.add_argument("--url", required=True)
    p2.add_argument("--method", default="GET")
    p2.add_argument("--body", default="")
    p2.add_argument("--no-sign", action="store_true")
    p2.add_argument("--port", type=int, default=PORT)
    p2.set_defaults(fn=cmd_call)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 1
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
