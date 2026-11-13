# -*- coding: utf-8 -*-
"""一致性复扫：把「金额在前」与「订阅数 / 客单价」几种语序一起查。

覆盖（全部字段，含 list / dict 下钻）：
  1) MRR $X            → 对比 metrics.mrr
  2) 近 30 天收入/流水 $X → 对比 last_30d_revenue
  3) 累计 $X           → 对比 all_time
  4) $X MRR / $X 的 MRR → 对比 metrics.mrr
  5) $X 客单(价)        → 对比 mrr / subscriptions（2% 容差）
  6) N 个订阅 / 活跃订阅 N → 对比 subscriptions
  7) 「N 个订阅 … $X」  → X 视为 MRR，对比 metrics.mrr

只报告，不写盘。退出码非 0 表示仍有不符。
"""
import os
import sys
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]

_LINK = r"(?:\s|为|达到|达|约|是|：|:|，|、|有){0,12}"
_NOT_PCT = r"(?!\s*(?:增速|增长|环比|同比|下降|上升|下滑|上涨))"

PAT = {
    "mrr": re.compile(r"MRR" + _NOT_PCT + "(" + _LINK + r")\$\s?([\d][\d,]*(?:\.\d+)?)"),
    "l30": re.compile(r"(?:近\s?30\s?天(?:已验证)?(?:收入|流水)|(?<!近)30\s?天(?:收入|流水))" + _NOT_PCT
                      + r"([^0-9$%]{0,8})\$\s?([\d][\d,]*(?:\.\d+)?)"),
    "all": re.compile(r"(?:累计(?:收入)?)" + _NOT_PCT + "(" + _LINK + r")\$\s?([\d][\d,]*(?:\.\d+)?)"),
    "mrr_post": re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(?:的)?\s*MRR"),
    "arpo": re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(?:的)?\s*客单"),
}
MK = {"mrr": "mrr", "l30": "last_30d_revenue", "all": "all_time"}


def walk(case):
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield "%s[%d]" % (f, i), x
        elif isinstance(v, dict):
            for k, y in v.items():
                if isinstance(y, str):
                    yield "%s.%s" % (f, k), y


def main():
    cases = CS.load_cases()
    problems = []
    for c in cases:
        m = c.get("metrics") or {}
        mrr, subs = m.get("mrr"), m.get("subscriptions")
        for key in ("mrr", "l30", "all"):
            want = m.get(MK[key])
            if not isinstance(want, (int, float)) or want <= 0:
                continue
            for path, t in walk(c):
                for mt in PAT[key].finditer(t):
                    if "万" in t[mt.end():mt.end() + 2]:
                        continue
                    got = float(mt.group(2).replace(",", ""))
                    if abs(got - want) / max(abs(want), 1e-9) > 0.01:
                        problems.append((c["id"], path, key, got, want))
        if isinstance(mrr, (int, float)) and mrr > 0:
            for path, t in walk(c):
                for mt in PAT["mrr_post"].finditer(t):
                    got = float(mt.group(1).replace(",", ""))
                    if abs(got - mrr) / mrr > 0.01:
                        problems.append((c["id"], path, "mrr_post", got, mrr))
        if isinstance(mrr, (int, float)) and isinstance(subs, (int, float)) and subs > 0:
            want = mrr / subs
            for path, t in walk(c):
                for mt in PAT["arpo"].finditer(t):
                    got = float(mt.group(1).replace(",", ""))
                    if abs(got - want) / want > 0.02:
                        problems.append((c["id"], path, "arpo", got, round(want, 2)))
                for mt in re.finditer(r"(\d[\d,]*)\s?个(?:活跃|付费)?订阅", t):
                    got = float(mt.group(1).replace(",", ""))
                    if got != subs:
                        problems.append((c["id"], path, "subs", got, subs))

    print("一致性复扫：%d 处 / %d 条" % (len(problems), len({p[0] for p in problems})))
    for p in problems[:40]:
        print("   %-28s %-20s %-9s 文中=%-10s 台账=%s" % p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
