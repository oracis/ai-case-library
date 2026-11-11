#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autoshorts：把「28000+ 付费创作者」从断言改成累计口径，并把自洽性核算写进 metric_note。

起因：写第一篇长文稿时把两个公开数字放在一起算了一遍 ——
  最低档 $19/月 × 28,000 = $532K/月，是公开 MRR（$103K）的五倍；
  反过来按 $19 底价推，$103K 只装得下约 5,400 个在订席位。
结论：28,000 只能是**累计**付过费/注册过的创作者数，不能当「当前订阅数」写进一句话。

处理：
1. `one_liner` / `metrics.headline` 去掉「28000+ 付费创作者」这个断言，改写成
   与 MRR 自洽的表述（累计 250 万+ 条视频）。
2. `metrics.metric_note` 追加「⚠ 口径自洽性核算」段，保留原始页面文案不动。
3. `metrics.paying_customers` 改名语义：保留字段但降级为 `creators_cumulative`，
   避免下游把累计数当当前付费数做除法。

用法
----
    python scripts/_oneoff/patch_autoshorts_claims.py --dry
    python scripts/_oneoff/patch_autoshorts_claims.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")

ONE_LINER = ("一个人做的「不露脸短视频」生成 + 自动发布订阅，MRR 约 ¥69.2万（≈$103K），"
             "零融资、每天只干 3–4 小时")
HEADLINE = ("MRR 约 ¥69.2万（≈$103K，2026-08），一个人、零融资；"
            "累计生成 250 万+ 条视频")
EXTRA = (
    "\n\n⚠ 2026-10-26 口径自洽性核算（本人，非官方口径）：页面「28,000+ 付费创作者」与 "
    "MRR ¥69.2万 放在一起算不通 —— 最低档 $19/月，若真有 2.8 万人在订，月收入应为 $532K 上下，"
    "是公开 MRR 的五倍；反过来按 $19 底价推，$103K 只装得下约 5,400 个在订席位。"
    "故 28,000 只能是**累计**付过费或累计注册的创作者数，不可当「当前订阅数」，"
    "更不可用来对 MRR 做除法。headline 已按此改写，`metrics.creators_cumulative` 保留原数备查。"
)


def main():
    dry = "--dry" in sys.argv
    with open(CASES, encoding="utf-8") as f:
        data = json.load(f)
    c = next((x for x in data if x["id"] == "autoshorts"), None)
    if not c:
        print("没有 autoshorts，退出")
        return 1
    m = c["metrics"]
    print("改前 one_liner:", c["one_liner"])
    print("改前 headline :", m.get("headline"))
    print("改前 paying   :", m.get("paying_customers"))
    c["one_liner"] = ONE_LINER
    m["headline"] = HEADLINE
    if "paying_customers" in m:
        m["creators_cumulative"] = m.pop("paying_customers")
    if "口径自洽性核算" not in (m.get("metric_note") or ""):
        m["metric_note"] = (m.get("metric_note") or "") + EXTRA
    print("改后 one_liner:", c["one_liner"])
    print("改后 headline :", m["headline"])
    print("改后 creators :", m.get("creators_cumulative"))
    if dry:
        print("[dry] 未写盘")
        return 0
    atomicio.write_json(CASES, data, indent=2)
    print("已写入 %s" % CASES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
