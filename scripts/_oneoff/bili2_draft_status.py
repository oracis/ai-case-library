#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：查 B站二号(9224)草稿箱里指定 case/标题的草稿状态。

2026-11-26 续发前用：确认上一轮 SIGTERM 前建的 roster-for-slack 草稿
aid=379994 是否还在、是否重复，避免再建第二份白烧额度。

用法：
    python scripts/_oneoff/bili2_draft_status.py            # 列全部草稿+重复组
    python scripts/_oneoff/bili2_draft_status.py --kw roster  # 只看关键词
"""
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9224
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-bili2-profile")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default=None, help="只看标题含此关键词的草稿")
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
    drafts = api.list_drafts(cdp, pn=1, ps=200)
    print("草稿箱总数:", len(drafts))

    g = defaultdict(list)
    for d in drafts:
        g[(d.get("title") or "").strip().lower()].append(d)

    if args.kw:
        print("=== 关键词 %r ===" % args.kw)
        for d in drafts:
            if args.kw.lower() in (d.get("title") or "").lower():
                print("  aid=%s mtime=%s title=%s" % (
                    d.get("article_id"), d.get("mtime"), d.get("title")))

    dup = {t: v for t, v in g.items() if len(v) > 1}
    print("=== 同标题重复组: %d ===" % len(dup))
    for t, v in sorted(dup.items(), key=lambda x: -len(x[1])):
        print("  x%d %s" % (len(v), t[:50]))
        for d in v:
            print("     aid=%s mtime=%s" % (d.get("article_id"), d.get("mtime")))


if __name__ == "__main__":
    main()
