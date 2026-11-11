#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复（2026-10-13）：把 14 条「TrustMRR 收录（收入未公开）」的假阳性
写成真相。

背景：triage 把这批报成 backfill（「先跑零成本补数」），但逐个查 TrustMRR
详情页后确认 —— 数字不是没抓到，**验证收入就是 0**（Current MRR = 0、
近 30 天 = 0）。根因是 merge_record() 只合非零值，headline 永远停在
「收入未公开」，于是每轮初筛都重报一遍。

处理：
  · 13 条（详情页可查、验证收入为 0）→ headline 改成
    「TrustMRR 验证：近 30 天收入 $0（累计 $X）」。累计值照实附在后面，
    明确标注是历史值，不当当月收入用。
    这样 triage.has_numbers() 认它有数字（不再报 backfill），
    revenue_of() 解析出 0 → tiny_revenue → 落到 later。
  · 1 条 slurp（详情页 404，已下榜/改 slug，无从验证）→ 不编造 $0，
    改用人工挂起标记 hold=true，triage 对挂起条目判 later。

只改 headline / 加 hold 两个字段，不动其他任何数据。写盘走
harvest.save_json()，保留各文件原换行格式（inbox=CRLF，archive=LF）。
可重复执行（幂等）：已是新 headline 的记录会跳过。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest as H                                          # noqa: E402

# id -> 累计收入（美元）。没有累计或累计为 0 的不附注。
ALL_TIME = {
    "steamyy": 247.61,
    "what-s-the-app": 13582.30,
    "clevia": 126.35,
    "scopira": 1085.0,
    "lutains": 349.90,
}

# 13 条验证收入为 0 的条目（12 条在 inbox，clevia/pman-ai 在本轮被归档）
ZERO_IDS = [
    "steamyy", "orunvi", "physique-forecast", "what-s-the-app",
    "clevia", "pman-ai",
    "scopira", "exitfounder-io", "motion-spirit", "lutains",
    "passpadi", "iloveframes", "pullar",
]

# 详情页 404 的条目：不写 $0（无从验证），改挂起
HOLD_IDS = {
    "slurp": "TrustMRR 详情页 404（疑下榜或改 slug），收入无从验证，暂不推进",
}

OLD_MARK = "收入未公开"


def money(v):
    if v == int(v):
        return "$%s" % format(int(v), ",")
    return "$%s" % format(round(v, 2), ",.2f")


def new_headline(rid):
    at = ALL_TIME.get(rid)
    if at:
        return "TrustMRR 验证：近 30 天收入 $0（累计 %s，历史值）" % money(at)
    return "TrustMRR 验证：近 30 天收入 $0"


def fix_file(name):
    path = os.path.join(H.DATA_DIR, name + ".json")
    if not os.path.exists(path):
        print("[i] 没有 %s，跳过" % path)
        return 0
    records = H.load_json(name, []) or []
    if not isinstance(records, list):
        print("[!] %s 不是列表，跳过" % name)
        return 0

    touched = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if rid in ZERO_IDS:
            m = rec.setdefault("metrics", {})
            hl = m.get("headline") or ""
            if hl == new_headline(rid):
                continue                                # 已是真相，幂等跳过
            if OLD_MARK not in hl:
                print("   · %-18s [跳过] headline 不是预期旧值：%r" % (rid, hl))
                continue
            m["headline"] = new_headline(rid)
            touched += 1
            print("   · %-18s headline → %s" % (rid, m["headline"]))
        elif rid in HOLD_IDS:
            if rec.get("hold") is True:
                continue                                # 已挂起，幂等跳过
            rec["hold"] = True
            rec["hold_reason"] = HOLD_IDS[rid]
            touched += 1
            print("   · %-18s 挂起：%s" % (rid, HOLD_IDS[rid]))

    print("[*] %s：%d 条改动" % (name, touched))
    if touched:
        H.save_json(name, records)
        print("    已写入 data/%s.json（保留原换行格式）" % name)
    return touched


def main():
    print("=" * 64)
    print("  零收入假阳性修复（13 条 headline + 1 条挂起）")
    print("=" * 64)
    total = fix_file("inbox") + fix_file("inbox_archive")
    print()
    print("完成：共 %d 条已更新。" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
