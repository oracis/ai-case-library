#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读回读：腾讯云 easymix 真发后，创作中心是否有新文章（2026-11-26）。

同一次调用内起 9228 CDP。只 open + fetch 列表，不点任何按钮。
"""
import json, os, re, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y   # noqa: E402
import tc_manage as TM      # noqa: E402

TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"
proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open(TM.LIST_URL)
time.sleep(10)
rows = TM.fetch_list(cdp, pages=4)
norm = lambda s: re.sub(r"\s+", "", str(s or ""))
hits = [r for r in rows if norm(r.get("title")) == norm(TITLE)]
print("总文章:", len(rows), "| 同标题:", len(hits))
for h in sorted(hits, key=lambda r: int(r.get("id") or 0)):
    print("  id=%s state=%s why=%s" % (h.get("id"), h.get("state"), str(h.get("why"))[:100]))
# 草稿箱是否还有 353117
cdp.open("https://cloud.tencent.com/developer/article/write-new")
time.sleep(8)
LIST = """fetch("/developer/api/article/getUserArticleDrafts?_="+Date.now(),
 {method:"POST",credentials:"include",cache:"no-store",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify({page:1,pageSize:50,contentType:"rich"})})
 .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""
r = cdp.eval(LIST)
if isinstance(r, str) and not r.startswith("ERR|"):
    d = json.loads(r); items = d.get("list") or d.get("data") or []
    if isinstance(items, dict): items = items.get("list") or []
    ids = [str(x.get("draftId")) for x in items]
    print("草稿箱数:", len(ids), "| 353117 还在:", "353117" in ids, "| ids:", ids)
