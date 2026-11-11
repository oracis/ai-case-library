#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 howtomakemi.com/cases/<slug>/ 的页面解析成结构化事实。

只做「读页面 → 出 JSON」，不判断、不写库。判断留给 anti_cases 的人工表。
输出 tmp/neg/parsed.json，key 是 slug。

为什么单独写一个：页面是 Astro 服务端渲染的静态 HTML（无 script 载荷），
结构稳定（h2 小节 + fact-card），用正则比引第三方解析器省事，
也和库里「零依赖」的约定一致。
"""

import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "tmp", "neg")
OUT = os.path.join(ROOT, "tmp", "neg", "parsed.json")

SLUGS = """higgsfield lovable cognition primeintellect appliedcompute openrouter morph bland-ai
retell decagon sanas opencode n8n hightouch glean crustdata micro1
medvi geviti develop-health hello-clever tern-group tjm-labs fable-security huntress lexroom
paraform rox crowdreply
skello omnidocs picturethis doomers""".split()


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|td|th|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fact(s, key):
    """取一张 fact-card：值 + 副标题。"""
    m = re.search(
        r'class="fc-k"[^>]*>' + re.escape(key) + r'</span>\s*'
        r'<b class="fc-v[^"]*"[^>]*>(.*?)</b>'
        r'(?:\s*<span class="fc-sub"[^>]*>(.*?)</span>)?',
        s, re.S)
    if not m:
        return None, None
    return strip_tags(m.group(1)), strip_tags(m.group(2) or "")


def parse(path):
    s = open(path, encoding="utf-8", errors="ignore").read()
    d = {}

    m = re.search(r"<h1[^>]*>(.*?)</h1>", s, re.S)
    d["name"] = strip_tags(m.group(1)) if m else None
    m = re.search(r'<p class="one"[^>]*>(.*?)</p>', s, re.S)
    d["one_liner"] = strip_tags(m.group(1)) if m else None
    m = re.search(r'class="ev ev-([a-z]+)"[^>]*>(.*?)</span>', s, re.S)
    d["ev_badge"] = strip_tags(m.group(2)) if m else None
    m = re.search(r'class="chip chip-cat"[^>]*>(.*?)</span>', s, re.S)
    d["category"] = strip_tags(m.group(1)) if m else None
    m = re.search(r'class="cta" href="([^"]+)"', s)
    d["site"] = m.group(1) if m else None

    v, sub = fact(s, "月收入")
    d["page_label_key"] = "月收入"
    d["page_label_value"], d["page_label_sub"] = v, sub
    d["founder"], _ = fact(s, "创始人")
    d["country"], _ = fact(s, "国家 / 地区")

    # 按 h2 切小节
    parts = re.split(r"<h2[^>]*>(.*?)</h2>", s, flags=re.S)
    secs = {}
    for i in range(1, len(parts) - 1, 2):
        title = strip_tags(parts[i])
        secs[title] = strip_tags(parts[i + 1])
    d["sections"] = secs

    for k in ("产品是什么", "怎么赚的钱", "流量从哪来", "站长是谁",
              "这个案例能学到什么", "来源与数据", "一句话总结", "数据口径"):
        d[k] = secs.get(k, "")

    # 「其他信息」段里再切一次 key: value
    other = secs.get("其他信息", "")
    for k in ("营收模式", "流量来源", "数据口径", "原文链接"):
        m = re.search(re.escape(k) + r"\s*\n(.+)", other)
        if m:
            d["other_" + k] = m.group(1).strip()
    if "数据口径" in secs:
        d["数据口径"] = secs["数据口径"]
    elif "other_数据口径" in d:
        d["数据口径"] = d["other_数据口径"]

    return d


def main():
    out = {}
    miss = []
    for slug in dict.fromkeys(SLUGS):
        p = os.path.join(SRC, slug + ".html")
        if not os.path.exists(p):
            miss.append(slug)
            continue
        try:
            out[slug] = parse(p)
        except Exception as e:                                   # noqa: BLE001
            miss.append("%s(%s)" % (slug, e))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("解析 %d 条 → %s" % (len(out), OUT))
    if miss:
        print("缺/失败：", miss)
        return 1
    # 完整性体检：关键字段空的比例
    keys = ("name", "one_liner", "page_label_value", "产品是什么", "怎么赚的钱", "数据口径")
    for k in keys:
        empty = [s for s, v in out.items() if not v.get(k)]
        print("  %-14s 空 %d %s" % (k, len(empty), ("→ " + ", ".join(empty[:8])) if empty else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
