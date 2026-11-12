# -*- coding: utf-8 -*-
"""收尾第三批：3 处「客单价被写成 MRR、订阅数停在旧值」的句子定点改写。

来源：apply_refresh_plan_20261112 写入后断言不符 3 处——
  boxer-ai how_it_makes_money / flint why_it_works[1] / flint playbook[2]
原因：这些句子把客单价冠以「MRR $X」，且括号里的订阅数是旧值（139，现 131），
      PAT 的 _LINK 只允许空白/连接词，不会跨过「÷ 活跃订阅数」去替换，
      所以计划生成器正确地没有自动改——需人工给新措辞。

改法：一律写成「当前 MRR $M ÷ N 个订阅 ≈ $客单价」，客单价按 M÷N 复算两位小数。
默认干跑；--yes 写入并回读断言。
"""
import os
import sys
import re
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

EDITS = [
    ("boxer-ai", "how_it_makes_money", None,
     "按素材已算好的客单价：MRR $6.56 对应 691 个活跃订阅，即每个订阅每月贡献 $7.16。",
     "按月折算的客单价约 $6.56（当前 MRR $4,534 ÷ 691 个活跃订阅）。"),
    ("flint", "why_it_works", 1,
     "对 $12.02 的月客单价（MRR $10.87 ÷ 139，素材已算好）而言，一个月被问候三十次的感知价值足以支撑续订，所以订阅能停在 139 个而不是首月后散掉。",
     "对约 $10.87 的月客单价（当前 MRR $1,424 ÷ 131 个订阅）而言，一个月被问候三十次的感知价值足以支撑续订，所以订阅能停在 131 个而不是首月后散掉。"),
    ("flint", "playbook", 2,
     "$12.02 的客单价（MRR $10.87 ÷ 139）意味着收入每月被重新决定一次",
     "约 $10.87 的客单价（当前 MRR $1,424 ÷ 131 个订阅）意味着收入每月被重新决定一次"),
]


def get_field(case, field, idx):
    v = case.get(field)
    if idx is None:
        return v if isinstance(v, str) else None
    if isinstance(v, list) and 0 <= idx < len(v):
        return v[idx]
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

    # 1) 防漂移：旧串必须逐字命中且唯一
    bad = []
    for cid, field, idx, before, _after in EDITS:
        cur = get_field(by[cid], field, idx)
        if cur is None:
            bad.append((cid, field, idx, "字段不存在"))
        elif cur.count(before) != 1:
            bad.append((cid, field, idx, "命中 %d 次" % cur.count(before)))
    print("防漂移：%d / %d 通过" % (len(EDITS) - len(bad), len(EDITS)))
    for b in bad:
        print("   ", b)
    if bad:
        return

    # 2) 算术自洽：客单价 == MRR ÷ 订阅数（±0.02）
    for cid, field, idx, _b, after in EDITS:
        m = by[cid].get("metrics") or {}
        mrr, subs = m.get("mrr"), m.get("subscriptions")
        want = round(mrr / subs, 2)
        got = [float(x.replace(",", "")) for x in re.findall(r"约 \$([\d,]+(?:\.\d+)?)[^（]{0,8}（当前 MRR", after)]
        assert got and abs(got[0] - want) <= 0.02, (cid, got, want)
    print("客单价复算：%d / %d 相符" % (len(EDITS), len(EDITS)))

    if not args.yes:
        print("干跑结束，未写盘。")
        return

    with open(os.path.join(ROOT, "tmp", "cases_before_arpo3_20261112.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    for cid, field, idx, before, after in EDITS:
        cur = get_field(by[cid], field, idx)
        set_field(by[cid], field, idx, cur.replace(before, after))
    CS.save_cases(cases)
    print("已写盘 %d 处" % len(EDITS))

    # 3) 独立回读
    back = {c["id"]: c for c in CS.load_cases()}
    fail = []
    for cid, field, idx, before, after in EDITS:
        cur = get_field(back[cid], field, idx) or ""
        if before in cur or after not in cur:
            fail.append((cid, field, idx))
    print("回读案例 %d 条，断言不符 %d 处" % (len(back), len(fail)))
    for f in fail:
        print("   ", f)


if __name__ == "__main__":
    main()
