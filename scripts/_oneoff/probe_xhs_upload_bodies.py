# -*- coding: utf-8 -*-
"""一次性探针 7：抓上传链路的响应体 —— permit / ros-upload 返回什么，
fileId 和「能活的 URL」从哪来。传 1 张图后把相关响应体 dump 出来。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

HIT = ("permit", "ros-upload", "upload", "spectrum", "fe-platform",
       "fileinfo", "media")

s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s" % d.get("title", "")[:30])
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    print("图 %d 张" % n)

    rows = F.load_plan([d])
    path = rows[0]["local"][1]
    s.sub.send("Network.enable", {})
    nodes = s._file_nodes()
    s.sub.send("DOM.setFileInputFiles", {"nodeId": nodes[0], "files": [path]})
    print("已塞文件，监听 75s...")

    reqs = {}          # requestId -> {url, status, body}
    deadline = time.time() + 75
    while time.time() < deadline:
        try:
            raw = s.sub.ws.recv_text()
        except Exception as e:
            print("[warn] ws：%s" % e)
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
            url = p.get("request", {}).get("url", "")
            if any(k in url for k in HIT):
                reqs[rid] = {"url": url[:140], "phase": "sent"}
        elif meth == "Network.responseReceived":
            if rid in reqs:
                reqs[rid]["status"] = (p.get("response") or {}).get("status")
                reqs[rid]["phase"] = "resp"
        elif meth == "Network.loadingFinished":
            if rid in reqs and reqs[rid].get("phase") == "resp":
                try:
                    r = s.sub.send("Network.getResponseBody",
                                   {"requestId": rid})
                    body = (r.get("result") or {})
                    b = body.get("body") or ""
                    if body.get("base64Encoded"):
                        import base64
                        b = base64.b64decode(b).decode("utf-8", "replace")
                    reqs[rid]["body"] = b[:1500]
                except Exception as e:
                    reqs[rid]["body"] = "<%s>" % e

    out = []
    for rid, v in reqs.items():
        if v.get("phase") == "sent":
            continue
        out.append(v)
        print("\n[%s] %s" % (v.get("status"), v["url"]))
        if v.get("body"):
            print("  body: %s" % v["body"][:800])
    with open("tmp/xhs_upload_bodies.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n共 %d 个响应，明细在 tmp/xhs_upload_bodies.json" % len(out))
    s.leave(save=False)
finally:
    s.close()
