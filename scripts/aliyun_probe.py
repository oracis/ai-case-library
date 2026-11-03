#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云滑块状态探针（只读，不写任何东西）。

回答一个问题：**用户到底拖过滑块没有？**
- 看 9228 上现有页面的 URL（还在 /article/new?edit=1703041 说明没发表成功）
- 看页面上 `.aliyunCaptcha-top` 是否还在（还在 = 没通过）
- 看有没有已发表页 /article/<id>
- 看草稿箱/发表列表里有没有同名标题

用法：
    python -X utf8 scripts/aliyun_probe.py
    python -X utf8 scripts/aliyun_probe.py --title "马力欧"
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9228


def _http(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open("http://127.0.0.1:%d%s" % (PORT, path), timeout=10).read())


def list_pages():
    out = []
    for t in _http("/json"):
        if t.get("type") != "page" or not t.get("webSocketDebuggerUrl"):
            continue
        out.append({"id": t.get("id"), "title": t.get("title"),
                    "url": t.get("url")})
    return out


def eval_in(target_id, js, timeout=15):
    """在指定 target 上 eval。返回 (ok, value_or_err)。"""
    from juejin_publish import _WS
    url = None
    for t in _http("/json"):
        if t.get("id") == target_id:
            url = t.get("webSocketDebuggerUrl")
            break
    if not url:
        return False, "no ws url"
    ws = _WS(url)
    try:
        ws.send_text(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": js, "returnByValue": True,
                       "awaitPromise": True}}))
        deadline = __import__("time").time() + timeout
        while __import__("time").time() < deadline:
            r = json.loads(ws.recv_text())
            if r.get("id") == 1:
                res = r.get("result", {}).get("result", {})
                if res.get("subtype") == "error":
                    return False, str(res.get("description"))[:120]
                return True, res.get("value")
        return False, "timeout"
    finally:
        try:
            ws.sock.close()
        except Exception:                                       # noqa: BLE001
            pass


CAPTCHA_JS = """
(function(){
  var el = document.querySelector('.aliyunCaptcha-top');
  var iframes = [];
  var fs = document.querySelectorAll('iframe');
  for (var i=0;i<fs.length;i++){
    var s = fs[i].src || '';
    if (s.indexOf('captcha')>=0) iframes.push(s.slice(0,80));
  }
  return {
    url: location.href,
    hasCaptcha: !!el,
    captchaVisible: el ? (el.offsetParent!==null || getComputedStyle(el).display!=='none') : false,
    captchaText: el ? (el.textContent||'').trim().slice(0,120) : '',
    captchaIframes: iframes,
    title: document.title,
    bodyHead: (document.body ? (document.body.innerText||'').slice(0,300) : '')
  };
})()
"""


def main():
    title_kw = ""
    if "--title" in sys.argv:
        title_kw = sys.argv[sys.argv.index("--title") + 1]

    pages = list_pages()
    print("=== 9228 现有页面 (%d) ===" % len(pages))
    for p in pages:
        print("  [%s] %s" % (p["id"][:8], (p["title"] or "")[:40]))
        print("        %s" % (p["url"] or "")[:130])

    hit = None
    for p in pages:
        if "developer.aliyun.com" in (p["url"] or ""):
            hit = p
            break
    if not hit:
        print("\n✗ 9228 上没有阿里云页面（窗口被关了？）")
        return 2

    ok, val = eval_in(hit["id"], CAPTCHA_JS)
    print("\n=== 阿里云页状态 ===")
    if not ok:
        print("  eval 失败: %s" % val)
        return 3
    print("  URL        : %s" % val.get("url"))
    print("  标题       : %s" % val.get("title"))
    print("  滑块容器   : %s" % ("存在" if val.get("hasCaptcha") else "不存在"))
    print("  滑块可见   : %s" % val.get("captchaVisible"))
    if val.get("captchaText"):
        print("  滑块文案   : %s" % val.get("captchaText"))
    if val.get("captchaIframes"):
        print("  验证码iframe: %s" % val["captchaIframes"])
    print("  正文首段   : %s" % (val.get("bodyHead") or "").replace("\n", " / ")[:220])

    art = None
    import re
    m = re.search(r"/article/(\d+)", val.get("url") or "")
    if m:
        art = m.group(1)
    print("\n判读：")
    if art:
        print("  ✅ 已发表 https://developer.aliyun.com/article/%s" % art)
    elif val.get("hasCaptcha"):
        print("  ⏳ 滑块还在 ⇒ 尚未通过（要么没拖，要么被重跑刷新了）")
    else:
        print("  ⚠ 没有滑块也没有 /article/<id> ⇒ 需人工看一眼页面")

    if title_kw:
        ok2, val2 = eval_in(hit["id"], """
        (function(){
          var t = %s;
          var txt = document.body ? (document.body.innerText||'') : '';
          return {inPage: txt.indexOf(t)>=0, snippet: txt.slice(0,200)};
        })()""" % json.dumps(title_kw))
        if ok2:
            print("  页面含标题「%s」: %s" % (title_kw, val2.get("inPage")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
