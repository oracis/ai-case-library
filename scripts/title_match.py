# -*- coding: utf-8 -*-
"""标题归一化与唯一命中判据（跨平台台账对账共用）。

为什么单列一个模块：B站/头条两边都要「按标题认领线上文章」，一旦各写一份，
判据就会漂移 —— 一边放 12 字前缀容差、另一边放 20 字，同一篇稿在两个平台
得到的结论就不一样了（2026-11-20 对账时发现的苗头）。

判据只有一条：**归一化后完全相等，或一方是另一方前缀且长度差 ≤ MAX_GAP**。
候选必须唯一。绝不按相似度投票 —— `bili_draft_sync.py` 的教训是纯标题
匹配会错配（harperai 的草稿标题里一个产品名都没有，只有正文首句能认）。
"""
import re
import unicodedata

MAX_GAP = 12

_PUNCT = r"[\s\-_：:，,。.、！!？?（）()\[\]【】「」《》/|·—~～*\"'’“”]+"


def norm(s):
    """归一化：全角→半角、去掉空白与标点、ASCII 小写。只用于比较，不用于展示。"""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(_PUNCT, "", s).lower()


def title_match(a, b):
    """两个标题是否算「同一篇」。注意：这是必要条件，不是充分条件 ——
    调用方还要求**候选唯一**才敢认领。"""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    lo, hi = (na, nb) if len(na) <= len(nb) else (nb, na)
    return hi.startswith(lo) and (len(hi) - len(lo)) <= MAX_GAP
