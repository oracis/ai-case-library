#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-05：把 tmp/draft_text.json 里 129 条存量草稿的违禁措辞清干净。

判据 = draft_case_text.WORDING_BANS 的 6 类（**不扩**）；
清洗 = 同模块 scrub_wording()（本轮已把 SCRUB_RULES 补齐到覆盖旧稿全部形态）。

为什么不扩判据：全量体检显示判据外形态混着正当叙述
（「MRR ÷ 活跃订阅算好，直接引用」46 条是 SYSTEM 允许的写法），
内存试跑一刀切规则后 161 条改动且造出「均由TrustMRR」「用 TrustMRR
档案记录收入」等病句 ⇒ 不采纳，单列清单交下一轮。

只写 tmp/draft_text.json；不碰 data/、不 apply、不晋升、不发布、不 push。
幂等：备份只在不存在时创建，统计以备份为基准；重跑零改动、报告一致。
"""
import collections
import copy
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio            # noqa: E402
import draft_case_text as D  # noqa: E402
import publish_guard as PG   # noqa: E402
import scrub_jargon as SJ    # noqa: E402

STAGE = os.path.join(ROOT, "tmp", "draft_text.json")
BAK = os.path.join(ROOT, "tmp", "draft_text.bak-20261105-wording.json")
TM = os.path.join(ROOT, "tmp", "tm_md")
SUMMARY = os.path.join(TM, "summary.json")
REPORT = os.path.join(ROOT, "tmp", "htm_screen", "scrub_stock_wording_20261105.md")
RESIDUAL = os.path.join(ROOT, "tmp", "htm_screen", "wording_residual_20261105.md")
NEW_IDS = ("catering-funnels", "talkeasy", "bubblav", "arq")  # 上一轮已清洗，须零变化

STAMP = "2026-11-05"


def money(d):
    return set(re.findall(r"\$[\d,]+(?:\.\d+)?",
                          json.dumps(d, ensure_ascii=False)))


def visible(d):
    return "\n".join(t for _f, t in D._visible_texts(d))


def main():
    if not os.path.exists(BAK):
        with io.open(STAGE, "rb") as f, io.open(BAK, "wb") as g:
            g.write(f.read())
        print("备份原始草稿 -> %s" % os.path.basename(BAK))

    orig = json.load(io.open(BAK, encoding="utf-8"))       # 统计基准（重跑稳定）
    stage = json.load(io.open(STAGE, encoding="utf-8"))
    before = copy.deepcopy(stage)                          # 本次运行前的快照
    summ = json.load(io.open(SUMMARY, encoding="utf-8"))
    raw = io.open(STAGE, "rb").read()

    # ---- 基准统计（以原始备份为准，重跑也稳定）----
    hits_before = {k: D.wording_bans(v) for k, v in orig.items()}
    bad_ids = [k for k, v in hits_before.items() if v]
    label_cnt = collections.Counter(
        h.split(" @ ")[0] for k in bad_ids for h in hits_before[k])

    changed, total_repl, changed_labels = [], 0, collections.Counter()
    preexisting_blocks = []
    for k, v in stage.items():
        old_hits = D.wording_bans(v)
        out, n = D.scrub_wording(v)
        if out == v:
            continue

        # ---- 逐条硬断言 ----
        assert money(out) == money(v), "%s 金额集合被改动" % k
        assert not D.wording_bans(out), "%s 清洗后仍违规: %s" % (k, D.wording_bans(out))
        again, n2 = D.scrub_wording(out)
        assert n2 == 0 and again == out, "%s 清洗不幂等" % k

        # 素材齐备时回套四道守卫（无素材的 5 条与 --revalidate 一样跳过）
        slug = v.get("_slug") or k
        s = D.lookup_summary(summ, slug, k) or {}
        md_path = os.path.join(TM, slug + ".md")
        md = io.open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
        if s or md:
            src = D.extract_numbers(D.facts_card(slug, s, md))
            assert not D.validate(out, src, s), "%s 清洗后 validate 变红" % k
        # 相对判据：存量稿自身已带黑话/block（如「候选」），不是清洗引入的。
        # 只要求「不许新增」，不要求清洗后归零 —— 归零属另一轮 scrub_jargon 批次。
        txt0 = visible(v)
        txt = visible(out)
        blocks0 = [x for x in PG.scan_content("wechat", txt0)
                   if x["level"] == "block"]
        blocks = [x for x in PG.scan_content("wechat", txt)
                  if x["level"] == "block"]
        assert {x["rule"] for x in blocks} <= {x["rule"] for x in blocks0}, \
            "%s 清洗引入新的发布闸 block: %s" % (k, blocks)
        assert (not SJ.JARGON.search(txt)) or SJ.JARGON.search(txt0), \
            "%s 清洗引入黑话" % k
        if blocks0:
            preexisting_blocks.append(k)

        # ---- 留痕（与 draft_one() 产出的字段同名）----
        out["_wording_scrubbed"] = n
        out["_wording_hits"] = []
        out["_wording_fixed_at"] = STAMP
        stage[k] = out
        changed.append(k)
        total_repl += n
        for h in old_hits:
            changed_labels[h.split(" @ ")[0]] += 1

    # ---- 全局硬断言 ----
    assert [k for k in stage if D.wording_bans(stage[k])] == [], "全库仍有违规措辞"
    for cid in NEW_IDS:
        assert stage[cid] == before[cid], "上一轮已清洗的 4 条被改动: %s" % cid
    untouched = [k for k in before if k not in changed and stage[k] != before[k]]
    assert not untouched, "无关键被改动: %s" % untouched[:5]
    assert set(stage) == set(orig), "键集合变化"

    # 幂等重跑：0 改动时不重写文件、不覆盖首次报告（报告里「实际改动 N 条」
    # 指的是首次运行，重跑覆盖成 0 会让交付记录失真）。
    if not changed and os.path.exists(REPORT):
        print("已清洗（幂等重跑）：0 条改动，保留首次报告与产物")
        return

    atomicio.write_json(
        STAGE, stage, indent=2,
        newline=atomicio.detect_newline(STAGE),
        trailing_newline=raw.endswith(b"\n"))

    # ---- 判据外残留体检（只读，供下一轮，不据此改动）----
    ext_pats = collections.OrderedDict((
        ("任意 `API 直读`", r"API\s*直读"),
        ("任意 `支付网关直读`", r"支付网关直读"),
        ("`已验证收入`（非近 30 天前缀）", r"已验证收入"),
        ("`已算好`（非裸批注）", r"已算好"),
        ("`÷ / 除以活跃订阅`", r"除以活跃订阅数|÷\s*活跃订阅"),
        ("`定价压在`", r"定价压在"),
    ))
    ext_rows = {}
    for lab, pat in ext_pats.items():
        rx = re.compile(pat)
        rows = [(k, f, t) for k, v in stage.items()
                for f, t in D._visible_texts(v) if rx.search(t)]
        ext_rows[lab] = rows

    # ---- 报告 ----
    lines = [
        "# 存量 129 条措辞清洗 · 2026-11-05", "",
        "> **只写 `tmp/draft_text.json`**；未写 `data/`、未跑 `apply_draft_text.py`、",
        "> 未晋升、未发布、未 push、未联网、未调 LLM。",
        "> 判据 = `draft_case_text.WORDING_BANS` 的 6 类（**未扩**）；",
        "> 清洗 = 同模块 `scrub_wording()`（本轮已补齐 `SCRUB_RULES`）。", "",
        "## 一、结果", "",
        "| 项 | 清洗前 | 清洗后 |", "|---|---|---|",
        "| 违禁措辞命中条数 | %d | **0** |" % len(bad_ids),
        "| 实际改动条数 | — | %d |" % len(changed),
        "| 规则替换总次数 | — | %d |" % total_repl,
        "| 金额集合被改动的条数 | — | **0** |",
        "| 4 条上一轮已清洗新稿 | — | 逐键零变化 |",
        "| 未改动键 | — | 逐键零变化 |", "",
        "命中类型分布（清洗前，按命中次数）：", "",
        "| 类型 | 次数 |", "|---|---|",
    ]
    for lab, n in label_cnt.most_common():
        lines.append("| %s | %d |" % (lab, n))
    lines += [
        "", "> 改动 %d 条 = 判据命中 %d 条 + 混合批注变体 %d 条（如" % (
            len(changed), len(bad_ids), len(changed) - len(bad_ids)),
        "> `（支付网关直读，已算好）` 不被 `wording_bans()` 命中，但确属同类批注，",
        "> 由括号规则顺带清掉，金额不变）。", "",
        "## 二、为什么先补规则（根因，不是逐句打补丁）", "",
        "首版 `SCRUB_RULES` 只认 4 条新稿的句式。全量只读预演发现：", "",
        "| 预演 | 结果 |", "|---|---|",
        "| 补齐前对 129 条跑 `scrub_wording()` | **38 条洗完仍违规**（46 处直读断言 + 5 处除法复述）|",
        "| 另有 | **11 条命中但零改动**（`收入由支付网关 API 直读` 等形态无对应替换）|",
        "| 补齐后 | **0 条残留**、0 条金额变化、实际改动 131 条 |", "",
        "本轮在 `scripts/draft_case_text.py` 的 `SCRUB_RULES` 补齐 7 类真实形态：",
        "「收入数据经 X API 直读验证」「收入由 X 与 Y 两个支付网关」「收入由/来自/通过 X 支付网关」",
        "「收入由支付网关」「，由 X 支付网关」「用/使用 X 等支付网关 API 直读收入」",
        "「（支付网关…）」混合批注，以及把「除以活跃订阅数」两条窄规则并成一条",
        "（兼容 `推算客单价约/为`、`得出客单价/为` 四种收尾）。",
        "锁在 `scripts/test_draft_case_text.py::StockWording`（7 项，用真实存量原句），",
        "`test_draft_case_text.py` **51 tests OK**。", "",
        "## 三、逐条硬断言（脚本内 assert，全过）", "",
        "- 金额集合 `set($…)` 前后**完全一致**（含金额替换全部反向引用带回数字）。",
        "- 清洗后 `wording_bans()` 为空；二次 `scrub_wording()` 零替换（幂等）。",
        "- 素材齐备的条目回套 `validate()` 四道守卫全绿；无素材的 5 条按 `--revalidate` 同款保护跳过。",
        "- 发布闸 block / 黑话用**相对判据**：清洗后 rule 集合不得比清洗前多（不许新增）；",
        "  本次改动条目中有 %d 条原本就自带发布闸 block（如 `sotie-ai-best-friend` 的" % len(set(preexisting_blocks)),
        "  「候选」），保持原状；**这不是全库计数**，归零属另一轮 `scrub_jargon` 批次。",
        "- 4 条新稿与所有未改动键逐键零变化；键集合仍是 %d 条。" % len(stage), "",
        "## 四、判据外残留（本轮**不动**，单列清单）", "",
        "全量体检发现判据外还有同类形态，但**不能一刀切**：内存里试过一组扩展规则，",
        "会把 `均由支付网关直读` 改成 `均由TrustMRR 档案记录…`、`用支付网关直读收入`",
        "改成 `用 TrustMRR 档案记录收入`（161 条改动、多处病句）；",
        "而 `÷ 活跃订阅` 里 46 条是 SYSTEM 允许的「直接引用算好的客单价」正当叙述。",
        "按项目「形状闸先跑全量看误报率」的教训，留给下一轮先定判据再扩。", "",
        "| 形态 | 命中条数 | 片段数 | 判定 |", "|---|---|---|---|",
    ]
    judge = {
        "任意 `API 直读`": "同类强断言，形状杂，需先设计不造病句的替换",
        "任意 `支付网关直读`": "同上",
        "`已验证收入`（非近 30 天前缀）": "同类强断言（如「累计已验证收入」）",
        "`已算好`（非裸批注）": "混有正当叙述，需逐形态裁定",
        "`÷ / 除以活跃订阅`": "**多为正当叙述**，不可整批清",
        "`定价压在`": "1 条，需人工确认",
    }
    for lab, rows in ext_rows.items():
        lines.append("| %s | %d | %d | %s |" % (
            lab, len(set(k for k, _f, _t in rows)), len(rows), judge[lab]))
    lines += [
        "", "明细见 `tmp/htm_screen/wording_residual_20261105.md`。", "",
        "## 五、边界与下一步", "",
        "- 只改**未发布的暂存草稿**；`out/articles/*.html` 本地副本与已发布正文**不回填**（项目红线）。",
        "- 清洗是确定性替换、不追求文采；这些草稿 `apply` 前仍需人工抽检（闸只认形状、不认语义）。",
        "- 判据外残留若要做，先扩 `WORDING_BANS` + `SCRUB_RULES` + 回归，别再逐条打补丁。",
        "- `founder` / `traffic_sources` 仍是 hint 级空字段，与本轮无关。", "",
        "备份：`tmp/draft_text.bak-20261105-wording.json`（原始 181 条，只在不存在时创建）。",
    ]
    atomicio.write_text(REPORT, "\n".join(lines) + "\n")

    # ---- 残留明细清单 ----
    rl = ["# 措辞清洗判据外残留清单 · 2026-11-05", "",
          "> 只读体检产物，**本轮未据此改动正文**。供下一轮定判据用。", ""]
    for lab, rows in ext_rows.items():
        rl += ["## %s（%d 条 / %d 段）" % (
            lab, len(set(k for k, _f, _t in rows)), len(rows)), ""]
        for k, f, t in rows[:60]:
            m = re.search(ext_pats[lab], t)
            frag = t[max(0, m.start() - 30):min(len(t), m.end() + 40)] if m else t[:100]
            rl.append("- `%s` / `%s`：%s" % (k, f, frag.replace("\n", " ")))
        if len(rows) > 60:
            rl.append("- …（其余 %d 段略）" % (len(rows) - 60))
        rl.append("")
    atomicio.write_text(RESIDUAL, "\n".join(rl) + "\n")

    print("清洗完成：命中 %d 条 -> 0；实际改动 %d 条，替换 %d 次，金额变化 0" %
          (len(bad_ids), len(changed), total_repl))
    print("报告 -> %s" % os.path.basename(REPORT))
    print("残留清单 -> %s" % os.path.basename(RESIDUAL))
    for lab, rows in ext_rows.items():
        print("  判据外 %-28s %3d 条" % (lab, len(set(k for k, _f, _t in rows))))


if __name__ == "__main__":
    main()
