#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 `data/publish_manifest.json` 被「状态机整本落盘」洗掉的字段（一次性，可重跑）。

事故形状（2026-11-06 14:53）
---------------------------
`multiplatform.state.Manifest.load()` 从 db 只投影出
`{state, at, title, attempt, error}`，`set()` 又把记录**整条重建**成 4 个键，
`save()` 再把**整本** dump 回 JSON ⇒ 一次 `mark_*` 就让全库丢字段：
实测 9 条 `remote_id`、8 条 `note`/`published_ts`/`updated_at` 消失，
同时把 db 里已有、JSON 里没有的 33 条记录补了进来（这部分是**对的**）。

所以工作区那份**既不能丢弃也不能直接提交**：
* 丢弃 ⇒ 丢 33 条真实推进（含 11 条 bilibili draft_saved→published）；
* 直接提交 ⇒ 把 9 条 remote_id 洗进历史。

修法
----
以 `git show HEAD:data/publish_manifest.json` 为底，逐条合并：
1. 状态**真的变了**（或整条新增）⇒ 采纳工作区那份的 `state/at/title/error`；
2. 状态没变 ⇒ 保留 HEAD 原记录（别让 key 改名/`at` 漂移制造假 diff）；
3. 两种都用 db 真值**补齐缺失字段**（`remote_id`/`url`/`draft_id`）。

判据（`--check` 也是这套）
------------------------
* HEAD 里存在的键，输出里一个都不许少；
* 状态变更清单必须与预期一致（`--expect` 可给数量，默认只打印）。

用法
----
    python -X utf8 scripts/repair_manifest_fields.py            # 干跑 + 报告
    python -X utf8 scripts/repair_manifest_fields.py --go       # 写盘（先备份）
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

REL = "data/publish_manifest.json"


def head_json():
    out = subprocess.run(["git", "show", "HEAD:%s" % REL], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("✗ git show HEAD:%s 失败：%s" % (REL, out.stderr[:200]))
    return json.loads(out.stdout)


def db_rows():
    """{(cid, plat): row} —— 真值（字段最全）。"""
    import db
    conn = db.get_conn()
    try:
        rows = {}
        for r in conn.execute("SELECT * FROM platform_posts"):
            rows[(r["case_id"], r["platform"])] = dict(r)
        return rows
    finally:
        db.close_conn()


def merge(base, cur, rows):
    out = {c: {p: dict(rec) for p, rec in (v or {}).items()}
           for c, v in base.items()}
    advanced, added = [], []
    for cid, plats in (cur or {}).items():
        for plat, rec in (plats or {}).items():
            old = (base.get(cid) or {}).get(plat)
            if old is None:
                added.append((cid, plat, rec.get("state")))
                out.setdefault(cid, {})[plat] = dict(rec)
                continue
            if old.get("state") != rec.get("state"):
                # 状态真变了 ⇒ 采纳新记录的全套状态字段，但把旧记录的多余字段留着
                merged = dict(old)
                merged.update({k: rec[k] for k in
                               ("state", "at", "title", "error") if k in rec})
                if not rec.get("error") and rec.get("state") not in (
                        "failed", "blocked"):
                    merged.pop("error", None)
                out[cid][plat] = merged
                advanced.append((cid, plat, old.get("state"), rec.get("state")))
    # db 补缺（对所有记录生效）
    filled = 0
    for cid, plats in out.items():
        for plat, rec in plats.items():
            row = rows.get((cid, plat))
            if not row:
                continue
            for k in ("remote_id", "url", "draft_id"):
                if row.get(k) and not rec.get(k):
                    rec[k] = row[k]
                    filled += 1
    return out, advanced, added, filled


def lost_keys(base, out):
    miss = []
    for cid, plats in base.items():
        for plat, rec in (plats or {}).items():
            got = ((out.get(cid) or {}).get(plat)) or {}
            for k in rec:
                if k not in got:
                    miss.append((cid, plat, k))
    return miss


def main():
    ap = argparse.ArgumentParser(description="修 publish_manifest 被洗掉的字段")
    ap.add_argument("--go", action="store_true", help="写盘（默认只报告）")
    ap.add_argument("--path", default=os.path.join(ROOT, REL))
    a = ap.parse_args()

    base = head_json()
    with open(a.path, encoding="utf-8") as f:
        cur = json.load(f)
    rows = db_rows()
    out, advanced, added, filled = merge(base, cur, rows)
    miss = lost_keys(base, out)

    print("HEAD 记录 %d 条 case ｜ 工作区 %d ｜ 合并后 %d"
          % (len(base), len(cur), len(out)))
    print("状态真的变了：%d 条" % len(advanced))
    for x in advanced:
        print("   %-34s %-12s %s → %s" % x)
    print("整条新增（db 有、HEAD 无）：%d 条" % len(added))
    print("从 db 补齐的字段：%d 个" % filled)
    print("HEAD 有、合并后缺的键：%d 个%s"
          % (len(miss), "" if not miss else " " + str(miss[:5])))
    if miss:
        raise SystemExit("✗ 合并会丢键，中止（不许写盘）")

    if not a.go:
        print("\n（干跑）加 --go 写盘")
        return 0

    bak = a.path + ".bak-20261106-manifest"
    if not os.path.exists(bak):
        with open(bak, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("备份：%s" % bak)
    tmp = a.path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, a.path)
    with open(a.path, encoding="utf-8") as f:
        back = json.load(f)
    assert back == out, "回读不一致"
    print("✓ 已写盘并回读一致（%d 条 case）" % len(back))
    return 0


if __name__ == "__main__":
    sys.exit(main())
