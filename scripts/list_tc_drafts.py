# -*- coding: utf-8 -*-
"""只读：列全腾讯云草稿箱，按 createTime 升序找最久一条。

2026-11-13 由 tmp/ 提升为常驻工具（scripts/ 与 tmp/ 同为仓库根下一层，
ROOT 写法不用改）。⛔ 只读，不发任何写请求；别再把这类工具写回 tmp/。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open("https://cloud.tencent.com/developer/creator/article")
time.sleep(4)
r = cdp.eval("""
fetch("/developer/api/article/getUserArticleDrafts",
      {method:"POST", credentials:"include",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();})
  .catch(function(e){return "ERR|"+e;})""")
if not isinstance(r, str) or r.startswith("ERR|"):
    print("FETCH_FAIL", str(r)[:300])
    sys.exit(2)
data = json.loads(r)
items = data.get("list") or data.get("data") or []
if isinstance(items, dict):
    items = items.get("list") or []
rows = [it for it in items if isinstance(it, dict)]
print("TOTAL", len(rows))
rows.sort(key=lambda x: x.get("createTime") or 0)
for it in rows:
    print("DRAFT", it.get("draftId"), "ct=", it.get("createTime"),
          "title=", repr((it.get("title") or "")[:70]),
          "plain=", repr((it.get("plain") or "")[:60]))
print("OLDEST", json.dumps(rows[0], ensure_ascii=False)[:600] if rows else "NONE")
