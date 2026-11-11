# -*- coding: utf-8 -*-
"""一次性探针：草稿编辑器里塞一张图，看上传请求到底发没发、结果如何。

只针对第一条草稿、只传一张本地图（该图反正要重传，不算破坏）。
"""
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
    print("目标：%s（%s）" % (d.get("title", "")[:30], d.get("saved", "")))
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, settled = s.wait_settled()
    print("图 %d 张" % n)

    # 找一张本地图
    rows = F.load_plan([d])
    r = rows[0]
    if not r["local"]:
        raise SystemExit("没找到本地图")
    path = r["local"][0]
    print("试传：%s" % path)

    # 开 Network 监听，然后塞文件
    s.sub.send("Network.enable", {"maxPostDataSize": 65536})
    nodes = s._file_nodes()
    print("file input 节点：%d 个" % len(nodes))
    if not nodes:
        raise SystemExit("没有 file input")
    s.sub.send("DOM.setFileInputFiles", {"nodeId": nodes[0], "files": [path]})

    # 裸读 ws 收事件 90 秒
    reqs = {}          # requestId -> {url, status, failed, mime, ts}
    deadline = time.time() + 90
    n_ev = 0
    while time.time() < deadline:
        try:
            raw = s.sub.ws.recv_text()
        except Exception as e:
            print("[warn] ws 读异常：%s" % e)
            break
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        meth = m.get("method") or ""
        if not meth.startswith("Network."):
            continue
        n_ev += 1
        p = m.get("params") or {}
        rid = p.get("requestId") or ""
        if meth == "Network.requestWillBeSent":
            url = p.get("request", {}).get("url", "")
            if any(k in url for k in ("upload", "media", "image", "img",
                                      "spectrum", "file", "edin")) \
                    or "xhscdn" in url or "xiaohongshu" in url:
                reqs[rid] = {"url": url[:120], "phase": "sent",
                             "ts": time.time()}
        elif meth == "Network.responseReceived":
            if rid in reqs:
                resp = p.get("response") or {}
                reqs[rid]["status"] = resp.get("status")
                reqs[rid]["phase"] = "resp"
        elif meth == "Network.loadingFailed":
            if rid in reqs:
                reqs[rid]["phase"] = "FAILED"
                reqs[rid]["error"] = (p.get("errorText") or "")[:80]
        # 数一下编辑器里的图
        if time.time() - deadline > -5:
            pass
    print("\n== 收到 %d 条 Network 事件，命中 %d 个请求 ==" % (n_ev, len(reqs)))
    for rid, v in reqs.items():
        print("  [%s] %s %s" % (v.get("status") or v.get("phase"),
                                v.get("error", ""), v["url"]))
    # 编辑器里现在几张图、最后一张的 src/nw
    info = s.img_info()
    print("\n编辑器图数：%d" % len(info))
    for it in info[-3:]:
        print("  nw=%s src=%s" % (it.get("nw"), (it.get("src") or "")[:80]))
finally:
    try:
        s.leave()
    except Exception:
        pass
    s.close()
