#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把确认过的 4 条人工裁定写进 SCORES / DELIVERY 两张表（幂等、保格式）。

只改 scripts/score_china_fit.py 与 scripts/score_solo_fit.py 两个文件的字典尾部，
不改任何 data/ 数据；派生分由两个评分脚本自己算。

2026-11-06 实测：两个 .py 都是 LF + 末尾有换行、字典以顶格 `}` 结束，
SCORES 用 `dict(...)`、DELIVERY 用 `(score, "...")`，长文本按多行字符串拼接。
"""
import json
import os
import py_compile
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "tmp", "htm_screen", "fit_scores_draft_20261106.json")
CHINA = os.path.join(ROOT, "scripts", "score_china_fit.py")
SOLO = os.path.join(ROOT, "scripts", "score_solo_fit.py")
IDS = ["catering-funnels", "talkeasy", "bubblav", "arq"]
BANNER = ("    # ── 2026-11-06 晋升的 4 条（人工裁定，依据 tmp/tm_md/*.md；"
          "草拟见 tmp/htm_screen/fit_scores_draft_20261106.md） ──\n")


def q(s):
    """Python/JSON 双引号字符串字面量（中文不转义）。"""
    return json.dumps(s, ensure_ascii=False)


def wrap(text, head_indent):
    """把长中文按 56 字切成多行字符串拼接，返回带换行的源码块。"""
    chunks = [text[i:i + 56] for i in range(0, len(text), 56)] or [""]
    pad = " " * head_indent
    lines = []
    for i, ch in enumerate(chunks):
        last = (i == len(chunks) - 1)
        lines.append(("" if i == 0 else pad) + q(ch) + ("" if not last else ","))
    return "\n".join(lines)


def insert(path, varname, block, ids):
    raw = open(path, "rb").read()
    assert b"\r\n" not in raw, "%s 不是纯 LF，先人工确认" % path
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines)
                 if re.match(r"^" + varname + r"\s*=\s*\{", l))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip("\n") == "}")
    body = "".join(lines[start + 1:end])
    for cid in ids:
        assert (q(cid) + ":") not in body, "%s 里已有 %s，拒绝重复插入" % (varname, cid)
    new = text.splitlines(keepends=True)
    new.insert(end, BANNER + block)
    out = "".join(new).encode("utf-8")
    assert b"\r\n" not in out
    assert out.endswith(b"\n") == raw.endswith(b"\n")
    open(path, "wb").write(out)
    py_compile.compile(path, doraise=True)
    return len(out) - len(raw), end + 1


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    scores, delivery = d["SCORES"], d["DELIVERY"]

    china_block = []
    for cid in IDS:
        s = scores[cid]
        dims = ", ".join("%s=%d" % (k, s[k]) for k in
                         ("demand", "payment", "compliance", "acquisition",
                          "localization", "competition"))
        blocker = "None" if s["blocker"] is None else q(s["blocker"])
        note = wrap(s["note"], 13)          # 对齐到 note=" 后第一个字符
        china_block.append(
            "    %s: dict(\n"
            "        %s,\n"
            "        blocker=%s,\n"
            "        note=%s\n"
            "    ),\n" % (q(cid), dims, blocker, note))
    china_block = "".join(china_block)

    solo_block = []
    for cid in IDS:
        score, why = delivery[cid]
        head = "    %s: (%d, " % (q(cid), score)
        note = wrap(why, len(head))
        solo_block.append(head + note.rstrip(",") + "),\n")
    solo_block = "".join(solo_block)

    d1, at1 = insert(CHINA, "SCORES", china_block, IDS)
    d2, at2 = insert(SOLO, "DELIVERY", solo_block, IDS)
    print("score_china_fit.py  +%d bytes，插入于第 %d 行前" % (d1, at1))
    print("score_solo_fit.py   +%d bytes，插入于第 %d 行前" % (d2, at2))

    # 回读：新表内容与草拟 JSON 逐字一致
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import score_china_fit as CF
    import score_solo_fit as SF
    for cid in IDS:
        s = CF.SCORES[cid]
        for k in ("demand", "payment", "compliance", "acquisition",
                  "localization", "competition", "blocker", "note"):
            assert s[k] == scores[cid][k], "%s.%s 与草拟不一致" % (cid, k)
        assert tuple(SF.DELIVERY[cid]) == tuple(delivery[cid]), "%s delivery 不一致" % cid
        print("  [OK] %-18s SCORES=%s DELIVERY=%d" %
              (cid, {k: s[k] for k in ("demand", "payment", "compliance",
                                       "acquisition", "localization", "competition")},
               SF.DELIVERY[cid][0]))
    print("表条数：SCORES %d / DELIVERY %d" % (len(CF.SCORES), len(SF.DELIVERY)))
    print("语法编译 + 逐字回读全部通过")


if __name__ == "__main__":
    main()
