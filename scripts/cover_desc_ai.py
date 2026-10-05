#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 AI 为案例生成 `cover_desc`（封面专用一句话介绍）。

为什么要有这个脚本（2026-10-05新增）：

`cover_desc` 是**纯数据层字段** —— `wechat_publish.cover_desc()` 只负责按
优先级读它（cover_kv.desc > cover_desc > one_liner > 正则兜底），
`ai_verify.py` / `verify_rules.py` 全都不碰它。后果是：
新案例入库时没人写这个字段 → 41 条里只有 10 条有，
剩下 31 条全靠one_liner 兜底 → 封面动辄 40+ 字，
触发「封面文字缩到下限仍溢出:['t-line']」警告（实测easymix 43 字）。

⚠ **硬约束：封面不显示金额**（项目口径：封面 = 项目名 + 一句话介绍）。
  所以提示词明确禁止出现 $/€/£/数字金额，且**代码侧再兜一层**
  （`assert_no_money`），不指望模型100% 听话。

用法：
    # 先看会改哪些（不写盘、不调模型）
    python -X utf8 scripts/cover_desc_ai.py --plan

    # 只对缺 cover_desc 的案例生成为候选（默认）
    python -X utf8 scripts/cover_desc_ai.py --draft

    # 人工过目后写盘
    python -X utf8 scripts/cover_desc_ai.py --apply

    # 连缺失的算上，已有手写的也重写（一般不用，手写优先）
    python -X utf8 scripts/cover_desc_ai.py --draft --all

门禁（`--apply` 前必过，缺一不可）：
  1. 字数 12– 30（字号分档是 12/20/30/42，越界必溢出）；
  2. 不含金额（正则 + 货币语境判定，避免 `B2B` 之类误报）；
  3. 不与 `one_liner` 逐字相同（相同 = 没简写，白花钱）；
  4. LLM 返回必须是可解析的 JSON 对象。

⚠ 免key 端点：默认 `opencode.ai/zen/v1` + `space-bunny-free`，
  切模型走 `--ai-base/--ai-model/--ai-key`，先 `--ping` 再批量
  （记忆铁律 8：空 Bearer、必须发 UA、门禁走 ai_ready()）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CASES = os.path.join(ROOT, "data", "cases.json")
DRAFT = os.path.join(ROOT, "data", "cover_desc_drafts.json")

MIN_LEN, MAX_LEN = 12, 30

# 金额语境：货币符号 或 「数字+万/千/百万 + 收入类词」。
# ⚠ 只看货币符号会误报 `B2B`/`SaaS` 这类含大写B 的串（记忆铁律：封面审计）。
_MONEY = re.compile(
    r"[$€£¥￥]\s?\d"                       # $100 / €250
    r"|\d[\d,\.]*\s*(?:万|千|百万|亿)"# 30 万 / 2百万
    r"|\b(?:MRR|ARR|revenue|mrr|arr)\b",# MRR $7,188
    re.I)

SYS = (
    "你是科技媒体的封面文案编辑。任务：把一个 AI 产品的介绍压缩成"
    "**一句中文封面标语**，用于四平台（公众号/头条/B站/小红书）封面图。"
)

RULES = (
    "硬性要求：\n"
    "1. **长度 %d–%d 个汉字，这是最硬的约束**。写完自己数一遍，"
    "超了就砍形容词和定语，只留「做什么 + 给谁用」。"
    "⚠ 不合格的例子：「Voklit为数字游民、小商户和销售团队提供"
    "跨境通话与短信，支持接收验证码。」——38 字，砍成"
    "「跨境通话与短信工具，支持接收验证码」才合格。\n"
    "2. **绝对不能出现任何金额、收入、用户数等数字指标**"
    "（不要 $、€、£，不要「万」「收入」「MRR」这类词）。\n"
    "3. 说清「它是什么 + 为谁解决什么」，别写「智能」「高效」这种空话。\n"
    "4. 用陈述句，可以含一个逗号；不要句末句号以外的标点。\n"
    "5. 只输出 JSON：{\"cover_desc\": \"...\"}"
)


def money_hits(s):
    return _MONEY.findall(s or "")


def validate(desc, one_liner):
    """返回 (ok, reason)。门禁全在这里，改规则只改这一处。

    ⚠ 判据顺序有讲究：**「逐字相同」必须排在字数门禁前面**。
       真实数据里`one_liner` 常有 40+ 字（easymix 43 字），它必然同时
       触发「字数超限」——若先判字数，模型就算原样回吐 one_liner
       也只会被报成「字数超限」，这条「没简写」的判据等于永远测不到
       （2026-10-05 实测：回归测试就栽在这，8/9 过且失败原因具有
       误导性）。
    """
    d = (desc or "").strip()
    if not d:
        return False, "空文案"
    if one_liner and d == one_liner.strip():
        return False, "与 one_liner 逐字相同（没简写）"
    if not (MIN_LEN <= len(d) <= MAX_LEN):
        return False, "字数 %d 不在 %d–%d" % (len(d), MIN_LEN, MAX_LEN)
    if money_hits(d):
        return False, "含金额：%s" % money_hits(d)
    if d.endswith(("！", "!", "？", "?")):
        return False, "句末标点 disallowed"
    return True, "ok"


