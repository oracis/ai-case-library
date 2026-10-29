#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台化开头模板库（借鉴点 4，方法论来自「标题·开头钩子·爆文结构」专家）。

方法论：开头钩子按 **冲突 / 悬念 / 利益 / 场景** 四型生成，按案例特征选型，
同型给多条候选。铁律（与专家技能边界一致，也和本库核实红线同构）：

  ⚠⚠ 事实、数字、经历**全部来自 case 字段**，模板只做排列组合。
     模板里允许出现的「公共事实」仅限：案例来自公开挂牌/公开收入来源、
     数字经核验入库 —— 这两条是全库成立的事实。任何具体金额、
     「网上都说」「被大厂忽视」这类未核实断言一律不进模板。

四平台消费关系（改错地方会复读）：
  小红书  xhs_publish.build_body  ← xhs_hook()（独立链路）
  公众号  make_article.build_sections 无纠错分支 ← wechat_lead()（源头）
  头条    out/articles/<id>.md → toutiao_publish.build_article（读源头 md）
  B站     out/articles/<id>.html → bilibili_publish（读源头 html）
  ⇒ 公众号/头条/B站同源：weacht_lead 升级后三平台同时受益。
    tt_lede/bili_prefix 预留**默认不接**——verdict 已在首节第 2 段，
    再插「先说结论」就是复读（同源产物只能有一份开头）。

选择是**确定性的**：同一 case 永远出同一条钩子（重建幂等的硬要求）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 素材提取

def _headline(c):
    return ((c.get("metrics") or {}).get("headline") or "")


def _listed(c):
    """案例是否来自公开挂牌/榜单（「明码标价」钩子的适用判据）。"""
    h = _headline(c)
    return bool(re.search(r"（[^）]*(TrustMRR|榜单|挂牌)[^）]*）", h)) or "挂牌" in h


def _solo(c):
    sf = c.get("solo_fit") or {}
    return (sf.get("score") or 0) >= 50


def _resale(c):
    """转售/交易型生意（不写代码也能做的判据）。"""
    models = "、".join(c.get("models") or [])
    return any(k in models for k in ("转售", "交易市场", "分销", "代理"))


def _verdict_clip(c, n=40):
    v = (c.get("verdict") or "").strip()
    if len(v) > n:
        v = v[:n].rstrip("，。；：、") + "…"
    return v


# ---------------------------------------------------------------- 小红书钩子

# 四型 × 2 条。标 (已) 的是 10-01 上线的老池成员（风格已被平台实测接受，
# 一律保留）；标 (新) 的按同一口径补写。pred 返回 False 的自动跳过。
_XHS_POOL = [
    # 冲突型：反差感——门槛低 vs 收入真
    ("冲突", "一个人 + 现成的上游，就能开这门生意，收入是真的👇",     # (已)
     lambda c: _solo(c)),
    ("冲突", "它做的事听起来平平无奇，账本却是真的👇",               # (新)
     lambda c: bool((c.get("verdict") or ""))),
    # 悬念型：把「怎么做到的」藏住
    ("悬念", "不写代码也能做：这门生意靠的是转售，不是技术👇",        # (已)
     lambda c: _resale(c)),
    ("悬念", "收入挂在明面上的小生意，它到底靠什么赚钱？往下看👇",    # (新)
     lambda c: True),
    # 利益型：直接给利益点——账本
    ("利益", "明码标价挂牌出售的海外小生意，我把它的账算给你看👇",    # (已)
     lambda c: _listed(c)),
    ("利益", "挂着明码标价的生意，这期账本有点意思，翻给你看👇",      # (新)
     lambda c: _listed(c)),
    # 场景型：具体、可信的画面
    ("场景", "又一个一人公司：收入是真的，成本结构比你想的薄👇",      # (已)
     lambda c: _solo(c)),
    ("场景", "刷到一家把收入挂在明面上的小生意，顺手把账拆了👇",      # (新)
     lambda c: True),
]


def xhs_hook(c):
    """按案例特征选一条小红书开头钩子（确定性，重建幂等）。

    「悬念B」「场景B」两条 pred 恒真，保证可选集永不为空。
    """
    pool = [(t, s) for t, s, p in _XHS_POOL if p(c)]
    # 确定性：id 哈希在「当前可选集」里轮换；同一 case 重建结果恒同
    idx = sum(ord(x) for x in (c.get("id") or "")) % len(pool)
    return pool[idx][1]


# ---------------------------------------------------------------- 公众号开头

def wechat_lead(c):
    """无纠错案例的公众号开头（叙事三段式）。

    有纠错案例**不走这里**——引号 claim + 「我去核了一遍」是全库实测
    转化率最高的钩子（make_article.build_sections 注释），保持不动。

    三段：场景句（一句话入戏）→ verdict（编辑判断，原有素材）
          → 路线句（本文结构，复用页脚口径）。
    场景句只用库公共事实；路线句是结构承诺，不产生新事实。
    """
    scene = "今天拆一个海外小生意，收入数字都是公开可核验的。"
    parts = [scene]
    v = (c.get("verdict") or "").strip()
    if v:
        parts.append(v)
    parts.append("它干什么、钱从哪来、做到多大、值不值得抄，下面一篇讲完。")
    return [p for p in parts if p]


# ---------------------------------------------------------------- 预留（不接）

def tt_lede(c):
    """头条导语——**默认不接**，见模块头注释（同源复读问题）。

    头条「结论先行」当前由 make_article 源头改善间接达成：
    无纠错案例开头 = 场景句(15字) + verdict，verdict 位置仍在前两段。
    """
    return None


def bili_prefix(c):
    """B站专栏社区前缀——**默认不接**（源头改善已自动受益，加前缀易显话术）。"""
    return None
