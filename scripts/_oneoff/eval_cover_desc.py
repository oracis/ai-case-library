# -*- coding: utf-8 -*-
"""评估新的子句级封面清洗：金额残留 / 空文案 / 残渣 / 名称重复。"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import wechat_publish as wp  # noqa: E402

_MONEY = re.compile(
    r"(?:"
    r"\$" + r"\d[\d,.]*"
    + r"|" + r"\d[\d,.]*" + r"\s*(?:美元|美金|元|人民币|刀|usd|rmb|cny)"
    + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:万|千)\s*(?:美元|美金|元|人民币))"
    + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:万|千)"
                    r"(?=\s*(?:MRR|收入|营收|流水|用户|月)))"
    + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:K|M|B)\s*(?:MRR|ARR)?"
                    r"(?=\s*(?:收入|营收|流水|/月|月)))"
    + r"|(?:月入|年收入|营收|月收入|收入|流水|MRR|ARR)"
                    r"\s*(?:达|到|从|为|[:：])?\s*\$?" + r"\d[\d,.]*"
    + r")",
    re.I)

RESIDUE = re.compile(r"(?:^[，,、]|[，,、]\s*(?:万|千|[KkMmBb])\s*(?:[，,。、]|$)"
                     r"|[/／](?:月|年)\s*(?:[，,。、]|$)|从起的|,,)")


def main():
    data = json.load(io.open(os.path.join(ROOT, "data", "cases.json"),
                             encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    money, empty, dup, residue, changed = [], [], [], [], []
    for c in cases:
        d = wp.cover_desc(c)
        if _MONEY.search(d):
            money.append((c["id"], d))
        if not d:
            empty.append(c["id"])
        else:
            nm = (c.get("name") or "").strip().lower()
            if nm and d.lower().startswith(nm):
                dup.append((c["id"], d))
            if RESIDUE.search(d):
                residue.append((c["id"], d))
        changed.append(c["id"])

    print("案例 %d 条" % len(cases))
    print("金额残留 %d：%s" % (len(money), money[:5]))
    print("残渣 %d：%s" % (len(residue), residue[:5]))
    print("空文案 %d：%s" % (len(empty), empty[:10]))
    print("以项目名开头 %d（封面已有大字项目名，重复）" % len(dup))
    for cid, d in dup[:12]:
        print("   -", cid, "|", d[:50])


if __name__ == "__main__":
    main()
