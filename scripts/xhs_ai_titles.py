#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 AI 给全部案例改写小红书标题（落盘 data/xhs_title_overrides.json）。

为什么存在：make_xhs_title 的规则兜底遇到长 one_liner 会硬切成残句
（「ChatGP」「客服机」），或全体退化成「海外小生意」。规则负责下限，
这份覆盖表负责上限。

用法：
    python scripts/xhs_ai_titles.py            # 生成/更新覆盖表
    python scripts/xhs_ai_titles.py --force    # 已有的覆盖也重新生成
    python scripts/xhs_ai_titles.py --dry      # 只打印发给 AI 的提示词

做法：
  1. 每条案例只把【金额前缀 + 名称 + one_liner + headline】给 AI；
  2. AI 只产出「：」后面的业务短语（金额前缀本地拼装，保证口径不被改写）；
  3. 本地校验：≤TITLE_MAX、非通用词、不与别的标题撞车；不合格回退规则标题。
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A        # noqa: E402
import xhs_publish as X      # noqa: E402

OVERRIDES = os.path.join(ROOT, "data", "xhs_title_overrides.json")
GENERIC = ("海外小生意", "一人公司生意", "海外小项目", "拆解一个")

SYSTEM = """你是小红书运营编辑。案例库要批量产出笔记标题，规则引擎只能机械截断，
现在请你重写每条的「业务短语」部分。

规则：
1. 只输出 JSON：{"titles": {"<case_id>": "<业务短语>", ...}}，不要解释。
2. 业务短语 ≤11 个汉字（给「月收$398K：」这类金额前缀留位置），也不要省略号。
3. 必须说清「这是个什么生意」，用具体名词，禁止出现「海外小生意」「一人公司生意」
   这类空话，禁止残句（比如切一半的英文品牌名）。
4. 口语化、有画面感，像小红书标题的后半句。例：
   「帮电商品牌全网打假」「文档一键变客服机器人」「让品牌进 AI 答案」。
5. 英文产品名可以保留，但若占太多字就用中文说清它做什么。
6. 37 条要互相错开句式，别都长一个样。"""

USER_TMPL = """案例清单（id | 金额前缀 | 产品名 | 一句话业务 | 收入口径原文）：

%s

请给每条产出一个业务短语。金额前缀只是给你参考口径，不用输出它。"""


def load_key():
    sec = {}
    p = os.path.join(ROOT, "data", "secrets.json")
    if os.path.exists(p):
        sec = json.load(open(p, encoding="utf-8"))
    A.configure(api_key=os.environ.get("CASE_LIB_AI_KEY") or sec.get("ai_key"),
                base=os.environ.get("CASE_LIB_AI_BASE") or sec.get("ai_base"),
                model=os.environ.get("CASE_LIB_AI_MODEL") or sec.get("ai_model"))


def lead_of(c):
    h = (c.get("metrics") or {}).get("headline") or ""
    amt = X.first_amount(h)
    if not amt:
        return None
    return ("%s成交" % amt) if "成交" in h else ("月收%s" % amt)


def build_listing(cases, old):
    rows = []
    for c in cases:
        lead = lead_of(c) or "（无金额）"
        ol = X.short_desc(c)
        h = (c.get("metrics") or {}).get("headline") or ""
        rows.append("%s | %s | %s | %s | %s" %
                    (c["id"], lead, c.get("name", ""), ol, h[:40]))
    return USER_TMPL % "\n".join(rows)


def validate(cases, got, budget_map):
    """只接受：长度达标、非通用、无省略号、不撞车。返回 (接受表, 拒绝原因)。"""
    ok, bad, seen = {}, {}, {}
    for c in cases:
        cid = c["id"]
        t = (got.get(cid) or "").strip()
        if not t:
            bad[cid] = "AI 没给"
            continue
        t = re.sub(r"[…。！？]+$", "", t).strip()
        if len(t) > budget_map[cid]:
            bad[cid] = "超长(%d>%d)：%s" % (len(t), budget_map[cid], t)
            continue
        if len(t) < 4:
            bad[cid] = "太短：%s" % t
            continue
        if any(g in t for g in GENERIC):
            bad[cid] = "通用词：%s" % t
            continue
        full_base = "%s：%s" % (budget_map[cid] and "", t)   # 撞车看全文层面
        key = t
        if key in seen:
            bad[cid] = "与 %s 撞车：%s" % (seen[key], t)
            continue
        seen[key] = cid
        ok[cid] = t
    return ok, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="AI 重写小红书标题")
    ap.add_argument("--dry", action="store_true", help="只打印提示词")
    ap.add_argument("--force", action="store_true",
                    help="已有覆盖的案例也重新生成（默认跳过）")
    ap.add_argument("--model", help="临时换模型")
    args = ap.parse_args(argv)

    load_key()
    cases = X.load_cases()
    if not args.force:
        old = {}
        if os.path.exists(OVERRIDES):
            old = json.load(open(OVERRIDES, encoding="utf-8"))
        cases = [c for c in cases if c["id"] not in old]
    if not cases:
        print("全部案例都有覆盖了（--force 可重生成）。")
        return 0

    old_ov = {}
    if os.path.exists(OVERRIDES):
        old_ov = json.load(open(OVERRIDES, encoding="utf-8"))
    budget_map = {}
    for c in cases:
        lead = lead_of(c)
        budget_map[c["id"]] = X.TITLE_MAX - len(lead) - 1 if lead else X.TITLE_MAX

    prompt = build_listing(cases, old_ov)
    if args.dry:
        print("=== SYSTEM ===\n%s\n=== USER ===\n%s" % (SYSTEM, prompt))
        return 0

    print("发 %d 条给 AI（%s）..." % (len(cases), A.AI_MODEL))
    raw = A.llm([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": prompt}], temperature=0.6)
    got = json.loads(raw).get("titles", {})
    ok, bad = validate(cases, got, budget_map)

    merged = dict(old_ov)
    for cid, t in ok.items():
        c = next(c for c in cases if c["id"] == cid)
        lead = lead_of(c)
        # 存完整标题（含金额前缀），make_xhs_title 直接整条返回
        merged[cid] = ("%s：%s" % (lead, t)) if lead else t
    with open(OVERRIDES, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)

    print("\n接受 %d 条，拒绝 %d 条 → data/xhs_title_overrides.json"
          % (len(ok), len(bad)))
    for cid, t in sorted(ok.items()):
        print("  ✓ %-22s %s：%s" % (cid, lead_of(next(c for c in cases if c["id"] == cid)), t))
    for cid, why in sorted(bad.items()):
        print("  ✗ %-22s %s" % (cid, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
