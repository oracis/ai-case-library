#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查阿里云草稿服务端到底有没有内容（只读）。

问题现象：本地稿件正常，但 `?edit=<aid>` 打开的编辑页标题/正文是空的。
两种可能：
  A. 草稿服务端就是空的（建稿时没存进去）⇒ 得重新建稿
  B. 草稿有内容，但编辑页没载入（前端 SPA 拿不到）⇒ 得在页面上补注入

判据：直接调阿里云自己的草稿读取接口（页面同源、带登录 cookie）。
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9228
AID = ""
TITLE_KW = "马力欧"


def _http(path):
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open("http://127.0.0.1:%d%s" % (PORT, path), timeout=10).read())


def eval_in(target_id, js, timeout=20):
    from juejin_publish import _WS
    url = None
    for t in _http("/json"):
        if t.get("id") == target_id:
            url = t.get("webSocketDebuggerUrl")
    if not url:
        return False, "no ws"
    ws = _WS(url)
    try:
        ws.send_text(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": js, "returnByValue": True,
                       "awaitPromise": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = json.loads(ws.recv_text())
            if r.get("id") == 1:
                res = r.get("result", {}).get("result", {})
                if res.get("subtype") == "error":
                    return False, str(res.get("description"))[:200]
                return True, res.get("value")
        return False, "timeout"
    finally:
        try:
            ws.sock.close()
        except Exception:                                       # noqa: BLE001
            pass


# 页面同源 fetch 草稿列表/详情，登录 cookie 自动带上
FETCH_JS = """
(function(){
  var aid = %s;
  var out = {aid: aid, tried: []};
  var urls = [
    '/developer/api/articleDraft/getDraft?aid=' + aid,
    '/developer/api/articleDraft/detail?aid=' + aid,
    '/developer/api/articleDraft/get?id=' + aid,
    '/developer/api/articleDraft/list'
  ];
  return Promise.all(urls.map(function(u){
    return fetch(u, {credentials:'include'}).then(function(r){
      return r.text().then(function(t){ return {u:u, status:r.status, body:t.slice(0,600)}; });
    }).catch(function(e){ return {u:u, err:String(e).slice(0,80)}; });
  })).then(function(rs){
    out.tried = rs;
    return JSON.stringify(out);
  });
})()
""" % json.dumps(AID)


def main():
    global AID
    args = sys.argv[1:]
    if "--aid" in args:
        AID = args[args.index("--aid") + 1]
    if not AID:
        print("用法: python scripts/aliyun_draft_check.py --aid 1703041")
        return 2

    pages = [t for t in _http("/json")
             if t.get("type") == "page" and "developer.aliyun.com" in (t.get("url") or "")]
    if not pages:
        print("✗ 9228 上没有阿里云页面")
        return 3
    tid = pages[0]["id"]
    ok, val = eval_in(tid, FETCH_JS, timeout=40)
    if not ok:
        print("eval 失败: %s" % val)
        return 4
    data = json.loads(val)
    print("=== 草稿 aid=%s 服务端回读 ===" % AID)
    for r in data.get("tried", []):
        print("\n--- %s → %s" % (r.get("u"), r.get("status") or r.get("err")))
        b = r.get("body") or ""
        if TITLE_KW in b:
            print("  ✅ 含标题关键词「%s」" % TITLE_KW)
        if b:
            print("  " + b[:400].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
