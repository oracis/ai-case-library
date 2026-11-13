# -*- coding: utf-8 -*-
"""勘察第 2、3 项：正文数字与台账真值的偏差，并按「可安全替换 / 需重算 / 需重写」分级。

只读，不写盘。输出供决定改法用。
"""
import sys
import os
import re
import json
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]


def walk(case):
    """(path, text) 下钻 list / dict，覆盖全部正文容器。"""
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield "%s[%d]" % (f, i), x
                elif isinstance(x, dict):
                    for k, y in x.items():
                        if isinstance(y, str):
                            yield "%s[%d].%s" % (f, i, k), y
        elif isinstance(v, dict):
            for k, y in v.items():
                if isinstance(y, str):
                    yield "%s.%s" % (f, k), y


def fmt(v):
    return "{:,.0f}".format(v) if float(v).is_integer() else "{:,.2f}".format(v).rstrip("0").rstrip(".")


def amounts(v):
    return ["{:,.0f}".format(v), "{:,.1f}".format(v), "{:,.2f}".format(v), str(int(v))]


# 数字出现模式（按指标前缀归类）
PAT_MRR = re.compile(r"MRR[^0-9$]{0,12}\$\s?([\d][\d,]*)")
PAT_L30 = re.compile(r"近\s?30\s?天(?:已验证)?收入[^0-9$]{0,6}\$\s?([\d][\d,]*)")
PAT_ALL = re.compile(r"累计(?:收入)?[^0-9$]{0,8}\$\s?([\d][\d,]*)")

# 危险模式
PAT_DIV = re.compile(r"(除以|÷|/)\s?(?:活跃)?订阅")
PAT_TREND = re.compile(r"(从|由)\s?\$[\d,]+\s?(涨到|增至|升到|降到|降至|跌到|回落到|变为)")
PAT_PCT_30 = re.compile(r"近\s?30\s?天(?:收入)?增速[^0-9+\-]{0,4}([-+]?\d+(?:\.\d+)?)\s?%")
PAT_PCT_MRR = re.compile(r"MRR\s?增速[^0-9+\-]{0,4}([-+]?\d+(?:\.\d+)?)\s?%")

cases = CS.load_cases()
stat = Counter()
safe_rows, div_rows, trend_rows, pct_rows = [], [], [], []

for c in cases:
    m = c.get("metrics") or {}
    mrr, l30, allt = m.get("mrr"), m.get("last_30d_revenue"), m.get("all_time")
    subs = m.get("subscriptions")
    mg, rg = m.get("mrr_growth_30d"), m.get("revenue_growth_30d")
    for path, text in walk(c):
        # 金额
        for key, pat, tv, subs_ok in (("mrr", PAT_MRR, mrr, True),
                                      ("l30", PAT_L30, l30, False),
                                      ("all", PAT_ALL, allt, False)):
            if not isinstance(tv, (int, float)):
                continue
            for mt in pat.finditer(text):
                got = float(mt.group(1).replace(",", ""))
                if abs(got - tv) < 0.005:
                    continue
                stat["amt_" + key] += 1
                row = (c["id"], path, key, got, tv, mt.start(), text[max(0, mt.start() - 55):mt.start() + 70])
                if PAT_DIV.search(text) or PAT_TREND.search(text):
                    if PAT_TREND.search(text):
                        trend_rows.append(row)
                    else:
                        div_rows.append(row)
                else:
                    safe_rows.append(row)
        # 百分比
        for key, pat, tv in (("rg", PAT_PCT_30, rg), ("mg", PAT_PCT_MRR, mg)):
            if not isinstance(tv, (int, float)):
                continue
            for mt in pat.finditer(text):
                got = float(mt.group(1))
                if abs(got - tv) < 0.05:
                    continue
                stat["pct_" + key] += 1
                pct_rows.append((c["id"], path, key, got, tv, text[max(0, mt.start() - 55):mt.start() + 55]))

print("=== 偏差计数 ===")
for k in ("amt_mrr", "amt_l30", "amt_all", "pct_rg", "pct_mg"):
    print("  %-8s %d 处" % (k, stat[k]))
print()
print("=== 分级（金额处）===")
print("  安全（纯现值陈述）:", len(safe_rows), "处 /", len({r[0] for r in safe_rows}), "条")
print("  需重算（含 ÷订阅）:", len(div_rows), "处 /", len({r[0] for r in div_rows}), "条")
print("  需重写（含 从A到B）:", len(trend_rows), "处 /", len({r[0] for r in trend_rows}), "条")
print()
print("=== 安全类样本 15 处 ===")
for r in safe_rows[:15]:
    print("  %-28s %-22s %s: %s → %s" % (r[0], r[1], r[2], fmt(r[3]), fmt(r[4])))
    print("     …%s…" % r[6].replace("\n", " "))
print()
print("=== 需重算样本 10 处 ===")
for r in div_rows[:10]:
    print("  %-28s %-22s %s: %s → %s" % (r[0], r[1], r[2], fmt(r[3]), fmt(r[4])))
    print("     …%s…" % r[6].replace("\n", " "))
print()
print("=== 需重写样本 8 处 ===")
for r in trend_rows[:8]:
    print("  %-28s %-22s %s: %s → %s" % (r[0], r[1], r[2], fmt(r[3]), fmt(r[4])))
    print("     …%s…" % r[6].replace("\n", " "))
print()
print("=== 百分比样本 12 处 ===")
for r in pct_rows[:12]:
    print("  %-28s %-22s %s: %s%% → %s%%" % (r[0], r[1], r[2], r[3], r[4]))
    print("     …%s…" % r[5].replace("\n", " "))
print()
print("按字段分布（安全类）:", Counter(r[1].split("[")[0] for r in safe_rows).most_common())
print("按字段分布（百分比）:", Counter(r[1].split("[")[0] for r in pct_rows).most_common())
