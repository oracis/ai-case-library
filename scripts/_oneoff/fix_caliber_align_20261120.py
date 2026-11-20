#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复（2026-11-20，PRD #210）：本轮入库 2 条非订阅制案例的口径对齐。

背景
----
本轮 12 条正式入库后 run_promote 验收 FAIL，报两项：
  · caliber vs metrics.caliber 不一致 2 条
    chadme / redstring 顶层 caliber=last_30d（来自 AI 预核草稿），
    metrics.caliber=mrr（来自候选池门面）；
  · last_30d 门面自洽 2/6、口径裁定留痕 0。

用户先按「两个快照数字」的描述裁定「保持 MRR」，看到 AI 草稿
caliber_reason 的真实理由（非订阅制/买断制生意，门面数字必须是
TrustMRR 直读的近 30 天已验证收入）后**改判**：尊重 AI 理由，
把 metrics 对齐 last_30d。

只改这两条的这些字段：
  metrics.caliber      mrr -> last_30d
  metrics.headline     MRR $X -> 近 30 天收入 $Y（$Y 取本条 metrics.last_30d_revenue，
                       不回源、不编数）
  metrics.metric_note  首句口径标签同步 + 末尾追加「口径裁定：…」留痕
  one_liner            MRR $X -> 近 30 天收入 $Y（摘要与门面同口径）
顶层 caliber 已是 last_30d，不动。其余字段逐字不动。

铁律
----
  * 新数字只取该案例已有 metrics.last_30d_revenue，不回源、不重算
  * 写前备份 cases.json + 用 case_store.load_cases() 取真值
  * 单条走 case_store.save_case()（真值 SQLite + 自动重导镜像）
  * 缺省干跑，--yes 才写；写后独立回读 + 逐字段越界检查 + 三方一致

用法：python scripts/_oneoff/fix_caliber_align_20261120.py [--yes]
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS  # noqa: E402

BACKUP = "tmp/cases_before_caliber_align_20261120.json"

# id -> (旧 headline 前缀, 旧 one_liner 前缀, last_30d_revenue 断言值)
EXPECT = {
    "chadme": (
        "MRR $648，TrustMRR 核对",
        "ChadMe 是 AI 颜值评分应用，MRR $648，",
        1522.0,
    ),
    "redstring-mobile-detective-game": (
        "MRR $535，TrustMRR 核对",
        "Redstring 是单人开发的手机侦探游戏，MRR $535，",
        1089.0,
    ),
}

TRACE = ("口径裁定（2026-11-20，用户）：AI 预核判定本条为非订阅制/买断制生意，"
         "门面按 TrustMRR 直读的近 30 天已验证收入计；本条改按 last_30d 口径，"
         "正文 MRR 数字为同期快照，仅作参考，不作门面口径。")


def money(v):
    return "$%s" % format(round(float(v)), ",")


