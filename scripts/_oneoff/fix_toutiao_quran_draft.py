#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条 quran-unlock：台账记 published，实际还在线上草稿箱 → 改回 draft。

## 证据（2026-11-20 实测）

  · 线上草稿箱 38 条里命中 `7699902878202315302`「Quran Unlock：古兰经习惯打卡」
  · 线上已发表 45 条里**没有**这条（含 "uran"/"古兰" 关键词 0 命中）
  · 台账 state=published、gid 空、title 空

⇒ 这是台账虚记（不是平台删稿）。改成 draft 并记上草稿 id，FIFO 才会把它
  当待发稿排进去。⚠ 别反过来补个已发 id 了事 —— 那会让它永远不发。

用法：python scripts/_oneoff/fix_toutiao_quran_draft.py [--apply]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as L  # noqa: E402

PLAT, CID = "toutiao", "quran-unlock"
GID = "7699902878202315302"
TIP = ("｜11-20 对账：线上草稿箱有（gid=%s），已发表列表没有 ⇒ "
       "台账虚记 published，改回 draft" % GID)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    r = recs.get(CID)
    if not r:
        print("⛔ 台账里没有 %s" % CID)
        return 2
    print("当前：state=%s remote_id=%r title=%r"
          % (r["state"], r.get("remote_id"), r.get("title")))
    if r["state"] == L.DRAFT and str(r.get("remote_id")) == GID:
        print("已是目标状态，无需修改")
        return 0
    if r.get("remote_id") and str(r["remote_id"]) != GID:
        print("⛔ 已有别的 remote_id=%s ⇒ 拒绝盲改" % r["remote_id"])
        return 2

    print("计划：state published → draft，记 gid=%s" % GID)
    if not a.apply:
        print("[干跑] 加 --apply 才写盘")
        return 0

    r["state"] = L.DRAFT
    r["remote_id"] = GID
    r["draft_remote_id"] = GID
    r["title"] = r.get("title") or "Quran Unlock：古兰经习惯打卡"
    r["evidence"] = "ledger_reconcile"
    r["note"] = (r.get("note") or "") + TIP
    L.save(PLAT, recs)

    after = L.get(PLAT, CID) or {}
    ok = after.get("state") == L.DRAFT and str(after.get("remote_id")) == GID
    print("回读：state=%s gid=%s ⇒ %s"
          % (after.get("state"), after.get("remote_id"), "OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
