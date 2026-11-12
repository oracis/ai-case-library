# -*- coding: utf-8 -*-
"""第 2、3 项第三步：把替换计划写入台账，并做全量断言回读。

用法
----
    python scripts/_oneoff/apply_refresh_plan_20261112.py           # 干跑
    python scripts/_oneoff/apply_refresh_plan_20261112.py --yes     # 写盘
"""
import sys
import os
import re
import json
import argparse
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

VERIFY_DATE = "2026-11-12"
PLAN = os.path.join(ROOT, "tmp", "refresh_plan_20261112.json")
BACKUP = os.path.join(ROOT, "tmp", "cases_before_refresh_20261112.json")


def set_field(case, path, idx, value):
    if idx is None:
        case[path] = value
    else:
        case[path][idx] = value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    dry = not args.yes

    d = json.load(open(PLAN, encoding="utf-8"))
    plan = d["plan"]
    print("计划 %d 条，正文替换 %d 处" % (len(plan), sum(len(p["edits"]) for p in plan)))

    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    # 防漂移：before 必须与当前值逐字一致
    drift = []
    for p in plan:
        case = by.get(p["id"])
        if case is None:
            drift.append((p["id"], "案例缺失"))
            continue
        for e in p["edits"]:
            cur = case.get(e["path"]) if e["idx"] is None else case[e["path"]][e["idx"]]
            if cur != e["before"]:
                drift.append((p["id"], "%s[%s] 现文与计划不符" % (e["path"], e["idx"])))
        m = case.get("metrics") or {}
        if (m.get("headline") or "") != p["headline"]["before"]:
            drift.append((p["id"], "headline 现文与计划不符"))
        if (m.get("metric_note") or "") != p["note"]["before"]:
            drift.append((p["id"], "metric_note 现文与计划不符"))
    if drift:
        print("防漂移拦截 %d 处，终止：" % len(drift))
        for x in drift[:15]:
            print("   ", x)
        return 1

    if dry:
        print("防漂移全部通过（0 处不符）。干跑结束，未写盘。")
        return 0

    # 备份
    json.dump(cases, open(BACKUP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写盘前备份 ->", BACKUP)

    n_edit = 0
    for p in plan:
        case = by[p["id"]]
        for e in p["edits"]:
            set_field(case, e["path"], e["idx"], e["after"])
            n_edit += 1
        m = case.get("metrics") or {}
        for k, v in p["metrics"].items():
            if v is None and k in ("last_30d_revenue", "all_time"):
                continue
            m[k] = v
        m["headline"] = p["headline"]["after"]
        m["metric_note"] = p["note"]["after"]
        # 页面「增长」行渲染的是 metrics.growth（字符串），必须同步，否则显示旧增速
        if isinstance(p["metrics"].get("revenue_growth_30d"), (int, float)) and m.get("growth"):
            m["growth"] = "%+.1f%%（近 30 天）" % p["metrics"]["revenue_growth_30d"]
        case["metrics"] = m
        case["verified_at"] = VERIFY_DATE

    CS.save_cases(cases)
    print("已写盘：正文 %d 处，台账 %d 条" % (n_edit, len(plan)))

    # ---------- 全量断言回读 ----------
    back = CS.load_cases()
    bby = {c["id"]: c for c in back}
    print("回读案例 %d 条" % len(back))

    fail = []
    for p in plan:
        c = bby[p["id"]]
        m = c.get("metrics") or {}
        mrr = m.get("mrr")
        subs = m.get("subscriptions")
        for k, v in p["metrics"].items():
            if v is None:
                continue
            if abs(float(m.get(k) or 0) - float(v)) > 0.01:
                fail.append((p["id"], "metrics." + k, m.get(k), v))
        if m.get("headline") != p["headline"]["after"]:
            fail.append((p["id"], "headline", m.get("headline"), p["headline"]["after"]))
        if c.get("verified_at") != VERIFY_DATE:
            fail.append((p["id"], "verified_at", c.get("verified_at"), VERIFY_DATE))
        # 正文断言：MRR 数字必须等于新值（1% 容差）；客单价必须等于 mrr/subs
        want_arpo = round(mrr / subs, 2) if subs else None
        for f in ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
                  "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]:
            v = c.get(f)
            texts = []
            if isinstance(v, str):
                texts.append((f, v))
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    if isinstance(x, str):
                        texts.append(("%s[%d]" % (f, i), x))
            for path, t in texts:
                for mt in re.finditer(r"MRR(?!\s*(?:增速|增长|环比))(?:\s|为|达到|达|约|是|：|:|，|、|有){0,12}\$\s?([\d][\d,]*(?:\.\d+)?)", t):
                    got = float(mt.group(1).replace(",", ""))
                    if abs(got - mrr) / max(mrr, 1e-9) > 0.01:
                        fail.append((p["id"], path, "MRR=%s" % got, mrr))
                if want_arpo:
                    for mt in re.finditer(r"客单价(?:约|为)?\s?\$([\d,]+(?:\.\d+)?)", t):
                        got = float(mt.group(1).replace(",", ""))
                        if abs(got - want_arpo) > 0.02:
                            fail.append((p["id"], path, "客单价=%s" % got, want_arpo))

    print("断言不符 %d 处" % len(fail))
    for x in fail[:20]:
        print("   ", x)

    # 越界检查：不在计划内的案例必须完全不变
    before = {c["id"]: c for c in json.load(open(BACKUP, encoding="utf-8"))}
    planned = {p["id"] for p in plan}
    out_of_scope = []
    for cid, c in bby.items():
        if cid in planned:
            continue
        if json.dumps(c, ensure_ascii=False, sort_keys=True) != json.dumps(before[cid], ensure_ascii=False, sort_keys=True):
            out_of_scope.append(cid)
    print("越界改动案例：%d 个" % len(out_of_scope))
    for x in out_of_scope[:10]:
        print("   ", x)

    return 0 if not fail and not out_of_scope else 2


if __name__ == "__main__":
    sys.exit(main())
