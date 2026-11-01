#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云草稿正文体检：看行距是「多余空段落」还是「平台 p 的 margin」。

用法：
  python scripts/yun_inspect.py --port 9228            # 读当前打开的那篇
  python scripts/yun_inspect.py --port 9228 --dump 3000

判据（2026-10-31 建，起因：用户反馈腾讯云正文行间距过大）：
  · 若编辑器 HTML 里出现连续 `<p><br></p>` 或 `&nbsp;` 空段落 ⇒ 我们灌多了
  · 若段落数 == markdown 段数，且 `p` 的 computedStyle margin 很大 ⇒ 平台样式
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from juejin_publish import _CDP, _WS          # noqa: E402


def _no_proxy_list(port):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open("http://127.0.0.1:%d/json/list" % port,
                              timeout=8).read())


class Tab:
    """直连某个已有 tab 的轻量 CDP 客户端（复用 _WS，不做 session 复用）。"""

    def __init__(self, ws_url):
        self.ws = _WS(ws_url)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send_text(json.dumps({"id": self._id, "method": method,
                                      "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv_text())
            if r.get("id") == self._id:
                return r

    def eval(self, js):
        r = self.send("Runtime.evaluate",
                      {"expression": js, "returnByValue": True,
                       "awaitPromise": True})
        res = r.get("result", {}).get("result", {})
        return res.get("value")


def pick(port, want="write-new"):
    for t in _no_proxy_list(port):
        if t.get("type") != "page":
            continue
        if want in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    return None


JS_REPORT = """
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  if (!ed) return JSON.stringify({err: 'no #cdc-rich-editor'});
  var ps = ed.querySelectorAll('p, h1, h2, h3, h4, blockquote, li, pre, div');
  var empty = 0, total = ed.querySelectorAll('p').length;
  var sample = [];
  var kids = ed.children;
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    var txt = (k.innerText || '').trim();
    var cs = getComputedStyle(k);
    if (!txt) empty++;
    if (i < 8) {
      sample.push({tag: k.tagName, cls: (k.className||'').toString().slice(0,50),
                   txt: txt.slice(0, 26), empty: !txt,
                   mt: cs.marginTop, mb: cs.marginBottom, lh: cs.lineHeight,
                   fs: cs.fontSize});
    }
  }
  // 取一个普通段落的实际渲染高度与间距
  var p0 = ed.querySelector('p');
  var info = null;
  if (p0) {
    var cs2 = getComputedStyle(p0);
    info = {mt: cs2.marginTop, mb: cs2.marginBottom, lh: cs2.lineHeight,
            fs: cs2.fontSize, h: p0.getBoundingClientRect().height,
            outline: cs2.outline};
  }
  return JSON.stringify({
    childCount: kids.length, pCount: total, emptyTop: empty,
    firstChildren: sample, firstP: info,
    htmlHead: ed.innerHTML.slice(0, 1600),
    editorStyle: (function(){ var c = getComputedStyle(ed);
      return {lh: c.lineHeight, fs: c.fontSize, ws: c.whiteSpace}; })()
  });
})()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9228)
    ap.add_argument("--url", default="write-new")
    ap.add_argument("--dump", type=int, default=0, help="额外打印正文 HTML 前 N 字符")
    args = ap.parse_args()

    t = pick(args.port, args.url)
    if not t:
        print("没有打开的目标页（URL 含 %r）" % args.url)
        return 2
    print("tab: %s" % t["url"][:110])
    tab = Tab(t["webSocketDebuggerUrl"])
    raw = tab.eval(JS_REPORT)
    if not isinstance(raw, str):
        print("eval 返回异常: %r" % (raw,))
        return 2
    d = json.loads(raw)
    if d.get("err"):
        print(d["err"])
        return 2
    print("子块数=%d  p 数=%d  空块数=%d" % (d["childCount"], d["pCount"],
                                           d["emptyTop"]))
    print("编辑器样式: %s" % json.dumps(d["editorStyle"], ensure_ascii=False))
    print("首个 p: %s" % json.dumps(d["firstP"], ensure_ascii=False))
    print("--- 前 8 个子块 ---")
    for c in d["firstChildren"]:
        print("  %-10s %-16s mt=%-6s mb=%-6s lh=%-7s fs=%-5s empty=%s | %s"
              % (c["tag"], (c["cls"] or "-")[:16], c["mt"], c["mb"], c["lh"],
                 c["fs"], c["empty"], c["txt"]))
    if args.dump:
        print("--- html 前 %d ---" % args.dump)
        print((d.get("htmlHead") or "")[:args.dump])
    return 0


if __name__ == "__main__":
    sys.exit(main())
