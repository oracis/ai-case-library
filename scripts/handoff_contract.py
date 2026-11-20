#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两阶段交接的准入契约（2026-11-20 显式化）。

本模块把「候选 → 案例」(②→③) 的准入判据从 `promote_ready.py` 的 CLI 脚本里
抽出，成为任何入口都能复用的纯函数。判据与 `server.py` 的 promote 端点
**必须一致**（同一个 `verify_rules`）——server 端会再查一遍，本地预检只是
为了别让批量跑到一半被 409 打断，不是要绕过谁。

契约 `precheck(cid, cands, vers, case_ids)`：
  入参  cid       候选 id
        cands     candidates.json 的 list[dict]（经 candidate_store 加载）
        vers      verifications.json 的 dict[id -> draft]
        case_ids  已有案例 id 集合（经 case_store 加载，真值在 SQLite）
  出参  (candidate, result, None)  准入通过
        (None, None, 原因字符串)    拒绝，调用方必须不碰该条

三道闸（顺序不可换，全部沿用 verify_rules 的判据）：
  1. 候选存在 + 未进案例库（幂等判据）；
  2. verifications.json 有草稿 + `VR.evaluate()` 判 publishable；
  3. `VR.content_gate()` 过（正文四硬字段写齐）。

⚠ 纯函数：不写盘、不联网、不改入参。任何入口都可安全调用。
⚠ 逐字搬自 `promote_ready.py`（2026-11-20），判据未改。若要改判据，
  **必须同步 server.py 的 promote 端点**，否则本地预检与服务端判定分叉。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify_rules as VR  # noqa: E402


def precheck(cid, cands, vers, case_ids):
    """返回 (candidate, result, None) 或 (None, None, 原因)。"""
    cand = next((x for x in cands if x.get("id") == cid), None)
    if cand is None:
        return None, None, "候选不存在（可能已晋升）"
    if cid in case_ids:
        return None, None, "已在 cases.json"
    draft = vers.get(cid)
    if draft is None:
        return None, None, "没有核实草稿（先跑 verify_cookie.py）"
    result = VR.evaluate(draft)
    if not result["publishable"]:
        if result["gates_failed"]:
            why = "；".join(g["label"] for g in result["gates_failed"])
        else:
            why = "；".join(m["label"] for m in result["missing"])
        return None, None, "规则引擎不过：" + why
    cg = VR.content_gate(cand)
    if not cg["ok"]:
        why = "；".join(p["label"] for p in cg["blocked"])
        return None, None, "正文闸门不过：" + why
    return cand, result, None
