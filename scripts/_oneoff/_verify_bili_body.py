# -*- coding: utf-8 -*-
"""远端回读验收：B站草稿正文是否已含「我的判断」实质内容。"""
import sys, os, re, json
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, "scripts")
import bili_draft_api as api, wechat_publish as wp, bilibili_publish as bp

IDS = ["nutria-9e3eaa", "fastwrite-io", "downconvert-sketchup-files", "macwall",
       "growingupvideo", "homy", "stealth-rdp", "white-glove-content",
       "pet-plus-us", "wagiff-app", "quotedeck"]

cdp = wp.CDP(bp.PUB_PORT)
api._bili_tab(cdp)
ds = api.list_drafts(cdp)
ov = json.load(open("data/bili_title_overrides.json", encoding="utf-8"))
by_title = {}
for d in ds:
    by_title.setdefault((d.get("title") or "").strip(), str(d.get("article_id")))

ok = bad = 0
for cid in IDS:
    want = ov[cid][:30]
    aid = by_title.get(want)
    if not aid:
        print("  !! %-26s 远端找不到草稿《%s》" % (cid[:26], want))
        bad += 1
        continue
    v = api.view_draft(cdp, aid)
    body = (v.get("content") or "") if isinstance(v, dict) else ""
    plain = re.sub(r"<[^>]+>", "", body)
    i = plain.find("我的判断")
    seg = plain[i + 4:i + 4 + 120] if i >= 0 else ""
    has = bool(seg.strip()) and ("核实记录" not in seg[:60])
    print("  %s aid=%-7s %-26s 判断段=%s"
          % ("OK " if has else "!! ", aid, cid[:26],
             (seg.strip()[:40] if seg.strip() else "<无>")))
    ok += 1 if has else 0
    bad += 0 if has else 1
print()
print("B站远端正文含实质判断: %d / %d（异常 %d）" % (ok, len(IDS), bad))