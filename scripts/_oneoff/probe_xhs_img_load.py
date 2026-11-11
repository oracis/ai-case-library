# -*- coding: utf-8 -*-
"""一次性探针 8：开编辑器时监听图片请求 —— 记录里的 URL 是 200 的，
为什么 img.preview naturalWidth=0？看它实际请求的 URL 和响应。"""
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
    # 先开 Network 监听再导航
    s.sub.send("Network.enable", {})
    s.sub.send("Page.navigate", {"url": xp.PUB_URL})
    time.sleep(8)
    s.js("1")
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s" % d.get("title", "")[:30])

    # 记录开编辑器前的图片请求基线
    reqs = {}
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            m = json.loads(s.sub.ws.recv_text())
            p = m.get("params") or {}
            if (m.get("method") or "") == "Network.requestWillBeSent":
                u = p.get("request", {}).get("url", "")
                if "sns-creator-preview" in u or "fe-platform" in u:
                    reqs[p.get("requestId")] = u[:150]
        except Exception:
            break
    print("开编辑器前图片请求 %d 个" % len(reqs))

    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    info = s.img_info()
    print("图 %d 张（裂 %d）" % (n, sum(1 for i in info if i["nw"] == 0)))
    for it in info:
        print("  nw=%s src=%s" % (it.get("nw"), (it.get("src") or "")))

    # 再收 15s 事件
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            m = json.loads(s.sub.ws.recv_text())
        except Exception:
            break
        meth = m.get("method") or ""
        if not meth.startswith("Network."):
            continue
        p = m.get("params") or {}
        rid = p.get("requestId") or ""
        if meth == "Network.requestWillBeSent":
            u = p.get("request", {}).get("url", "")
            if "sns-creator-preview" in u or "fe-platform" in u \
                    or "xhscdn" in u:
                reqs[rid] = u[:200]
        elif meth == "Network.responseReceived":
            if rid in reqs:
                st = (p.get("response") or {}).get("status")
                print("  [%s] %s" % (st, reqs[rid][:150]))
        elif meth == "Network.loadingFailed":
            if rid in reqs:
                print("  [FAIL %s] %s" % (p.get("errorText"),
                                          reqs[rid][:150]))
    # 没配对的也打出来
    print("== 未配对请求 ==")
    for rid, u in reqs.items():
        print("  ? %s" % u[:150])
finally:
    s.close()
