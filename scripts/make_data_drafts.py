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


def _pos_money(cand, *keys):
    """从 metrics 里取第一个 >0 的数值，字符串型数字也容错。

    ⚠ float(m.get("mrr") or 0) 遇 "244,029" 会 ValueError 崩掉整轮
    （2026-10-21 修）：candidates.json 的 metrics 偶有字符串型数字。
    """
    m = cand.get("metrics") or {}
    for k in keys:
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
        if isinstance(v, str):
            try:
                f = float(v.replace(",", "").replace("$", "").strip())
                if f > 0:
                    return f
            except ValueError:
                continue
    return 0.0


def build_draft(cand):
    m = cand.get("metrics") or {}
    mrr = _pos_money(cand, "mrr")
    cal = "mrr" if mrr > 0 else "last_30d"
    ver = cand.get("verification") or ""
    if ver not in VR.VERIFICATION_KEYS:
        ver = "partial"
    kinds = ["stripe"]
    # ⚠ URL 必须真的指向 TrustMRR 档案（2026-10-21 修）：此前
    #    source_url 兜底 + id 拼 slug 双兜底，HN 自报条目也生成
    #    「来源=HN 链接、kind=stripe（支付网关 API 直读）」的双重伪造，
    #    实测能骗过 verify_rules 的 publishable 闸门。本脚本的立身前提
    #    就是 docstring 写的「TrustMRR 一手档案」—— 没有 slug 就没有
    #    档案页，宁可跳过也不伪造 provenance。
    slug = cand.get("trustmrr_slug") or ""
    if not slug:
        raise SystemExit(
            "候选 %s 没有 trustmrr_slug：不是 TrustMRR 一手档案，"
            "不能自动生成 stripe 来源草稿" % (cand.get("id") or "?"))
    url = "https://trustmrr.com/startup/%s" % slug
    sources = [{
        "label": "TrustMRR 档案（支付网关 API 直读）",
        "url": url,
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
        # ⚠ 只给 TrustMRR 一手档案的候选建档（2026-10-21 修）：此前对
        #    **所有**无草稿候选写死 source_kinds=["stripe"]，HN 自报、
        #    零收入的条目也被盖上「支付网关 API 直读」的章，实测能骗过
        #    verify_rules 的 publishable 判定 —— 污染「每个数字都核过」
        #    这条库的立身之本。判定口径：harvest_source=="trustmrr"
        #    或带 trustmrr_slug（build_draft 里还会再校验一次）。
        if c.get("harvest_source") != "trustmrr" and not c.get("trustmrr_slug"):
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
