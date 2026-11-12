# -*- coding: utf-8 -*-
"""第 2、3 项第二步：生成「台账 + 正文 + 摘要」统一到 2026-11-12 回源真值的替换计划。

只读，不写库。产物：tmp/refresh_plan_20261112.json
设计要点：
  - 数值替换按**指标前缀**定位（MRR / 近 30 天收入 / 累计 / 订阅数 / 增速 / 客单价），
    不依赖正文里的旧值是否等于 metrics 旧值（正文常停更在更早素材）。
  - 客单价一律按 新 MRR ÷ 新订阅数 重算；订阅数缺失则不动客单价并标记。
  - 同一字段内若同一指标出现多个不同数字（对比句），标记 MANUAL，不自动改。
  - metric_note 只改进「实测口径/近30天收入/MRR/累计/订阅/增速」的数字，保留环比、峰值、留存等原信息。
"""
import sys
import os
import re
import json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]

# 指标 → 正文里可能的前缀（中间允许少量虚词）
# 负向断言：排除「MRR 增速 10%」「MRR 环比 -3%」把百分比当金额
_NOT_PCT = r"(?!\s*(?:增速|增长|环比|同比|下降|上升|下滑|上涨))"
# 中间虚词只允许空白与常见连接词：避免「MRR ÷ 活跃订阅数）为 $11.76」把客单价当成 MRR 金额
_LINK = r"(?:\s|为|达到|达|约|是|：|:|，|、|有){0,12}"
PAT = {
    "mrr": re.compile(r"(MRR)" + _NOT_PCT + "(" + _LINK + r")(\$?\s?)([\d][\d,]*(?:\.\d+)?)"),
    "l30": re.compile(r"(近\s?30\s?天(?:已验证)?收入|近\s?30\s?天收入)" + _NOT_PCT + "(" + _LINK + r")(\$?\s?)([\d][\d,]*(?:\.\d+)?)"),
    "all": re.compile(r"(累计(?:收入)?)" + _NOT_PCT + "(" + _LINK + r")(\$?\s?)([\d][\d,]*(?:\.\d+)?)"),
    "arpo": re.compile(r"(客单价(?:约|为)?)([^0-9$%]{0,6})(\$?\s?)([\d][\d,]*(?:\.\d+)?)"),
}
# 订阅数两种语序都要认：后置「活跃订阅 28 个」/「活跃订阅数 65」+
# 前置「28 个活跃订阅」「36 个付费订阅」
PAT_SUBS_POST = re.compile(r"((?:活跃|付费)订阅(?:数)?)([^0-9]{0,4})(\d[\d,]*)(\s?(?:个|户))?")
PAT_SUBS_PRE = re.compile(r"(\d[\d,]*)(\s?个)((?:活跃|付费)?订阅)")


def sub_subs(text, n):
    """两种语序都替换：'35 个订阅' 与 '活跃订阅 28 个'。"""
    t = PAT_SUBS_PRE.sub(lambda mt: "%d" % n + mt.group(2) + mt.group(3), text)
    t = PAT_SUBS_POST.sub(lambda mt: mt.group(1) + mt.group(2) + "%d" % n + (mt.group(4) or ""), t)
    return t
PAT_PCT_RG = re.compile(r"(近\s?30\s?天(?:收入)?增速)([^0-9+\-]{0,4})([-+]?\d+(?:\.\d+)?)(\s?%)")
PAT_PCT_MG = re.compile(r"(MRR\s?增速)([^0-9+\-]{0,4})([-+]?\d+(?:\.\d+)?)(\s?%)")


def fmt(v, like=None):
    """按原数字的格式（有无千分位、小数位）输出。"""
    if v is None:
        return None
    if like and "." in like:
        dec = len(like.split(".")[1])
        s = ("{:.%df}" % dec).format(v)
    elif isinstance(v, float) and not float(v).is_integer():
        s = ("%.2f" % v).rstrip("0").rstrip(".")
    else:
        s = "%.0f" % v
    if like and "," in like:
        try:
            whole, _, dec = s.partition(".")
            whole = "{:,}".format(int(whole))
            s = whole + ("." + dec if dec else "")
        except ValueError:
            pass
    return s


def walk(case):
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, None, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield f, i, x


def detect_multi(text):
    """同一字段内同一指标出现多个不同数字 → 需人工。"""
    bad = []
    for key in ("mrr", "l30", "all"):
        vals = {m.group(4).replace(",", "") for m in PAT[key].finditer(text)}
        if len(vals) > 1:
            bad.append("%s:%s" % (key, sorted(vals)))
    return bad


