# -*- coding: utf-8 -*-
"""只读回读腾讯云草稿箱清单（不写平台侧）。2026-11-11 腾讯云事故复核用。"""
import json
import sys
import time
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

LIST_JS = """
fetch("/developer/api/article/getUserArticleDrafts",
      {method:"POST", credentials:"include",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();})
  .catch(function(e){return "ERR|"+e;})
"""

proc, cdp = Y._connect()
print("9228:", "复用" if proc is None else "本次新起")
cdp.open("https://cloud.tencent.com/developer/article/write-new")
time.sleep(6)
r = cdp.eval(LIST_JS)
if not isinstance(r, str) or r.startswith("ERR|"):
    raise SystemExit("READ FAIL: %s" % str(r)[:300])
d = json.loads(r)
items = d.get("list") or d.get("data") or []
if isinstance(items, dict):
    items = items.get("list") or []
print("腾讯云草稿箱条数", len(items))
for x in items[:25]:
    print("  %-9s | %-48s | %-19s | plain=%d | head=%r" % (
        str(x.get("draftId")), str(x.get("title"))[:48],
        str(x.get("gmtCreate") or x.get("createTime") or "")[:19],
        len(x.get("plain") or ""), str(x.get("plain") or "")[:34]))
