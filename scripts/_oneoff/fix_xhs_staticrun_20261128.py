#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小红书 static-run 发布后台账规整（2026-11-28）。

publish_one 发出后只在 xhs_published.json 写了 id+at，草稿行没移出、
note_id/title 也没回填 ⇒ ledger_health 报 DUP + NO_ID。
本脚本按创作中心已发布列表标题唯一命中补 note_id/title，并把草稿行移出。

用法（同一条前台命令内起 9223 + 本脚本）：
    python scripts/_launch_9223.py --hold 180 &
    python scripts/_oneoff/fix_xhs_staticrun_20261128.py [--apply]
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
import xhs_webapi as X  # noqa: E402

CID = "static-run"
DRAFTS = os.path.join(ROOT, "data", "xhs_drafts.json")
PUBS = os.path.join(ROOT, "data", "xhs_published.json")
DB = os.path.join(ROOT, "data", "ledger.db")
NOTE = "2026-11-28 10:57 publish_one FIFO 发出；回读命中已发布列表首条，note_id 来自 user/posted 接口"


def dump_like(path, rows):
    _, _, nl, ind = L._read_file(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=ind).replace("\n", nl))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cdp, _ = X.connect(9223)
    notes, _ = X.posted_notes(pages=3, cdp=cdp)
    hits = [n for n in notes if "Static.run" in (n.get("title") or "")]
    print("线上命中 %d 条" % len(hits))
    if len(hits) != 1:
        raise SystemExit("标题非唯一命中，不写盘：%s" % [n.get("title") for n in hits[:5]])
    nid, otitle = str(hits[0]["note_id"]), hits[0]["title"]
    print("note_id=%s title=%s" % (nid, otitle))

    drafts = json.load(open(DRAFTS, encoding="utf-8"))
    pubs = json.load(open(PUBS, encoding="utf-8"))
    print("草稿侧 %d 条 / 已发侧 %d 条" % (
        sum(1 for r in drafts if r.get("id") == CID),
        sum(1 for r in pubs if r.get("id") == CID)))
    if not a.apply:
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    for p in (DRAFTS, PUBS, DB):
        shutil.copy2(p, os.path.join(ROOT, "tmp", "backup_%s_%s" % (os.path.basename(p), ts)))
    print("备份后缀 %s" % ts)

    drafts = [r for r in drafts if r.get("id") != CID]
    for r in pubs:
        if r.get("id") == CID:
            r["note_id"] = nid
            r["title"] = otitle
            r["note"] = NOTE
    dump_like(DRAFTS, drafts)
    dump_like(PUBS, pubs)

    conn = sqlite3.connect(DB)
    row = conn.execute("select * from platform_posts where case_id=? and platform='xiaohongshu'",
                       (CID,)).fetchone()
    if row:
        conn.execute("update platform_posts set state='published', remote_id=?, title=?, "
                     "published_at=?, updated_at=?, note=? where case_id=? and platform='xiaohongshu'",
                     (nid, otitle, "2026-11-28 10:57", "2026-11-28 10:57", NOTE, CID))
        print("db UPDATE 1 行")
    else:
        print("db 无该行（小红书真值在 JSON，跳过）")
    conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
