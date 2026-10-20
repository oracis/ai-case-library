#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全库正文体检：抓空段落、占位符、样板残留。

不只查「我的判断」——任何 `##` 小节渲染为空、或正文里残留
「（正文为空）」「待补充」这类占位符，都算问题。
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "out", "articles")

PLACEHOLDER = [
    "（正文为空）", "(正文为空)", "待补充", "TODO", "TBD",
    "XXX", "xxx占位", "暂无内容", "（无）", "（待填",
]

# 正文章节标题（## 级）
SEC_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def main():
    problems = {}
    stats = {"files": 0, "sections": 0, "empty_sections": 0}

    for fn in sorted(os.listdir(ART)):
        if not fn.endswith(".md"):
            continue
        cid = fn[:-3]
        path = os.path.join(ART, fn)
        txt = open(path, encoding="utf-8").read()
        stats["files"] += 1

        issues = []

        # 1) 空小节：## 标题后面到下一个标题/结尾，去掉空行和分隔线后没内容
        heads = list(SEC_RE.finditer(txt))
        for i, m in enumerate(heads):
            start = m.end()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(txt)
            body = txt[start:end]
            # 去掉 HTML 注释、分隔线、表格边框行后的实质内容
            cleaned = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            cleaned = re.sub(r"^\s*[-*_]{3,}\s*$", "", cleaned, flags=re.M)
            cleaned = cleaned.strip()
            stats["sections"] += 1
            if not cleaned:
                issues.append("空小节：## %s" % m.group(1))
                stats["empty_sections"] += 1

        # 2) 占位符残留
        for ph in PLACEHOLDER:
            # 排除出现在「核对用来源」注释块里的
            for m in re.finditer(re.escape(ph), txt):
                ctx = txt[max(0, m.start() - 200):m.end() + 200]
                if "<!--" in ctx and "-->" in ctx.split(ph)[0][-200:]:
                    continue
                issues.append("占位符残留：%s" % ph)
                break

        # 3) 正文太短（< 400 纯文字，可能是空壳）
        plain = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
        plain = re.sub(r"^#{1,6}\s+.*$", "", plain, flags=re.M)
        plain = re.sub(r"^\s*[-*_]{3,}\s*$", "", plain, flags=re.M)
        plain = re.sub(r"\s+", "", plain)
        if len(plain) < 400:
            issues.append("正文过短：%d 字" % len(plain))

        if issues:
            problems[cid] = issues

    print("扫描 %d 篇 / %d 个小节，空小节 %d 个"
          % (stats["files"], stats["sections"], stats["empty_sections"]))
    print("有问题的文章：%d 篇" % len(problems))
    print()
    for cid, iss in sorted(problems.items()):
        print("== %s" % cid)
        for x in iss:
            print("    - %s" % x)


if __name__ == "__main__":
    main()