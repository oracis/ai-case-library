#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 Article 模型：一篇 case → 各平台共用的输入。

之前每个 *_publish.py 各自读 `out/articles/<id>.html` 各自抽标题、各自清洗
正文，结果是同一个案例在小红书/头条/B站三处标题长度策略各不相同（踩过的
坑：公众号 `<h1>` 是 `1Lookup：MRR $244,029；累计收入...` 这种数据串，
直接拿去当 B站 标题会超长且难看）。

这里把「标题/正文/平台专属增强」收成一处：

* `title` 优先用各平台已经精修过的标题文件（`out/<plat>/<id>/meta.json`），
  顺序即精修程度；
* 平台可声明 `title_priority`，把某个平台的精修标题提到最前；
* 都取不到才回退到公众号 `<h1>` 清洗结果。
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

OUT = os.path.join(ROOT, "out")


# ---- 源码定位 -------------------------------------------------------------
def source_html(cid):
    """一篇 case 的母版 HTML（公众号长文，唯一真源）。"""
    p = os.path.join(OUT, "articles", cid + ".html")
    return p if os.path.isfile(p) else None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def platform_title(cid, plat):
    """某平台已经精修好的标题（读它的 meta.json / note.json），没有则 None。"""
    for name in ("meta.json", "note.json"):
        d = _read_json(os.path.join(OUT, plat, cid, name))
        if isinstance(d, dict):
            t = (d.get("title") or "").strip()
            if t:
                return t
    return None


def _h1_title(html):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
    if not m:
        return None
    t = re.sub(r"<[^>]+>", "", m.group(1))
    t = re.sub(r"\s+", " ", t).strip()
    # 公众号 h1 常是「产品名：MRR $x；累计收入 $y」的数据串，截到第一个分号
    return t.split("；")[0].split(";")[0].strip() or t


def pick_title(cid, html, title_priority=()):
    """按优先级挑标题。找不到任何标题时返回空串（由调用方决定是否跳过）。"""
    for plat in title_priority:
        t = platform_title(cid, plat)
        if t:
            return t
    for plat in ("toutiao", "bili", "xhs", "wechat"):
        t = platform_title(cid, plat)
        if t:
            return t
    return _h1_title(html) or ""


# ---- Article --------------------------------------------------------------
class Article:
    """一篇待发文章。字段刻意保持最小：各平台要什么自己从 html 里取。"""

    __slots__ = ("cid", "name", "html", "path", "_title_cache")

    def __init__(self, cid, name="", html=None, path=None):
        self.cid = cid
        self.name = name or ""
        self.path = path or source_html(cid)
        self.html = html if html is not None else (
            _read_text(self.path) if self.path else None)
        self._title_cache = {}

    @property
    def ok(self):
        """母版 HTML 是否齐 —— 不齐就不该进任何平台（发出去是空页）。"""
        return bool(self.html)

    def title(self, title_priority=()):
        key = tuple(title_priority)
        if key not in self._title_cache:
            self._title_cache[key] = pick_title(self.cid, self.html, key)
        return self._title_cache[key]

    def __repr__(self):
        return "<Article %s %r>" % (self.cid, self.name)


def _read_text(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def load_article(cid, name=""):
    return Article(cid, name)


def load_cases():
    """库里全部 case（复用 xhs_publish 的读法，保证与旧队列同口径）。"""
    import xhs_publish as x
    return x.load_cases()
