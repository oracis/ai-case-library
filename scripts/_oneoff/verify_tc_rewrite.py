#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立只读回读两朵云已有草稿的改稿结果；--register 才登记台账。

2026-11-08：rewrite_yun_draft.py 的双回读因平台 plain 字段压掉空格 +
剥掉 Markdown 引用符 '>' 而假失败，但平台侧其实已改好。这里用归一化
判据做一次真正独立的回读，并以备份里的旧稿首句作为 OLD_GONE 基准。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
from csdn_publish import split_md  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--platform", default="tencentcloud")
ap.add_argument("--draft", default="338078")
ap.add_argument("--file", default="D:/open-source-games/content/ep-007-openttd/platform/tencentcloud.md")
ap.add_argument("--case", default="ext-ep-007-openttd")
ap.add_argument("--expect-total", type=int, default=12)
ap.add_argument("--register", action="store_true")
a = ap.parse_args()

norm = lambda s: "".join((s or "").split()).lstrip(">")  # noqa: E731
raw = open(a.file, encoding="utf-8").read()
title, body = split_md(raw)
head = body.strip().split("\n")[0][:24]

proc, cdp = Y._connect()
# _connect() 只起浏览器/端口，CDP 还没 attach 到任何页面 target；
# 必须先 open 一个页面，否则 cdp.ws 是 None，eval 直接 AttributeError。
cdp.open("https://cloud.tencent.com/developer/article/write-new?draftId=%s"
         % a.draft)
time.sleep(6)
LIST_TC = """
fetch("/developer/api/article/getUserArticleDrafts",
      {method:"POST", credentials:"include",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""
r = cdp.eval(LIST_TC)
if not isinstance(r, str) or r.startswith("ERR|"):
    raise SystemExit("草稿箱读失败: %s" % str(r)[:200])
d = json.loads(r)
items = d.get("list") or d.get("data") or []
if isinstance(items, dict):
    items = items.get("list") or []
rows = [(str(x.get("draftId")), x.get("title") or "", x.get("plain") or "")
        for x in items if isinstance(x, dict)]
target = [x for x in rows if x[0] == a.draft]

bak_path = os.path.join(ROOT, "tmp", "yun_draft_backup_%s_%s.json"
                        % (a.platform, a.draft))
bak = json.load(open(bak_path, encoding="utf-8"))
old_head = (bak.get("body") or "")[:20]

print("草稿总数:", len(rows), "｜目标存在:", bool(target))
if not target:
    sys.exit(2)
rid, rtitle, rbody = target[0]
checks = {
    "id_unchanged": rid == a.draft,
    "title_exact": rtitle.strip() == title.strip(),
    "new_body_hit": norm(head[:12]) in norm(rbody),
    "old_body_gone": (not old_head) or (norm(old_head) not in norm(rbody)),
    "no_new_draft": len(rows) == a.expect_total,
}
print("新稿标题:", title)
print("平台标题:", repr(rtitle))
print("平台正文首 70:", repr(rbody[:70]))
print("备份旧稿首句:", repr(old_head))
for k, v in checks.items():
    print("  %-15s = %s" % (k, v))
ok = all(checks.values())
print("OVERALL =", ok)
if not ok:
    sys.exit(3)
if a.register:
    url = ("https://cloud.tencent.com/developer/article/write-new?draftId=%s"
           % a.draft)
    Y._remember_db(a.case, {a.platform: {"ok": True, "draftId": a.draft,
                                         "url": url}}, title=title)
    print("✓ 台账已登记 draft:", a.draft)
else:
    print("（只读，未登记；加 --register 才写台账）")
