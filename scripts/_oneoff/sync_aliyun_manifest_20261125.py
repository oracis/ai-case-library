#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把阿里云 6 条的真值从 db 同步进 publish_manifest.json（单键合并）。

背景：`yun_state.record_publish()` 只写 db（真值），manifest 是镜像，需要单独补。
⛔ 不调 `Manifest.save()`：它按 db 覆盖 state/at 并整本落盘，会带出全表回退（PRD #203）。
这里只「读 → 改 6 个子键 → 原样写回」，indent=1 + 无尾换行，写完回读校验。

用法::

    python scripts/_oneoff/sync_aliyun_manifest_20261125.py          # 干跑
    python scripts/_oneoff/sync_aliyun_manifest_20261125.py --yes    # 落库
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402
import db        # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
CASES = ["upvoty", "ext-ep-007-openttd", "ext-ep-009-lichess",
         "stan", "autoreels-ai", "flodesk"]


def db_rows():
    conn = db.get_conn()
    cur = conn.cursor()
    q = ",".join("?" * len(CASES))
    cur.execute("select case_id,state,remote_id,title,url,published_at,draft_id "
                "from platform_posts where platform='aliyun' and case_id in (%s)" % q,
                CASES)
    cols = [c[0] for c in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    rows = db_rows()
    with open(MANIFEST, encoding="utf-8") as f:
        before = json.load(f)
    after = json.loads(json.dumps(before))

    for case in CASES:
        r = rows.get(case)
        if not r:
            print("%-22s ✗ db 无行，跳过" % case)
            continue
        if r["state"] != "published":
            print("%-22s db state=%s，跳过（只同步 published）" % (case, r["state"]))
            continue
        node = after.setdefault(case, {})
        if not isinstance(node, dict):
            raise SystemExit("✗ manifest 里 %s 不是对象，停手人工看" % case)
        prev = node.get("aliyun")
        entry = dict(prev) if isinstance(prev, dict) else {}
        entry.update({
            "state": "published",
            "remote_id": r["remote_id"],
            "title": r["title"],
            "url": r["url"],
            "at": r["published_at"],
            "draft_id": r["draft_id"] or entry.get("draft_id", ""),
            "published_at": r["published_at"],
            "attempt": entry.get("attempt", 0),
        })
        node["aliyun"] = entry
        print("%-22s %-8s %s" % (case, r["remote_id"], r["published_at"]))

    changed = []
    for cid in set(before) | set(after):
        b = before.get(cid) or {}
        aa = after.get(cid) or {}
        for plat in set(b) | set(aa):
            if b.get(plat) != aa.get(plat):
                changed.append("%s.%s" % (cid, plat))
    lost = [k for k in before if k not in after]
    print("\n变更项:", sorted(changed))
    print("丢键:", lost)
    bad = [c for c in changed if c.split(".")[0] not in CASES or not c.endswith(".aliyun")]
    if lost or bad:
        raise SystemExit("✗ 变更超出预期范围（%s），停手" % (lost or bad))

    if not a.yes:
        print("（干跑）确认无误后加 --yes")
        return 0

    with atomicio.file_lock(MANIFEST + ".lock"):
        atomicio.write_text(MANIFEST, json.dumps(after, ensure_ascii=False, indent=1))
    with open(MANIFEST, encoding="utf-8") as f:
        back = json.load(f)
    print("\n条目总数 before/after:", len(before), len(back))
    need_dash = b"-\r\n" not in open(MANIFEST, "rb").read()[:0]   # 占位，不做判断
    with open(MANIFEST, "rb") as f:
        raw = f.read()
    print("尾部字节:", raw[-6:], "| 有尾换行:", raw.endswith(b"\n"))
    for case in CASES:
        got = (back.get(case) or {}).get("aliyun") or {}
        want = rows.get(case) or {}
        print("  %-22s state=%s remote=%s at=%s" % (
            case, got.get("state"), got.get("remote_id"), got.get("at")))
        if want.get("state") == "published" and (
                got.get("state") != "published"
                or got.get("remote_id") != want.get("remote_id")):
            print("     ✗ 回读不符")
            return 1
    print("✓ manifest 已同步")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
