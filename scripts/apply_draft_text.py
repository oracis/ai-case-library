#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把暂存区的起草正文写回候选池（**过了 content_gate 才写**）。

这是 draft_case_text.py 的下半段。为什么分两步：
    起草是**推断**，入库是**承诺**。中间留一道门，才能把「模型觉得」和
    「核过」分开 —— 而整个库卖的就是后者。

写回前必过的两道检查：
    1. `_needs_human` 为真的跳过（机器守卫没放行的，交人工改）。
       想强制写入要显式给 --include-flagged，并且会打印警告。
    2. `verify_rules.content_gate()`：正文四硬字段（one_liner / what_it_does /
       how_it_makes_money / verdict）长度与存在性。不过就不写。

格式：candidates.json 是 indent=2 + CRLF + 无末尾换行（与 cases.json 的
LF 不同！）。写回后做字节级自检，还原不成功直接中止。

用法：
    python scripts/apply_draft_text.py --dry            # 只看会写哪些
    python scripts/apply_draft_text.py                  # 正式写回
    python scripts/apply_draft_text.py --ids a,b,c
    python scripts/apply_draft_text.py --include-flagged # 连没过守卫的也写（不推荐）
"""

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules  # noqa: E402

CAND = os.path.join(ROOT, "data", "candidates.json")
STAGE = os.path.join(ROOT, "tmp", "draft_text.json")

FIELDS = ("one_liner", "what_it_does", "how_it_makes_money", "verdict",
          "why_it_works", "playbook", "replicability")


def load(path, default):
    if not os.path.exists(path):
        return default
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return json.loads(f.read())


def main():
    ap = argparse.ArgumentParser(description="起草正文写回候选池")
    ap.add_argument("--ids", help="逗号分隔；不给则处理暂存区全部")
    ap.add_argument("--include-flagged", action="store_true",
                    help="连 _needs_human 的也写（不推荐）")
    ap.add_argument("--dry", action="store_true", help="只报告，不写盘")
    args = ap.parse_args()

    stage = load(STAGE, {})
    cands = load(CAND, [])
    if not stage:
        print("[!] 暂存区为空：先跑 scripts/draft_case_text.py")
        return 1
    by_id = {c.get("id"): c for c in cands}

    wanted = ([x.strip() for x in args.ids.split(",") if x.strip()]
              if args.ids else list(stage))

    written, gate_fail, flagged, missing = [], [], [], []
    for cid in wanted:
        d = stage.get(cid)
        if not d:
            continue
        rec = by_id.get(cid)
        if rec is None:
            missing.append(cid)
            continue

        problems = d.get("_problems") or []
        if problems and d.get("_needs_human") and not args.include_flagged:
            flagged.append((cid, problems))
            continue

        # 先落字段再做闸门检查：闸门看的是合并后的结果
        for k in FIELDS:
            v = d.get(k)
            if v not in (None, "", []):
                rec[k] = v

        gate = verify_rules.content_gate(rec)
        if not gate["ok"]:
            gate_fail.append((cid, gate["verdict"]))
            continue
        written.append(cid)

    print("暂存区 %d 条｜候选池 %d 条" % (len(stage), len(cands)))
    print("\n== 可写入 %d 条 ==" % len(written))
    for c in written:
        print("  ✓ %s" % c)
    if flagged:
        print("\n== 跳过：机器守卫没放行（%d 条，需人工改）==" % len(flagged))
        for c, p in flagged:
            print("  ⚠ %-30s %s" % (c[:30], "；".join(p)[:70]))
    if gate_fail:
        print("\n== 跳过：content_gate 不过（%d 条）==" % len(gate_fail))
        for c, v in gate_fail:
            print("  ✗ %-30s %s" % (c[:30], v[:70]))
    if missing:
        print("\n== 跳过：候选池里没有这个 id（%d 条）==" % len(missing))
        print("  " + "、".join(missing[:10]))

    if args.dry:
        print("\n--dry：未写入")
        return 0
    if not written:
        print("\n没有可写入的条目，未改动候选池")
        return 0

    s = json.dumps(cands, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with io.open(CAND, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    # 字节级自检：candidates.json 用 CRLF，写错就是全量 diff
    with io.open(CAND, "r", encoding="utf-8", newline="") as f:
        back = f.read()
    if back != s:
        raise SystemExit("[!] 写回格式与预期不一致，已中止")
    print("\n已写入 data/candidates.json（%d 条）" % len(written))
    print("下一步：python scripts/promote_ready.py --ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
