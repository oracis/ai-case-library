#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计「核实等级有没有被来源撑住」，并把虚标的降回证据配得上的等级。

为什么需要它
------------
核实等级是给读者看的信任凭证：标「支付网关验证」，读者就以为我们看过 Stripe 后台。
但库里一度有 11 条案例标着 stripe / official，登记的来源却只有 press / review
（SaaSXtra、Steal What Works、NeoDrop 这类第三方拆解站），一个支付或官方页面都没有。

对一个以「数字可信」为卖点的库，这比漏一条案例严重得多：它不是保守，是替读者
完成了一次他们没授权我们做的信任背书。所以宁可被看成保守，也不能虚标。

判定规则在 scripts/verify_rules.py（`best_supported_level` / `evidence_gap`），
这里只负责遍历案例、报告、写回 —— 规则不在这里重写第二遍。

用法
----
    python scripts/audit_evidence.py            # 只报告，不改数据
    python scripts/audit_evidence.py --apply    # 降级写回（先备份，且留降级记录）

降级不留哑账：每条被降的案例都会多一个字段 ——

    "verification_downgraded": {
        "from": "stripe", "to": "partial",
        "reason": "…", "at": "2026-09-17", "tool": "audit_evidence.py"
    }

事后想知道「它原本标的是什么」查得到；想改回去也就删这一个字段的事。

依赖：仅 Python 标准库。不联网。
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

DATA = os.path.join(ROOT, "data")
CASES = os.path.join(DATA, "cases.json")
BACKUP_DIR = os.path.join(ROOT, "_backfill_backup")


def kinds_of(case):
    ks = []
    for s in (case.get("sources") or []):
        if isinstance(s, dict) and s.get("kind"):
            ks.append(str(s["kind"]))
    return ks


def audit(cases):
    """返回 [(case, 建议等级 or None, 理由)]。撑得住的不出现在结果里。"""
    out = []
    for c in cases:
        ks = kinds_of(c)
        if not ks:
            out.append((c, None, "一条来源都没登记 —— 连『口径待核』都撑不住，需人工重核"))
            continue
        to, reason = VR.evidence_gap(c.get("verification"), ks)
        if to is None and reason:
            out.append((c, None, reason))
        elif to:
            out.append((c, to, reason))
    return out


def main():
    ap = argparse.ArgumentParser(description="核实等级与来源证据的一致性审计")
    ap.add_argument("--apply", action="store_true",
                    help="把虚标的等级降回证据撑得住的那一档（默认只报告）")
    args = ap.parse_args()

    cases = json.load(open(CASES, encoding="utf-8"))
    today = datetime.now().strftime("%Y-%m-%d")
    hits = audit(cases)

    print("=" * 72)
    print("  核实等级审计：共 %d 条案例，%d 条登记等级高于来源证据"
          % (len(cases), len(hits)))
    print("=" * 72)

    auto, manual = [], []
    for c, to, reason in hits:
        if to is None:
            manual.append((c, reason))
            continue
        auto.append((c, to, reason))
        print("\n· %s（%s）" % (c.get("name") or c["id"], c["id"]))
        print("  %s → %s" % (VR.LEVEL_LABEL.get(c.get("verification"),
                                                c.get("verification")),
                             VR.LEVEL_LABEL.get(to, to)))
        print("  %s" % reason)

    if manual:
        print("\n以下 %d 条**不自动处理**（连最弱等级都撑不住，只能人工）：" % len(manual))
        for c, reason in manual:
            print("  · %s：%s" % (c["id"], reason))

    if not hits:
        print("\n没有虚标。每条案例标着的等级都有对应来源。")

    if not args.apply:
        if hits:
            print("\n（只报告。要写回就加 --apply，会先备份 data/cases.json）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR, "cases.json.bak-%s" % stamp)
    shutil.copy2(CASES, bak)
    print("\n已备份 → %s" % os.path.relpath(bak, ROOT))

    n = 0
    for c, to, reason in auto:
        c["verification_downgraded"] = {
            "from": c.get("verification"),
            "to": to,
            "reason": reason,
            "at": today,
            "tool": "scripts/audit_evidence.py",
        }
        c["verification"] = to
        c["updated_at"] = today
        n += 1

    json.dump(cases, open(CASES, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("已降级 %d 条并写回 data/cases.json" % n)
    print("（原始等级都留在各自的 verification_downgraded.from 里，可还原）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
