#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彻底统一评分文案：把 SCORES / DELIVERY 两张静态表里的文案副本剔除。

背景（PRD #183 → #184）
----------------------
`score_note_policy.resolve_note()` 已经让「库里是唯一真值」，但两张表本身仍
存着 465 + 466 份旧文案：只要有人往表里补一条、或误开覆盖开关，副本就会回潮。
本脚本把表收敛成**只存数值**：

  * `SCORES`：删掉 465 条 `note=` 关键字（多行字符串拼接、单行紧凑写法都在内）；
  * `DELIVERY`：466 条 `(分数, 理由)` 的第二位统一换成 `NOTE_IN_LIBRARY`
    （值为空串），保留 tuple 形状兼容历史调用方。

判定边界（都用 AST 的字节级位置，不按行猜）
------------------------------------------
  * `SCORES` 里 `note` 恒为最后一个 `dict(...)` 关键字；删除区间从它前面的
    逗号起、到字符串结束（含尾随逗号）止 —— 这样单行紧凑条目不会误删数值，
    多行条目不会留下空行或孤立逗号。
  * 删完用 `exec` 单独求值表定义节点，逐键比对：数值、blocker、键集合、条数
    必须与改前完全一致；新表不许再有 `note`，DELIVERY 第二位必须恒为空。

默认 `--check` 只读预演；`--apply` 才写盘（先备份到 tmp/），可重复执行。
⚠ 一次性脚本，但按红线必须进仓库；改完记得 `git add`。
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHINA = os.path.join(ROOT, "scripts", "score_china_fit.py")
SOLO = os.path.join(ROOT, "scripts", "score_solo_fit.py")
TMP = os.path.join(ROOT, "tmp")

#: 插到 DELIVERY 表头前：第二位恒为空串，只是为兼容历史 tuple 形状。
HEADER = (
    "# ------------------------------------------------------------------ 评分表\n"
    "# PRD #184：文案（delivery_note）的唯一真值在 case_library。表里第二位恒为\n"
    "# NOTE_IN_LIBRARY（空串），只为维持 (分数, 理由) 的历史 tuple 形状；要改文案\n"
    "# 请回库里改，不要写回这张表 —— 库里的数字会随快照刷新，源码副本必然漂移。\n"
    "NOTE_IN_LIBRARY = \"\"\n\n"
)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _line_starts(src):
    """每行首的字节偏移（AST 的 lineno 从 1 起，col_offset 是 UTF-8 字节偏移）。"""
    starts = [0]
    for line in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _abs(starts, lineno, col):
    return starts[lineno - 1] + col


def _table_node(src, name):
    tree = ast.parse(src.decode("utf-8"))
    return tree, next(
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == name for t in n.targets))


def _eval_table(src, name):
    """只求值表定义节点（纯 dict()/字符串常量，无副作用）。"""
    _tree, node = _table_node(src, name)
    mod = ast.Module(body=[node], type_ignores=[])
    ns = {"NOTE_IN_LIBRARY": ""}       # DELIVERY 第二位改成常量后，单独求值要能解析
    exec(compile(ast.fix_missing_locations(mod), "<%s>" % name, "exec"), ns)
    return ns[name]


def _edits_china(src):
    starts = _line_starts(src)
    _tree, node = _table_node(src, "SCORES")
    edits = []
    for key, val in zip(node.value.keys, node.value.values):
        if not isinstance(val, ast.Call) or getattr(val.func, "id", "") != "dict":
            raise SystemExit("SCORES 里有非 dict(...) 条目，停止")
        kws = val.keywords
        if not kws or kws[-1].arg != "note":
            continue                      # 已经清过（幂等）
        nk = kws[-1]
        start = _abs(starts, nk.lineno, nk.col_offset)
        j = start - 1
        while j >= 0 and src[j:j + 1] in (b" ", b"\t", b"\r", b"\n"):
            j -= 1
        if src[j:j + 1] != b",":
            raise SystemExit("note 前不是逗号（%r），停止" % src[j - 20:j + 20])
        end = _abs(starts, nk.value.end_lineno, nk.value.end_col_offset)
        k = end
        while k < len(src) and src[k:k + 1] in (b" ", b"\t"):
            k += 1
        if src[k:k + 1] == b",":
            k += 1
        edits.append((j, k, b"", "note"))
    return edits


