#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 backfill_trustmrr.py --dry-run 日志，统计补完后能过门槛的条数。"""
import collections
import pathlib
import re

LOG = pathlib.Path(__file__).resolve().parents[2] / "tmp" / "backfill_dry_v2.log"
txt = LOG.read_text(encoding="utf-8")

# 每行形如：  · promptflip   分类→Other；主指标→TrustMRR 验证：近 30 天收入 $0
amounts = []
rows = []
for line in txt.splitlines():
    if "主指标" not in line:
        continue
    m = re.search(r"\$([0-9,]+)", line)
    cid = line.split()[1] if len(line.split()) > 1 else "?"
    val = int(m.group(1).replace(",", "")) if m else None
    amounts.append(val)
    rows.append((cid, val, line.strip()))

print("含主指标更新条数:", len(amounts))
cnt = collections.Counter(amounts)
print("金额分布:", dict(sorted(cnt.items(), key=lambda x: (x[0] is None, x[0]))))
print(">=300 的条数:", sum(n for v, n in cnt.items() if v is not None and v >= 300))
print("=0 的条数:", cnt.get(0, 0))
print("None(解析失败):", cnt.get(None, 0))
print("\n=== 金额 >=300 的条目（可能解锁）===")
for cid, val, line in rows:
    if val is not None and val >= 300:
        print(" ", cid, "$" + str(val))
