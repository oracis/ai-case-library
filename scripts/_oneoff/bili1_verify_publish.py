#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：B站一号匿名空间已发表列表按标题命中（独立双回读）。

2026-11-26 续发：publish_one 自报 stealth-rdp「已发布 54098186」不算数，
用空间公开接口按标题命中 + 核 summary 才算证据。mid 从登录态 my_mid 取。

用法：
    python scripts/_oneoff/bili1_verify_publish.py --kw stealth --expect-id 54098186
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9223
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-debug-profile")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", required=True)
    ap.add_argument("--expect-id", default="")
    args = ap.parse_args()

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
    mid = api.my_mid(cdp)
    arts = api.list_published_articles(cdp, mid=mid, ps=30, max_page=8)
    print("一号 mid=%s 已发表 %d 篇" % (mid, len(arts)))
    hit = [a for a in arts if args.kw.lower() in (a.get("title") or "").lower()]
    print("按标题 %r 命中 %d 条" % (args.kw, len(hit)))
    for a in hit:
        print("  id=%s title=%s" % (a.get("id"), a.get("title")))
        print("  summary=%s" % str(a.get("summary"))[:260])
    ok = len(hit) == 1
    if args.expect_id:
        ok = ok and str(hit[0].get("id")) == args.expect_id
    print("判据（唯一命中%s）: %s" % (
        (" + id=" + args.expect_id) if args.expect_id else "",
        "PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
