# -*- coding: utf-8 -*-
"""一次性探针 13：触发一张图的加载，然后裸读 Network 事件，看真实失败原因。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    info = s.img_info()
    src = info[0].get("src")
    print("图 %d 张，测 src：%s" % (n, (src or "")[:100]))

    s.sub.send("Network.enable", {})
    # 触发加载（不等待，立刻切到裸读模式）
    s.sub.eval("""(function(){
      var im = new Image(); im.src = %s; return 'fired';})()""" % json.dumps(src),
      refresh_context=True)
    # 裸读 12s
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            raw = s.sub.ws.recv_text()
        except Exception as e:
            print("ws err：%s" % e)
            break
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        meth = m.get("method") or ""
        if not meth.startswith("Network."):
            continue
        p = m.get("params") or {}
        rid = p.get("requestId") or ""
        if meth == "Network.requestWillBeSent":
            u = p.get("request", {}).get("url", "")
            if "preview" in u or "spectrum" in u:
                print("→ REQ %s" % u[:120])
        elif meth == "Network.responseReceived":
            u = ((p.get("response") or {}).get("url") or "")
            st = (p.get("response") or {}).get("status")
            if "preview" in u or "spectrum" in u:
                print("← RESP %s %s" % (st, u[:120]))
        elif meth == "Network.loadingFailed":
            u = ""
            print("✗ FAIL %s %s (canceled=%s)" % (
                p.get("errorText"), p.get("type"),
                p.get("canceled")))
finally:
    s.close()
