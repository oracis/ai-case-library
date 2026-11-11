#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2026-10-26 AI 核验（verify_case.py）对 4 条案例的口径纠正，追加进 metric_note。

为什么
------
这 4 条（arcads / ahron / campfire / build-concierge）当时入库写的 metric_note 已经
点出了「页面月收入栏可疑」，但只是含糊一句「口径待核」。AI 核验逐条把矛盾算到了
具体倍数上（arcads 差一个数量级、ahron 差 12 倍、build-concierge 的月收入是反推的）。
这些是读者最容易被误导的地方，必须写进案例本身 —— 留在 data/verifications.json
（gitignore）里等于没写。

做法：在原 metric_note 后面**追加**一段，原文一个字不删（可回查）。
幂等：已含标记「2026-10-26 AI 核验」的跳过。

用法
----
    python scripts/_oneoff/patch_metric_notes.py --dry
    python scripts/_oneoff/patch_metric_notes.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio                                                   # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")
MARK = "⚠ 2026-10-26 AI 核验"

PATCH = {
    "arcads": "页面「💰 月收入 ¥1.01亿」是**模板错标**：若真按月收入计，年化约 $180M，"
              "而同页 ARR 台阶只到 $15M（2024-06 $1M → 2025-05 $6M → 2025-11 $10M），"
              "差一个数量级；正文写的是「2026 年**营收** ¥1.01亿」，故本条按 annual 入库。"
              "另：站点页脚「收入数据经 Stripe / Paddle 验证」是站点级通用声明——本条原文里"
              "没有任何支付侧数据，收入实为 GetLatka 对 CEO Romain Torres 的访谈转述。",

    "ahron": "页面「💰 月收入 ¥753万」与正文「2026 年 8 月 ARR 突破 ¥753万」相差 **12 倍**"
             "（若真是月收入，年化应到 ¥9,000万级），本条按 arr 入库。"
             "该数字来自第三方 ARR 数据库估算、未经审计，页面自己写「只能作为量级参考」；"
             "页面打的「收入可验证 / 支付平台验证」徽标对本条不成立。",

    "campfire": "「月收入 ¥336万」撑不到 MRR：①未标统计时点，②未定义「经常性」——"
                "公司按年收费，该数可能是某月确认收入或平均数，③仅见聚合站转述，"
                "页面摘录里没有任何一手链接。另两条不能当收入用：「年内营收十倍增长」"
                "只给倍数不给基期绝对值；累计约 ¥1.08亿 是股权融资额（种子 ¥2,352万 + "
                "A 轮 ¥2.35亿 + B 轮 ¥4.37亿），绝不可折算成营收。",

    "build-concierge": "「月收入 ¥112.2万」是页面用 ¥1,344万 ÷ 12 反推出来的，正文并无月度披露；"
                       "且同段并存的「年化营业额接近 ¥266.4万」与 ¥1,344万 自相矛盾 —— "
                       "两个数都不能单独当结论。收入来源是创始人 LinkedIn 自述，"
                       "无第二方来源或支付侧数据佐证，故等级只到 founder。",
}


def main():
    dry = "--dry" in sys.argv
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    by_id = {c["id"]: c for c in cases}

    changed = []
    for cid, add in PATCH.items():
        c = by_id.get(cid)
        if not c:
            print("[!] 缺案例 %s" % cid)
            continue
        m = c.setdefault("metrics", {})
        note = m.get("metric_note") or ""
        if MARK in note:
            print("= %-17s 已有核验注记，跳过" % cid)
            continue
        m["metric_note"] = (note.rstrip() + "\n\n" + MARK + "（verify_case.py）：" + add).strip()
        changed.append((cid, len(note), len(m["metric_note"])))

    for cid, a, b in changed:
        print("+ %-17s metric_note %d → %d 字" % (cid, a, b))
    if dry:
        print("\n[dry] 未写盘。%d 条会被追加。" % len(changed))
        return 0
    if not changed:
        return 0
    atomicio.write_json(CASES, cases, indent=2)
    print("\n已写盘：%d 条，cases 共 %d 条" % (len(changed), len(cases)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
