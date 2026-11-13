# -*- coding: utf-8 -*-
"""收尾第六批：`china_fit.note` / `solo_fit.delivery_note` 里的订阅数与 MRR 尾数。

这批是「N 个订阅 $X」的变体（模板没有「，」前缀、或写成叙述句），
统一对齐 metrics.subscriptions 与 metrics.mrr；叙述性金额（如「月流水从 A 掉到 B」）不动。
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

EDITS = [
    ("mentiondesk", "china_fit", "note", "它只有 6 个订阅、月流水从", "它只有 4 个订阅、月流水从"),
    ("lina", "china_fit", "note", "126 个订阅 $422，客单 $3.35", "123 个订阅 $400，客单 $3.25"),
    ("rareui", "china_fit", "note", "3 个订阅 $650，客单 $216", "2 个订阅 $500，客单 $250"),
    ("hand-off", "solo_fit", "delivery_note", "6 个订阅 $1,136，等于每人每月 $189", "4 个订阅 $757，等于每人每月 $189"),
    ("static-forms", "china_fit", "note", "127 个订阅 $1,009", "134 个订阅 $1,040"),
    ("pluma", "china_fit", "note", "214 个订阅 $1,410", "209 个订阅 $1,288"),
    ("claw-mart", "china_fit", "note", "53 个订阅 $1,060，靠低价走量", "49 个订阅 $980，靠低价走量"),
    ("blipix", "china_fit", "note", "43 个订阅 $1,646", "46 个订阅 $1,793"),
    ("mind-reach", "china_fit", "note", "212 个订阅 $2,149", "206 个订阅 $2,075"),
    ("garagelab", "china_fit", "note", "51 个订阅 $1,998，靠一次性冲动撑", "109 个订阅 $4,290，靠一次性冲动撑"),
    ("fitcal", "china_fit", "note", "2,177 个订阅撑 $4,763", "2,016 个订阅撑 $4,403"),
    ("mausbot", "china_fit", "note", "126 个订阅 $7,327", "305 个订阅 $13,306"),
    ("aiwritebook", "china_fit", "note", "434 个订阅 $12,548", "424 个订阅 $12,218"),
    ("controlresell", "solo_fit", "delivery_note", "449 个订阅说明是团队在跑", "459 个订阅说明是团队在跑"),
    ("convertlabs", "china_fit", "note", "477 个订阅撑 $28,579", "469 个订阅撑 $27,890"),
    ("webclaw", "china_fit", "note", "9 个订阅 $362，价目表比收入好看", "8 个订阅 $343，价目表比收入好看"),
    ("ae-intelligence", "solo_fit", "delivery_note", "配 1,065 个付费订阅", "配 1,116 个付费订阅"),
]


def get_field(case, field, idx):
    v = case.get(field)
    if isinstance(v, dict):
        return v.get(idx)
    return None


def set_field(case, field, idx, text):
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

    if not args.yes:
        print("干跑结束，未写盘。")
        return 0

    with open(os.path.join(ROOT, "tmp", "cases_before_subfix_20261113.json"), "w", encoding="utf-8") as f:
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
