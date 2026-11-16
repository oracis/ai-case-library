#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分文案（china_fit.note / solo_fit.delivery_note）的唯一裁定口。

背景（PRD #182 → #183 → #184）
------------------------------
`SCORES` / `DELIVERY` 原先是写在评分脚本里的静态人工表，而库里的
`china_fit.note` / `solo_fit.delivery_note` 会在数据刷新时按新快照改数字
（11-14 那轮把「35 个订阅 $496」刷成了「37 个订阅 $506」）。跑分脚本原先
只在「表里没有」时 preserve；**只要表里有**，就把表里的旧文案整段写回库，
一次重跑洗掉 52 处人工判断。#183 加了本裁定口让库里优先，#184 再把两张表
里的 465 + 466 份文案副本整体剔除（表只存数值），从此**没有第二份文案可覆盖**。

口径：库里是唯一真值
--------------------
  * 库里已有「非兜底」人工判断（score 非空、provisional 非真）且文案非空
    ⇒ **保留库里文案**。
  * 库里没判断、是 provisional、或文案为空 ⇒ 用各评分脚本 `fallback_*()`
    生成的中位数兜底说明当新案例初值，并照旧打 `provisional`。
  * 没有覆盖开关、没有 `--refresh-notes`、没有表文案：想改文案只能回库里改。

⛔ 这里只裁文案，不动分数、dims、rank、blocker —— 那些属于跑分的真推进。
"""
from __future__ import annotations

#: section → 该 section 里的文案字段名。
SECTION_KEY = {
    "china_fit": "note",
    "solo_fit": "delivery_note",
}


def plain(s):
    """纯文本字段：站点上 esc() 直出，markdown 的 `**` 会显示成字面星号。"""
    return (s or "").replace("**", "")


def is_human_judgement(section):
    """库里这份判断是不是「人工填的」（不是中位数兜底的临时值）。"""
    return bool(section) and section.get("score") is not None \
        and not section.get("provisional")


def resolve_note(case, section, initial_note):
    """返回 ``(要写回的文案, 是否保留了库里判断)``。

    :param case: 单条案例 dict（跑分主循环里的 ``c``）
    :param section: ``"china_fit"`` / ``"solo_fit"``
    :param initial_note: 库里没有判断时的初值（评分脚本 ``fallback_*()``
                         生成的中位数兜底说明）；库里有人判断时**不会用到**。

    ⛔ 没有 refresh 参数：#184 之后静态表已无文案副本，任何来源都不得覆盖
       库里已存在的人工判断。
    """
    if section not in SECTION_KEY:
        raise KeyError("未知评分段：%s" % section)
    key = SECTION_KEY[section]
    prev = case.get(section) or {}
    stored = plain(prev.get(key))
    if is_human_judgement(prev) and stored:
        return stored, True
    return plain(initial_note), False
