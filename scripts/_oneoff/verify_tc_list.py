#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：同进程自起 9228，回读腾讯云创作中心列表首条。

`tc_manage.py status` 自己不起 Chrome（只连已在线的 9228），而本机
Chrome 每次跑完会被整组回收 ⇒ 必须包一层 `_connect()`，见 PRD #91。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y  # noqa: E402
import tc_manage as T  # noqa: E402

_ap = argparse.ArgumentParser()
_ap.add_argument("--aid", default="2779001")
_ap.add_argument("--title-key", default="不用 Cookie")
_args, _ = _ap.parse_known_args()
AID = _args.aid
TITLE_KEY = _args.title_key


def main():
    proc, cdp = Y._connect()
    print("9228 =", "本次新起" if proc is not None else "复用已有实例")
    try:
        tid = cdp.open(T.LIST_URL)
        cdp.send("Page.enable")
        time.sleep(9)
        rows = T.fetch_list(cdp, pages=1)
        print("列表条数 =", len(rows))
        for r in rows[:5]:
            print("  %-8s %-10s %s" % (r["id"], r["state"] or "?",
                                       r["title"][:50]))
        hit = [r for r in rows if r["id"] == AID]
        if not hit:
            print("\n✗ 列表里没有", AID, "⇒ 不能算发布成功")
            return 1
        r = hit[0]
        ok = (r["state"] == "published" and TITLE_KEY in r["title"])
        print("\n命中 =", r)
        print("结论 =", "✅ 已发表列表按标题命中" if ok else "✗ 状态/标题不符")
        return 0 if ok else 1
    finally:
        try:
            cdp.send_browser("Target.closeTarget", {"targetId": tid})
        except Exception:
            pass
        try:
            cdp.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
