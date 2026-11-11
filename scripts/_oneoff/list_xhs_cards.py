#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：列小红书草稿箱全部卡片标题 + 孤儿清单，用来判定「认不出来」还是「真不在箱里」。

为什么需要
--------
`diag_xhs_13.py` 有 3 条（easymix / harperai / quran-unlock）`match_drafts` 匹配不上。
台账里它们的 `remote_id` / `draft_id` 都是空串 —— 可能 (a) 从没建过远端草稿，
(b) 建过但标题被改过、认不出来。两者处置完全不同：
  (a) ⇒ 什么都不用做；
  (b) ⇒ 箱里有稿就必须管，不能因为「认不出来」当它不存在。
所以先把箱里 41 条标题全打出来，人工对一眼。

⛔ 只读：只开草稿箱读卡片，不点编辑、不输入。
用法：
    python -X utf8 tmp/list_xhs_cards.py
    python -X utf8 tmp/list_xhs_cards.py --grep 抵押
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import xhs_publish as xp                # noqa: E402
import wechat_publish as wp             # noqa: E402
import chrome_cdp_launch as cl          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", default="")
    a = ap.parse_args()

    wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=cl.PLAT_PROFILE)
    sub = xp._draft_cdp()
    cards = xp.fetch_drafts(sub=sub) or []
    print("草稿箱 %d 条\n" % len(cards))
    for i, d in enumerate(cards, 1):
        t = (d.get("title") or "").strip()
        if a.grep and a.grep not in t:
            continue
        print("%2d. %-52s ｜ %s" % (i, t[:52], d.get("saved", "")))
    matched, orphans = xp.match_drafts(cards)
    print("\n对上 %d ｜ 孤儿 %d" % (len(matched), len(orphans)))
    print("\n--- 孤儿（认不出是哪条 case 的卡片）---")
    for o in orphans:
        print("  %-52s ｜ %s" % ((o.get("title") or "")[:52], o.get("saved", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
