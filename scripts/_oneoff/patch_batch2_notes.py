#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2026-10-26 AI 核验（verify_case.py）对 batch2 三条的纠正写进案例。

三件事：
1. `boredhumans` 删掉 `metrics.mrr` —— 页面那个「月收入 ¥492.6万」只是 ARR÷12 的
   算术折算，不是独立测得的 MRR。按本库口径纪律（见 MEMORY：非订阅制/推算值不许
   混入口径），它只能留在 metric_note 里当「折算说明」，不能当独立数字摆出来。
   （同一个坑 2026-10-26 在 build-concierge 上已经踩过一次。）
2. 三条的 metric_note 追加核验结论（原文一个字不删）。
3. 幂等：已含标记的跳过；`mrr` 已经删掉的不会重复报错。

用法
----
    python scripts/_oneoff/patch_batch2_notes.py --dry
    python scripts/_oneoff/patch_batch2_notes.py
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
    "autoshorts": "①同一页并存两个当前值：头部「MRR ¥69.2万」与一句话总结/营收模式栏"
                  "「¥67.2万 / 0 到 ¥67,200 MRR」——本库取头部 ¥69.2万（≈$103K），差额属页面折算不一致；"
                  "②早期台阶（¥6,720 / 2000 / 2万 / 4万）是币种混写，只能当量级，不能用来算增速；"
                  "③$103K 是按 6.72 倒算的，不是页面直读的美元数；"
                  "④数据来源是创始人自报 + Startup Founder Stories / MRR Story 等二手转述，"
                  "没有任何 Stripe 直读页面或流水截图，站点页脚「经支付平台验证」对本条不成立。",

    "rosie": "页面数据卡把 ¥672万 标成「💰 月收入」，实为 **ARR**（若按月收入计，年化会差 12 倍），"
             "本条按 arr 入库；按 ARR 折算月均约 $83K，属推算值，原文没有给出任何月度实际收入。"
             "数字出自「第三方 ARR 数据库记录」经聚合站转述，无公司公告、财报或创始人一手链接，"
             "站点「收入可验证 / 官方披露」徽标对本条不成立。",

    "boredhumans": "①月值（≈$733K）只是 ARR÷12 的算术折算，不是独立测得的 MRR —— "
                   "本条只保留 arr，不单列 mrr；②全页仅「公开报道给出的数字是……」一句，"
                   "没有报告名、没有链接、没有统计时点；③官网自述「100% free AI tools… "
                   "No sign-up required」，未见任何付费档或定价页——「展示广告 + 高级订阅」的"
                   "变现结构同样只有聚合站转述，无一手续证。",
}

# 要从 metrics 里删掉的推算值
DROP_METRIC = {"boredhumans": ["mrr"]}


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
        dropped = []
        for k in DROP_METRIC.get(cid, []):
            if k in m:
                m.pop(k)
                dropped.append(k)
        note = m.get("metric_note") or ""
        if MARK in note:
            print("= %-12s 已有核验注记（删字段: %s）" % (cid, dropped or "无"))
            continue
        m["metric_note"] = (note.rstrip() + "\n\n" + MARK + "（verify_case.py）：" + add).strip()
        changed.append((cid, dropped, len(note), len(m["metric_note"])))

    for cid, dropped, a, b in changed:
        print("+ %-12s 删 %-6s metric_note %d → %d 字"
              % (cid, ",".join(dropped) or "-", a, b))
    if dry:
        print("\n[dry] 未写盘。%d 条会被改。" % len(changed))
        return 0
    if not changed:
        return 0
    atomicio.write_json(CASES, cases, indent=2)
    print("\n已写盘：%d 条，cases 共 %d 条" % (len(changed), len(cases)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
