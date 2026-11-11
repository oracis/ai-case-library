# -*- coding: utf-8 -*-
"""一次性探针 16：Python 下载图片 → base64 → data: URL 注入 img src。"""
import base64
import json
import os
import sys
import time
import urllib.request

os.environ["no_proxy"] = "*"

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_b64(url):
    r = _OP.open(url, timeout=20)
    data = r.read()
    ct = r.headers.get("Content-Type", "")
    if data[:4] == b"\x89PNG":
        mime = "image/png"
    elif data[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    else:
        mime = ct or "application/octet-stream"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


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
    info = s.img_info()
    total, broken = s.img_health()
    print("换前：%d 张，裂 %d" % (total, broken))

    for i, it in enumerate(info):
        if it.get("nw", 0) > 0:
            continue
        src = it.get("src") or ""
        if not src.startswith("http"):
            continue
        try:
            du = fetch_b64(src)
        except Exception as e:
            print("  [%d] 下载失败：%s" % (i, e))
            continue
        s.sub.eval("window.__du%d = %s" % (i, json.dumps(du)),
                   refresh_context=True)
        r = s.js("(function(){var imgs=document.querySelectorAll("
                 "'.img-container img.preview');var im=imgs[%d];"
                 "if(!im) return 'no-img'; im.src=window.__du%d;"
                 "return 'set';})()" % (i, i))
        print("  [%d] %s" % (i, r))

    time.sleep(2)
    total, broken = s.img_health()
    print("换后：%d 张，裂 %d" % (total, broken))
finally:
    s.close()