def ping():
    import ai_verify as AV
    AV.configure(api_key="", base="https://opencode.ai/zen/v1",
                 model="space-bunny-free")
    if not AV.ai_ready():
        print("[!] ai_ready() 为假 —— 门禁不允许跑")
        return 2
    AV.ping_llm()
    return 0


def ask_llm(case):
    import ai_verify as AV
    AV.configure(api_key="", base="https://opencode.ai/zen/v1",
                 model="space-bunny-free")
    payload = {
        "name": case.get("name") or case.get("id"),
        "one_liner": case.get("one_liner") or "",
        "what_it_does": (case.get("what_it_does") or "")[:600],
        "category": case.get("category") or "",
    }
    user = ("产品资料：\n%s\n\n%s"
            % (json.dumps(payload, ensure_ascii=False, indent=1),
               RULES % (MIN_LEN, MAX_LEN)))
    raw = AV.llm([{"role": "system", "content": SYS},
                  {"role": "user", "content": user}],
                 temperature=0.4)
    try:
        obj = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None, "模型没返回 JSON：%r" % (raw or "")[:120]
        try:
            obj = json.loads(m.group(0))
        except ValueError as e:
            return None, "JSON 解析失败：%s" % e
    return (obj.get("cover_desc") or "").strip(), None


def load_cases():
    with io.open(CASES, encoding="utf-8", newline="") as f:
        return json.loads(f.read())


def save_cases_like(raw, data):
    """保持原格式：indent=2 + CRLF + 无末尾换行（记忆铁律 16）。"""
    out = json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with io.open(CASES, "w", encoding="utf-8", newline="") as f:
        f.write(out)


def main():
    ap = argparse.ArgumentParser(description="AI 生成封面一句话介绍")
    ap.add_argument("--plan", action="store_true", help="只列会改哪些")
    ap.add_argument("--draft", action="store_true", help="生成候选到本地草稿")
    ap.add_argument("--apply", action="store_true", help="把草稿写进 cases.json")
    ap.add_argument("--all", action="store_true", help="连已有 cover_desc 也重写")
    ap.add_argument("--ping", action="store_true", help="只测端点通不通")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.ping:
        return ping()

    cases = load_cases()
    todo = [c for c in cases
            if args.all or not (c.get("cover_desc") or "").strip()]

    if args.plan:
        print("待生成 cover_desc：%d / %d" % (len(todo), len(cases)))
        for c in todo:
            cur = (c.get("cover_desc") or "").strip() or "(空，走 one_liner 兜底)"
            print("  %-20s %s" % (c["id"], cur[:40]))
        return 0

    if args.draft:
        drafts = {}
        if os.path.exists(DRAFT):
            with io.open(DRAFT, encoding="utf-8") as f:
                drafts = json.load(f)
        n_ok = n_bad = 0
        for c in todo[:args.limit] if args.limit else todo:
            cid = c["id"]
            desc, err = ask_llm(c)
            if err:
                print("  [LLM ERR] %-20s %s" % (cid, err))
                n_bad += 1
                continue
            ok, why = validate(desc, c.get("one_liner"))
            mark = "OK " if ok else "BAD"
            print("  [%s] %-20s (%d字) %s%s"
                  % (mark, cid, len(desc or ""), desc,
                     "" if ok else "← " + why))
            drafts[cid] = {"cover_desc": desc, "ok": ok, "why": why,
                           "auto": bool(ok)}
            n_ok += int(ok)
            n_bad += int(not ok)
        with io.open(DRAFT, "w", encoding="utf-8") as f:
            json.dump(drafts, f, ensure_ascii=False, indent=2)
        print("\n候选 %d 条可用 / %d 条要人改→ %s"
              % (n_ok, n_bad, os.path.relpath(DRAFT, ROOT)))
        return 0

    if args.apply:
        if not os.path.exists(DRAFT):
            print("[!] 没有草稿，先跑 --draft")
            return 2
        with io.open(DRAFT, encoding="utf-8") as f:
            drafts = json.load(f)
        with io.open(CASES, encoding="utf-8", newline="") as f:
            raw = f.read()
        data = json.loads(raw)
        applied, skipped = [], []
        for c in data:
            cid = c.get("id")
            rec = drafts.get(cid)
            if not rec:
                continue
            if not rec.get("auto"):
                skipped.append((cid, rec.get("why")))
                continue
            if not args.all and (c.get("cover_desc") or "").strip():
                skipped.append((cid, "已有手写 cover_desc，跳过"))
                continue
            ok, why = validate(rec["cover_desc"], c.get("one_liner"))
            if not ok:
                skipped.append((cid, "写盘前复检不过：" + why))
                continue
            c["cover_desc"] = rec["cover_desc"]
            applied.append(cid)
        save_cases_like(raw, data)
        print("[OK] 写入 %d 条：%s" % (len(applied), ", ".join(applied)))
        for cid, why in skipped:
            print("  [skip] %-20s %s" % (cid, why))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())