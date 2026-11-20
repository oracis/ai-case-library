#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉 B站一号台账里的 clawlink 幻影行（2026-11-20 用户裁定「清掉幻影行」）。

核查结论（实测证据见当日流水）：
  · 一号已投稿 43 条，标题全表无 ClawLink；一号草稿箱 49 条无 376928。
  · 376928 落在**二号账号**的草稿 id 段（二号现役草稿 379226–379229，
    mid=409825959），一号草稿 id 段是 342xxx–345xxx。
  · 该稿 10-31 已在二号发布：aid 53699085 / www.bilibili.com/read/cv53699085。

动作：
  1) `bili_drafts.json` 里删掉 clawlink 行（一号线上无此对象）
  2) manifest 里 bilibili/clawlink → `deleted`，清掉错记的 remote_id/draft_id/url，
     并写清原因 ⇒ 状态机是终态，不会在一号重发
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ledger as L                       # noqa: E402
from multiplatform.state import Manifest  # noqa: E402

CASE = "clawlink"
PLAT = "bilibili"
NOTE = ("11-20 核查：376928 是二号账号（chrome-bili2-profile / mid 409825959）"
        "草稿 id 段，一号草稿箱与已投稿均无此稿；该稿 10-31 已发在二号"
        "（aid 53699085 / read/cv53699085）⇒ 一号此行作废、不重发")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    row = recs.get(CASE)
    if not row:
        print("台账里已经没有 %s，跳过删除" % CASE)
    else:
        print("待删行：state=%s remote_id=%s note=%s"
              % (row.get("state"), row.get("remote_id"),
                 (row.get("note") or "")[:60]))

    m = Manifest()
    old = m.get(CASE, PLAT)
    print("manifest 现状：%s" % json.dumps(old, ensure_ascii=False)[:220])

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 0

    if row:
        recs.pop(CASE, None)
        L.save(PLAT, recs)
        print("→ 已从 bili_drafts.json 删除")

    rec = dict(old)
    for k in ("remote_id", "draft_id", "url"):
        rec.pop(k, None)
    m.data.setdefault(CASE, {})[PLAT] = rec
    m.set(CASE, PLAT, "deleted", error=NOTE)

    after_led = L.get(PLAT, CASE)
    after_m = Manifest().get(CASE, PLAT)
    print("回读 ledger：%s" % ("已无此行 ✓" if not after_led else after_led))
    print("回读 manifest：%s" % json.dumps(after_m, ensure_ascii=False)[:260])
    import sqlite3
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    try:
        r = list(conn.execute(
            "SELECT platform,state,remote_id,note FROM platform_posts "
            "WHERE case_id=? AND platform=?", (CASE, PLAT)))
        print("回读 db：%s" % (r or "无行"))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
