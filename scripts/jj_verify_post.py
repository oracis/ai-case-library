#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""核验掘金已发布文章是否已换成新版（表格 + 无 KaTeX）。

判据取自线上 HTML（curl 直取，绕浏览器缓存）：
  table>0 且 katex==0 且 "美元">0  → OK
  table==0 且 katex>0              → STALE（仍是旧版）

用法：
  python scripts/jj_verify_post.py 7702463374432796699
  python scripts/jj_verify_post.py --url https://juejin.cn/post/xxxx --watch 10
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url: str) -> str:
    r = subprocess.run(
        ["curl", "-s", "--max-time", "40", "-A", UA,
         "-H", "Accept-Language: zh-CN,zh;q=0.9", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout or ""


def stats(html: str) -> dict:
    # 只统计正文容器内，避免把页面脚本里的 $ 算进来
    m = re.search(r'<div[^>]+class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>\s*<div',
                  html, re.S)
    body = m.group(1) if m else html
    return {
        "table": body.count("<table"),
        "katex": body.count("katex"),
        "usd": body.count("美元"),
        "dollar": body.count("$"),
        "len": len(body),
    }


def check(url: str, quiet: bool = False) -> bool:
    s = stats(fetch(url))
    ok = s["table"] > 0 and s["katex"] == 0
    if not quiet:
        tag = "✅ 已更新" if ok else "❌ 仍是旧版"
        print("%s  table=%d katex=%d 美元=%d $=%d len=%d"
              % (tag, s["table"], s["katex"], s["usd"], s["dollar"], s["len"]))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("aid", nargs="?", help="文章 id（数字）")
    ap.add_argument("--url")
    ap.add_argument("--watch", type=int, default=0, help="每 N 分钟轮询一次，0=只查一次")
    ap.add_argument("--times", type=int, default=12)
    a = ap.parse_args()

    url = a.url or ("https://juejin.cn/post/%s" % a.aid if a.aid else "")
    if not url:
        ap.error("给 aid 或 --url")

    if not a.watch:
        return 0 if check(url) else 1

    for i in range(a.times):
        print(time.strftime("%H:%M:%S"), end=" ")
        if check(url):
            return 0
        if i < a.times - 1:
            time.sleep(a.watch * 60)
    return 1


if __name__ == "__main__":
    sys.exit(main())
