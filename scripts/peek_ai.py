# -*- coding: utf-8 -*-
"""只读探针：看清 AI 到底收到了什么、又答了什么。

为什么需要它：核实草稿只存 AI 的**结论**（caliber / verification / musts），
不存它的推理过程。于是界面上只剩下「还差 1 项必填」这种结果，
看不出 AI 是没找到证据、还是找到了反证 —— 排查这类问题必须先看到原文。

**不写任何文件**：只把与 verify_one 完全相同的提示词发一次，打印原始 JSON。
所以它不会覆盖已有草稿，可以放心在真候选上跑。

    python scripts/peek_ai.py 1lookup        # 默认 1lookup
    python scripts/peek_ai.py stan

它打印三样：
  1. 这次抓到了哪几页原文（以及候选自带的已知地址有哪些）
  2. 发给 AI 的【当前卡点】段（AI 判断「这条缺什么」的唯一依据）
  3. AI 的原始回答，逐个关键字段带类型
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A                                              # noqa: E402

CID = sys.argv[1] if len(sys.argv) > 1 else "1lookup"

sec = {}
p = os.path.join(ROOT, "data", "secrets.json")
if os.path.exists(p):
    sec = json.load(open(p, encoding="utf-8"))
st = A.configure(api_key=os.environ.get("CASE_LIB_AI_KEY") or sec.get("ai_key"),
                 base=os.environ.get("CASE_LIB_AI_BASE") or sec.get("ai_base"),
                 model=os.environ.get("CASE_LIB_AI_MODEL") or sec.get("ai_model"))
print("模型配置：", st if isinstance(st, dict) else st)
print()

cands = json.load(open(os.path.join(ROOT, "data", "candidates.json"), encoding="utf-8"))
cand = next((c for c in cands if c.get("id") == CID), None)
if not cand:
    print("没有这条候选：", CID)
    sys.exit(1)

old = json.load(open(os.path.join(ROOT, "data", "verifications.json"),
                     encoding="utf-8")).get(CID) or {}
blockers, _ = A.current_blockers(old)
print("== %s  %s" % (CID, cand.get("name")))
print("当前草稿的卡点：", [k for _, k, _ in blockers] or "（无）")
print("当前草稿的 caliber：", old.get("caliber") or "(空)",
      "| musts:", old.get("musts"))
print()

pages, used = A.collect_pages(cand, max_pages=5, log=lambda m: print("  " + m))
if not pages:
    print("没抓到原文，无法继续")
    sys.exit(1)

blockers, _ = A.current_blockers(old)
cand2 = dict(cand, _old_musts=(old.get("musts") or []))
cand_brief = {k: cand2.get(k) for k in
              ("id", "name", "name_en", "origin", "one_liner", "category",
               "metrics", "models", "note", "blocking")}
user = A.USER_TMPL % {
    "cand": json.dumps(cand_brief, ensure_ascii=False, indent=1),
    "blockers": "\n".join("- %s" % label for _, _, label in blockers) or "（空）",
    "pages": "\n\n".join(pages)[:24000],
}
print()
print("=== 发给 AI 的【当前卡点】段 ===")
print(user.split("【网页原文摘录】")[0].split("【当前卡点】")[1])

ai = A.extract_json(A.llm([{"role": "system", "content": A.SYSTEM_PROMPT},
                           {"role": "user", "content": user}]))
print()
print("=== AI 原始回答（关键字段）===")
print("  caliber           :", repr(ai.get("caliber")))
print("  caliber_reason    :", repr(ai.get("caliber_reason")))
print("  caliber_consistent:", repr(ai.get("caliber_consistent")),
      "  ← 类型:", type(ai.get("caliber_consistent")).__name__)
print("  number            :", repr(ai.get("number")))
print("  confidence        :", repr(ai.get("confidence")))
print("  verification      :", repr(ai.get("verification")))
print()
print("=== 映射成草稿会怎样 ===")
d = A.map_ai_to_draft(cand2, ai, publish_mode=False)
print("  musts :", d["musts"])
print("  缺的必填:", [m["key"] for m in A.VR.evaluate(d)["missing"]])
print()
print("=== 草稿里存了 caliber_consistent 吗 ===")
print("  草稿字段:", sorted(d.keys()))