def main(argv):
    write = "--yes" in argv
    cases = CS.load_cases()
    by_id = {c.get("id"): c for c in cases}
    before = {cid: dict(by_id[cid]) for cid in EXPECT if cid in by_id}

    print("候选案例 %d 条（期望 2）" % len(before))
    if len(before) != len(EXPECT):
        print("缺失：%s" % sorted(set(EXPECT) - set(before)))
        return 2

    targets = {}
    for cid, (old_hl, old_ol, rev) in EXPECT.items():
        c = by_id[cid]
        m = c.get("metrics") or {}
        hl = m.get("headline") or ""
        note = m.get("metric_note") or ""
        ol = c.get("one_liner") or ""
        got_rev = m.get("last_30d_revenue")
        if not hl.startswith(old_hl) or not ol.startswith(old_ol):
            print("防漂移失败：%s headline/one_liner 已变，跳过" % cid)
            return 2
        if float(got_rev or 0) != rev or m.get("caliber") != "mrr":
            print("防漂移失败：%s last_30d_revenue/caliber 已变（%r/%r）"
                  % (cid, got_rev, m.get("caliber")))
            return 2
        if c.get("caliber") != "last_30d":
            print("防漂移失败：%s 顶层 caliber 已变（%r）" % (cid, c.get("caliber")))
            return 2

        amt = money(rev)
        new_hl = "近 30 天收入 %s，TrustMRR 核对" % amt
        # 用完整 one_liner 做替换（不是断言前缀），避免丢尾句。
        # 客单价是「MRR ÷ 活跃订阅数」的派生值，换口径后不再成立 ⇒ 删该分句，
        # 「活跃订阅 N 个」是原始事实，保留（同 fix_one_liner_caliber2 惯例）。
        old_num = old_hl.split("MRR $")[1].split("，")[0]
        new_ol = ol.replace("MRR $%s" % old_num, "近 30 天收入 %s" % amt)
        new_ol = re.sub(r"，客单价 \$[\d,.]+。", "。", new_ol)
        # metric_note 首句只换口径标签与数字，其余逐字保留；末尾追加留痕
        old_first = "实测口径：MRR $%s" % old_hl.split("MRR $")[1].split("，")[0]
        new_first = "实测口径：近 30 天收入 %s" % amt
        if not note.startswith(old_first):
            print("防漂移失败：%s metric_note 首句不符：%s" % (cid, note[:60]))
            return 2
        new_note = new_first + note[len(old_first):]
        if "口径裁定（2026-11-20" not in new_note:
            new_note = new_note.rstrip() + "。" + TRACE if new_note.rstrip()[-1:] != "。" \
                else new_note.rstrip() + TRACE

        new_c = dict(c)
        new_m = dict(m)
        new_m["caliber"] = "last_30d"
        new_m["headline"] = new_hl
        new_m["metric_note"] = new_note
        new_c["metrics"] = new_m
        new_c["one_liner"] = new_ol
        targets[cid] = new_c
        print("\n=== %s" % cid)
        print(" headline: %s -> %s" % (hl, new_hl))
        print(" one_liner: %s -> %s" % (ol, new_ol))
        print(" metric_note 首句: %s -> %s" % (old_first, new_first))
        print(" metric_note 留痕尾: ...%s" % new_note[-90:])
        print(" metrics.caliber: mrr -> last_30d")

    if not write:
        print("\n--dry：未写盘（加 --yes 真写）")
        return 0

    import shutil
    shutil.copy2("data/cases.json", BACKUP)
    print("\n备份: %s" % BACKUP)

    for cid, new_c in targets.items():
        CS.save_case(new_c)
        print("写回: %s" % cid)

    # ---------- 独立回读 ----------
    after = CS.load_cases()
    after_by = {c.get("id"): c for c in after}
    print("\n=== 独立回读 ===")
    ok = True
    for cid, new_c in targets.items():
        got = after_by[cid]
        m = got.get("metrics") or {}
        checks = [
            ("metrics.caliber", m.get("caliber"), "last_30d"),
            ("caliber", got.get("caliber"), "last_30d"),
            ("headline", m.get("headline"), new_c["metrics"]["headline"]),
            ("metric_note", m.get("metric_note"), new_c["metrics"]["metric_note"]),
            ("one_liner", got.get("one_liner"), new_c["one_liner"]),
        ]
        for name, a, b in checks:
            if a != b:
                print("  FAIL %s %s: %r != %r" % (cid, name, a, b))
                ok = False
        print("  %s caliber/headline/note/one_liner 回读 %s" %
              (cid, "OK" if all(a == b for _, a, b in checks) else "FAIL"))

    # ---------- 越界：只许 metrics / one_liner 变 ----------
    allowed = {"metrics", "one_liner"}
    over = []
    for cid, old in before.items():
        new = after_by[cid]
        for k in set(old) | set(new):
            if k in allowed:
                continue
            if json.dumps(old.get(k), ensure_ascii=False, sort_keys=True) != \
                    json.dumps(new.get(k), ensure_ascii=False, sort_keys=True):
                over.append((cid, k))
    print("越界字段差异 %d：%s" % (len(over), over[:8]))
    ok = ok and not over

    # ---------- 其余 587 条零改动 ----------
    old_all = {c["id"]: c for c in json.load(open(BACKUP, encoding="utf-8"))}
    changed = [cid for cid, c in old_all.items()
               if cid not in EXPECT and json.dumps(c, ensure_ascii=False, sort_keys=True) !=
               json.dumps(after_by.get(cid), ensure_ascii=False, sort_keys=True)]
    print("其余 %d 条零改动：%s" % (len(old_all) - len(EXPECT), not changed))
    ok = ok and not changed

    # ---------- 三方一致 + 验收四项 ----------
    import sqlite3
    conn = sqlite3.connect("data/ledger.db")
    db_n = conn.execute("SELECT COUNT(*) FROM case_library WHERE deleted=0").fetchone()[0]
    conn.close()
    mirror = json.load(open("data/cases.json", encoding="utf-8"))
    print("三方 SQLite %d / load_cases %d / cases.json %d 一致=%s" %
          (db_n, len(after), len(mirror), db_n == len(after) == len(mirror)))
    ok = ok and db_n == len(after) == len(mirror)

    bad_cal = [(c.get("published_from"), c.get("caliber"),
                (c.get("metrics") or {}).get("caliber")) for c in after
               if c.get("caliber") != (c.get("metrics") or {}).get("caliber")
               and c.get("caliber") is not None]
    aligned = [c for c in after if c.get("caliber") == "last_30d"]
    facade = sum(1 for c in aligned
                 if str((c.get("metrics") or {}).get("headline") or "").startswith("近 30 天收入"))
    traced = sum(1 for c in after if "口径裁定" in str((c.get("metrics") or {}).get("metric_note") or ""))
    print("遗留 caliber 不一致 %d（历史 5 条不在本轮范围）" % len(bad_cal))
    print("last_30d 门面自洽 %d/%d｜口径裁定留痕 %d" % (facade, len(aligned), traced))
    print("\n结论：%s" % ("全部通过" if ok else "存在未通过项"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
