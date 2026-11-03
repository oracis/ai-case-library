#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KaTeX 平台的美元写法统一器：`$7,306` → `7,306 美元`。

为什么必须有这一步（2026-11-02 实撞）
------------------------------------
CSDN / 腾讯云 / 阿里云 / 掘金 的正文是 markdown + KaTeX 渲染。**一段里出现
两个及以上 `$` 时，中间整段会被当成公式**（行内公式 `$...$` 的分隔符），
表现是「MRR 7,306、576 个订阅、近 30 天收入 19,349」被吃成一块斜体数学式，
数字全挤在一起。历史稿子里两种写法都有：腾讯云稿写 `\\$7,306`（导入后解成
`$`）、阿里云稿写裸 `$7,306`，两种都踩同一个坑。

⇒ 本脚本把它们**统一改写成「7,306 美元」**，正文里不再出现 `$`。

不碰什么
--------
- 代码块（``` 围栏）与行内 `code`：`${order.id}`、`$HOME` 这类是代码，不是钱。
- 没有数字跟随的 `$`（例如 `$` 单独出现）不动，交给人工看。
- 非金额的 `$` 变量名（`$foo`）不动。

用法
----
    python scripts/katex_money_fix.py drafts/newbatch/tencentcloud/*.md --go
    python scripts/katex_money_fix.py --scan drafts/newbatch/aliyun   # 只体检
"""
import argparse
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `$` + 数字（可带千分位与小数）：$7,306 / $199 / $9.99 / \$84,214
MONEY_RE = re.compile(r"\\?\$(?=\d)(\d[\d,]*(?:\.\d+)?)")
FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
SENTINEL = "\x00CODE%d\x00"


def _protect(text):
    """把代码块/行内代码换成占位符，避免被当成金额改写。"""
    keep = []

    def stash(m):
        keep.append(m.group(0))
        return SENTINEL % (len(keep) - 1)

    text = FENCE_RE.sub(stash, text)
    text = INLINE_CODE_RE.sub(stash, text)
    return text, keep


def _restore(text, keep):
    for i, s in enumerate(keep):
        text = text.replace(SENTINEL % i, s)
    return text


def fix_text(text):
    """返回 (新文本, 替换次数)。"""
    body, keep = _protect(text)
    n = len(MONEY_RE.findall(body))
    body = MONEY_RE.sub(lambda m: "%s 美元" % m.group(1), body)
    return _restore(body, keep), n


def scan_file(path):
    text = io.open(path, encoding="utf-8").read()
    body, _ = _protect(text)
    return MONEY_RE.findall(body)


def iter_files(args):
    out = []
    for a in args:
        if os.path.isdir(a):
            out += sorted(glob.glob(os.path.join(a, "*.md")))
        else:
            out += sorted(glob.glob(a))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="KaTeX 平台美元写法统一：$N → N 美元")
    ap.add_argument("paths", nargs="+", help="md 文件或目录")
    ap.add_argument("--go", action="store_true", help="落盘（默认只报告）")
    ap.add_argument("--scan", action="store_true", help="只统计待改数量")
    a = ap.parse_args(argv)

    files = iter_files(a.paths)
    if not files:
        raise SystemExit("没有匹配到 md 文件")
    total = 0
    for p in files:
        hits = scan_file(p)
        if not hits:
            continue
        total += len(hits)
        rel = os.path.relpath(p, ROOT)
        if a.scan:
            print("  %-52s 待改 %d 处：%s" % (rel, len(hits), ", ".join(hits[:6])))
            continue
        text = io.open(p, encoding="utf-8").read()
        new, n = fix_text(text)
        if not a.go:
            print("  %-52s %d 处（未落盘，加 --go）" % (rel, n))
            continue
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print("  %-52s 已改 %d 处" % (rel, n))
    print("合计 %d 处金额写法" % total if a.scan else "完成（合计 %d 处）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
