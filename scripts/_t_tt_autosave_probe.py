#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t_tt_autosave_probe.py -- 一次性诊断（2026-10-26）。

头条 publish 连续 3 轮「自动保存：没等到保存提示」且草稿箱确无草稿。
按红线探法：注入 fetch/XHR hook 记 window.__net，正常填一稿，
观察 ① 保存 XHR 发不发 ② 页面 toast 文案到底是什么。
只填标题+正文（不传封面），结束关 tab，不点发布。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as tt  # noqa: E402

CID = sys.argv[1] if len(sys.argv) > 1 else "agentsguild"
TITLE_FIX = sys.argv[2] if len(sys.argv) > 2 else ""   # 干净标题对照实验用

HOOK = (
    "(function(){"
    "if (window.__net) return 'already';"
    "window.__net=[];"
    "var rec=function(k,u,m,b){try{window.__net.push({t:Date.now(),k:k,"
    "u:String(u),m:m||'',b:(b?String(b).slice(0,200):'')});}catch(e){}};"
    "var of=window.fetch;"
    "window.fetch=function(u,o){rec('fetch',u,(o&&o.method)||'GET',o&&o.body);"
    "return of.apply(this,arguments);};"
    "var oo=XMLHttpRequest.prototype.open, os=XMLHttpRequest.prototype.send;"
    "XMLHttpRequest.prototype.open=function(m,u){this.__m=m;this.__u=u;"
    "return oo.apply(this,arguments);};"
    "XMLHttpRequest.prototype.send=function(b){rec('xhr',this.__u,this.__m,b);"
    "return os.apply(this,arguments);};"
    "if (navigator.sendBeacon){var ob=navigator.sendBeacon.bind(navigator);"
    "navigator.sendBeacon=function(u,b){rec('beacon',u,'POST',b);"
    "return ob(u,b);};}"
    "return 'hooked';})()"
)


def main():
    meta_p = os.path.join(tt.OUT, CID, "meta.json")
    art_p = os.path.join(tt.OUT, CID, "article.html")
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    with open(art_p, encoding="utf-8") as f:
        full = f.read()
    body_html = full.split("\n", 1)[1] if full.startswith("<h1>") else full

    cdp, t = tt._open(tt.TT_EDITOR, wait=10)
    print("[probe] editor opened:", cdp.eval("location.href"))
    print("[probe] hook:", cdp.eval(HOOK))
    title = TITLE_FIX or meta["title"]
    print("[probe] title used:", repr(title))
    ok = tt._focus_and_type(cdp, tt.SEL["title"], title)
    print("[probe] title typed:", ok)
    got = cdp.eval("(function(){var e=document.querySelector(%s);"
                   "if(!e) return 'notfound'; e.focus(); return 'ok';})()"
                   % json.dumps(tt.SEL["body"]))
    print("[probe] body focus:", got)
    if got == "ok":
        cdp.send("Runtime.evaluate", {
            "expression": ("document.execCommand('insertHTML', false, %s)"
                           % json.dumps(body_html, ensure_ascii=False)),
            "returnByValue": True})
    t0 = time.time()
    nudged = False
    for i in range(9):                        # ~100s 观察窗
        time.sleep(10)
        net = cdp.eval("JSON.stringify(window.__net||[])") or "[]"
        try:
            net = json.loads(net)
        except ValueError:
            net = []
        tail = (cdp.eval("(function(){var s=(document.body.innerText||'');"
                         "return s.slice(-260);})()") or "").replace("\n", " | ")
        # 全量流量：按 host+path 前缀去重计数
        from collections import Counter
        cnt = Counter(str(x.get("u", "")).split("?")[0][:90] for x in net)
        print("[%3ds] total=%d uniq=%d  toast: %s"
              % (time.time() - t0, len(net), len(cnt), tail[-120:]))
        for u, c in cnt.most_common(8):
            print("     x%d %s" % (c, u))
        if not nudged and time.time() - t0 > 45:
            # 保存触发 nudge：blur 编辑器 + Ctrl+S 键序 + 再点一下标题
            cdp.eval("(function(){var e=document.querySelector(%s);"
                     "if(e){e.blur();} document.body.focus();})()"
                     % json.dumps(tt.SEL["body"]))
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "modifiers": 2, "key": "s",
                "code": "KeyS", "windowsVirtualKeyCode": 83})
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "modifiers": 2, "key": "s",
                "code": "KeyS", "windowsVirtualKeyCode": 83})
            nudged = True
            print("  [nudge] blur + Ctrl+S 已发")
        if any(("draft" in str(x.get("u", "")).lower()
                or "save" in str(x.get("u", "")).lower()) for x in net):
            print("[probe] 看到保存相关请求，收尾")
    cdp.close_target(t["id"])


if __name__ == "__main__":
    main()
