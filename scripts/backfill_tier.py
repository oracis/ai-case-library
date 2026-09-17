#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已发布案例补默认档位（tier）。

为什么需要
----------
`tier` 是后来才加进案例的字段，之前发布的案例大多没有它。而后台把
「没有 tier」显示成「备选」（admin/app.js：`c.tier || 'backup'`），
于是精品池看起来只有一两条 —— 但那些案例**根本没被评过档**，不是被判过档。
这是两回事，混在一起会让人以为是规则把大家刷下去了。

政策
----
见 verify_rules.default_case_tier：来源里有一手证据（stripe / official）
的案例默认进精品池 —— 一手来源是最强的可信度信号。没有一手来源的进备选池
（不是淘汰，是「还需要再核」）。

顺手补一个 `tier_reason`：原先案例上只有一个 quality_score 数字，
事后看不出「凭什么给它这个档」。写清理由，这条数据才可审计。

用法
----
    python scripts/backfill_tier.py --dry-run   # 先看会改什么
    python scripts/backfill_tier.py             # 实际写入（幂等）

依赖：仅 Python 标准库。
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules as VR                                              # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")


def main():
    ap = argparse.ArgumentParser(description="给已发布案例补默认档位")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--force", action="store_true",
                    help="连已有 tier 的也重算（默认只补缺的）")
    args = ap.parse_args()

    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)

    print("=" * 68)
    print("  案例默认定档 · 政策见 verify_rules.default_case_tier")
    print("=" * 68)
    print("  %-20s %-9s → %-9s %s" % ("id", "原档", "新档", "理由"))
    print("  " + "-" * 66)

    changed, skipped = 0, 0
    counts = {}
    for c in cases:
        if c.get("tier") and not args.force:
            skipped += 1
            counts[c["tier"]] = counts.get(c["tier"], 0) + 1
            continue
        tier, reason = VR.default_case_tier(c)
        old = c.get("tier") or "(无)"
        if old != tier:
            changed += 1
        c["tier"] = tier
        c["tier_reason"] = reason
        counts[tier] = counts.get(tier, 0) + 1
        mark = " " if old == tier else "*"
        print("%s %-20s %-9s → %-9s %s" % (mark, c.get("id", "")[:20], old, tier, reason))

    print()
    print("  %d 条中：改动 %d 条，跳过（已有档位）%d 条" % (len(cases), changed, skipped))
    print("  定档结果：精品池 %d 条 · 备选池 %d 条"
          % (counts.get(VR.TIER_PREMIUM, 0), counts.get(VR.TIER_BACKUP, 0)))

    if args.dry_run:
        print("\n--dry-run：未写入。")
        return 0

    bak_dir = os.path.join(ROOT, "_backfill_backup")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, "cases.json.bak-%s"
                       % datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(CASES, bak)
    with open(CASES, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("\n  已写入 data/cases.json（备份：%s）" % os.path.basename(bak))
    return 0


if __name__ == "__main__":
    sys.exit(main())
