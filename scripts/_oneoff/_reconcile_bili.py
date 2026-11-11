# -*- coding: utf-8 -*-
import sys, os, json, re
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, "scripts")
import bili_draft_api as api, wechat_publish as wp, bilibili_publish as bp
import importlib.util
spec = importlib.util.spec_from_file_location("rt", "scripts/bili_retitle.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

cdp = wp.CDP(bp.PUB_PORT)
api._bili_tab(cdp)
ds = api.list_drafts(cdp)
print("远端草稿总数:", len(ds))
ov = json.load(open("data/bili_title_overrides.json", encoding="utf-8"))
RE = bp.TITLE_MONEY_RE

rem = {str(d.get("article_id")): (d.get("title") or "").strip() for d in ds}

print()
print("=== 18 条对账（远端 vs override）===")
bad = 0
for aid, cid in sorted(rt.AID_MAP.items()):
    new = ov.get(cid, "")
    got = rem.get(aid)
    ok = got == new[:30]
    if not ok:
        bad += 1
    print(("  OK " if ok else "  !! "), cid[:24].ljust(26), (got or "<missing>")[:42])
print("18 条未对齐:", bad)

print()
print("=== 50 条里仍带金额的标题 ===")
n = 0
for d in ds:
    t = (d.get("title") or "").strip()
    if RE.search(t):
        n += 1
        print("  ", d.get("article_id"), repr(t)[:50])
print("带金额总数:", n)
