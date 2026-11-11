#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复上一轮批量清理把 36 个 `out/articles/*.md` 清成 0 字节的事故。

事故根因（tmp/batch_clean_verify_notes.py）
------------------------------------------
    with open(p, 'w', encoding='utf-8') as f:          # ← 先截断成 0 字节
        f.write(clean(open(p, 'r', encoding='utf-8').read()))   # ← 再读自己=空串

`open(p,'w')` 在**进入 with 的瞬间**就把文件清空，内层 `read()` 读到的是空串，
于是「读到的原文」恒为空、写回也是空。日志里还写着「清理 N 行」，是同一个
`process_file` 里先读的那一份算出来的，看起来全绿 —— 属于典型的**假绿**：
判据（"注记是否还在"）对空文件恒成立。

修法
----
1. 原文从 git 取（`3b69e9c^`）—— 36/36 都能取回，不依赖任何残留副本；
2. **先把全文读进内存**，再算新文本，再写；
3. 写前逐条验：注记消失、体积 ≥ 原体积 60%、行数减少恰为 2；
4. 原子落盘（atomicio），失败即回滚该文件。

用法：
    python -X utf8 tmp/fix_zeroed_articles.py            # 干跑
    python -X utf8 tmp/fix_zeroed_articles.py --apply
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

MARK = "我去核了一遍"
BASE = "3b69e9c^"


def git_show(path):
    r = subprocess.run(["git", "show", "%s:%s" % (BASE, path)],
                       cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git 取不回 %s" % path)
    return r.stdout.decode("utf-8")


def strip_note(text):
    """删掉注记那一行 + 紧随其后的一个空行。返回 (新文本, 删掉的行)。

    ⚠ 每个文件的注记都恰好是 1 行（本轮 36/36 实测），且后面跟一个空行；
       把空行一起删掉，避免留下连续 2 个空行。
    """
    lines = text.splitlines(keepends=True)
    idx = [i for i, l in enumerate(lines) if MARK in l]
    if len(idx) != 1:
        raise RuntimeError("注记行数 %d，预期 1" % len(idx))
    i = idx[0]
    removed = lines[i]
    end = i + 1
    if end < len(lines) and not lines[end].strip():
        removed += lines[end]
        end += 1
    return "".join(lines[:i] + lines[end:]), removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    log = os.path.join(ROOT, "tmp", "batch_clean_log.json")
    d = json.load(open(log, encoding="utf-8"))
    paths = [r[0] for r in d["results"] if r[1] != "skip"]

    ok = bad = 0
    for rel in paths:
        full = os.path.join(ROOT, rel)
        size_now = os.path.getsize(full)
        try:
            raw = git_show(rel)
            new, removed = strip_note(raw)
        except Exception as e:                             # noqa: BLE001
            print("%-44s ✗ %s" % (rel, e))
            bad += 1
            continue

        # 四条硬校验，任一不过就不写
        checks = {
            "注记已无": MARK not in new,
            "体积达标": len(new.encode("utf-8")) >= len(raw.encode("utf-8")) * 0.6,
            "行数减2": len(new.splitlines()) == len(raw.splitlines()) - 2,
            "留下正文": len(new.strip()) > 200,
        }
        fail = [k for k, v in checks.items() if not v]
        if fail:
            print("%-44s ✗ 校验不过：%s" % (rel, "、".join(fail)))
            bad += 1
            continue

        print("%-44s 现 %5d B ← 恢复 %5d B（删注记 %d 字）"
              % (rel, size_now, len(new.encode("utf-8")), len(removed)))
        if a.apply:
            atomicio.write_text(full, new)
        ok += 1

    print("\n%s：可修 %d ｜ 失败 %d" % ("已修" if a.apply else "干跑", ok, bad))
    if a.apply:
        zero = [p for p in paths if os.path.getsize(os.path.join(ROOT, p)) == 0]
        print("复核 0 字节残留：%d（期望 0）" % len(zero))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
