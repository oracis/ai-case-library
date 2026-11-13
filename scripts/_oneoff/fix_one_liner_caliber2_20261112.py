# -*- coding: utf-8 -*-
"""2026-11-12 第二批：历史 13 条 one_liner 与门面口径冲突，并按证据纠正 6 条 caliber。

背景
----
上一轮 `fix_one_liner_caliber_20261112.py` 只修了晋升批次里的 18 条，库里另有
13 条同病（`caliber=last_30d` 但摘要仍写 MRR）。用户裁定「一起修」。

这 13 条不是同一个病，分三组，改法不同：

  A 组 3 条（appmockupgenerator / claw-mart / webclaw）
    门面 headline 已是「近 30 天收入 $X」、caliber=last_30d 也对
    ⇒ 只把 one_liner 的「MRR $Y」换成「近 30 天收入 $X」。

  B 组 6 条（lunarbuild / creativeclaw / rankspot / pdf-vault /
            coldcaller-pro / knightvision）
    门面 headline 写的是「MRR $X」、metric_note 首句也是
    「实测口径：MRR $X」，但 `case.caliber` 标了 `last_30d`
    ⇒ **caliber 字段本身错了**（DATA_SCHEMA:121 定义 caliber
      = 「headline 首数字用哪个口径」；make_article.timepoint_row 会
      把它印成数据卡「口径：近 30 天滚动流水」，读者会把 MRR 读成流水）。
    其中 lunarbuild / creativeclaw / pdf-vault / coldcaller-pro / knightvision
    的 `metrics.caliber` 明确写着 `mrr`，同一对象两字段自相矛盾，
    这是 caliber 错的内证，不是推断。
    ⇒ caliber 改回 mrr；one_liner 的 MRR 数字对齐 `metrics.mrr`（= headline）。

  C 组 4 条（agentsguild / agentskit / aftersora / coaching-marketing-kit）
    非订阅制/买断制，MRR 恒 0，门面已声明「（非订阅制，MRR 恒 0）」，
    caliber=last_30d 正确。但摘要以「MRR $0」开头，读者只看摘要会以为没收入；
    agentsguild 摘要还挂着旧数字 $122（门面已是 $398）。
    ⇒ 去掉「MRR $0」开头，改成产品描述 + 近 30 天收入。

铁律
----
  * 新数字只取该案例已有字段（`metrics.last_30d_revenue` / `metrics.mrr`），
    不回源、不新造 —— 一手 md 是 2026-10-29 的快照，库内是 11-04 核对值，
    两者尾数本就不同（时点差），本次不追尾数，只统一口径
  * 客单价若是「MRR ÷ 订阅数」的派生值（lunarbuild $753.88 = 6031/8），
    口径换掉后不再成立 ⇒ 删分句，不重算
  * 只改 `one_liner` 与 B 组的 `caliber`，其余字段逐字不动；写前备份、写后
    逐字段比对越界改动
  * 缺省干跑，`--yes` 才写盘

用法：python scripts/_oneoff/fix_one_liner_caliber2_20261112.py [--yes]
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS          # noqa: E402

BACKUP = "tmp/cases_before_one_liner_fix2_20261112.json"

# id -> (旧文案, 新文案)。旧值用于防漂移：库里已被别处改过就跳过并报错。
ONE_LINER = {
    # ---- A 组：门面已是近 30 天，只改摘要 ----
    "appmockupgenerator": (
        "App Mockup Generator 靠 AI 为 App 开发者生成应用商店截图，MRR $36，活跃订阅 4 个。",
        "App Mockup Generator 靠 AI 为 App 开发者生成应用商店截图，近 30 天收入 $355，"
        "活跃订阅 4 个。",
    ),
    "claw-mart": (
        "Claw Mart 是 Felix Craft 运营的 AI 配置商店，MRR $1,060，客单价 $20。",
        "Claw Mart 是 Felix Craft 运营的 AI 配置商店，近 30 天收入 $1,576，客单价 $20。",
    ),
    "webclaw": (
        "Webclaw 是面向 AI 代理的网页抓取 API，当前 MRR $362，活跃订阅 9 个。",
        "Webclaw 是面向 AI 代理的网页抓取 API，近 30 天收入 $900，活跃订阅 9 个。",
    ),
    # ---- B 组：门面是 MRR，摘要数字对齐 metrics.mrr；caliber 另改 ----
    "lunarbuild": (
        "LunarBuild 是低代码建站服务，MRR $6,031，8 个活跃订阅，客单价 $753.88",
        "LunarBuild 是低代码建站服务，MRR $3,864，8 个活跃订阅。",
    ),
    "creativeclaw": (
        "CreativeClaw 是面向创意团队的 AI 工作流工具，MRR $11,590，活跃订阅 220 个。",
        "CreativeClaw 是面向创意团队的 AI 工作流工具，MRR $12,240，活跃订阅 220 个。",
    ),
    "rankspot": (
        "RankSpot 是 AI SEO 自动化平台，MRR $1,548，按文章计费，19 个活跃订阅。",
        "RankSpot 是 AI SEO 自动化平台，MRR $1,627，按文章计费，19 个活跃订阅。",
    ),
    "pdf-vault": (
        "卖可转售 PDF 加托管销售页的授权生意：$29 一次性授权 + $9/月托管，当前 MRR $2,214，"
        "活跃订阅 246 个。",
        "卖可转售 PDF 加托管销售页的授权生意：$29 一次性授权 + $9/月托管，当前 MRR $2,133，"
        "活跃订阅 246 个。",
    ),
    "coldcaller-pro": (
        "法国 B2B 外呼线索库 coldcaller.pro：€19–99/月订阅，88 个活跃订阅，MRR $2,817，一人运营。",
        "法国 B2B 外呼线索库 coldcaller.pro：€19–99/月订阅，88 个活跃订阅，MRR $2,810，一人运营。",
    ),
    "knightvision": (
        "AI 图片与视频广告素材生成平台 Knightvision，MRR $7,978，活跃订阅 200 个，客单价 $39.89。",
        "AI 图片与视频广告素材生成平台 Knightvision，MRR $8,018，活跃订阅 200 个，"
        "客单价 $39.89。",
    ),
    # ---- C 组：MRR 恒 0，摘要不以 MRR $0 开头 ----
    "agentsguild": (
        "MRR $0，近 30 天已验证收入 $122，为游戏开发者提供 AI 代理和技能工具包",
        "为游戏开发者提供 AI 代理和技能工具包，近 30 天收入 $398。",
    ),
    "agentskit": (
        "MRR $0，为开发者提供AI工程和营销工具",
        "为开发者提供 AI 工程和营销工具，近 30 天收入 $1,139。",
    ),
    "aftersora": (
        "MRR $0，近 30 天已验证收入 $1,539，为创作者提供 AI 视频和图像生产工具",
        "为创作者提供 AI 视频和图像生产工具，近 30 天收入 $1,539。",
    ),
    "coaching-marketing-kit": (
        "MRR $0，为数字创作者提供可定制的Canva模板",
        "为数字创作者提供可定制的 Canva 模板，近 30 天收入 $2,893。",
    ),
}

# B 组：caliber 写错了，按 headline + metric_note + metrics.caliber 的证据改回 mrr
CALIBER_FIX = (
    "lunarbuild",
    "creativeclaw",
    "rankspot",
    "pdf-vault",
    "coldcaller-pro",
    "knightvision",
)

ALLOWED_FIELDS = {"one_liner", "caliber"}


def _amount(text):
    m = re.search(r"\$([\d,]+)", text or "")
    return m.group(1).replace(",", "") if m else None


def _snapshot_diff(before, after):
    """逐字段比对，返回 [(id, 字段)]，只报顶层键级差异。"""
    diff = []
    for cid, old in before.items():
        new = after.get(cid)
        if new is None:
            diff.append((cid, "<整条缺失>"))
            continue
        for k in set(old) | set(new):
            if json.dumps(old.get(k), ensure_ascii=False, sort_keys=True) != \
                    json.dumps(new.get(k), ensure_ascii=False, sort_keys=True):
                diff.append((cid, k))
    for cid in set(after) - set(before):
        diff.append((cid, "<新增整条>"))
    return diff


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
            skipped.append((cid, "当前文案与预期不符，跳过（防覆盖）: %r" % cur[:50]))
            continue
        plan.append((cid, "one_liner", old, new))

    for cid in CALIBER_FIX:
        rec = by_id.get(cid)
        if not rec:
            skipped.append((cid, "案例不存在"))
            continue
        m = rec.get("metrics") or {}
        hl = m.get("headline") or ""
        # 三重内证：headline 以 MRR 开头、metrics.mrr 存在、数字与 headline 一致
        if not re.match(r"^MRR\s*\$", hl.strip()):
            skipped.append((cid, "headline 不是 MRR 口径，拒绝改 caliber: %r" % hl[:40]))
            continue
        if m.get("mrr") is None:
            skipped.append((cid, "metrics.mrr 缺失，拒绝改 caliber"))
            continue
        if _amount(hl) != _amount("$%s" % (int(m["mrr"]) if float(m["mrr"]).is_integer()
                                           else m["mrr"])):
            skipped.append((cid, "metrics.mrr 与 headline 数字不一致，拒绝改 caliber"))
            continue
        if m.get("caliber") not in (None, "", "mrr"):
            skipped.append((cid, "metrics.caliber=%r 不是 mrr，需人工裁定" % m.get("caliber")))
            continue
        cur_cal = rec.get("caliber") or ""
        if cur_cal == "mrr":
            skipped.append((cid, "caliber 已是 mrr"))
            continue
        if cur_cal != "last_30d":
            skipped.append((cid, "caliber=%r 非 last_30d，跳过" % cur_cal))
            continue
        plan.append((cid, "caliber", "last_30d", "mrr"))

    n_cases = len({p[0] for p in plan})
    print("计划改动 %d 处 / %d 条案例" % (len(plan), n_cases))
    for cid, field, old, new in plan:
        print("  %-24s %-10s %s\n%+38s-> %s" % (cid, field, old, "", new))
    for cid, why in skipped:
        print("  [!] 跳过 %-24s %s" % (cid, why))

    if not write:
        print("\n干跑结束（未写盘）。确认无误后加 --yes。")
        return 0
    if not plan:
        print("没有可写条目，退出。")
        return 1

    os.makedirs("tmp", exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    print("\n已备份 -> %s" % BACKUP)

    before = json.loads(json.dumps(cases, ensure_ascii=False))
    before_by_id = {c["id"]: c for c in before}

    for cid, field, _old, new in plan:
        rec = by_id[cid]
        if field == "one_liner":
            rec["one_liner"] = new
        elif field == "caliber":
            rec["caliber"] = new

    n = CS.save_cases(cases)
    print("[write] 写入 %d 条，镜像已重导" % n)

    # ---------- 独立回读（重新从库读，不用内存对象） ----------
    back_list = CS.load_cases()
    back = {c["id"]: c for c in back_list}
    print("\n=== 回读 ===")
    print("案例数 %d（写前 %d）｜id 集合一致=%s"
          % (len(back), len(before), set(back) == set(before_by_id)))

    bad = []
    for cid, (old, new) in ONE_LINER.items():
        got = back.get(cid, {}).get("one_liner")
        if got != new:
            bad.append((cid, "one_liner", got))
    for cid in CALIBER_FIX:
        got = back.get(cid, {}).get("caliber")
        if got != "mrr":
            bad.append((cid, "caliber", got))
    print("断言不符 %d 处：%s" % (len(bad), bad[:6]))

    # ---------- 越界改动 ----------
    diff = _snapshot_diff(before_by_id, back)
    over = [d for d in diff if d[1] not in ALLOWED_FIELDS]
    print("字段差异 %d 处（允许 one_liner/caliber）｜越界 %d 处：%s"
          % (len(diff), len(over), over[:6]))

    # ---------- 残留：13 条内摘要首金额 vs 门面 ----------
    left = []
    for cid in ONE_LINER:
        c = back[cid]
        m = c.get("metrics") or {}
        hl = m.get("headline") or ""
        ol = c.get("one_liner") or ""
        h = _amount(hl)
        got = re.findall(r"MRR\s*\$?([\d,]+)", ol, re.I)
        if re.match(r"^MRR", hl.strip()):
            if not got or any(g.replace(",", "") != h for g in got):
                left.append((cid, "摘要 MRR 与门面 MRR 不符", got, h))
        else:
            if "近 30 天" not in ol or h not in (ol.replace(",", "")):
                left.append((cid, "摘要未写近 30 天门面数字", got, h))
    print("13 条内口径残留：%d %s" % (len(left), left[:5]))

    # ---------- caliber 与 headline 开头口径是否一致 ----------
    def kind(hl):
        hl = (hl or "").strip()
        for pat, k in ((r"^MRR", "mrr"), (r"^近 30 天", "last_30d"),
                       (r"^ARR", "arr"), (r"^累计", "lifetime")):
            if re.match(pat, hl, re.I):
                return k
        return "other"

    mis = [(cid, back[cid].get("caliber"), kind((back[cid].get("metrics") or {}).get("headline")))
           for cid in list(ONE_LINER) + list(CALIBER_FIX)
           if kind((back[cid].get("metrics") or {}).get("headline")) != "other"
           and back[cid].get("caliber") != kind((back[cid].get("metrics") or {}).get("headline"))]
    print("20 条（13 条 + B 组重合）内 caliber/headline 错位：%d %s" % (len(mis), mis[:5]))

    # ---------- 三方一致性 ----------
    import sqlite3
    conn = sqlite3.connect("data/ledger.db")
    db_n = conn.execute(
        "SELECT COUNT(*) FROM case_library WHERE deleted=0").fetchone()[0]
    conn.close()
    with open("data/cases.json", encoding="utf-8") as f:
        mirror = json.load(f)
    print("三方：SQLite %d ｜ load_cases %d ｜ cases.json %d ｜ 一致=%s"
          % (db_n, len(back_list), len(mirror),
             db_n == len(back_list) == len(mirror)))

    ok = (not bad) and (not over) and (not left) and (not mis)
    print("\n结论：%s" % ("全部通过" if ok else "存在未通过项，请查看上面输出"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
