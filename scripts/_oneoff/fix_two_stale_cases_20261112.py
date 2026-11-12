# -*- coding: utf-8 -*-
"""第 1 项：claw-mart / webclaw 两条台账门面数字滞后，回源 2026-11-12 一手快照整体刷新。

背景
----
两条的 corrections 已写明 metrics 那组是旧同步：
  claw-mart  近30天 $1,576 / MRR $1,140 来自 2026-10-14 旧同步
  webclaw    近30天 $900    / MRR $400   为 2026-09 月数据
但 corrections 里转述的「最新值」也不准（claw-mart 写 $1,355，一手原文是 $1,335）。
本轮不照抄转述，直接回源 https://trustmrr.com/startup/<slug>.md 取 2026-11-12 快照。

回源结果（Revenue data last synced）
  claw-mart  2026-11-12T15:35:38Z  MRR 980  近30天 1,434  累计 184,454  订阅 49  收入增速 -19.3%  MRR增速 -15.5%
  webclaw    2026-11-12T10:03:44Z  MRR 343  近30天 343    累计 6,628    订阅 8   收入增速 -61.9%  MRR增速 -14.2%

自洽校验：两条的 all_time 均等于一手月度收入合计
  claw-mart 35,023+107,721+21,547+7,384+3,796+2,795+2,133+2,073+1,455+527 = 184,454 ✓
  webclaw   49.5+367+709+3,639+408+906+362+188                        = 6,628.5 ✓
客单价复算：claw-mart 980/49 = 20.00 ✓（文中 $20 不用改）；webclaw 343/8 = 42.875 → $42.88

用法
----
    python scripts/_oneoff/fix_two_stale_cases_20261112.py          # 干跑
    python scripts/_oneoff/fix_two_stale_cases_20261112.py --yes    # 写盘
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

VERIFY_DATE = "2026-11-12"

# 字段级替换：(case_id, 容器, 定位, 旧串, 新串)。旧串必须唯一命中，否则跳过并报警。
TEXT_EDITS = [
    # ---- claw-mart：正文本来就写对了（截至 11-03 的 $1,060/53），随回源推进到 11-12 ----
    ("claw-mart", "what_it_does", None,
     "截至 2026 年 11 月 3 日，活跃订阅数为 53，MRR 为 $1,060。",
     "截至 2026 年 11 月 12 日，活跃订阅数为 49，MRR 为 $980。"),
    ("claw-mart", "how_it_makes_money", None,
     "按 MRR $1,060 除以活跃订阅数 53 计算，客单价为 $20。",
     "按 MRR $980 除以活跃订阅数 49 计算，客单价为 $20。"),
    ("claw-mart", "why_it_works", 1,
     "活跃订阅数 53 对应 MRR $1,060",
     "活跃订阅数 49 对应 MRR $980"),
    ("claw-mart", "why_it_works", 2,
     "近 30 天已验证收入 $1,335",
     "近 30 天已验证收入 $1,434"),
    ("claw-mart", "one_liner", None,
     "近 30 天收入 $1,576，客单价 $20。",
     "近 30 天收入 $1,434，客单价 $20。"),
    # ---- webclaw ----
    ("webclaw", "what_it_does", None,
     "当前 MRR 为 $362，活跃订阅数 9 个。",
     "当前 MRR 为 $343，活跃订阅数 8 个。"),
    ("webclaw", "how_it_makes_money", None,
     "当前 MRR $362 除以活跃订阅 9 个，得出客单价 $40.22。",
     "当前 MRR $343 除以活跃订阅 8 个，得出客单价 $42.88。"),
    ("webclaw", "why_it_works", 0,
     "素材显示当前 MRR $362，活跃订阅 9 个，客单价 $40.22。",
     "素材显示当前 MRR $343，活跃订阅 8 个，客单价 $42.88。"),
    ("webclaw", "why_it_works", 2,
     "素材显示近 30 天增速 -58.9%，MRR 增速 -9.5%。",
     "素材显示近 30 天增速 -61.9%，MRR 增速 -14.2%。"),
    ("webclaw", "one_liner", None,
     "近 30 天收入 $900，活跃订阅 9 个。",
     "近 30 天收入 $343，活跃订阅 8 个。"),
]

# 指标与门面（整字段覆盖，值来自回源）
METRIC_SETS = {
    "claw-mart": {
        "mrr": 980.0,
        "last_30d_revenue": 1434.0,
        "all_time": 184454.0,
        "subscriptions": 49,
        "active_subscriptions": 49,
        "growth": "-19.3%（近 30 天收入）",
        "headline": "近 30 天收入 $1,434",
        "caliber": "last_30d",
        "metric_note": (
            "实测口径：近 30 天收入 $1,434（TrustMRR 从支付网关 API 直读，2026-11-12 同步，Stripe）。"
            "MRR $980、累计 $184,454、活跃订阅 49 户，客单价约 $20（MRR ÷ 订阅数）。"
            "近 30 天收入增速 -19.3%，MRR 增速 -15.5%（两者口径不同，讲增长用 MRR）。"
            "⚠ 该业务主体是一次性买断的数字商品，MRR 不能代表全部门面收入。"
        ),
    },
    "webclaw": {
        "mrr": 343.0,
        "last_30d_revenue": 343.0,
        "all_time": 6628.0,
        "subscriptions": 8,
        "active_subscriptions": 8,
        "growth": "-61.9%（近 30 天收入）",
        "headline": "近 30 天收入 $343",
        "caliber": "last_30d",
        "metric_note": (
            "实测口径：近 30 天收入 $343（TrustMRR 从支付网关 API 直读，2026-11-12 同步，Stripe）。"
            "MRR $343、累计 $6,628、活跃订阅 8 户，客单价约 $42.88（MRR ÷ 订阅数）。"
            "近 30 天收入增速 -61.9%，MRR 增速 -14.2%（两者口径不同，讲增长用 MRR）。"
        ),
    },
}

# 纠错记录订正：claim 保留（它本就要引用被纠正的旧说法），truth 补最新复核值
CORRECTION_FIX = {
    "claw-mart": [
        ("最新一次同步（2026-11-03T03:32:06Z）的 Last 30 days 为 $1,355，页面另存快照值 $1,395；这组数字来自 2026-10-14 的旧同步，已过期",
         "2026-11-03 同步的 Last 30 days 为 $1,335（一手 .md 原文）；$1,576 来自 2026-10-14 的旧同步，已过期。"
         "2026-11-12 回源复核已更新为 $1,434（Revenue data last synced 2026-11-12T15:35:38Z）"),
        ("页面 Current MRR 为 $1,060，活跃订阅 53 个，近 30 天 MRR 增速 -14.5%；且该业务主体是一次性买断商品，MRR 不能代表全部门面收入",
         "页面 Current MRR 为 $1,060，活跃订阅 53 个，近 30 天 MRR 增速 -14.5%；且该业务主体是一次性买断商品，MRR 不能代表全部门面收入。"
         "2026-11-12 回源复核：MRR $980、活跃订阅 49、近 30 天 MRR 增速 -15.5%"),
        ("页面 All time / Last 12 months 均为 $183,947（该业务 2026-02 上线，一年内累计≈全时段累计）",
         "页面 All time / Last 12 months 均为 $183,947（该业务 2026-02 上线，一年内累计≈全时段累计）。"
         "2026-11-12 回源复核：$184,454"),
    ],
    "webclaw": [
        ("实际 TrustMRR 档案显示 Last 30 days revenue 为 $362（同步于 2026-11-03），$900 为旧数据（2026-09 月数据）",
         "实际 TrustMRR 档案显示 Last 30 days revenue 为 $362（同步于 2026-11-03），$900 为旧数据（2026-09 月数据）。"
         "2026-11-12 回源复核：$343"),
        ("实际 Current MRR 为 $362",
         "实际 Current MRR 为 $362。2026-11-12 回源复核：$343，活跃订阅 8"),
    ],
}


def get_container(case, field, idx):
    v = case.get(field)
    if idx is None:
        return ("str", None)
    if isinstance(v, list):
        return ("list", v)
    return ("str", None)


def apply_text_edits(cases, dry):
    by = {c["id"]: c for c in cases}
    applied, skipped = 0, []
    for cid, field, idx, old, new in TEXT_EDITS:
        case = by.get(cid)
        if case is None:
            skipped.append((cid, field, idx, "案例不存在"))
            continue
        kind, cont = get_container(case, field, idx)
        if kind == "list":
            if idx >= len(cont) or not isinstance(cont[idx], str):
                skipped.append((cid, field, idx, "下标越界"))
                continue
            cur = cont[idx]
            if cur.count(old) != 1:
                skipped.append((cid, field, idx, "旧串命中 %d 次" % cur.count(old)))
                continue
            if not dry:
                cont[idx] = cur.replace(old, new)
            applied += 1
        else:
            cur = case.get(field)
            if not isinstance(cur, str):
                skipped.append((cid, field, idx, "字段非字符串"))
                continue
            if cur.count(old) != 1:
                skipped.append((cid, field, idx, "旧串命中 %d 次" % cur.count(old)))
                continue
            if not dry:
                case[field] = cur.replace(old, new)
            applied += 1
    return applied, skipped


def apply_metrics(cases, dry):
    by = {c["id"]: c for c in cases}
    for cid, vals in METRIC_SETS.items():
        case = by[cid]
        m = case.get("metrics")
        if not isinstance(m, dict):
            m = {}
            case["metrics"] = m
        if not dry:
            m.update(vals)
            case["verified_at"] = VERIFY_DATE
    return len(METRIC_SETS)


def apply_corrections(cases, dry):
    by = {c["id"]: c for c in cases}
    applied, skipped = 0, []
    for cid, pairs in CORRECTION_FIX.items():
        case = by[cid]
        corr = case.get("corrections") or []
        for old, new in pairs:
            hit = None
            for item in corr:
                if isinstance(item, dict) and isinstance(item.get("truth"), str) and item["truth"] == old:
                    hit = item
                    break
            if hit is None:
                skipped.append((cid, "truth 未逐字命中", old[:36]))
                continue
            if not dry:
                hit["truth"] = new
            applied += 1
    return applied, skipped


# ---------- 校验 ----------
EXPECT = {
    "claw-mart": {"mrr": 980.0, "l30": 1434.0, "all": 184454.0, "subs": 49,
                  "hl_tok": "$1,434", "ol_tok": "$1,434",
                  "no": ["$1,576", "$1,140", "$183,040"]},
    "webclaw": {"mrr": 343.0, "l30": 343.0, "all": 6628.0, "subs": 8,
                "hl_tok": "$343", "ol_tok": "$343",
                "no": ["$900", "$6,285"]},
}


def read_back():
    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}
    bad = []
    for cid, e in EXPECT.items():
        c = by[cid]
        m = c.get("metrics") or {}
        if abs((m.get("mrr") or -1) - e["mrr"]) > 0.5:
            bad.append((cid, "mrr", m.get("mrr"), e["mrr"]))
        if abs((m.get("last_30d_revenue") or -1) - e["l30"]) > 0.5:
            bad.append((cid, "last_30d", m.get("last_30d_revenue"), e["l30"]))
        if abs((m.get("all_time") or -1) - e["all"]) > 0.5:
            bad.append((cid, "all_time", m.get("all_time"), e["all"]))
        if m.get("subscriptions") != e["subs"]:
            bad.append((cid, "subs", m.get("subscriptions"), e["subs"]))
        if e["hl_tok"] not in (m.get("headline") or ""):
            bad.append((cid, "headline", m.get("headline"), e["hl_tok"]))
        if e["ol_tok"] not in (c.get("one_liner") or ""):
            bad.append((cid, "one_liner", c.get("one_liner"), e["ol_tok"]))
    return cases, bad


def stale_scan(cases):
    """改后：正文/摘要里不应再有被作废的旧值（corrections 里作为 claim 引用除外）。"""
    by = {c["id"]: c for c in cases}
    issues = []
    for cid, e in EXPECT.items():
        c = by[cid]
        for f, v in c.items():
            if f in ("corrections",):
                continue
            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            for old in e["no"]:
                if old in s:
                    issues.append((cid, f, old))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真正写盘（默认干跑）")
    args = ap.parse_args()
    dry = not args.yes

    cases = CS.load_cases()
    print("载入案例 %d 条，模式=%s" % (len(cases), "干跑" if dry else "写盘"))

    n_text, skipped_text = apply_text_edits(cases, dry)
    n_metric = apply_metrics(cases, dry)
    n_corr, skipped_corr = apply_corrections(cases, dry)

    print("正文替换命中 %d 处（预期 %d）" % (n_text, len(TEXT_EDITS)))
    for s in skipped_text:
        print("  [SKIP] %s / %s[%s] %s" % s)
    print("指标覆盖 %d 条（预期 %d）" % (n_metric, len(METRIC_SETS)))
    print("纠错记录订正 %d 处（预期 %d）" % (n_corr, sum(len(v) for v in CORRECTION_FIX.values())))
    for s in skipped_corr:
        print("  [SKIP] %s %s | %s" % s)

    if skipped_text or skipped_corr:
        print("\n存在跳过项，终止（防漂移：旧串不再逐字匹配，说明数据已被改动）")
        return 1

    if dry:
        print("\n干跑结束，未写盘。加 --yes 生效。")
        return 0

    CS.save_cases(cases)
    print("\n已写盘。开始独立回读……")
    back, bad = read_back()
    print("回读案例 %d 条" % len(back))
    if bad:
        print("断言不符 %d 处：" % len(bad))
        for b in bad:
            print("  ", b)
    else:
        print("断言不符 0")
    st = stale_scan(back)
    print("旧值残留（排除 corrections 引用）：%d 处" % len(st))
    for x in st:
        print("  ", x)
    return 0 if not bad and not st else 2


if __name__ == "__main__":
    sys.exit(main())
