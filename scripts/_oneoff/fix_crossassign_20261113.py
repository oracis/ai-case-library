# -*- coding: utf-8 -*-
"""收尾第五批：修刷新脚本在「订阅数 / 客单价 / MRR 同句」时的交叉写错。

根因：批次一、二按「指标前缀」逐个替换，同一句里同时出现
「N 个活跃订阅 × $X 客单价 = $Y MRR」时，三个槽位的替换值互相串位，
产出如「354 个活跃订阅 × $354.08 客单价 = $2.97 MRR」（原为
「373 × $3.08 = $1,148」）、「144 个活跃订阅支撑 $144 MRR」这类句子。

本脚本只做**显式定点替换**（每条断言唯一命中 + 算术复算），不猜模式。
默认干跑；--yes 写入并回读。
"""
import os
import sys
import re
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

# (case_id, field, index|None, before, after)
EDITS = [
    # ---- A. 「$X MRR」金额在前 ----
    ("selltora", "china_fit", "note", "，$517 MRR。", "，$308 MRR。"),
    ("assignmentgpt-ai", "china_fit", "note", "$574 MRR，49 个订阅。", "$516 MRR，45 个订阅。"),
    ("autotube", "china_fit", "note", "，$624 MRR。", "，$644 MRR。"),
    ("ai-song-clip-generator", "why_it_works", 0,
     "使 149 个活跃订阅能支撑 $904 MRR", "使 149 个活跃订阅能支撑 $864 MRR"),
    ("ai-song-clip-generator", "china_fit", "note",
     "180 个订阅 $904，客单 $5.02", "149 个订阅 $864，客单 $5.80"),
    ("apparelai", "china_fit", "note", "它的 $1,020 MRR", "它的 $904 MRR"),
    ("blitzreels", "china_fit", "note", "它的 $1,979 MRR", "它的 $1,354 MRR"),
    ("controlresell", "how_it_makes_money", None,
     "客单价（MRR ÷ 活跃订阅数）为 $459.12", "客单价（MRR ÷ 活跃订阅数）为 $48.85"),
    ("controlresell", "china_fit", "note",
     "但按 449 个订阅与 $21,606 MRR 反推客单约 $48", "但按 459 个订阅与 $22,422 MRR 反推客单约 $49"),
    ("neoscene", "why_it_works", 0, "等于 $3,236 的 MRR", "等于 $2,151 的 MRR"),
    ("neoscene", "playbook", 0, "NeoScene 的 $3,236 MRR", "NeoScene 的 $2,151 MRR"),
    ("neoscene", "playbook", 2, "就能撑起 $3,236 MRR", "就能撑起 $2,151 MRR"),
    ("babelbot", "why_it_works", 0, "78 个活跃订阅对应 $78.12 客单价", "78 个活跃订阅对应 $12.83 客单价"),
    ("babelbot", "china_fit", "note", "60 个订阅 $787，客单低", "78 个订阅 $1,001，客单低"),
    ("scan-em-all", "why_it_works", 0, "66 个活跃订阅对应 $66 MRR", "66 个活跃订阅对应 $451 MRR"),
    ("scan-em-all", "playbook", 1, "（如 $6.8 客单价）", "（如 $6.83 客单价）"),
    ("coupleai", "why_it_works", 1, "144 个活跃订阅支撑 $144 MRR", "144 个活跃订阅支撑 $2,974 MRR"),
    ("coupleai", "why_it_works", 2, "使 $2,923 MRR 能转化为", "使 $2,974 MRR 能转化为"),
    ("coupleai", "china_fit", "note", "145 个订阅 $2,923", "144 个订阅 $2,974"),
    ("bibly", "one_liner", None, "每月 $4,945 MRR", "每月 $5,014 MRR"),
    ("bibly", "why_it_works", 1, "$4.63 客单价门槛低", "$4.66 客单价门槛低"),

    # ---- B. 「客单价」被写成订阅数 ----
    ("stampsnap", "china_fit", "note",
     "$3.68 的客单价靠订阅习惯堆量，104 个订阅撑起 $383",
     "$3.53 的客单价靠订阅习惯堆量，98 个订阅撑起 $346"),
    ("dreamini-ai", "china_fit", "note", "$27.36 客单，25 个订阅 $684", "$28.50 客单，44 个订阅 $1,254"),
    ("url2pin", "china_fit", "note", "$15.75 客单，53 个订阅 $835", "$16.25 客单，115 个订阅 $1,869"),
    ("videototext", "china_fit", "note", "$14.67 客单，49 个订阅 $719", "$14.61 客单，44 个订阅 $643"),
    ("conecta-fitness", "china_fit", "note", "客单 $9.35，75 个订阅 $701", "客单 $8.95，63 个订阅 $564"),
    ("cole-striler", "china_fit", "note", "$16.04 客单，125 个订阅 $2,005", "$16.15 客单，123 个订阅 $1,987"),
    ("buma-baby-tracker", "china_fit", "note",
     "$8.14 客单，149 个订阅 $1,213", "$7.23 客单，179 个订阅 $1,294"),
    ("fishingai", "why_it_works", 0, "$3.47 的客单价", "$3.29 的客单价"),
    ("chatdash-llc", "china_fit", "note", "愿意按 $175 的客单付", "愿意按 $180.27 的客单付"),
    ("chatdash-llc", "china_fit", "note", "102 个订阅撑起 $17,935 MRR", "99 个订阅撑起 $17,847 MRR"),
    ("convitede", "why_it_works", 1,
     "354 个活跃订阅 × $354.08 客单价 = $2.97 MRR",
     "354 个活跃订阅 × $2.97 客单价 = $1,050 MRR"),
    ("convitede", "china_fit", "note", "373 个订阅撑起 $1,148", "354 个订阅撑起 $1,050"),
    ("boxer-ai", "playbook", 0,
     "本案 $7.16 的客单价对应 6.56 个订阅、MRR $4,534",
     "本案 $6.56 的客单价对应 691 个订阅、MRR $4,534"),

    # ---- C. 「N 个订阅 $X」里的订阅数与 MRR ----
    ("cleopatra-ai", "china_fit", "note", "，35 个订阅 $496", "，37 个订阅 $506"),
    ("sbc-crafter", "china_fit", "note", "，10 个订阅 $360", "，110 个订阅 $367"),
    ("creatorskit", "china_fit", "note", "，27 个订阅 $253", "，24 个订阅 $226"),
    ("calma", "china_fit", "note", "，106 个订阅 $456", "，105 个订阅 $445"),
    ("car-identifier", "china_fit", "note", "，105 个订阅每月 $576", "，101 个订阅每月 $511"),
    ("cattus-ai", "china_fit", "note", "，32 个订阅 $595", "，31 个订阅 $561"),
    ("creads", "china_fit", "note", "，17 个订阅 $593", "，15 个订阅 $531"),
    ("parlai", "china_fit", "note", "，79 个订阅 $759", "，73 个订阅 $680"),
    ("clawhosters-com", "china_fit", "note", "，24 个订阅 $736", "，23 个订阅 $663"),
    ("social-genius-text-analyzer", "china_fit", "note", "，34 个订阅 $384", "，37 个订阅 $384"),
    ("clawdi", "china_fit", "note", "，64 个订阅 $1,948", "，62 个订阅 $1,919"),
]