def build_plan():
    report = json.load(open(os.path.join(ROOT, "tmp", "refresh_report_20261112.json"), encoding="utf-8"))
    by = {c["id"]: c for c in CS.load_cases()}

    plan, manual, skipped = [], [], []
    for x in report:
        cid = x["id"]
        sync = x.get("sync") or ""
        new = x["new"]
        if not sync.startswith("2026-11") or sync < "2026-11-08" or not new.get("mrr"):
            skipped.append((cid, "回源数据不可用 sync=%s mrr=%s" % (sync, new.get("mrr"))))
            continue
        case = by.get(cid)
        if case is None:
            skipped.append((cid, "案例不在库"))
            continue
        m = case.get("metrics") or {}
        item = {"id": cid, "sync": sync, "edits": []}
        new_mrr = float(new["mrr"])
        new_l30 = float(new["l30"]) if new.get("l30") else None
        new_all = float(new["all"]) if new.get("all") else None
        new_subs = new.get("subs")
        new_subs = int(float(new_subs)) if new_subs else None
        new_mg = new.get("mg")
        new_rg = new.get("rg")
        arpo = round(new_mrr / new_subs, 2) if new_subs else None

        for path, idx, text in walk(case):
            multi = detect_multi(text)
            if multi:
                manual.append((cid, path, idx, "同指标多值 " + ",".join(multi)))
                continue
            t = text

            def repl_num(key, tgt):
                nonlocal t
                if tgt is None:
                    return
                def f(mt):
                    like = mt.group(4)
                    return mt.group(1) + mt.group(2) + mt.group(3) + fmt(tgt, like)
                t = PAT[key].sub(f, t)

            repl_num("mrr", new_mrr)
            repl_num("l30", new_l30)
            repl_num("all", new_all)
            if new_subs:
                t = sub_subs(t, new_subs)
                if arpo:
                    def af(mt, v=arpo):
                        return mt.group(1) + mt.group(2) + mt.group(3) + ("%.2f" % v)
                    t = PAT["arpo"].sub(af, t)
            if new_rg is not None:
                t = PAT_PCT_RG.sub(lambda mt: mt.group(1) + mt.group(2) + "%g" % float(new_rg) + mt.group(4), t)
            if new_mg is not None:
                t = PAT_PCT_MG.sub(lambda mt: mt.group(1) + mt.group(2) + "%g" % float(new_mg) + mt.group(4), t)

            if t != text:
                item["edits"].append({"path": path, "idx": idx, "before": text, "after": t})

        # 台账字段
        item["metrics"] = {
            "mrr": new_mrr,
            "last_30d_revenue": new_l30,
            "all_time": new_all,
            "subscriptions": new_subs,
            "active_subscriptions": new_subs,
        }
        if new_mg is not None:
            item["metrics"]["mrr_growth_30d"] = float(new_mg)
            item["metrics"]["growth_30d"] = float(new_mg)
        if new_rg is not None:
            item["metrics"]["revenue_growth_30d"] = float(new_rg)
        # headline：只换数字
        hl = m.get("headline") or ""
        hl2 = hl
        for key, tgt in (("mrr", new_mrr), ("l30", new_l30), ("all", new_all)):
            if tgt is None:
                continue
            def hf(mt, tgt=tgt):
                return mt.group(1) + mt.group(2) + mt.group(3) + fmt(tgt, mt.group(4))
            hl2 = PAT[key].sub(hf, hl2)
        item["headline"] = {"before": hl, "after": hl2}

        # metric_note：定点换数字
        note = m.get("metric_note") or ""
        n = note
        def nf(mt, tgt=new_mrr):
            return mt.group(1) + mt.group(2) + mt.group(3) + fmt(tgt, mt.group(4))
        if new_mrr:
            n = PAT["mrr"].sub(nf, n)
        if new_l30:
            n = PAT["l30"].sub(lambda mt: mt.group(1) + mt.group(2) + mt.group(3) + fmt(new_l30, mt.group(4)), n)
        if new_all:
            n = PAT["all"].sub(lambda mt: mt.group(1) + mt.group(2) + mt.group(3) + fmt(new_all, mt.group(4)), n)
        if new_subs:
            n = sub_subs(n, new_subs)
        if new_rg is not None:
            n = PAT_PCT_RG.sub(lambda mt: mt.group(1) + mt.group(2) + "%g" % float(new_rg) + mt.group(4), n)
        if new_mg is not None:
            n = PAT_PCT_MG.sub(lambda mt: mt.group(1) + mt.group(2) + "%g" % float(new_mg) + mt.group(4), n)
        item["note"] = {"before": note, "after": n}

        plan.append(item)

    out = {"plan": plan, "manual": manual, "skipped": skipped}
    with open(os.path.join(ROOT, "tmp", "refresh_plan_20261112.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("计划案例 %d 条，正文替换 %d 处，需人工 %d 处，跳过 %d 条"
          % (len(plan), sum(len(p["edits"]) for p in plan), len(manual), len(skipped)))
    print()
    print("--- 需人工（同指标多值）前 12 ---")
    for x in manual[:12]:
        print("   %-26s %-20s %s" % (x[0], x[1], x[3]))
    print()
    print("--- 样本 3 条的正文改写 ---")
    for p in plan[:3]:
        print("=" * 90)
        print(p["id"], "| sync", p["sync"][:10])
        print("  headline:", p["headline"]["before"], "→", p["headline"]["after"])
        for e in p["edits"][:3]:
            print("  [%s]" % e["path"])
            print("    BEFORE:", e["before"][:150])
            print("    AFTER :", e["after"][:150])
    print()
    print("计划已写 tmp/refresh_plan_20261112.json")


if __name__ == "__main__":
    build_plan()
