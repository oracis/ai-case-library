#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cleanup_draft_published_at.py —— 清理「state 非 published 却带 published_at」的脏行。

背景（2026-11-10 对账）
------------------------
`data/ledger.db` 的 `platform_posts` 里有 82 行 `state='draft'` 却带着
`published_at`：bilibili 46 + wechat 36。逐条看 note 全是「草稿同步 /
更新草稿 aid=…」和「按远端草稿箱 list_ex 回填 appmsgid」，即**建草稿时把
草稿时间当发布时间写了**。

wechat 那 36 行的 note 里已经写着「[11-09 错账清理] 原 published_at=…
已迁出」——说明 11-09（PRD #102）清理成功过，**后来又被刷回来了**。
刷回源头是 `ledger._db_sync()`：它把 JSON 统一视图的 `at`（对草稿就是建稿
时间）无条件写进 `published_at`，不看 state；只要有人调 `L.save()`，整本
记录就会逐条 upsert 一遍，把 NULL 重新写成旧值。

本脚本做两件事（分开开关）：
  · 清存量：`published_at=NULL`，原值迁进 note（默认干跑，--apply 才写）
  · 复核：清完后 `state!='published' AND published_at 非空` 必须为 0

⛔ 只做单行 UPDATE，不用 `Manifest.save()`，不 checkout/restore 回滚。
⛔ 只清 `state != 'published'` 的行；published 行一律不碰。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "data", "ledger.db")

MARK = "｜[11-10 对账] 原 published_at='%s' 已迁出（state 保持 %s）"


def fetch_dirty(conn):
    cur = conn.execute(
        "SELECT id, platform, case_id, state, remote_id, published_at, note "
        "FROM platform_posts "
        "WHERE state != 'published' AND published_at IS NOT NULL AND published_at != '' "
        "ORDER BY platform, published_at")
    return cur.fetchall()


def build_note(old_note, pt, state):
    """原值迁进 note；已迁过同一值的只补一句复清标记，不重复堆叠。"""
    old_note = old_note or ""
    if ("原 published_at='%s'" % pt) in old_note:
        if "[11-10 对账]" in old_note:
            return None                      # 已经记过，不写
        return old_note + "｜[11-10 对账] 再次复清同一值（草稿同步回写所致）"
    return old_note + (MARK % (pt, state))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="真写；缺省只干跑")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit("db 不存在：%s" % a.db)

    conn = sqlite3.connect(a.db)
    rows = fetch_dirty(conn)
    print("目标库：%s" % a.db)
    print("待清行：%d" % len(rows))
    if not rows:
        print("无需清理")
        return 0

    from collections import Counter
    print("按平台：", dict(Counter(r[1] for r in rows)))
    print("按状态：", dict(Counter(r[3] for r in rows)))

    n_note = 0
    for (rid, plat, cid, state, remote, pt, note) in rows:
        new_note = build_note(note, pt, state)
        if new_note is None:
            continue
        n_note += 1
        if a.apply:
            conn.execute(
                "UPDATE platform_posts SET published_at=NULL, note=? WHERE id=?",
                (new_note, rid))
    if a.apply:
        conn.commit()
        print("已写：%d 行（单行 UPDATE）" % len(rows))
    else:
        print("[dry] 将清 %d 行、其中 %d 行需补 note" % (len(rows), n_note))

    # 复核
    left = conn.execute(
        "SELECT COUNT(*) FROM platform_posts "
        "WHERE state != 'published' AND published_at IS NOT NULL AND published_at != ''"
    ).fetchone()[0]
    pub = conn.execute(
        "SELECT COUNT(*) FROM platform_posts WHERE state='published'").fetchone()[0]
    pub_no_pt = conn.execute(
        "SELECT COUNT(*) FROM platform_posts "
        "WHERE state='published' AND (published_at IS NULL OR published_at='')"
    ).fetchone()[0]
    print("复核 ① 非 published 带 published_at = %d（期望 0）" % left)
    print("复核 ② published 行数 = %d" % pub)
    print("复核 ③ published 但 published_at 为空 = %d（期望 0）" % pub_no_pt)
    conn.close()
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
