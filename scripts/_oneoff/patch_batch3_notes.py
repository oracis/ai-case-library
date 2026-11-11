#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 6 条「有条件」案例的 AI 核验结论写回 cases.json。

纪律：原始 metric_note 一个字不删，只在末尾追加「⚠ 2026-10-26 AI 核验」段；
涉及**读法**的改动（时点、字段语义）另改对应字段，并在 note 里说明改了哪一处。

本轮 AI 核验抓到的实质问题（按严重度）：
1. `salesrobot` 的 MRR 没有时点：公司 2020-05 成立 + 「27 个月从 0 做到」⇒ 约 2022 年，
   不是 2026 现值。headline 必须带时点提示，否则会被当当前收入引用。
2. `jobright` 的 52 万是融资稿（2025-06）的用户数，官网自述 3,000,000+ trusted users，
   不同时点不可混用；且它是用户数不是付费数，字段名从 paying_customers 改为 users。
3. `sandra-ai` 的 ¥295.7万 出自 GetLatka **估算**，页面却挂「官方披露」徽章。
4. `serviceform` 的 ¥1.01亿 是 Seedtable 年营收估测区间下界，页面挂「收入可验证」徽章。
5. `outward` 三个数字三种口径（8 个月累计 / 2025 税表 7 位数 / 8 位数 ARR），
   页面把它们混在「💰月收入」栏里，还推出「约 ¥557.8万每月以上」这种数。
6. `workhero` 的 ARR 只出自招聘启事（Built In 职位页），我未见原文；
   Datanyze 估测 $2M 与自述 $500K 相差 4 倍，不构成交叉验证。

6 条的 caliber 判定与 AI 完全一致（annual/annual/mrr/arr/arr/arr），无一条需要改口径。

用法
----
    python scripts/_oneoff/patch_batch3_notes.py --dry
    python scripts/_oneoff/patch_batch3_notes.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")

TAIL = {
    "serviceform": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（annual），但页面口径三处不自洽 —— "
        "①「💰 月收入」栏写 ¥1.01亿，实为 Seedtable 对**年营收**估测区间（¥1.01亿–¥2.69亿）的下界；"
        "②该页同时挂「官方披露 / 收入可验证」徽章，与正文自认「第三方估测、偏乐观」直接矛盾；"
        "③页脚「收入数据经 Stripe / Paddle 等支付平台验证」对本条不成立，全文没有任何支付侧直读数据。"
        "本库按区间下界记 annual、等级维持 partial，不按徽章升级。"
    ),
    "salesrobot": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（mrr），但**时点必须标明** —— "
        "「27 个月从 0 做到 ¥33.6万 MRR」没有截止月份，公司 2020-05 成立，按 27 个月推约为 2022 年，"
        "不是 2026 年现值；headline 已改为带时点提示，引用时不要当当前收入。另两处纠偏："
        "①「白标代理计划：代理商月利润 ¥6.7万+」是渠道方收益，不是 SalesRobot 自身收入，不能计入 MRR；"
        "②官网 Pricing 只显示入口与折扣、未见具体金额，本库写的三档价格来自第三方转述。"
        "等级维持 partial（创始人自述经聚合站转述，无 Stripe 直读）。"
    ),
    "jobright": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（arr），三处要分清 —— "
        "①「💰 月收入 ¥3,360万」标签是错的，正文写的是 ARR，按月收入读会把规模放大 12 倍；"
        "②52 万是 2025-06 融资稿的用户规模，官网自述 3,000,000+ trusted users，两个时点不可混用"
        "（字段已从 paying_customers 改为 users 并注明来源）；"
        "③「美国月访问 500 万」是流量口径，不是收入或用户规模证据。"
        "等级维持 partial（ARR 与盈利性出自访谈与媒体转述，无财报或支付侧直读）。"
    ),
    "sandra-ai": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（annual），但**来源比页面标的弱一档** —— "
        "¥295.7万 实际出自 GetLatka **估算**（第三方估算站），页面却挂在「官方披露 / 公司或创始人自己公布」"
        "徽章下，两者矛盾；「约 ¥24.9万/月 run rate」是把年额除以 12 的折算值，不是观测到的 MRR，故未写进 metrics。"
        "level 维持 partial（估算值 + 聚合站转述）。"
    ),
    "outward": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（arr），但这页把**三种不同口径**混在一栏 —— "
        "①「商业化后 8 个月收入突破 ¥672万」是阶段性累计收入（页面把它填进「💰 月收入」栏，属误标）；"
        "②「查验 2025 年税表验证 7 位数收入」是 2025 自然年已发生收入（annual 口径，只有量级、无具体数字）；"
        "③「8 位数 ARR（超 ¥6,720万）」才是前瞻性年经常性收入。三者不可互相换算，本库只采用 ③ 且取下界 $10.0M。"
        "另：页脚「经支付平台验证」对本条不成立（来源是 Business Insider 报道 + 其称查验的税表），等级维持 partial。"
    ),
    "workhero": (
        "\n\n⚠ 2026-10-26 AI 核验（verify_case.py）：口径判定一致（arr），证据链比页面标的薄 —— "
        "①「💰 月收入 ¥336万」标签错，正文是「数月内做到 ¥336万 ARR」，按月收入读会放大 12 倍；"
        "②该 ARR 唯一出处是公司自己的招聘启事（Built In 职位页），本轮只拿到聚合站转述、未见招聘页原文；"
        "③Datanyze 估测年营收约 ¥1,344万（≈$2M）与自述 $500K ARR 不同源且相差约 4 倍，**不构成交叉验证**。"
        "产品与在营状态可核实，收入只能算部分核实，等级维持 partial。"
    ),
}

HEADLINE = {
    "salesrobot": ("MRR 约 ¥33.6万（≈$50K，页面未标时点；按 2020-05 成立 + 27 个月推约 2022 年），"
                   "4100+ 用户、110+ 家白标代理，bootstrapped"),
}
GROWTH = {
    "salesrobot": "27 个月从 0 到 MRR ¥33.6万（创始人自述，未标截止月份；约 2022 年）",
}


def main():
    dry = "--dry" in sys.argv
    with open(CASES, encoding="utf-8") as f:
        data = json.load(f)
    by_id = {c["id"]: c for c in data}
    for cid, tail in TAIL.items():
        c = by_id.get(cid)
        if not c:
            print("[!] 缺 %s" % cid)
            continue
        m = c["metrics"]
        note = m.get("metric_note") or ""
        if "AI 核验" in note:
            print("  = %-12s 已有核验段，跳过" % cid)
            continue
        m["metric_note"] = note + tail
        if cid in HEADLINE:
            print("  ~ %-12s headline: %s" % (cid, HEADLINE[cid][:50]))
            m["headline"] = HEADLINE[cid]
        if cid in GROWTH:
            m["growth"] = GROWTH[cid]
        if cid == "jobright" and "paying_customers" in m:
            m["users"] = m.pop("paying_customers")
            print("  ~ %-12s paying_customers → users" % cid)
        print("  + %-12s note +%d 字" % (cid, len(tail)))
    if dry:
        print("[dry] 未写盘")
        return 0
    atomicio.write_json(CASES, data, indent=2)
    print("已写入 %s" % CASES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
