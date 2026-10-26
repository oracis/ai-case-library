#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 data/drafts_pending.json 的 title 与 drafts/ 里的草稿首行对齐。

为什么需要这个脚本
------------------
`gen_platform_drafts.py` 生成草稿时，会把草稿首行的标题抄进台账的 `title`
字段。之后任何对草稿首行的改动（比如 `fix_draft_titles.py` 那次「标题全面去
金额」，一次改了 41 条）都不会自动回写台账，于是台账里一直留着旧标题——
**而旧标题恰恰是带金额的那一版**。

这个不一致很危险：台账标题看起来就是「这条草稿叫什么」，将来发布脚本若直接
取 `title` 字段，就会绕过「标题禁金额」这条铁律，把 `从0到40K MRR…` 原样推
到平台上——而且不会有任何报错。

用法
----
    python scripts/sync_draft_ledger.py          # 同步（幂等）
    python scripts/sync_draft_ledger.py --check  # 只体检，不改文件

幂等：title 已与首行一致时什么都不写。
"""

from __future__ import print_function

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "data", "drafts_pending.json")


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def first_heading(path):
    """取草稿首行标题，去掉前导 '# '。不是标题行则返回空串。"""
    first = read(path).split("\n")[0].strip()
    return first[2:].strip() if first.startswith("# ") else ""


def norm_sep(p):
    """台账里的 file 字段是 os.path.relpath 的产物，Windows 下是反斜杠。

    入库后要能在 Linux 上打开，统一成正斜杠。
    """
    return p.replace("\\", "/")


def load():
    if not os.path.isfile(LEDGER):
        return None
    return json.loads(read(LEDGER))


def scan(ledger):
    """返回需要修正的条目 [(index, 旧title, 新title, 新file)]。"""
    out = []
    for i, rec in enumerate(ledger.get("drafts") or []):
        rel = norm_sep(rec.get("file") or "")
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            out.append((i, rec.get("title", ""), None, rel, "草稿文件不存在"))
            continue
        head = first_heading(path)
        if not head:
            out.append((i, rec.get("title", ""), None, rel, "草稿首行不是标题"))
            continue
        if head != rec.get("title", "").strip() or rel != rec.get("file"):
            out.append((i, rec.get("title", ""), head, rel, ""))
    return out


def check_only():
    ledger = load()
    if ledger is None:
        print("没有台账 %s，跳过。" % os.path.relpath(LEDGER, ROOT))
        return 0
    todo = scan(ledger)
    n = len(ledger.get("drafts") or [])
    print("台账 %d 条，需同步 %d 条。" % (n, len(todo)))
    for i, old, new, rel, err in todo[:20]:
        if err:
            print("  ! %s" % err)
        else:
            print("  - %s" % (old[:46] or "(空)"))
            print("  + %s" % (new[:46] or "(空)"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不改文件")
    args = ap.parse_args()

    if args.check:
        return check_only()

    ledger = load()
    if ledger is None:
        print("没有台账，什么都不做。")
        return 0

    todo = scan(ledger)
    errs = [t for t in todo if t[4]]
    if errs:
        for i, old, new, rel, err in errs:
            print("[ERROR] %s：%s" % (rel, err))
        print("有 %d 条草稿对不上，先修这些再同步。" % len(errs))
        return 1

    if not todo:
        print("台账 title 已与草稿首行一致，无需改动。")
        return 0

    for i, old, new, rel, err in todo:
        ledger["drafts"][i]["title"] = new
        ledger["drafts"][i]["file"] = rel

    with io.open(LEDGER, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")

    print("同步 %d 条 title（并把 file 路径统一为正斜杠）。" % len(todo))
    for i, old, new, rel, err in todo[:10]:
        print("  - %s" % (old[:46] or "(空)"))
        print("  + %s" % (new[:46] or "(空)"))
    if len(todo) > 10:
        print("  … 另 %d 条" % (len(todo) - 10))
    return 0


if __name__ == "__main__":
    sys.exit(main())
