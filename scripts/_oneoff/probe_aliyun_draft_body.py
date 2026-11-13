#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：把阿里云某份草稿的服务端正文取下来，与本地稿做「旧稿独有片段」比对。

为什么需要：`rewrite_yun_draft.py` 的 OLD_GONE 判据用「旧稿前 20 字是否还在」，
但同一案例重写时首段常常被原样沿用 ⇒ 判据恒为 False，正文明明已换成新稿也报
FAIL（假红）。这里换一个不依赖首段的判据：从旧稿里挑一段「新稿里没有」的文字
当探针，探针在服务端正文里消失、且新稿特征段出现，才算真的换掉了。

用法：
  python -X utf8 scripts/_oneoff/probe_aliyun_draft_body.py --aid 1699451 \
      --file drafts/newbatch/aliyun/headshotpro.md \
      --old tmp/yun_draft_backup_aliyun_1699451.json
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "_oneoff"))

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

import aliyun_publish_go as G          # noqa: E402
import yun_finalize as Y               # noqa: E402

WIN = 30


def norm(s):
    return re.sub(r"\s+", "", s or "")


def old_only_probe(old, new):
    """从旧稿里找第一个「新稿中不存在」的 30 字窗口。"""
    o, n = norm(old), norm(new)
    for i in range(0, max(1, len(o) - WIN), 10):
        seg = o[i:i + WIN]
        if len(seg) == WIN and seg not in n:
            return seg
    return ""


def has_large_old_segment(rbody, old, new):
    """服务端正文里是否还留着大段旧稿专属内容（超过 WIN*2 的连续旧稿片段）。"""
    r, o, n = norm(rbody), norm(old), norm(new)
    for i in range(0, max(1, len(o) - WIN * 2), WIN):
        seg = o[i:i + WIN * 2]
        if len(seg) == WIN * 2 and seg in r and seg not in n:
            return seg
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", required=True)
    ap.add_argument("--file", required=True, help="本地新稿 .md")
    ap.add_argument("--old", required=True, help="rewrite 生成的旧稿备份 json")
    a = ap.parse_args()

    new_md = open(a.file, encoding="utf-8").read()
    new_body = "\n".join(
        l for l in new_md.splitlines() if not l.startswith("# "))
    old_body = (json.load(open(a.old, encoding="utf-8")) or {}).get("body") or ""

    proc, cdp = Y._connect()
    cdp.open("https://developer.aliyun.com/my#/article")
    time.sleep(6)
    d = G.draft_detail(cdp, a.aid)
    body = html.unescape(d.get("content") or "")

    probe = old_only_probe(old_body, new_body)
    left = has_large_old_segment(body, old_body, new_body)
    tail = norm(new_body)[-40:]

    print("aid          =", a.aid)
    print("HTTP         =", d.get("http"))
    print("平台标题     =", repr((d.get("title") or "")[:60]))
    print("平台正文长度 =", len(body))
    print("本地新稿长度 =", len(norm(new_body)))
    print("旧稿长度     =", len(norm(old_body)))
    print("旧稿独有探针 =", repr(probe))
    print("探针仍在正文? ", (norm(probe) in norm(body)) if probe else "n/a")
    print("残留旧稿大段? ", repr(left[:40]) if left else "无")
    print("新稿结尾在正文?", norm(tail) in norm(body))
    ok = (d.get("http") == "200" and bool(probe)
          and norm(probe) not in norm(body) and not left
          and norm(tail) in norm(body))
    print("\n判定:", "OK 正文已换成新稿" if ok else "MISS 正文仍是旧稿/不完整")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
