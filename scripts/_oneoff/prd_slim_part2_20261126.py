# -*- coding: utf-8 -*-
"""补丁：把堆在 PRD 末尾（§9 术语表之后）的 #128–229 也迁进 CHANGELOG。"""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
prd = os.path.join(ROOT, "docs", "PRD.md")
chg = os.path.join(ROOT, "docs", "CHANGELOG.md")

cur = open(prd, encoding="utf-8").read().splitlines()
# #128 起点：形如 "| 128 | " 的第一行
i128 = next(i for i, l in enumerate(cur) if l.startswith("| 128 |"))
tail = "\n".join(cur[i128:]).rstrip()
print("尾部日志 %d 行，%d 条" % (
    len(tail.splitlines()), len(re.findall(r"^\| \d+ \|", tail, re.M))))

# 追加到 CHANGELOG（带分隔说明）
chg_txt = open(chg, encoding="utf-8").read().rstrip()
chg_txt += "\n\n---\n\n## 续：#128–229（原堆在 PRD §9 术语表之后，无章节归属；2026-11-26 一并迁出）\n\n" + tail + "\n"
open(chg, "w", encoding="utf-8", newline="").write(chg_txt)

# PRD 删掉这段，指针节补上数量
kept = cur[:i128]
# 清理指针节前面的空行
while kept and kept[-1] == "":
    kept.pop()
txt = "\n".join(kept) + "\n"
# 更新指针条目数：原两段 + 续段
n_all = len(re.findall(r"^\| \d+ \|", chg_txt, re.M))
txt = re.sub(r"共 \d+ 条，编号", "共 %d 条，编号" % n_all, txt)
open(prd, "w", encoding="utf-8", newline="").write(txt)
print("PRD 现在 %d 行；CHANGELOG %d 行 / %d 条" % (
    len(txt.splitlines()), len(chg_txt.splitlines()), n_all))
