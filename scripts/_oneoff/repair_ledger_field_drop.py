#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「被整本重刷洗掉」的台账字段按 git HEAD 复原（单字段级，不做 checkout）。

背景（2026-11-24 实测）
--------------------
`scripts/wechat_publish.py::load_published()` 优先从 SQLite 读记录，重建时**只保留
5 个键** `{title, published_at, status, appmsgid, file}`；`save_published()` 随后把这份
dict 整本写回 `data/wechat_published.json`。结果：**每次发布都会把上一条已发表记录的
审计字段洗掉**（publish_type / published_appmsgid / sent_via / send_reason / note /
verified_at），只有本次刚发的那条因为 `_mark_done` 塞了完整 rec 才留得住。

同源事故：`state.py::Manifest.save()`（PRD #203）。判据也一样 —— 不信脚本自报，
拿 `git show HEAD:` 逐字段比对，只补「HEAD 有值、当前缺失/为空」的键。

顺带处理的另一类
---------------
CSDN 台账（`data/csdn_drafts.json`）里两条记录被今天的发布整条覆盖：`article_id`
变成 null（CSDN 接口返回 None），但 `url` 已经指向新文章 ⇒ 从 url 反解回填，
并与 `data/ledger.db` 的 `remote_id` 交叉校验，不匹配就不写。

用法
----
    python scripts/_oneoff/repair_ledger_field_drop.py            # 干跑
    python scripts/_oneoff/repair_ledger_field_drop.py --apply    # 写盘
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

# (文件, 记录 key, 复原方式, 字段清单)
#   restore  : 值取自 git HEAD（只补「当前缺失/空」的键）
#   from_url : 值从当前记录 url 反解（CSDN /details/<id>），并与 db.remote_id 校验
RULES = [
    ("data/wechat_published.json", "wagiff-app", "restore",
     ["publish_type", "published_appmsgid", "sent_via", "send_reason", "note", "verified_at"]),
    ("data/wechat_published.json", "homy", "restore",
     ["publish_type", "published_appmsgid", "sent_via", "send_reason", "note", "verified_at"]),
    ("data/zhihu_drafts.json", "upvoty", "restore", ["url"]),
    ("data/csdn_drafts.json", "ext-ep-002-2048", "from_url", ["article_id"]),
    ("data/csdn_drafts.json", "ext-ep-001-super-mario-bros", "from_url", ["article_id"]),
]


def head_json(rel: str):
    raw = subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT)
    return json.loads(raw.decode("utf-8"))


def cur_json(rel: str):
    return json.load(io.open(os.path.join(ROOT, rel), encoding="utf-8"))


def detect_newline(path: str) -> str:
    """按现有字节判断换行：CRLF 文件继续 CRLF，LF 文件继续 LF。"""
    with open(path, "rb") as f:
        raw = f.read()
    return "\r\n" if b"\r\n" in raw else ""


def db_remote_id(case_id: str) -> str:
    try:
        conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
        try:
            row = conn.execute(
                "select remote_id from platform_posts where platform='csdn' and case_id=?",
                (case_id,)).fetchone()
            return (row[0] if row and row[0] else "")
        finally:
            conn.close()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只干跑）")
    args = ap.parse_args()

    by_file: dict[str, list] = {}
    for rel, key, mode, fields in RULES:
        by_file.setdefault(rel, []).append((key, mode, fields))

    total_plan = 0
    for rel, items in by_file.items():
        path = os.path.join(ROOT, rel)
        head = head_json(rel)
        cur = cur_json(rel)
        plan = []
        for key, mode, fields in items:
            hrec = head.get(key) or {}
            crec = cur.get(key)
            if not isinstance(crec, dict):
                print("  ✗ %s / %s 当前记录不存在，跳过（需人工判断）" % (rel, key))
                continue
            for f in fields:
                curval = crec.get(f)
                if curval not in (None, ""):
                    continue
                if mode == "restore":
                    newval = hrec.get(f)
                    if newval in (None, ""):
                        print("  · %s / %s.%s HEAD 也是空，跳过" % (rel, key, f))
                        continue
                    src = "HEAD"
                else:  # from_url
                    url = str(crec.get("url") or "")
                    m = re.search(r"/(\d{6,})\b", url)
                    if not m:
                        print("  ✗ %s / %s.%s 无 url 可反解" % (rel, key, f))
                        continue
                    newval = m.group(1)
                    rid = db_remote_id(key)
                    if rid and rid != newval:
                        print("  ✗ %s / %s.%s url=%s 与 db.remote_id=%s 不一致，拒写"
                              % (rel, key, f, newval, rid))
                        continue
                    src = "url+db"
                plan.append((key, f, newval, src))
        if not plan:
            print("=== %s 无需修复" % rel)
            continue

        print("=== %s 待补 %d 处" % (rel, len(plan)))
        for key, f, v, src in plan:
            print("   %-30s %-20s → %-46s [%s]" % (key, f, str(v)[:46], src))
        total_plan += len(plan)
        if not args.apply:
            continue

        # 逐个记录：按 HEAD 的键序重建，再补当前独有的键（尽量少 diff）
        touched = {}
        for key, f, v, _src in plan:
            touched.setdefault(key, {})[f] = v
        for key, kv in touched.items():
            crec = cur[key]
            hrec = head.get(key) or {}
            newrec = {}
            for hk in hrec:                       # HEAD 键序优先
                if hk in kv:
                    newrec[hk] = kv[hk]
                elif hk in crec:
                    newrec[hk] = crec[hk]
            for ck in crec:                       # 当前独有的键追加到尾部
                if ck not in newrec:
                    newrec[ck] = crec[ck]
            for ck, cv in kv.items():             # 兜底：HEAD 也没有的键
                if ck not in newrec:
                    newrec[ck] = cv
            cur[key] = newrec

        bak = path + ".bak.repair"
        with open(path, "rb") as f:
            raw0 = f.read()
        with open(bak, "wb") as f:
            f.write(raw0)
        nl = detect_newline(path)
        with atomicio.file_lock(path + ".lock", timeout=30.0):
            atomicio.write_json(path, cur, indent=2, newline=nl, trailing_newline=False)
        print("   ✓ 已写盘（备份 %s，换行 %s）" % (os.path.basename(bak), "CRLF" if nl else "LF"))

    if not args.apply:
        print("\n干跑结束：共 %d 处待补；确认后加 --apply" % total_plan)
        return 0

    # 写后回读：确认没引入新的字段丢失
    print("\n=== 写后回读 ===")
    bad = 0
    for rel, items in by_file.items():
        cur = cur_json(rel)
        head = head_json(rel)
        for key, mode, fields in items:
            crec, hrec = cur.get(key) or {}, head.get(key) or {}
            for f, hv in hrec.items():
                if hv in (None, "") or f not in fields:
                    continue
                if crec.get(f) in (None, ""):
                    print("  ✗ %s / %s.%s 仍为空" % (rel, key, f))
                    bad += 1
    print("回读结果：%s" % ("全部补回" if bad == 0 else "%d 处异常" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
