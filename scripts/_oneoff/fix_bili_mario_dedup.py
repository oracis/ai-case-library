#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站一号：马力欧重复稿处置留痕。

事实（2026-11-20 21xx 公开页实测）：
  · 53683188《游戏厅 #1：Super Mario Bros》  2026-10-30 17:46  ← 早，已删
  · 53786328《用 Backbone 游戏引擎复刻超级马力欧兄弟》2026-11-06 13:43 ← 晚，保留

改动：ext-ep-001-super-mario-bros 的 note 追加一句删除留痕；
      published_at / verified_at / remote_id 一律不动。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ledger as L          # noqa: E402

PLAT = "bilibili"
CASE = "ext-ep-001-super-mario-bros"
TIP = ("｜11-20 删重：10-30 发的《游戏厅 #1：Super Mario Bros》(aid 53683188) "
       "与本篇同题材，已按「删早留新」删除；保留本篇 53786328")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    r = recs.get(CASE)
    if not r:
        print("⛔ 台账没有 %s" % CASE)
        return 2
    print("现状 state=%s remote_id=%s verified_at=%s"
          % (r.get("state"), r.get("remote_id"), r.get("verified_at")))
    note = r.get("note") or ""
    if TIP in note:
        print("已留痕，无需重复")
        return 0
    r["note"] = note + TIP
    print("note → %s" % r["note"][-120:])
    if not a.apply:
        print("[干跑] 加 --apply 才写盘")
        return 0
    L.save(PLAT, recs)
    after = L.get(PLAT, CASE) or {}
    print("回读 state=%s remote_id=%s verified_at=%s"
          % (after.get("state"), after.get("remote_id"), after.get("verified_at")))
    print("note 留痕:", "✓" if TIP in (after.get("note") or "") else "✗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
