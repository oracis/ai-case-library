#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读干跑：B站 16 条带注记的草稿，若摘掉那段引用块，正文会变成什么。

⛔ 不调用 save_draft，不写平台、不写盘。只打印每条「改前/改后」的形状与差异，
   用来在真正批量改之前拿到「只动这一块、其余逐字不变」的证据。

用法：
    python -X utf8 tmp/dryrun_bili_draft_clean.py
    python -X utf8 tmp/dryrun_bili_draft_clean.py --dump 342372   # 看单条原始结构
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

MARK = "我去核了一遍"


def strip_marker_para(html):
    """从编辑器 HTML 里摘掉含注记的那**一个块**，其余逐字保留。

    ⚠ 不能整段正则删「到下一个标签」——注记块里可能嵌 `<br>` 或行内标签，
       且长度跨行不定。做法是先把 HTML 切成顶层块，命中注记的整块丢掉。
    """
    blocks = re.findall(r"<([a-zA-Z0-9]+)[^>]*>.*?</\1>", html, re.S)
    # 顶层块切割（非嵌套前提：B站编辑器正文是平铺段落，无嵌套容器）
    parts = re.split(r"(?=<(?:p|blockquote|h[1-6]|div|ul|ol|pre)[^>]*>)", html)
    out, dropped = [], []
    for p in parts:
        if MARK in p:
            dropped.append(p)
            continue
        out.append(p)
    return "".join(out), dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()

    import wechat_publish as wp
    import bili_draft_api as ba
    import bilibili_publish as bp

    cdp = wp.CDP(a.port or bp.PUB_PORT)
    ba._bili_tab(cdp)
    drafts = ba.list_drafts(cdp)

    targets = [d for d in drafts
               if a.dump and str(d.get("article_id") or d.get("aid")) == a.dump]
    if not targets:
        targets = []
        for d in drafts:
            aid = d.get("article_id") or d.get("aid")
            try:
                v = ba.view_draft(cdp, aid)
            except Exception:                              # noqa: BLE001
                continue
            if MARK in (v.get("content") or ""):
                targets.append(d)
        if a.dump:
            print("没找到 %s" % a.dump)
            return

    if a.dump:
        v = ba.view_draft(cdp, targets[0].get("article_id") or targets[0].get("aid"))
        print("draft/view 返回字段：", sorted(v))
        print()
        for k in sorted(v):
            if k in ("content",):
                continue
            print("  %-20s = %s" % (k, json.dumps(v[k], ensure_ascii=False)[:200]))
        c = v.get("content") or ""
        print("\ncontent 类型 %s，长度 %d" % (type(c).__name__, len(c)))
        print("---- 前 600 字 ----")
        print(c[:600])
        print("\n---- 含注记的那一块 ----")
        _, dropped = strip_marker_para(c)
        for d in dropped:
            print(d[:500])
        return

    print("带注记草稿 %d 条\n" % len(targets))
    ok = 0
    for d in targets:
        aid = d.get("article_id") or d.get("aid")
        v = ba.view_draft(cdp, aid)
        old = v.get("content") or ""
        new, dropped = strip_marker_para(old)
        same_rest = (old.replace(dropped[0], "", 1) == new) if dropped else False
        print("%-9s %-38s 改前 %5d → 改后 %5d  摘掉 %d 块  其余逐字不变=%s"
              % (aid, (v.get("title") or "")[:36], len(old), len(new),
                 len(dropped), same_rest))
        if dropped and same_rest and len(new) > 200:
            ok += 1
    print("\n可安全摘除（只少那一块、其余逐字不变）：%d / %d" % (ok, len(targets)))


if __name__ == "__main__":
    main()
