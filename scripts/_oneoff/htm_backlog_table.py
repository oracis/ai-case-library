#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 howtomakemi「官方披露 · 收入口径 · AI 相关」待入库 backlog 表（markdown）。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import harvest
from text_clean import norm_name

INGESTED = {"arcads", "instantly", "ahron", "fyxer", "heyreach",
            "campfire", "wispr-flow", "build-concierge"}


def cny(s):
    m = re.match(r"¥([\d,\.]+)([万亿]?)", s or "")
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return v * (100000000 if m.group(2) == "亿" else (10000 if m.group(2) == "万" else 1))


def caliber(r):
    inc = r.get("income") or ""
    if not inc or inc in ("—", "-"):
        return "none"
    money = (r.get("money") or "") + " " + (r.get("income_note") or "")
    for m in re.finditer(re.escape(inc), money):
        ctx = money[max(0, m.start() - 30):m.end() + 30]
        if re.search(r"轮|融资|领投|raised|估值|Series", ctx):
            return "fund"
    return "rev" if re.search(r"ARR|年化|年收[入营]|月收入|营收|MRR|每月|run-rate", money) else "unk"


def main():
    d = [r for r in json.load(open(os.path.join(ROOT, "tmp", "htm_official_scan.json"),
                                   encoding="utf-8")) if "error" not in r]
    keys = harvest.seen_keys()
    ids, slugs, names = keys["ids"], keys["slugs"], keys["names"]

    def new(r):
        return not (r["slug"] in ids or r["slug"] in slugs or norm_name(r["name"]) in names)

    rows = [r for r in d if new(r) and caliber(r) == "rev"
            and r.get("has_ai") and r["slug"] not in INGESTED]
    rows.sort(key=lambda r: -cny(r["income"]))

    lines = ["| slug | 产品 | 页面月收入 | 地区 | 页面口径 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (
            r["slug"], r["name"], r["income"], r["region"], r.get("income_note", "")[:40]))
    out = os.path.join(ROOT, "tmp", "backlog_table.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("backlog %d 条 → %s" % (len(rows), out))


if __name__ == "__main__":
    main()
