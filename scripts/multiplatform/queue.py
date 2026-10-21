#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待发队列的排序口径：**最新录入的排最前**，且不依赖文件顺序。

**为什么单独拎出来（2026-10-21 修）。** 头条与小红书的 `pending_cases()` 各自
写着 `return todo[::-1]`，注释与 `cmd_queue` 的打印都声称「库里最新的排最前」。
但 `load_cases()` 返回的是 `data/cases.json` 的**文件顺序**，而这个文件本来就
是新的在前（实测：头几条 `updated_at` 2026-10-15，末几条 2026-09-11）——
`[::-1]` 一翻变成**最旧的先发**，跟注释和打印全都相反，谁也没发现。

靠「文件顺序 + 取反」这种隐式约定本身就是雷：任何一次重排、补录、按 id 排序
落盘，都会静默换掉「下一条发谁」的口径，而队列看着一切正常。所以改成显式按
日期倒序：

* 日期取 `updated_at`（拿不到退 `verified_at`），都是 `YYYY-MM-DD` 字符串，
  字典序即时间序；
* 日期相同**保持传入顺序**（`sorted` 稳定），不制造新的隐式约定；
* 两个日期都没有的沉到最后 —— 宁可排后面，也不许它冒充最新的插队头。
"""

# 取哪个字段当「录入时间」：先 updated_at（案例最近一次改动），再 verified_at
DATE_FIELDS = ("updated_at", "verified_at")


def recency(case):
    """案例的「录入/更新时间」字符串（`YYYY-MM-DD`），都没有返回空串。"""
    for key in DATE_FIELDS:
        val = str(case.get(key) or "").strip()
        if val:
            return val
    return ""


def newest_first(cases):
    """按录入时间倒序（最新在前）；同日期保持原顺序，无日期的沉底。"""
    return sorted(cases, key=recency, reverse=True)