def get_field(case, field, idx):
    v = case.get(field)
    if idx is None:
        return v if isinstance(v, str) else None
    if isinstance(v, list) and isinstance(idx, int):
        return v[idx] if 0 <= idx < len(v) else None
    if isinstance(v, dict) and isinstance(idx, str):
        return v.get(idx)
    return None


def set_field(case, field, idx, text):
    if idx is None:
        case[field] = text
    else:
        case[field][idx] = text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    bad = []
    for cid, field, idx, before, _after in EDITS:
        cur = get_field(by[cid], field, idx)
        if cur is None:
            bad.append((cid, field, idx, "字段缺失"))
        elif cur.count(before) != 1:
            bad.append((cid, field, idx, "命中 %d 次" % cur.count(before)))
    print("防漂移：%d / %d 唯一命中" % (len(EDITS) - len(bad), len(EDITS)))
    for b in bad:
        print("   ", b)
    if bad:
        return 1

    # 算术复算：改写后句中出现的 MRR / 客单价必须与台账一致
    warns = []
    for cid, field, idx, _b, after in EDITS:
        m = by[cid].get("metrics") or {}
        mrr, subs = m.get("mrr"), m.get("subscriptions")
        for mt in re.finditer(r"\$([\d,]+(?:\.\d+)?)\s*MRR", after):
            got = float(mt.group(1).replace(",", ""))
            if abs(got - mrr) / max(mrr, 1e-9) > 0.01:
                warns.append((cid, "MRR", got, mrr))
        for mt in re.finditer(r"\$([\d,]+(?:\.\d+)?)\s*(?:的)?客单", after):
            got = float(mt.group(1).replace(",", ""))
            want = mrr / subs if subs else None
            if want and abs(got - want) / want > 0.02:
                warns.append((cid, "客单价", got, round(want, 2)))
    print("算术复算告警：%d 处" % len(warns))
    for w in warns:
        print("   ", w)

    if not args.yes:
        print("干跑结束，未写盘。")
        return 0

    with open(os.path.join(ROOT, "tmp", "cases_before_crossfix_20261113.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    for cid, field, idx, before, after in EDITS:
        cur = get_field(by[cid], field, idx)
        set_field(by[cid], field, idx, cur.replace(before, after, 1))
    CS.save_cases(cases)
    print("已写盘 %d 处" % len(EDITS))

    back = {c["id"]: c for c in CS.load_cases()}
    fail = []
    for cid, field, idx, before, after in EDITS:
        cur = get_field(back[cid], field, idx) or ""
        if before in cur or after not in cur:
            fail.append((cid, field, idx))
    print("回读 %d 条，断言不符 %d 处" % (len(back), len(fail)))
    for f in fail:
        print("   ", f)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
