# -*- coding: utf-8 -*-
"""把缺 `caliber` 的案例按证据补齐 —— 只写「能从原文/数字对得上」的那些。

`caliber` 是 verify_rules 里的 must：空着 = 「未选择收入口径」，一条已发布的
案例在后台会一直挂着这个红项。但补它不能靠猜 —— 口径标错一个，数字就差十倍，
这正是这个库最贵的那条红线。

判据（按顺序，先命中先用）：

  1. `metric_note` 里明写 run-rate / 年化运行率 → `run_rate`
     （headline 常写成 "ARR" 而 note 说清是年化运行率，以 note 为准）
  2. headline 里的口径词：ARR / MRR / 累计 / 年度收入 / 近 30 天 / GMV / 毛利·净利
  3. 没有口径词时，拿 headline 里的数字去和 metrics 的字段对：
     与 mrr 相等 → mrr，与 last_30d_revenue → last_30d，与 arr → arr，
     与 all_time → lifetime（相等判据是相对误差 ≤ 2%）
  4. 都不中 → 不写，报「无法判定」并在报告里给出原因

为什么要有第 3 条：kibu 的 headline 是「$234,319（TrustMRR 榜单值）」——
既没写 MRR 也没写累计，榜单列名本身有歧义。但它的 TrustMRR 档案里
`Current MRR: $234,319`，数字一模一样，这就是证据。反过来 bustem 的档案
已经 404，没有可对的东西，就只能留空，不能拿榜单列名去赌。

用法：
    python scripts/fill_caliber.py            # 只出报告，不写盘
    python scripts/fill_caliber.py --write    # 写回 data/cases.json
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio                                             # noqa: E402
import fetch_tm_md as F                                     # noqa: E402
import verify_rules as VR                                   # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")

# headline 里的口径词 → caliber。顺序有意义：先认最具体的
TOKENS = [
    (re.compile(r"run[- ]?rate|年化运行率", re.I), "run_rate"),
    (re.compile(r"\bARR\b|年化经常性收入", re.I), "arr"),
    (re.compile(r"\bMRR\b|月经常性收入", re.I), "mrr"),
    (re.compile(r"累计|lifetime|历史总", re.I), "lifetime"),
    # 「2025 全年 $1,032,000」这类按自然年合计的已发生收入，2026-10-21 起有
    # annual 可写。放在 lifetime 之后：一条 headline 若同时写「累计」与「全年」，
    # 门面数字通常是累计那个（「开业至今累计 X，其中 2025 全年 Y」）。
    (re.compile(r"全年|年度收入|年度营收|20\d\d\s*年[^0-9\n]{0,4}(?:收入|营收)", re.I), "annual"),
    (re.compile(r"近\s*30\s*天|last\s*30\s*days", re.I), "last_30d"),
    (re.compile(r"\bGMV\b|平台流水", re.I), "gmv"),
    (re.compile(r"毛利|净利|\bgross\b", re.I), "gross"),
]

# metrics 字段 → 口径。数字对得上就用它
METRIC_FIELD = [("mrr", "mrr"), ("last_30d_revenue", "last_30d"),
                ("arr", "arr"), ("all_time", "lifetime")]


def headline_nums(text):
    """抽出 headline 里的金额，统一成绝对数：$1.27M → 1270000，"$202K" → 202000。"""
    out = []
    # 单位后必须不是字母，否则「$20,086 MRR」里的 M 会被当成百万
    # （实测 20086 → 20086000000，差六个数量级）
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s*([MK])?(?![A-Za-z])", text or "", re.I):
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").upper()
        if unit == "M":
            v *= 1_000_000
        elif unit == "K":
            v *= 1_000
        out.append(v)
    return out


def num_match(a, b, tol=0.02):
    if not a or not b:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= tol


NEG_WORDS = re.compile(r"(非|不|未|无法|而非|不是)")


def _affirmative(pat, text, window=12):
    """在 text 里找 pat 的「肯定式」命中 —— 前 12 个字里出现过否定词的不算。

    metric_note 里最常见的句式是「这是累计收入，不是 ARR」「不宜换算成 ARR」：
    词都在，但意思正好相反。checkvibe 就这么被误判成 arr 过 —— 它的 note 是
    「6 周累计毛收入，非月度经常性收入。不宜换算成 ARR」，两个「ARR」都在
    否定句里，真口径是「累计」。
    """
    for m in pat.finditer(text or ""):
        head = text[max(0, m.start() - window):m.start()]
        if not NEG_WORDS.search(head):
            return True
    return False


def tm_profile(case):
    """取 TrustMRR 档案里能用来对数字的字段；拿不到就返回空 dict。"""
    slug = None
    for s in (case.get("sources") or []):
        m = re.search(r"trustmrr\.com/startup/([^/?#]+)", s.get("url") or "")
        if m:
            slug = re.sub(r"\.md$", "", m.group(1))
            break
    if not slug:
        # 有的来源只登记了榜单首页（http://trustmrr.com/），没有 slug。
        # 案例 id 与 slug 通常同名（kibu / coral / postiz…），拿它试一次；
        # 试不中就是 404，返回空，不影响判定。
        slug = str(case.get("id") or "")
    if not slug:
        return {}
    try:
        r = F.fetch(slug)
    except Exception:                                        # noqa: BLE001
        return {}
    md = r[0] if isinstance(r, tuple) else r
    if not md:
        return {}
    s = F.summarize(slug, md)
    return {"slug": slug, "mrr": s.get("cur_mrr"),
            "last_30d": s.get("rev_30d"), "all_time": s.get("all_time")}


def decide(case):
    """返回 (caliber, 理由)；判不了返回 (None, 理由)。"""
    m = case.get("metrics") or {}
    head = m.get("headline") or ""
    note = m.get("metric_note") or ""
    nums = headline_nums(head)

    # 1. note 明确说是年化运行率
    if re.search(r"run[- ]?rate|年化运行率", note, re.I):
        return "run_rate", "metric_note 明写 run-rate/年化运行率（headline 写 ARR 但 note 说清是运行率）"

    # 2. headline 口径词
    for pat, key in TOKENS:
        if pat.search(head):
            # 同一条 headline 里出现两个口径词时（如「累计 $1.27M，但近 30 天 $3K」），
            # 取首个出现的那个 —— 门面数字是它，后面的通常是对比句
            first = min((p.search(head).start() for p, k in TOKENS if k == key), default=0)
            others = [(p.search(head).start(), k) for p, k in TOKENS
                      if k != key and p.search(head) and p.search(head).start() < first]
            if others:
                continue
            return key, "headline 口径词：%s" % head[:40]

    # 2b. headline 没写口径词时，先比数字（数字比措辞硬），再退回 note 的措辞。
    #     kibu 的 headline「$234,319（TrustMRR 榜单值）」没有口径词，note 里反而
    #     同时出现了「MRR」和「累计」两个词 —— 措辞读不出结论，档案里的
    #     `Current MRR: $234,319` 才是一锤定音的那条。
    for field, key in METRIC_FIELD:
        for n in nums:
            if num_match(n, m.get(field)):
                return key, "headline 数字与 metrics.%s 一致" % field
    prof = tm_profile(case)
    for field, key in METRIC_FIELD:
        for n in nums:
            if num_match(n, prof.get(field)):
                return key, "headline 数字与 TrustMRR 档案 %s 一致（slug=%s）" % (field, prof.get("slug"))

    # note 措辞只在不表达歧义时才采信。bustem 的 note 白纸黑字写着
    # 「「Leaderboard MRR / All time」两种口径都说得通，进库前需到自己页面复核」——
    # 一条自己承认没定的说明，不能拿来定口径。
    ambiguous = re.search(r"口径有分歧|口径疑问|两种口径|都说得通|需复核|存疑|无法确认|说不通", note)
    if not ambiguous:
        for pat, key in TOKENS:
            hit = _affirmative(pat, note)
            if hit:
                return key, "metric_note 口径词：%s" % note[:40]

    # 3. 判不了
    if re.search(r"榜单值", head):
        return None, "headline 是榜单快照，且档案取不到数字可对（MRR 与累计两种解释都说得通）"
    return None, "headline 无口径词，数字也与 metrics/档案对不上"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写回 data/cases.json")
    args = ap.parse_args()

    cases = json.load(open(CASES, encoding="utf-8"))
    todo = [c for c in cases if not c.get("caliber")]
    print("缺 caliber：%d 条" % len(todo))

    done, skipped = [], []
    for c in todo:
        key, why = decide(c)
        if key and key in VR.CALIBER_KEYS:
            done.append((c, key, why))
        else:
            skipped.append((c, why))

    print("\n可判定 %d 条：" % len(done))
    for c, k, why in done:
        print("  %-22s → %-9s  %s" % (c["id"], k, why[:64]))
    print("\n判不了 %d 条（保持为空）：" % len(skipped))
    for c, why in skipped:
        print("  %-22s  %s" % (c["id"], why[:78]))

    if not args.write:
        print("\n（未写盘；加 --write 生效）")
        return 0

    for c, k, why in done:
        c["caliber"] = k
        c["updated_at"] = c.get("updated_at") or ""
    text = json.dumps(cases, ensure_ascii=False, indent=2)
    atomicio.write_text(CASES, text + "\n", newline=atomicio.detect_newline(CASES))
    print("\n已写回 %d 条 → %s" % (len(done), os.path.relpath(CASES, ROOT)))
    left = sum(1 for c in cases if not c.get("caliber"))
    print("剩余缺 caliber：%d 条" % left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
