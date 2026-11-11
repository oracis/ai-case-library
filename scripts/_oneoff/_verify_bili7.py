# -*- coding: utf-8 -*-
"""一次性校验：远端 B站草稿箱是否真有这 7 条（2026-10-10）。

日志里 B站 draft/add 返回了 aid 343497-343503（真创建证据），但 parse_batch
当时没匹配到成功标记，台账误标 failed。这里远端 list_drafts 比对 aid + 标题。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
os.environ["no_proxy"] = "127.0.0.1,localhost"

import wechat_publish as wp
import chrome_cdp_launch as L
import bili_draft_api as bili_api

PORT = 9223
EXPECT = {
    "augora-ai": 343497,
    "cometly": 343498,
    "podawaa": 343499,
    "publbee": 343500,
    "vectosolve": 343501,
    "vid-ai": 343502,
    "wpconvert-ai-convert-ai-sites-to-wordpress": 343503,
}

profile = os.path.expandvars(getattr(L, "PLAT_PROFILE", L.PROFILE))
ok = wp._autostart_chrome_if_needed(port=PORT, profile=profile)
print("autostart:", ok)
cdp = wp.CDP(PORT)
bili_api._bili_tab(cdp)
drafts = bili_api.list_drafts(cdp)
by_id = {d.get("article_id"): (d.get("title") or "") for d in drafts}
print("远端草稿总数:", len(drafts))
for cid, aid in EXPECT.items():
    title = by_id.get(aid)
    print("%-50s aid=%s -> %s" % (cid, aid, ("《%s》" % title) if title else "NOT FOUND"))
