#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「官网首页」这个来源从 official 改成 homepage。

为什么
------
`verify_rules.first_hand_kinds()` 只认 stripe / official 两种 kind，一旦命中就
按政策把案例放进精品池。但 2026-10-26 AI 核验暴露了一个事实：howtomakemi 入库的
那批案例，收入数字**全都不在官网上**（arcads / ahron 官网没有收入披露，gistly 官网
连定价都不公开），官网只证明了「这个产品还活着」。

给这种页面挂 `official`，等于把 A 级证据借给了只有 D 级事实的条目 —— 也正是
`test_verify_rules` 里那条「有一手来源的必须都在精品池」断言失败的原因
（7 条 partial 案例被政策要求进精品池，但实际摆在实核池）。

改法：官网首页单独一种 kind = homepage（C 级、不算一手来源）。
`test_verify_rules` 的断言因此自动成立，且**不用放宽那条政策**。

用法
----
    python scripts/_oneoff/fix_homepage_kind.py --dry
    python scripts/_oneoff/fix_homepage_kind.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio                                                   # noqa: E402
import verify_rules as VR                                         # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")

# 只动这批：howtomakemi 2026-10-26 入库的 10 条。逐条列出来，避免扫全库时
# 把「官网确实披露了数字」的老案例一起改掉。
TARGETS = ["arcads", "instantly", "ahron", "fyxer", "heyreach", "campfire",
           "wispr-flow", "build-concierge", "gistly", "research-match"]


def main():
    dry = "--dry" in sys.argv

    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    by_id = {c["id"]: c for c in cases}

    changed = []
    for cid in TARGETS:
        c = by_id.get(cid)
        if not c:
            print("[!] 缺案例 %s" % cid)
            continue
        hit = False
        for s in (c.get("sources") or []):
            if isinstance(s, dict) and s.get("kind") == "official":
                s["kind"] = "homepage"
                s["label"] = (s.get("label") or "").replace("官网", "官网首页")
                hit = True
        if not hit:
            continue
        fh = VR.first_hand_kinds(c)
        tier, why = VR.default_case_tier(c)
        changed.append((cid, sorted(fh), tier, why, c.get("tier")))

    print("%-17s %-14s %-9s %s" % ("id", "一手来源", "政策档位", "理由"))
    for cid, fh, tier, why, old in changed:
        print("%-17s %-14s %-9s %s（库存 %s）"
              % (cid, ",".join(fh) or "-", tier, why, old))

    if dry:
        print("\n[dry] 未写盘。%d 条会被改。" % len(changed))
        return 0
    atomicio.write_json(CASES, cases, indent=2)
    print("\n已写盘：%d 条官网来源改为 homepage，cases 共 %d 条"
          % (len(changed), len(cases)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
