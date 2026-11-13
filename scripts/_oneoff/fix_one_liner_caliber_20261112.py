# -*- coding: utf-8 -*-
"""2026-11-12 补修：18 条 last_30d 案例的 one_liner 仍写 MRR + 2 条过期快照补时点。

背景
----
晋升时按用户裁定的「先对齐再晋升」只改了 `metrics.headline` / `metric_note`
首句 / `metrics.caliber` 三处，**`one_liner` 没跟着改**。结果 49 条
`caliber=last_30d` 的案例里 18 条摘要仍写「MRR $Y」，与门面「近 30 天收入 $X」
并存、且全文没有任何一处说明两者关系，最大差 5.5 倍
（qoest 门面 $771 / 摘要 $4,274）。违反「收入口径不许混用」。

另有两条的营收快照本身已过期（qoest 2026-03-19、gostudio-ai 2026-06-30），
门面却写「近 30 天收入 $X」，读者会理解成「现在」。按用户裁定补时点。

铁律
----
  * 新数字只取该案例已有的 `metrics.last_30d_revenue`，不回源、不新造
  * 客单价（$213.7 / $570 / $5.98 / $12.03 / $125.67）是按
    「MRR ÷ 活跃订阅数」算出的派生值，口径换成 last_30d 后不再成立
    ⇒ **删除该分句，不重算**（重算等于造一个新口径的伪精确数字）
  * 只改 `one_liner`；qoest / gostudio-ai 另补 `headline` 与 `metric_note` 的时点
  * 其余字段逐字不动；写前备份、写后逐条断言
  * 缺省干跑，`--yes` 才写盘

用法：python scripts/_oneoff/fix_one_liner_caliber_20261112.py [--yes]
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS          # noqa: E402

TAG = "2026-11-12"
BACKUP = "tmp/cases_before_one_liner_fix_20261112.json"

# id -> (旧 one_liner 全文, 新 one_liner 全文)
# 旧值用于「防漂移」断言：如果库里已被别处改过，直接跳过并报错，不硬覆盖。
ONE_LINER = {
    "launch-club": (
        "Launch Club 靠 Reddit 营销服务，MRR $34,447，19 个活跃订阅。",
        "Launch Club 靠 Reddit 营销服务，近 30 天收入 $57,027，19 个活跃订阅。",
    ),
    "bencho": (
        "Bencho 是 React 组件库，当前 MRR $390，近 30 天收入 $33,165，活跃订阅 49。",
        "Bencho 是 React 组件库，近 30 天收入 $33,165，活跃订阅 49。",
    ),
    "store-stock-checker": (
        "Store Stock Checker 靠监控苹果门店库存，MRR $32,031，上线不到一个月。",
        "Store Stock Checker 靠监控苹果门店库存，近 30 天收入 $32,293，上线不到一个月。",
    ),
    "getlead": (
        "GetLead 是波兰 B2B 获客平台，MRR $1,583，活跃订阅 43 个，累计收入 $106,428。",
        "GetLead 是波兰 B2B 获客平台，近 30 天收入 $7,403，活跃订阅 43 个，"
        "累计收入 $106,428。",
    ),
    "qoest": (
        "Qoest 是 AI 代理 Markdown 档案，MRR $4,274，20 个活跃订阅，客单价 $213.7",
        "Qoest 是 AI 代理 Markdown 档案，近 30 天收入 $771（数据截至 2026-03-19），"
        "20 个活跃订阅。",
    ),
    "ecomstatement-com": (
        "意大利 AI 营销工具，MRR $1,140，2 个活跃订阅，客单价 $570。",
        "意大利 AI 营销工具，近 30 天收入 $3,412，2 个活跃订阅。",
    ),
    "beezz": (
        "BEEZZ 是面向情侣与家庭的共享记账应用，MRR $854，活跃订阅 215 人。",
        "BEEZZ 是面向情侣与家庭的共享记账应用，近 30 天收入 $3,342，活跃订阅 215 人。",
    ),
    "chart-scanner-ai": (
        "Chart Scanner AI 是移动端 AI 金融分析工具，当前 MRR $2,569，活跃订阅 99 份。",
        "Chart Scanner AI 是移动端 AI 金融分析工具，近 30 天收入 $3,013，活跃订阅 99 份。",
    ),
    "gostudio-ai": (
        "GoStudio AI 是印度 AI 影像编辑工具，MRR $598，活跃订阅 100，客单价 $5.98。",
        "GoStudio AI 是印度 AI 影像编辑工具，近 30 天收入 $2,527（数据截至 2026-06-30），"
        "活跃订阅 100。",
    ),
    "canvasninja": (
        "CanvasNinja 是 Chrome 插件，MRR $1,581，靠 SEO 和 UGC 内容获客，服务学生群体。",
        "CanvasNinja 是 Chrome 插件，近 30 天收入 $2,271，靠 SEO 和 UGC 内容获客，服务学生群体。",
    ),
    "catalogohoy": (
        "CatalogoHoy 为拉美小商户提供手机端商品目录与订单管理，当前 MRR $419，活跃订阅 27。",
        "CatalogoHoy 为拉美小商户提供手机端商品目录与订单管理，近 30 天收入 $2,073，活跃订阅 27。",
    ),
    "carpi-speak-learn-spanish": (
        "Carpi 是 iOS 上的西班牙语学习应用，MRR $433，活跃订阅 36，客单价 $12.03。",
        "Carpi 是 iOS 上的西班牙语学习应用，近 30 天收入 $1,498，活跃订阅 36。",
    ),
    "delegated-to": (
        "Delegated.to 为小团队提供 AI 员工仪表盘，MRR $377，活跃订阅 3 个，客单价 $125.67。",
        "Delegated.to 为小团队提供 AI 员工仪表盘，近 30 天收入 $1,275，活跃订阅 3 个。",
    ),
    "morphy": (
        "桌面端 AI 编程代理聚合工具，MRR $501，活跃订阅 70，2026年10月上线",
        "桌面端 AI 编程代理聚合工具，近 30 天收入 $1,062，活跃订阅 70，2026 年 10 月上线。",
    ),
    "deepnia": (
        "Deepnia 是 AI 社媒代运营工具，MRR $447，活跃订阅 38 个，2026 年 9 月上线。",
        "Deepnia 是 AI 社媒代运营工具，近 30 天收入 $935，活跃订阅 38 个，2026 年 9 月上线。",
    ),
    "cliputils": (
        "ClipUtils 是 28+ 在线媒体处理工具的 SaaS 平台，当前 MRR $426，活跃订阅 59。",
        "ClipUtils 是 28+ 在线媒体处理工具的 SaaS 平台，近 30 天收入 $723，活跃订阅 59。",
    ),
    "free-contact-form-to-email-api-for-modern-websites": (
        "Formbold 是免费联系表单后端 API，MRR $656，活跃订阅 93 个，2021 年上线。",
        "Formbold 是免费联系表单后端 API，近 30 天收入 $654，活跃订阅 93 个，2021 年上线。",
    ),
    "homestyler": (
        "HomeStyler 是 iOS 端 AI 室内设计应用，MRR $345，累计收入 $1,051，活跃订阅 73 个。",
        "HomeStyler 是 iOS 端 AI 室内设计应用，近 30 天收入 $176，累计收入 $1,051，"
        "活跃订阅 73 个。",
    ),
}

# 过期快照：id -> (headline 旧, headline 新, metric_note 前缀)
STALE = {
    "qoest": (
        "近 30 天收入 $771，TrustMRR 核对",
        "近 30 天收入 $771（截至 2026-03-19 快照），TrustMRR 核对",
        "⚠ 数据为 2026-03-19 快照，距今约 8 个月未更新。",
    ),
    "gostudio-ai": (
        "近 30 天收入 $2,527，TrustMRR 核对",
        "近 30 天收入 $2,527（截至 2026-06-30 快照），TrustMRR 核对",
        "⚠ 数据为 2026-06-30 快照，距今约 4 个月未更新。",
    ),
}

WATCH = ("one_liner", "caliber", "verified_at")


def main(argv):
    write = "--yes" in argv
    cases = CS.load_cases()
    by_id = {c.get("id"): c for c in cases}
    print("案例库 %d 条" % len(cases))

    plan, skipped = [], []
    for cid, (old, new) in ONE_LINER.items():
        rec = by_id.get(cid)
        if not rec:
            skipped.append((cid, "案例不存在"))
            continue
        cur = rec.get("one_liner") or ""
        if cur == new:
            skipped.append((cid, "已是目标文案"))
            continue
        if cur != old:
            skipped.append((cid, "当前文案与预期不符，跳过（防覆盖）: %r" % cur[:60]))
            continue
        if rec.get("caliber") != "last_30d":
            skipped.append((cid, "caliber=%r 不是 last_30d" % rec.get("caliber")))
            continue
        plan.append((cid, "one_liner", old, new))

    for cid, (h_old, h_new, prefix) in STALE.items():
        rec = by_id.get(cid)
        if not rec:
            skipped.append((cid, "案例不存在"))
            continue
        m = rec.get("metrics") or {}
        if m.get("headline") == h_old:
            plan.append((cid, "headline", h_old, h_new))
        elif m.get("headline") == h_new:
            pass
        else:
            skipped.append((cid, "headline 与预期不符: %r" % str(m.get("headline"))[:60]))
        note = m.get("metric_note") or ""
        if prefix not in note:
            plan.append((cid, "metric_note", note[:40] + "…", prefix + note[:40] + "…"))

    print("计划改动 %d 处 / %d 条案例" % (len(plan), len({p[0] for p in plan})))
    for cid, field, old, new in plan:
        print("  %-46s %-11s %s  ->  %s" % (cid, field, old[:70], new[:70]))
    for cid, why in skipped:
        print("  [!] 跳过 %-40s %s" % (cid, why))

    if not write:
        print("\n干跑结束（未写盘）。确认无误后加 --yes。")
        return 0

    if not plan:
        print("没有可写条目，退出。")
        return 1

    os.makedirs("tmp", exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    print("已备份 -> %s" % BACKUP)

    for cid, field, _old, new in plan:
        rec = by_id[cid]
        if field == "one_liner":
            rec["one_liner"] = new
        elif field == "headline":
            rec["metrics"]["headline"] = new
        elif field == "metric_note":
            note = rec["metrics"].get("metric_note") or ""
            prefix = new.split("…")[0]
            rec["metrics"]["metric_note"] = prefix + note

    n = CS.save_cases(cases)
    print("[write] 写入 %d 条，镜像已重导" % n)

    # ---- 独立回读：重新从库读，逐条断言 ----
    back = {c["id"]: c for c in CS.load_cases()}
    print("\n=== 回读 ===")
    print("案例数 %d（原 %d）id 集合一致=%s"
          % (len(back), len(cases), set(back) == set(by_id)))
    bad = []
    for cid, (old, new) in ONE_LINER.items():
        got = back[cid].get("one_liner")
        if got != new:
            bad.append((cid, "one_liner", got))
    for cid, (h_old, h_new, prefix) in STALE.items():
        m = back[cid].get("metrics") or {}
        if m.get("headline") != h_new:
            bad.append((cid, "headline", m.get("headline")))
        if not str(m.get("metric_note") or "").startswith(prefix):
            bad.append((cid, "metric_note 未带时点", str(m.get("metric_note"))[:40]))
    print("断言不符 %d 处：%s" % (len(bad), bad[:6]))

    # 残留：one_liner 里还有 MRR 且与门面数字不同
    import re

    def norm(s):
        return s.replace(",", "")

    left = []
    for cid in ONE_LINER:
        c = back[cid]
        hl = (c.get("metrics") or {}).get("headline") or ""
        h = re.search(r"\$([\d,]+)", hl)
        mm = re.findall(r"MRR\s*\$?([\d,]+)", c.get("one_liner") or "", re.I)
        if mm and (not h or not any(norm(x) == norm(h.group(1)) for x in mm)):
            left.append((cid, mm))
    print("one_liner 残留冲突口径：%d %s" % (len(left), left[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