def _edits_solo(src):
    starts = _line_starts(src)
    _tree, node = _table_node(src, "DELIVERY")
    edits = []
    for key, val in zip(node.value.keys, node.value.values):
        if not isinstance(val, ast.Tuple) or len(val.elts) != 2:
            raise SystemExit("DELIVERY 里有非二元组条目，停止")
        second = val.elts[1]
        if isinstance(second, ast.Name) and second.id == "NOTE_IN_LIBRARY":
            continue                      # 已经清过（幂等）
        start = _abs(starts, second.lineno, second.col_offset)
        end = _abs(starts, second.end_lineno, second.end_col_offset)
        edits.append((start, end, b"NOTE_IN_LIBRARY", "delivery_note"))
    if b"NOTE_IN_LIBRARY = \"\"" not in src:
        at = _abs(starts, node.lineno, 0)
        edits.append((at, at, HEADER.encode("utf-8"), "header"))
    return edits


def _apply(src, edits):
    out = src
    for start, end, repl, _tag in sorted(edits, key=lambda e: -e[0]):
        out = out[:start] + repl + out[end:]
    return out


def _verify(old, new, name):
    ast.parse(new.decode("utf-8"))                    # 语法必须仍然成立
    a, b = _eval_table(old, name), _eval_table(new, name)
    if set(a) != set(b) or len(a) != len(b):
        raise SystemExit("%s 键集合/条数被改：%d → %d" % (name, len(a), len(b)))
    if name == "SCORES":
        for cid in a:
            want = {k: v for k, v in a[cid].items() if k != "note"}
            if b[cid] != want or "note" in b[cid]:
                raise SystemExit("SCORES[%s] 数值被改或仍带 note" % cid)
    else:
        for cid in a:
            if b[cid][0] != a[cid][0] or b[cid][1] != "":
                raise SystemExit("DELIVERY[%s] 分数被改或第二位非空" % cid)
    return len(a)


def main():
    ap = argparse.ArgumentParser(description="剔除 SCORES/DELIVERY 文案副本")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只读预演）")
    ap.add_argument("--check", action="store_true", help="只读预演（默认行为，显式写法）")
    args = ap.parse_args()

    jobs = [(CHINA, "SCORES", _edits_china), (SOLO, "DELIVERY", _edits_solo)]
    built = []
    for path, name, collect in jobs:
        old = _read(path)
        edits = collect(old)
        new = _apply(old, edits)
        n = _verify(old, new, name)
        tags = {}
        for _s, _e, _r, tag in edits:
            tags[tag] = tags.get(tag, 0) + 1
        print("[%s] %s：%d 条，改动 %s，字节 %d → %d"
              % (name, os.path.basename(path), n, tags, len(old), len(new)))
        built.append((path, name, old, new, edits))

    if not args.apply:
        print("\n--check：只做 AST 边界与逐键比对，没有写盘。")
        return 0

    os.makedirs(TMP, exist_ok=True)
    for path, name, old, new, edits in built:
        if not edits:
            print("[%s] 无需改动（幂等）" % name)
            continue
        # ⚠ 备份不能用 .py 扩展名：check_tmp_leaks 会把 tmp/ 下的新脚本判 FAIL。
        bak = os.path.join(TMP, "unify184_pre_%s.py.txt" % name.lower())
        shutil.copyfile(path, bak)
        tmp = path + ".unify184.tmp"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, path)
        print("[%s] 已写盘，备份 %s" % (name, os.path.relpath(bak, ROOT).replace("\\", "/")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
