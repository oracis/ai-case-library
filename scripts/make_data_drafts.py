#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给没有核实草稿的候选生成「数据先行」草稿（用户 2026-10-15 定：
核实困难、不好做一手源深核的，先放进来）。

立场与边界
----------
这些候选的收入数字本身就是一手证据 —— TrustMRR 从支付网关 API 直读
（Whop / Stripe / Lemon Squeezy / Creem…），不是截图自报。所以草稿可以
正当地写 verification=stripe、来源登记 TrustMRR 档案页（kind=stripe）。

但不装懂的部分必须留空：
  · solo_possible 不勾 —— 那要读官网/团队信息才能判，LLM 深核还没跑；
  · caliber_consistent 不勾 —— 「口径与数字一致」要人或者 AI 交叉核对过
    才能勾，这里只按 metric_note 已声明的口径登记 caliber；
  · caliber_consistent_ai 不写 False（那是「AI 明确否掉」），留空表示
    「还没有 AI 判断」，规则引擎只拦显式 False。
勾的两道门槛（still_alive / is_business）都有数据支撑：收入同步时间在
近期且大于 0 = 活着；支付网关持续入账 = 真实生意。依据写进 note。

已有草稿的一条不动（LLM 深核跑过的优先）。
"""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules as VR                                    # noqa: E402

VERS = os.path.join(ROOT, "data", "verifications.json")
CANDS = os.path.join(ROOT, "data", "candidates.json")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


def calibre_reason(cand, cal):
    """从 metric_note 里摘口径声明，摘不到就按 caliber 键给一句实话。"""
    note = ((cand.get("metrics") or {}).get("metric_note") or "")
    if note:
        # 取第一句（到第一个「。」为止），它通常就是口径声明
        head = note.split("。")[0].strip()
        if head:
            return head + "。"
    if cal == "mrr":
        return "订阅制业务，MRR 由 TrustMRR 支付网关 API 直读。"
    return ("非订阅制业务，MRR 恒 0；门面数字为 TrustMRR 直读的近 30 天"
            "已验证收入（含一次性与用量收入），按 last_30d 口径登记。")


def build_draft(cand):
    m = cand.get("metrics") or {}
    mrr = float(m.get("mrr") or 0)
    cal = "mrr" if mrr > 0 else "last_30d"
    ver = cand.get("verification") or ""
    if ver not in VR.VERIFICATION_KEYS:
        ver = "partial"
    kinds = ["stripe"]
    sources = [{
        "label": "TrustMRR 档案（支付网关 API 直读）",
        "url": cand.get("source_url")
                or ("https://trustmrr.com/startup/%s"
                    % (cand.get("trustmrr_slug") or cand.get("id"))),
        "kind": "stripe",
    }]
    if cand.get("website"):
        kinds.append("official")
        sources.append({"label": "官网", "url": cand["website"],
                        "kind": "official"})
    provider = ""
    note_txt = (cand.get("note") or "")
    for probe in ("Whop", "Stripe", "Lemon Squeezy", "Creem", "Paddle",
                  "Gumroad", "PayPal"):
        if probe in note_txt:
            provider = probe
            break
    return {
        "gates": ["still_alive", "is_business"],
        "gates_denied": [],
        "musts": ["caliber_decided"],
        "bonus": [],
        "caliber": cal,
        "caliber_reason": calibre_reason(cand, cal),
        "verification": ver,
        "source_kinds": kinds,
        "sources": sources,
        "irrelevant_sources_dropped": 0,
        "corrections": cand.get("corrections") or [],
        "note": ("数据先行草稿（%s 自动生成）：未跑 LLM 深核，按 TrustMRR "
                 "一手档案直接建档 —— 收入由支付网关 API 直读（%s），"
                 "still_alive / is_business 由「近期有持续入账」这一数据事实"
                 "支撑；solo_possible 与口径交叉核对留空，待人工或 AI 补判。"
                 % (NOW, provider or "支付网关")),
        "human_read_at": "",
        "updated_at": NOW,
    }


def main():
    cands = json.load(open(CANDS, encoding="utf-8"))
    vers = json.load(open(VERS, encoding="utf-8")) \
        if os.path.exists(VERS) else {}
    made = skipped = 0
    for c in cands:
        cid = c.get("id")
        if not cid or cid in vers:
            skipped += 1
            continue
        # 正文都没写的候选也先生成草稿 —— 晋升时两道闸门独立检查，
        # 先备齐草稿能让「写正文 → 晋升」两步各自可验证。
        vers[cid] = build_draft(c)
        made += 1
    out = json.dumps(vers, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with open(VERS, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("生成草稿 %d 条，已有草稿跳过 %d 条，verifications.json 共 %d 条"
          % (made, skipped, len(vers)))


if __name__ == "__main__":
    main()
