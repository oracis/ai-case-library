#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书「页面内 fetch」可行性探针 —— 验证能否绕开 UI 点击。

## 要回答的问题

小红书一直按「草稿只在本机 IndexedDB、没有服务端接口」处理（2026-09 结论），
所以 `xhs_publish.py` 全程真实鼠标点击，最脆。但 2026-10-18 从已加载页面的
resource timing 里捞到这些**服务端接口**：

    edith.xiaohongshu.com/api/sns/v1/creator/red_app/web/consumer/list
    creator.xiaohongshu.com/api/galaxy/user/info
    creator.xiaohongshu.com/api/media/v1/upload/creator/permit

说明「没有服务端接口」这个前提至少是过时的。真正的问题变成：**签名能不能在
页面里就地做掉**。小红书网页版的签名对 `x-s` / `x-t` 两个头，社区已知它把
签名函数挂在 `window._webmsxyw` 上（本次实测 `typeof === "function"` ✅）。

⇒ 只要 `_webmsxyw` 可用，就能在**已登录页面上下文里**直接 fetch，
   cookie + 签名一起齐，既不用逆 JS 也不用点 DOM —— 和头条的
   「页面内 fetch + tt-anti-token」是同一种形态。

## 用法

    python -X utf8 scripts/probe_xhs_webapi.py                 # 只探测签名能力
    python -X utf8 scripts/probe_xhs_webapi.py --call          # 额外打一次只读接口
    python -X utf8 scripts/probe_xhs_webapi.py --call --path /api/sns/v1/xxx

⚠ 严格只读：只发 GET / 只读 POST，不创建任何笔记、不改任何草稿。
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_XHS_PORT", 9223))
EDITOR_HINT = "/publish/publish"

# 已知的只读接口（2026-10-18 从页面 resource timing 里捞出来的真实 URL）
READONLY = [
    ("creator_user_info", "https://creator.xiaohongshu.com/api/galaxy/user/info", "GET"),
    ("red_app_list",
     "https://edith.xiaohongshu.com/api/sns/v1/creator/red_app/web/consumer/list", "POST"),
]


def pick_page(port=PORT):
    """挑一个 creator.xiaohongshu.com 的真实页面（排除 service-worker 壳）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    targets = json.loads(op.open("http://127.0.0.1:%d/json/list" % port, timeout=5).read())
    pages = [t for t in targets
             if t.get("type") == "page" and "creator.xiaohongshu.com" in (t.get("url") or "")]
    # 优先编辑器页，其次任意页面
    for p in pages:
        if EDITOR_HINT in (p.get("url") or ""):
            return p
    return pages[0] if pages else None


def sign_js(path, data_json):
    """在页面里调 _webmsxyw 拿签名头。返回 JS 表达式。"""
    return """(function(){
      if (typeof window._webmsxyw !== 'function') return '{"err":"no_webmsxyw"}';
      var r = window._webmsxyw(%s, %s);
      return JSON.stringify(r || {});
    })()""" % (json.dumps(path), json.dumps(data_json or "{}"))


def fetch_js(url, method, body_json, signed, trace=False):
    """在页面上下文里 fetch。signed=True 时把 x-s / x-t / x-s-common 一起带上。

    ⚠ trace 默认关：`x-b3-traceid` 是小红书自家链路追踪头，但 edith 的
    `Access-Control-Allow-Headers` 并不列它 ⇒ 跨域请求会先在预检被打回，
    浏览器只报一句 `TypeError: Failed to fetch`（2026-10-18 实测）。
    """
    return """(function(){
      var url = %s, method = %s, body = %s;
      var headers = {'Content-Type':'application/json;charset=UTF-8'};
      if (%s) {
        try {
          var o = new URL(url);
          // ⚠ GET 的签名对象是 **pathname + search**（query 要一起签），
          //   POST 才是 pathname + body。签错对象服务端返 406
          //   `{"code":-1,"success":false}`（2026-10-18 实测）。
          var path = (method === 'GET') ? (o.pathname + o.search) : o.pathname;
          var sig = window._webmsxyw(path, method === 'GET' ? '{}' : (body || '{}'));
          if (sig && sig['X-s'])  headers['x-s'] = sig['X-s'];
          if (sig && sig['X-t'])  headers['x-t'] = sig['X-t'];
          if (sig && sig['X-s-Common']) headers['x-s-common'] = sig['X-s-Common'];
        } catch(e) { headers['x-sign-err'] = String(e).slice(0,80); }
      }
      if (%s) headers['x-b3-traceid'] = (Math.random().toString(16)+'0'.repeat(16)).slice(2,18);
      return fetch(url, {method:method, headers:headers, credentials:'include',
                         body: method==='GET' ? undefined : (body || '{}')})
        .then(function(r){ return r.text().then(function(t){
            return JSON.stringify({status:r.status, len:t.length, body:t.slice(0,600)});
        });})
        .catch(function(e){ return JSON.stringify({status:0, err:String(e).slice(0,120)}); });
    })()""" % (json.dumps(url), json.dumps(method), json.dumps(body_json or "{}"),
               "true" if signed else "false", "true" if trace else "false")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", action="store_true", help="额外打一次只读接口")
    ap.add_argument("--path", help="自定义只读接口完整 URL")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--body", default="{}")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    page = pick_page(args.port)
    if not page:
        print("✗ 没找到 creator.xiaohongshu.com 页面，先在 %d 这个 Chrome 里登录" % args.port)
        return 1

    cdp = wp.CDP(args.port)
    if not cdp.connect_target(page["id"]):
        print("✗ CDP 连接失败")
        return 1

    print("页面：%s" % cdp.eval("location.href"))
    print("登录态：a1=%s" % ("有" if "a1=" in (cdp.eval("document.cookie") or "") else "无"))

    has_sign = cdp.eval("typeof window._webmsxyw") == "function"
    print("签名函数 _webmsxyw：%s" % ("✅ 存在" if has_sign else "❌ 缺失"))
    if not has_sign:
        print("⇒ 只能继续走 UI 点击路线")
        return 2

    # 单独验证签名产出形态
    raw = cdp.eval(sign_js("/api/sns/v1/creator/red_app/web/consumer/list", "{}"))
    print("签名样例：%s" % (str(raw)[:260]))

    if not (args.call or args.path):
        print("\n（加 --call 才会真打接口）")
        return 0

    targets = [("custom", args.path, args.method, args.body)] if args.path \
        else [(n, u, m, "{}") for n, u, m in READONLY]
    for name, url, method, body in targets:
        out = cdp.eval(fetch_js(url, method, body, True), refresh_context=False)
        print("\n[%s] %s %s" % (name, method, url))
        try:
            d = json.loads(out) if isinstance(out, str) else out
            print("  HTTP %s  len=%s" % (d.get("status"), d.get("len")))
            print("  body: %s" % (d.get("body") or d.get("err"))[:400])
        except Exception:
            print("  原始返回：%s" % str(out)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
