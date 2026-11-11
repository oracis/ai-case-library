# -*- coding: utf-8 -*-
"""原型：按子句剥离金额/指标，验证 255 条封面文案是否还残留金额或残渣。"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import wechat_publish as wp

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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

# 指标类关键词：封面要稳定信息，凡带这些词的子句一律不留
_METRIC_KW = re.compile(
    r"(?:MRR|ARR|毛利|订阅|留存|倍数|估值|环比|增速|累计收入|收入|营收|流水"
    r"|用户|粉丝|下载|订阅数|付费)", re.I)
_SCALE = re.compile(r"\d[\d,.]*\s*(?:万|千|[KkMmBb])")
_ORPHAN = re.compile(r"^(?:万|千|[KkMmBb]|/月|/年|起|–|-|—|\d+[€$]?/?月?)$")

_SPLIT = re.compile(r"[：:，,、；;。]")


def strip_clauses(s):
    keep = []
    for cl in _SPLIT.split(s or ""):
        cl = cl.strip(" 　")
        if not cl:
            continue
        if _ORPHAN.match(cl):
            continue
        if _MONEY.search(cl) or wp._MONEY_TOKEN.search(cl):
            continue
        if _METRIC_KW.search(cl) and _SCALE.search(cl):
            continue
        keep.append(cl)
    return "，".join(keep)


def main():
    data = json.load(io.open(os.path.join(ROOT, "data", "cases.json"),
                             encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    hit, empty_after, changed = [], [], 0
    for c in cases:
        old = wp.cover_desc(c)
        new = strip_clauses(old)
        new = wp._tidy_desc(new)
        if old != new:
            changed += 1
        if not new:
            empty_after.append(c["id"])
        if _MONEY.search(new):
            hit.append((c["id"], new[:80]))
        if old != new and (_MONEY.search(old) or len(old) > 0):
            if any(x in old for x in ("万", "/月", "订阅", "MRR", "收入")):
                print("%-26s\n  旧: %s\n  新: %s" % (c["id"], old, new or "<空>"))
    print("\n改动 %d 条；新文案仍命中金额 %d 条 %s" % (len(cases), len(hit), hit[:5]))
    print("剥空（只剩项目名）%d 条: %s" % (len(empty_after), empty_after[:20]))


if __name__ == "__main__":
    main()
