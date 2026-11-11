#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清掉 `out/articles/*.html` 里残留的 36 条内部核验注记（默认干跑）。

为什么现在还剩
--------------
11-10 那次批量清理的目标就是这些 `.html`（`tmp/batch_clean_log.json` 里
「真改 36」的 case 集合与「至今仍带注记的 html」集合**完全相同**），
但 `tmp/fix_zeroed_articles.py` 从 `3b69e9c^` 恢复的是 `.md`，
`.html` 这边没被真正清掉 ⇒ **公众号 `build_one()` 读的正是 `.html`**，
任何时候重建都会把注记原样带回草稿。
（`.md` 已干净，`grep -rl 我去核了一遍 out/articles/*.md` → 0 命中）

为什么这个脚本不会重演「0 字节事故」
----------------------------------
① 先 `read()` 进内存，再写；`open(w)` 与 `open(r)` 不出现在同一表达式。
② 只删**一整行**且形状必须完全匹配：以 `<blockquote` 开头、含 MARK、
   以 `</blockquote>` 结尾。不是这种形状就拒改。
③ 四条硬校验：行数恰减 1 / 字节恰减 `len(line)+换行` / 新文本无 MARK /
   **去掉那行后与原文逐字相等**（保证一个字都没顺手改掉）。
④ `atomicio.write_text` 原子落盘，逐文件留 log。
⑤ 默认干跑，`--go` 才写。

用法
----
    python -X utf8 tmp/clean_html_notes.py          # 干跑
    python -X utf8 tmp/clean_html_notes.py --go    # 真改
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio                                              # noqa: E402

MARK = "我去核了一遍"
LOG = os.path.join(ROOT, "tmp", "clean_html_notes_log.json")


def note_lines(text):
    return [ln for ln in text.splitlines(keepends=True) if MARK in ln]


def shape_ok(line):
    s = line.strip()
    return (s.startswith("<blockquote") and s.endswith("</blockquote>")
            and MARK in s)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(ROOT, "out", "articles", "*.html")))
    dirty = [p for p in files if MARK in open(p, encoding="utf-8").read()]
    print("扫描 %d 个 html ｜ 带注记 %d 个 ｜ 模式 %s\n"
          % (len(files), len(dirty), "真改" if a.go else "干跑"))
    ok = bad = 0
    out = []
    for p in dirty:
        cid = os.path.splitext(os.path.basename(p))[0]
        old = open(p, encoding="utf-8").read()
        hits = note_lines(old)
        if len(hits) != 1 or not shape_ok(hits[0]):
            print("%-26s ✗ 注记形状不符（%d 行）⇒ 拒绝自动改" % (cid, len(hits)))
            bad += 1
            continue
        line = hits[0]
        # 逐行重建，绝不正则替换（避免吃掉相邻段落）
        lines = old.splitlines(keepends=True)
        new_lines = [ln for ln in lines if ln != line]
        new = "".join(new_lines)
        checks = {
            "line_minus_1": len(lines) - len(new_lines) == 1,
            "bytes_exact": len(old) - len(new) == len(line),
            "mark_gone": MARK not in new,
            "byte_equal_after_drop": "".join(
                ln for ln in lines if MARK not in ln) == new,
        }
        if not all(checks.values()):
            print("%-26s ✗ 校验失败 %s" % (cid, checks))
            bad += 1
            continue
        if a.go:
            atomicio.write_text(p, new)
        ok += 1
        out.append({"cid": cid, "bytes": len(new), "lines": len(new_lines)})
        print("%-26s ✓ 删 1 行 / -%d 字节 ⇒ %d 行 %d 字节"
              % (cid, len(line), len(new_lines), len(new)))
    print("\n可改 %d ｜ 拒绝 %d" % (ok, bad))
    if a.go:
        with open(LOG, "w", encoding="utf-8") as f:
            json.dump({"go": True, "changed": ok, "rejected": bad,
                       "results": out}, f, ensure_ascii=False, indent=2)
        print("已写 %d 个文件，日志 %s" % (ok, os.path.relpath(LOG, ROOT)))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
