# -*- coding: utf-8 -*-
"""2026-11-12 人工裁定：晋升前对齐口径错位 + 补齐 3 条必勾。

背景
----
81 条候选里有 19 条 `metrics.caliber=mrr`，而 AI 核实草稿判 `caliber=last_30d`。
`server.promote` 的 `verify_rules.resolve_case_timepoint()` 以**草稿为准**写案例
`caliber`，但 `metrics` 整本从候选搬 —— 不处理就会得到「案例 caliber=last_30d、
headline 却写 MRR $X」的自相矛盾案例（红线 1：收入口径不许混用）。

另有 3 条历史草稿缺 `caliber_decided` 必勾被 promote 预检拦下；草稿里 `caliber`
已有值，按用户裁定补勾（**不代勾 human_read**）。

铁律
----
  * 门面数字一律取候选已有的 `metrics.last_30d_revenue`，不回源、不编数
  * 只改 headline / metric_note 首句口径标签 / metrics.caliber 三处，其余逐字不动
  * `caliber_consistent_ai=False` 是 AI 当时的判断记录，保留不洗
  * 写前备份、写后逐条断言、格式按各文件原生换行保持
  * 缺省干跑，`--yes` 才写盘

用法：python scripts/_oneoff/align_caliber_20261112.py [--yes]
"""
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio          # noqa: E402
import candidate_store as CSt   # noqa: E402
import verify_rules as VR       # noqa: E402

CAND = "data/candidates.json"
VER = "data/verifications.json"
TAG = "2026-11-12"
EXPECT_MISALIGNED = 19
BLOCKED = ("get-funneled", "footlab-ai", "sonder")


def money(v):
    return "${:,}".format(int(round(float(v))))


def main():
    yes = "--yes" in sys.argv
    cands = CSt.load_candidates(path=CAND)
    with open(VER, "r", encoding="utf-8") as f:
        vers = json.load(f)
    by_id = {c["id"]: c for c in cands}

    targets = sorted(k for k in by_id
                     if (by_id[k].get("metrics") or {}).get("caliber") == "mrr"
                     and vers.get(k, {}).get("caliber") == "last_30d")
    assert len(targets) == EXPECT_MISALIGNED, (
        "口径错位条数漂移：预期 %d，实测 %d" % (EXPECT_MISALIGNED, len(targets)))

    planned = []
    for k in targets:
        m = by_id[k]["metrics"]
        rev = m.get("last_30d_revenue")
        assert isinstance(rev, (int, float)) and rev > 0, "%s 缺 last_30d_revenue" % k
        note = m.get("metric_note") or ""
        assert note.startswith("实测口径：MRR $"), "%s metric_note 首句格式不符" % k
        new_head = "近 30 天收入 %s，TrustMRR 核对" % money(rev)
        new_note, n = re.subn(
            r"^实测口径：MRR \$[\d,]+(?:\.\d+)?",
            lambda _m: "实测口径：近 30 天收入 " + money(rev), note, count=1)
        assert n == 1, "%s 首句替换失败" % k
        new_note += (" ⚠ 口径裁定 %s：AI 核验判该条不适用 MRR 门面口径，"
                     "改以支付侧近 30 天已验证收入为准；MRR 仅作缓存快照保留。" % TAG)
        planned.append((k, m["headline"], new_head, new_note, rev))

    print("口径错位对齐 %d 条（缺省干跑，--yes 才写盘）：" % len(planned))
    for k, old, new, _note, _rev in planned:
        print("  %-46s %s -> %s" % (k[:46], old, new))
    print("\n补齐必勾 %d 条：%s" % (len(BLOCKED), "、".join(BLOCKED)))
    for k in BLOCKED:
        d = vers.get(k) or {}
        assert d.get("caliber"), "%s 草稿没有 caliber，不能补勾" % k
        print("  %-46s caliber=%s musts=%s -> +caliber_decided" %
              (k, d.get("caliber"), d.get("musts")))

    if not yes:
        print("\n干跑：未写盘")
        return 0

    for src, tag in ((CAND, "candidates"), (VER, "verifications")):
        shutil.copy2(src, "tmp/%s_before_caliber_align_%s.json" %
                     (tag, TAG.replace("-", "")))

    for k, _old, new_head, new_note, _rev in planned:
        m = by_id[k]["metrics"]
        m["headline"] = new_head
        m["metric_note"] = new_note
        m["caliber"] = "last_30d"
    for k in BLOCKED:
        d = vers[k]
        d["musts"] = list(dict.fromkeys((d.get("musts") or []) + ["caliber_decided"]))
        assert VR.evaluate(d)["publishable"], "%s 补勾后仍不可发布" % k

    CSt.save_candidates(cands, path=CAND)
    atomicio.write_json(VER, vers, indent=2, newline=atomicio.detect_newline(VER))
    print("\n已写盘：data/candidates.json + data/verifications.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
