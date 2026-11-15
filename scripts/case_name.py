#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示名金额闸：判据单一源 + 入库清洗 + 写库硬拦（PRD #183）。

背景
----
全库标题（`prerender.case_title`）与头条封面（`toutiao_publish`）都禁止金额。
11-13/14 入库的 HN 案例把帖子标题原样写进 `name`，`~$20K` / `$300` 就这么
进了展示字段，直到全量测试才暴露（PRD #182）。`_oneoff/fix_hn_name_money.py`
只治了那两条；只要 `hn_leads.to_lead()` 还直接透传标题，下一条带价格的
Show HN 又会重来。

三层防线
--------
  1. `MONEY_PAT` / `MONEY_WORD_PAT` —— 判据的唯一定义，`prerender` 从这里
     引用（不再各抄一份正则）；
  2. `clean_display_name()` —— HN 线索 / 事实卡 / 候选池入口做确定性剥离，
     原帖标题原样留在 `hn_title`，证据不丢；
  3. `assert_clean_name()` —— `case_store.save_cases()/save_case()` 最后一道
     硬闸，任何来源、任何路径带金额写进真库都当场报错。

⛔ 只删金额，不补语义。剥不干净（例如名称本身是「收入管理」这种词）宁可
   报错让人工给名，也不让代码替读者编一句产品说明 —— 改写归模型/人工，
   事实裁定归代码。
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ 判据
#: 标题里不许出现的金额（2026-11-06 定，与 test_prerender「全库标题不含金额」
#: 用的是同一份）。`prerender.MONEY_PAT` / `MONEY_WORD_PAT` 直接引用这里。
MONEY_PAT = re.compile(r"[$¥€£￥]\s*\d|\b(?:MRR|ARR)\b", re.I)
MONEY_WORD_PAT = re.compile(r"收入|营收|客单价|单价|定价|月费|年费|流水|买断")

# ------------------------------------------------------------------ 剥离
#: 金额「片段」比判据宽：覆盖中文金额、K/M/B 后缀、MRR/ARR 后接数字。
#: 只用于从展示名里删掉成本/价格，**不用于**给收入下结论。
_MONEY_CHUNK = re.compile(
    r"(?:"
    # $20K / ~$5,371.18 / ¥1,000 / $20/mo
    # ⚠️ 开头不吃 \s*：金额前的空格要留给前文，否则 `My $300 X` 会粘成 `MyX`。
    # ⚠️ 后缀必须落在词边界：`$5,371.18 MRR` 里的 M 不是 K/M/B 后缀（实测踩过）。
    r"[~约≈]?[$¥€£￥]\s*\d[\d,.]*(?:[KMB]|万|亿)?\b"
    # ⚠️ 长选项必须排在短选项前：`mo|month` 会把 `$7/month` 削成 `nth`（实测踩过）。
    r"\s*(?:/\s*(?:month|mo|year|yr|月|年))?"
    # 20 万美元 / 300 元 / 5 万人民币
    r"|\d[\d,.]*\s*(?:万|亿|千)?\s*(?:美元|美金|元|人民币|刀|USD|RMB|CNY)"
    # 月入 5 万 / 年收入 100 万
    r"|(?:月入|年收入|营收|月收入|收入|流水|客单价|单价|定价|月费|年费|买断)"
    r"\s*(?:达|到|从|为|约|是|[:：])?\s*[~约≈]?\s*[$¥€£￥]?\s*"
    r"\d[\d,.]*\s*(?:[KMB]|万|亿)?\b"
    # MRR / ARR（可后接金额）
    r"|\b(?:MRR|ARR)\b"
    r"(?:\s*(?:达|到|为|约|[:：])?\s*[~约≈]?\s*[$¥€£￥]?\s*"
    r"\d[\d,.]*(?:[KMB]|万|亿)?\b)?"
    r")",
    re.I)

#: 金额被删掉后，前面常留下「trained for 」「costs 」「仅售 」这类悬空引导语。
#: 只在**紧邻金额、且全是白名单词**时一起删，不碰前面的产品名。
_LEAD_IN = re.compile(
    r"(?:\b(?:trained|built|made|costs?|costing|priced|pricing|starting|"
    r"only|just|about|around|roughly|under|over|for|at|with|of|from)\b"
    r"[\s,;:–—-]*)+\s*$",
    re.I)


def money_hits(name):
    """返回展示名里命中的金额/收入词片段（空 list = 干净）。"""
    s = str(name or "")
    return [m.group(0) for pat in (MONEY_PAT, MONEY_WORD_PAT)
            for m in pat.finditer(s)]


def clean_display_name(name):
    """删掉展示名里的金额片段；没命中金额时**一个字节都不改**。

    例子::

        'My $300 embroidery machine needed a subscription'
          → 'My embroidery machine needed a subscription'
        'A decent text-to-image model trained for ~$20K (Apache 2.0 Weights)'
          → 'A decent text-to-image model (Apache 2.0 Weights)'
    """
    s = str(name or "")
    chunks = list(_MONEY_CHUNK.finditer(s))
    if not chunks:
        return s
    parts, pos = [], 0
    for m in chunks:
        head = s[pos:m.start()]
        lead = _LEAD_IN.search(head)
        if lead:
            head = head[:lead.start()]
        parts.append(head)
        pos = m.end()
    parts.append(s[pos:])
    out = "".join(parts)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\(\s*\)|（\s*）|\[\s*\]", "", out)     # 删金额后留下的空括号
    out = re.sub(r"\s+([,.;:!?)）】])", r"\1", out)
    out = re.sub(r"[\s,;:|·–—-]+$", "", out)              # 悬空连接符
    out = re.sub(r"^[,\s;:|·–—-]+", "", out)
    return out


def assert_clean_name(name, where="name"):
    """硬闸：展示名带金额就抛 ValueError，不让它写进真库。"""
    hits = money_hits(name)
    if hits:
        raise ValueError(
            "%s 含金额/收入词（%s）：%r\n"
            "标题与头条封面禁止金额。请先用 clean_display_name() 处理，或人工给一个"
            "不含金额的展示名；原帖标题留在 hn_title 作证据，不要直接当 name。"
            % (where, "/".join(dict.fromkeys(hits[:3])), name))
    return name


def clean_or_raise(name, where="name"):
    """入库口统一调用：先确定性剥离，剥不干净就当场打回人工。"""
    return assert_clean_name(clean_display_name(name), where)
