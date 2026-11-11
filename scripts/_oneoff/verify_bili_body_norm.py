#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线校验「B站编辑器文本 ⇒ 母版逐字」的归一判据（不碰网络）。

实测已定的事实（探针跑出来的，别再猜）
--------------------------------
1. `draft/view.content` == 编辑器 `.tiptap.ProseMirror.eva3-editor` 的 innerText
   （同一条 bustem 两边都是 1059 字）⇒ **content 不是截断，它就是纯文本**。
2. 编辑器文本 **含注记整行**（注记是独立一行，前后各一个空行）。
3. 把注记整行摘掉后，与「母版 HTML 去标签」相比，diff **只有 insert**：
   `▶`（h2 前缀）与 `｜`（表格/分隔符的全角化）。**没有 delete / replace**
   ⇒ 平台上的正文一个字都没少。

判据
----
  norm_editor(t) = 去 HTML 标签 → 去空白 → 删 `▶`、`｜`（编辑器显示符）
  PASS ⇔ norm_editor(editor_no_note) == norm(master_html)

用法：
    python -X utf8 tmp/verify_bili_body_norm.py --cid bustem --text tmp/bustem_editor.txt
"""
import argparse
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

MARK = "我去核了一遍"
# 编辑器显示符：B站把 <h2> 渲染成「▶ 标题」，把表格/竖线渲染成全角「｜」
EDITOR_GLYPHS = ("▶", "｜", "|", "►", "▸")


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def norm_editor(s):
    t = norm(s)
    for g in EDITOR_GLYPHS:
        t = t.replace(g, "")
    return t


def drop_note_lines(text):
    """摘掉含注记的整行（实测注记独立成行，前后是空行）。"""
    keep, dropped = [], []
    for ln in str(text or "").splitlines():
        (dropped if MARK in ln else keep).append(ln)
    return "\n".join(keep), dropped


def build_body(md_path):
    import toutiao_publish as tt
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return tt.blocks_to_html(tt.md_to_blocks(md), footer=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="bustem")
    ap.add_argument("--text", required=True, help="编辑器 innerText dump 文件")
    ap.add_argument("--md", default="")
    a = ap.parse_args()

    md = a.md or os.path.join(ROOT, "out", "articles", a.cid + ".md")
    master = build_body(md)
    with open(a.text, encoding="utf-8") as f:
        etext = f.read()

    no_note, dropped = drop_note_lines(etext)
    m = norm(master)
    e_full = norm_editor(etext)
    e_clean = norm_editor(no_note)

    print("母版纯文本          : %d 字" % len(m))
    print("编辑器全文(原样)    : %d 字 / 归一后 %d" % (len(norm(etext)), len(e_full)))
    print("注记行              : 摘了 %d 行（%s）"
          % (len(dropped), ("%d 字" % len(norm(dropped[0]))) if dropped else "无"))
    print("编辑器摘注记归一后  : %d 字" % len(e_clean))
    print()
    print("逐字相等(摘注记+剥符): %s" % (e_clean == m))
    if e_clean == m:
        print("⇒ PASS：平台正文除注记外与母版**逐字一致**，可安全用作回写验收判据")
        return 0

    # 不相等时给出差在哪，别只说 False
    import difflib
    print("\n差异段（母版 vs 编辑器摘注记归一后）：")
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, m, e_clean, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        print("  %-7s 母版=%r  编辑器=%r" % (tag, m[i1:i2][:80], e_clean[j1:j2][:80]))
        n += 1
        if n >= 20:
            print("  …")
            break
    print("⇒ FAIL：判据不成立，别用它做验收")
    return 1


if __name__ == "__main__":
    sys.exit(main())
