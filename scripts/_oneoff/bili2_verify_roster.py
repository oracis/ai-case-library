#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：B站二号匿名空间已发表列表按标题命中 roster-for-slack。

2026-11-26 续发独立回读：publish_one 自报「已发布 54098105」不算数，
用空间公开接口 mid=409825959 按标题命中 + 核 summary 关键词才算证据。

用法：
    python scripts/_oneoff/bili2_verify_roster.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9224
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-bili2-profile")
MID = 409825959   # 二号账号 mid（publish_one.VERIFY_HINT 记录）


def main():
    import chrome_cdp_launch as cl
    import wechat_publish as wp
    import bili_draft_api as api
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    wp._autostart_chrome_if_needed(PORT, profile=PROFILE)
    cdp = wp.CDP(PORT)
    api._bili_tab(cdp, new_if_none=True)

    arts = api.list_published_articles(cdp, mid=MID, ps=30, max_page=8)
    print("二号 mid=%s 已发表 %d 篇" % (MID, len(arts)))
    kw = "roster for slack"
    hit = [a for a in arts if kw in (a.get("title") or "").lower()]
    print("按标题命中 %d 条" % len(hit))
    for a in hit:
        print("  id=%s title=%s" % (a.get("id"), a.get("title")))
        print("  summary=%s" % str(a.get("summary"))[:300])
        print("  words=%s image_urls=%s" % (
            a.get("words"), str(a.get("image_urls"))[:120]))
    ok = len(hit) == 1 and str(hit[0].get("id")) == "54098105"
    print("判据（唯一命中 + id=54098105）:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
