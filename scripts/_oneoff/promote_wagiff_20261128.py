#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Wagiff 已发侧定点补账（2026-11-28）。

线上已发表列表 + 公开页双回读确认：wagiff-app 已于 2026-11-27 10:15 发表，
远端 id=54107760（cv54107760 → opus 1275207182157086729）。原台账误记 draft。

⛔ 不走 bili_published_reconcile.py --apply：它的 L.save() 会把 JSON 合并视图
   整本回写并同步 db，实测会把 ~40 条已发行的 draft_id 全部清空（字段洗）。
本脚本只改两本 B站 JSON 中的 wagiff-app 一条 + db 单行 UPDATE，写前备份。

用法：python scripts/_oneoff/promote_wagiff_20261128.py [--apply]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ledger as L  # noqa: E402

CID = "wagiff-app"
OLD_ID = "343836"
NEW_ID = "54107760"
TITLE = "Wagiff：中东北非男性的戒断社区"
PUB_AT = "2026-11-27 10:15"
NOTE = ("｜草稿同步(2026-10-14 13:32): title 认领 aid=343836"
        "｜11-28 对账：线上已发表 54107760（公开页 2026年11月27日 10:15 + 标题/正文命中），"
        "原记 draft 是昨天 toast 假成功遗留 ⇒ 改 published，不再重发")
DRAFTS = os.path.join(ROOT, "data", "bili_drafts.json")
PUBS = os.path.join(ROOT, "data", "bili_published.json")
DB = os.path.join(ROOT, "data", "ledger.db")


def load_raw(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_like(path, rows):
    _, _, nl, ind = L._read_file(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=ind).replace("\n", nl))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    drafts = load_raw(DRAFTS)
    pubs = load_raw(PUBS)
    d_before = [r for r in drafts if r.get("id") == CID]
    p_before = [r for r in pubs if r.get("id") == CID]
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("select * from platform_posts where case_id=? and platform='bilibili'",
                       (CID,)).fetchone()
    print("BEFORE drafts=%d pubs=%d db=%s" % (
        len(d_before), len(p_before), dict(row) if row else None))
    if not a.apply:
        print("干跑，加 --apply 才写盘")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    for p in (DRAFTS, PUBS, DB):
        shutil.copy2(p, os.path.join(ROOT, "tmp", "backup_%s_%s" % (os.path.basename(p), ts)))
    print("备份后缀 %s" % ts)

    drafts = [r for r in drafts if r.get("id") != CID]
    pubs = [r for r in pubs if r.get("id") != CID]
    pubs.append({
        "id": CID,
        "at": PUB_AT,
        "article_id": NEW_ID,
        "verified_at": PUB_AT,
        "title": TITLE,
        "evidence": "published_reconcile",
        "note": NOTE,
    })
    dump_like(DRAFTS, drafts)
    dump_like(PUBS, pubs)

    cur = conn.execute(
        "update platform_posts set state='published', remote_id=?, draft_id='', "
        "url=?, published_at=?, updated_at=?, note=? "
        "where case_id=? and platform='bilibili'",
        (NEW_ID, "https://www.bilibili.com/read/cv" + NEW_ID, PUB_AT, PUB_AT, NOTE, CID),
    )
    conn.commit()
    print("db changed=%d" % cur.rowcount)
    after = conn.execute("select * from platform_posts where case_id=? and platform='bilibili'",
                         (CID,)).fetchone()
    print("AFTER db=", dict(after))
    print("AFTER drafts=%d pubs=%d" % (
        len([r for r in load_raw(DRAFTS) if r.get("id") == CID]),
        len([r for r in load_raw(PUBS) if r.get("id") == CID])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
