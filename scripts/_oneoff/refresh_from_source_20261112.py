# -*- coding: utf-8 -*-
"""第 2、3 项第一步：把「正文与台账数字有实质偏差」的案例重新回源，产出差异报告。

只读现有台账 + 联网抓一手 .md，不写台账。报告存 tmp/。
判据：相对差 > 1% 才算实质偏差（1% 以内视为四舍五入，不算错）。
"""
import sys
import os
import re
import json
import argparse
import urllib.request
import concurrent.futures as cf

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]
REL_TOL = 0.01

RE_MRR_TXT = re.compile(r"MRR[^0-9$]{0,12}\$\s?([\d][\d,]*)")
RE_MD = {
    "mrr": re.compile(r"Current MRR:\s*\$?([\d,]+)"),
    "l30": re.compile(r"Last 30 days revenue snapshot:\s*\$?([\d,]+)"),
    "all": re.compile(r"All-time revenue snapshot:\s*\$?([\d,]+)"),
    "subs": re.compile(r"Current active subscriptions:\s*([\d,]+)"),
    "mg": re.compile(r"Last 30 days MRR growth:\s*([-+]?[\d.]+)"),
    "rg": re.compile(r"Last 30 days revenue growth:\s*([-+]?[\d.]+)"),
    "sync": re.compile(r"Revenue data last synced:\s*([\d\-T:.Z]+)"),
}


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


def fetch(cid):
    url = "https://trustmrr.com/startup/%s.md" % cid
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        t = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    except Exception as e:
        return cid, None, "ERR %s" % str(e)[:50]
    if "Current MRR" not in t and "Last 30 days revenue" not in t:
        return cid, None, "NO-PROFILE"
    d = {}
    for k, rx in RE_MD.items():
        m = rx.search(t)
        if m:
            d[k] = m.group(1) if k == "sync" else float(m.group(1).replace(",", ""))
    return cid, d, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="tmp/refresh_report_20261112.json")
    args = ap.parse_args()

    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    # 找出有实质偏差的案例
    targets, offsets = [], {}
    for c in cases:
        m = c.get("metrics") or {}
        mrr = m.get("mrr")
        if not isinstance(mrr, (int, float)):
            continue
        off = []
        for path, t in walk(c):
            for mt in RE_MRR_TXT.finditer(t):
                got = float(mt.group(1).replace(",", ""))
                if abs(got - mrr) / max(abs(mrr), 1e-9) > REL_TOL:
                    off.append((path, got))
        if off:
            targets.append(c["id"])
            offsets[c["id"]] = off

    print("有实质偏差的案例：%d 条，偏差处 %d 处" % (len(targets), sum(len(v) for v in offsets.values())))

    results = {}
    errs = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for cid, d, err in ex.map(fetch, targets):
            if err:
                errs.append((cid, err))
                continue
            results[cid] = d

    print("抓取成功 %d / 失败 %d" % (len(results), len(errs)))
    for e in errs[:15]:
        print("   ", e)
    print()

    # 差异报告
    report = []
    for cid, d in results.items():
        m = by[cid].get("metrics") or {}
        row = {
            "id": cid,
            "old": {k: m.get(k) for k in ("mrr", "last_30d_revenue", "all_time", "subscriptions", "mrr_growth_30d", "revenue_growth_30d")},
            "new": {k: d.get(k) for k in ("mrr", "l30", "all", "subs", "mg", "rg")},
            "sync": d.get("sync"),
            "offsets": offsets[cid],
            "note": (m.get("metric_note") or "")[:120],
        }
        report.append(row)

    os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
    with open(os.path.join(ROOT, args.out), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 摘要
    changed = [r for r in report if r["new"].get("mrr") is not None and r["old"].get("mrr") is not None
               and abs(r["new"]["mrr"] - r["old"]["mrr"]) / max(abs(r["old"]["mrr"]), 1e-9) > REL_TOL]
    same = [r for r in report if r not in changed]
    print("回源后 MRR 与台账仍不同：%d 条；相同（说明正文才是旧数）：%d 条" % (len(changed), len(same)))
    print()
    print("--- 台账与回源不同（说明 metrics 过期，需改台账+正文）前 15 ---")
    for r in changed[:15]:
        print("  %-28s 台账=%-9s 回源=%-9s sync=%s" % (r["id"], r["old"]["mrr"], r["new"]["mrr"], (r["sync"] or "")[:10]))
    print()
    print("--- 台账与回源相同（正文是旧数，只改正文）前 15 ---")
    for r in same[:15]:
        print("  %-28s 台账=回源=%-9s sync=%s 偏差%d处" % (r["id"], r["old"]["mrr"], (r["sync"] or "")[:10], len(r["offsets"])))
    print()
    print("报告已写", args.out)


if __name__ == "__main__":
    main()
