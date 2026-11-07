#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要守卫：拦「笼统 / 与本文无关」的摘要，发布前必跑。

为什么存在
----------
2026-11-06 阿里云 2048 那篇发出去的摘要是一句栏目标语：
「「游戏厅」栏目，每期只讲一款游戏：它是什么、怎么玩、它用的是什么技术。」
它对这篇 2048 零信息量，读者看不出这篇讲什么。

根因两条（都在代码里）：
1. `yun_finalize.finalize_aliyun` 旧写法只在摘要为空时注入 ⇒ 建稿时填的
   兜底摘要（first_paragraph 抓到的栏目口号）把显式 --abstract 静默顶掉。
   已在 2026-11-06 改为按内容比对覆盖 + 回读一致才放行。
2. `first_paragraph` 不知道「栏目标语段」是模板，抓到的就是那句固定口号。
   已加 BOILER 跳过表。

2026-11-06 晚补：那句台词本身已从 106 份存量稿里删掉（游戏仓
`scripts/strip_column_line.py`，PRD #75）。`BOILER` 里的「栏目」「每期只讲」
**故意保留** —— 平台草稿箱与外部来稿仍可能有旧稿，摘要守卫要对它们照样拦。

但上面两条只是「不再偷换」，挡不住人/模型**写出**一条笼统摘要。
所以加这道确定性判据（不靠模型自觉）：

判据
----
1. 长度 60–300（阿里云摘要上限 300）；
2. 不含模板/口号句（栏目、每期只讲、本文介绍、先说清楚…）；
3. 至少命中 **1 个本篇专有锚点**：GitHub 仓库名 / 反引号里的文件路径 /
   许可证名 / 3 位以上数字 / 标题里的专名。

用法
----
    python -X utf8 scripts/abstract_guard.py --file 稿子.md --abstract "摘要文本"
    python -X utf8 scripts/abstract_guard.py --file 稿子.md   # 只列可用锚点
退出码 0 = 通过，1 = 不通过（附具体缺什么）。
"""
import argparse
import re
import sys

BOILER = ("栏目", "每期只讲", "本文介绍", "先说清楚", "这里只有源码",
          "不用付费解锁", "以下是正文", "本文将", "本篇文章")

LICENSES = ("MIT", "Apache-2.0", "GPL-3.0", "GPL-2.0", "LGPL-3.0",
            "BSD-3-Clause", "MPL-2.0", "AGPL-3.0", "Unlicense", "Zlib")


def collect_anchors(body, title=""):
    """从稿件正文里抽本篇专有锚点（摘要用得上才算「具体」）。"""
    anchors = {}
    for m in re.finditer(r"github\.com/([\w.\-]+)/([\w.\-]+)", body):
        anchors.setdefault("仓库名", set()).add(m.group(2).rstrip(").,`"))
    for m in re.finditer(r"`([^`\n]{2,60})`", body):
        token = m.group(1).strip()
        if re.search(r"[./]", token):
            anchors.setdefault("文件路径", set()).add(token)
    for lic in LICENSES:
        if re.search(r"(?<![A-Za-z0-9\-])%s(?![A-Za-z0-9\-])" % re.escape(lic), body):
            anchors.setdefault("许可证", set()).add(lic)
    for m in re.finditer(r"(\d{3,})", body):
        anchors.setdefault("数字", set()).add(m.group(1))
    # 标题里的英文专名（2048 / SuperTux / Quake）
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-]{2,}", title or ""):
        anchors.setdefault("标题专名", set()).add(m.group(0))
    return anchors


def check(abstract, body, title="", lo=60, hi=300):
    problems, notes = [], []
    n = len(abstract)
    if n < lo:
        problems.append("太短（%d 字 < %d），说不清这篇具体讲什么" % (n, lo))
    if n > hi:
        problems.append("超长（%d 字 > %d 平台上限）" % (n, hi))
    hit_boiler = [k for k in BOILER if k in abstract]
    if hit_boiler:
        problems.append("含模板/口号句：%s" % "、".join(hit_boiler))

    anchors = collect_anchors(body, title)
    matched = []
    for kind, vals in anchors.items():
        for v in vals:
            if kind == "数字":
                # 允许 13453 写成 13.5k / 13,453
                alts = {v, v.replace(",", ""), "{:,}".format(int(v))}
                if int(v) >= 1000:
                    alts.add("%.1fk" % (int(v) / 1000))
                if any(x in abstract for x in alts):
                    matched.append("%s:%s" % (kind, v))
            elif v and v in abstract:
                matched.append("%s:%s" % (kind, v))
    if not matched:
        problems.append("没有任何本篇专有锚点（仓库名/文件路径/许可证/数字都没有）")
        notes.append("可用锚点：" + "；".join(
            "%s=%s" % (k, ",".join(sorted(v)[:6])) for k, v in anchors.items()))
    return problems, matched, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="稿件 md（取锚点用）")
    ap.add_argument("--abstract", default="", help="待检摘要；省略则只列锚点")
    ap.add_argument("--lo", type=int, default=60)
    ap.add_argument("--hi", type=int, default=300)
    a = ap.parse_args()

    body = open(a.file, encoding="utf-8").read()
    title = ""
    for line in body.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break
    anchors = collect_anchors(body, title)
    print("可用锚点：")
    for k, v in anchors.items():
        print("  %-8s %s" % (k, "、".join(sorted(v)[:8])))
    if not a.abstract:
        return 0

    problems, matched, notes = check(a.abstract, body, title, a.lo, a.hi)
    print("\n摘要 %d 字，命中锚点 %d 个：%s"
          % (len(a.abstract), len(matched), "、".join(matched[:6]) or "无"))
    for nt in notes:
        print("  " + nt)
    if problems:
        print("\n✗ 不通过：")
        for p in problems:
            print("  - " + p)
        return 1
    print("\n✓ 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
