#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量清 `drafts/**.md` 的 `> 平台：… ｜ 状态：…` 管线水印行（2026-11-28）。

用法::

    python -X utf8 scripts/_oneoff/clean_drafts_watermark_20261128.py          # 干跑
    python -X utf8 scripts/_oneoff/clean_drafts_watermark_20261128.py --apply   # 真写

安全边界：默认干跑；只认行首 `> 平台：` 且每个文件恰好一行、紧随其后必须是空行；
写前把 52 个原文件按相对路径备份到 tmp/；逐文件原子写 + 回读校验；任何一步失败
整组回滚。不改标题、正文与行尾格式（原文件全为 LF、无 BOM）。
"""
import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

DRAFTS = os.path.join(ROOT, "drafts")
BACKUP_ROOT = os.path.join(ROOT, "tmp", "drafts_watermark_backup_20261128")
WATERMARK = re.compile(r"^\s*>\s*平台[:：]", re.M)


def inventory():
    """→ [(abs_path, raw_bytes, watermark_line_index)]；异常直接中止。"""
    out = []
    for root, dirs, files in os.walk(DRAFTS):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            with open(path, "rb") as f:
                raw = f.read()
            lines = raw.splitlines(keepends=True)
            hits = [i for i, ln in enumerate(lines)
                    if WATERMARK.match(ln.decode("utf-8", "replace"))]
            if not hits:
                continue
            if len(hits) != 1:
                raise RuntimeError("水印行不止一条：%s %s" % (path, hits))
            idx = hits[0]
            if idx + 1 >= len(lines) or lines[idx + 1].strip() not in (b"",):
                raise RuntimeError("水印行后不是空行：%s" % path)
            if b"\r\n" in raw:
                raise RuntimeError(" unexpected CRLF（需单独处理）：%s" % path)
            out.append((path, raw, idx))
    return out


def strip_pair(raw, idx):
    lines = raw.splitlines(keepends=True)
    del lines[idx:idx + 2]          # 水印行 + 紧随的空行
    return b"".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    items = inventory()
    print("命中 %d 个文件" % len(items))
    for path, raw, idx in items[:5]:
        print("  %s:%d  %s" % (os.path.relpath(path, ROOT), idx + 1,
                              raw.splitlines()[idx].decode("utf-8", "replace")[:100]))
    if len(items) > 5:
        print("  … 其余 %d 个" % (len(items) - 5))
    if not args.apply:
        print("[DRY] 未写盘；加 --apply 才执行")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(backup, exist_ok=True)
    saved = []
    try:
        for path, raw, idx in items:
            dst = os.path.join(backup, os.path.relpath(path, ROOT))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            atomicio.write_bytes(dst, raw)
            saved.append((path, raw, idx))
        for path, raw, idx in items:
            new = strip_pair(raw, idx)
            atomicio.write_bytes(path, new)
            back = open(path, "rb").read()
            if back != new:
                raise RuntimeError("回读不一致：%s" % path)
            if WATERMARK.search(back.decode("utf-8", "replace")):
                raise RuntimeError("水印仍在：%s" % path)
            if len(back.splitlines()) != len(raw.splitlines()) - 2:
                raise RuntimeError("行数不是恰好少 2：%s" % path)
    except BaseException as exc:
        for path, raw, idx in saved:
            atomicio.write_bytes(path, raw)
        print("[ROLLBACK] %s" % exc)
        return 2

    left = inventory()
    print("[APPLY] 已清 %d 个文件；残留 %d" % (len(items), len(left)))
    print("备份 → %s" % os.path.relpath(backup, ROOT))
    return 0 if not left else 2


if __name__ == "__main__":
    sys.exit(main())
