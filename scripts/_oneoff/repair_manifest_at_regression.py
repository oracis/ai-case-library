#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修回 manifest 被「整本落盘」洗掉的真值（2026-11-20）。

背景：`Manifest.save()` 落盘前 `load()` 会用 db 覆盖 `state` / `at`，于是
一次无关的 `set()` 就把全表按 db 重刷了一遍：
  · `at` 被换成 db 的 `updated_at`（有的比真发布日早一个月）；
  · `shipfast/csdn` 从 published 退成 draft（db 行丢了 remote_id，
    HEAD 里有 `公开页 200 标题命中` + remote_id 170193150）。
其余状态升级（90 条）是真推进，保留不动。

本脚本只做两件事，基准备份是 `git show HEAD:data/publish_manifest.json`：
  1) db 里 shipfast/csdn 恢复 published + remote_id/url/published_at
  2) manifest 里「state 没变、at 却回退」的行，把 at 还原成 HEAD 的值
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sqlite3                                        # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
DB = os.path.join(ROOT, "data", "ledger.db")
FIX_PUB = [("shipfast", "csdn")]


def head_manifest():
    raw = subprocess.check_output(
        ["git", "show", "HEAD:data/publish_manifest.json"], cwd=ROOT)
    return json.loads(raw.decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    base = head_manifest()
    cur = json.load(open(MANIFEST, encoding="utf-8"))

    # ---- 1) at 回退（state 未变）------------------------------------
    restore = []
    for cid, plats in base.items():
        for plat, b in (plats or {}).items():
            c = (cur.get(cid) or {}).get(plat)
            if not c:
                continue
            if c.get("state") != b.get("state"):
                continue
            ba, ca = str(b.get("at") or ""), str(c.get("at") or "")
            if ba[:16] and ca[:16] and ca[:16] < ba[:16]:
                restore.append((cid, plat, ca[:16], ba[:16]))
    print("at 回退 %d 条（state 未变）：" % len(restore))
    for cid, plat, ca, ba in restore:
        print("   %-30s %-13s %s → %s" % (cid[:30], plat, ca, ba))

    # ---- 2) shipfast/csdn 真值恢复 ----------------------------------
    fix_rows = []
    for cid, plat in FIX_PUB:
        b = (base.get(cid) or {}).get(plat) or {}
        c = (cur.get(cid) or {}).get(plat) or {}
        if b.get("state") == "published" and c.get("state") != "published":
            fix_rows.append((cid, plat, b, c))
    print("\n真值恢复 %d 条：" % len(fix_rows))
    for cid, plat, b, c in fix_rows:
        print("   %s/%s %s(rid=%s) → published(rid=%s)"
              % (cid, plat, c.get("state"), c.get("remote_id"),
                 b.get("remote_id")))

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 0

    for cid, plat, ca, ba in restore:
        cur[cid][plat]["at"] = base[cid][plat]["at"]

    for cid, plat, b, c in fix_rows:
        rec = dict(c)
        for k in ("state", "remote_id", "url", "title", "at", "error"):
            if b.get(k) is not None:
                rec[k] = b[k]
        cur.setdefault(cid, {})[plat] = rec
        conn = sqlite3.connect(DB)
        try:
            conn.execute(
                "UPDATE platform_posts SET state='published', remote_id=?, "
                "url=?, published_at=?, updated_at=?, "
                "note=COALESCE(NULLIF(note,''), ?) "
                "WHERE case_id=? AND platform=?",
                (b.get("remote_id") or "", b.get("url") or "",
                 b.get("at") or "", b.get("at") or "",
                 b.get("error") or "", cid, plat))
            conn.commit()
            print("db 恢复 %s/%s → %s" % (
                cid, plat, list(conn.execute(
                    "SELECT state,remote_id FROM platform_posts "
                    "WHERE case_id=? AND platform=?", (cid, plat)))))
        finally:
            conn.close()

    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST)
    print("manifest 已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
