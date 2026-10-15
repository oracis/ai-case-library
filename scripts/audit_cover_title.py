# -*- coding: utf-8 -*-
"""四平台封面描述 + 标题长度体检（2026-10-15 建）。

用户报「头条题图上英文简介、字数过多被截、标题过长被截除」。本脚本把
这类问题**一次性扫出来**，覆盖四个平台（头条/B站/小红书/公众号）。

## 为什么一个脚本能覆盖四个平台

四平台的封面描述走**同一条回退链**（`wechat_publish.cover_desc()`）：
    cover_kv.desc → cover_desc → one_liner → what_it_does 首句
`xhs_publish` 直接用 `wp.cover_desc()`（源码里注明「不自己写一份」），
`toutiao_publish` / `bilibili_publish` 同理。⇒ **一个数据层缺口，
四个平台一起中招**，不必分别排查。

## 三类问题

① **封面描述无中文** —— 回退到了英文 `one_liner`。
② **封面描述超 30 字** —— 字号分档 12/20/30/42，越界必溢出
   （`cover_desc_ai.py` 的门禁就是 12–30 字）。
③ **标题超平台上限被硬切** —— 头条 `cand[:TITLE_MAX]` 是拦腰切，
   句子会残缺（实测旧产物里「…转成 WordPress——一」）。

## 用法

    python3 scripts/audit_cover_title.py             # 体检（只读）
    python3 scripts/audit_cover_title.py --list      # 只输出问题 id 逗号串
    python3 scripts/audit_cover_title.py --all       # 扫全部案例，不只已构建的
    python3 scripts/audit_cover_title.py --ids tmp/cover_desc_only.txt

⚠ 只读，不写任何文件。
⚠ 「已构建」范围 = out/{toutiao,bili,xhs}/ 下的目录（= 实际排过版的），
  未构建的案例封面还没生成，查了也没意义。
"""
import json
import os
import re
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CJK = re.compile(r"[一-鿿]")
MONEY = re.compile(r"[\$￥€£]\s*\d|\d\s*(美元|美金|刀|万)")
# 营销套话：过了机器门禁但不该直接发（实测智谱初稿 7/16 命中）
FILLER = re.compile(r"神器|助您|助你|轻松|打造|速成|赋能|一站式|无忧")

COVER_MAX = 30          # 封面描述上限（字号分档 12/20/30/42，越界必溢出）
TITLE_MAX = {"toutiao": 30, "wechat": 64, "xhs": 20, "bili": 80}


def _mod(name):
    p = os.path.join(ROOT, "scripts", "%s.py" % name)
    sp = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(sp)
    sys.modules[name] = m
    sp.loader.exec_module(m)
    return m


def load_cases():
    d = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
    return d if isinstance(d, list) else d.get("cases", d)


def built_ids():
    out = set()
    for plat in ("toutiao", "bili", "xhs"):
        p = os.path.join(ROOT, "out", plat)
        if os.path.isdir(p):
            for d in os.listdir(p):
                if os.path.isdir(os.path.join(p, d)):
                    out.add(d)
    return out


def main():
    args = set(sys.argv[1:])
    wp = _mod("wechat_publish")
    tp = _mod("toutiao_publish")
    xhs = _mod("xhs_publish")

    cases = load_cases()
    only = None
    if "--ids" in args:
        p = sys.argv[sys.argv.index("--ids") + 1]
        only = {s.strip() for s in open(p, encoding="utf-8").read().split(",")
                if s.strip()}
    scope = built_ids() if "--all" not in args else None

    cov_bad, ttl_bad = [], []
    for c in cases:
        cid = c.get("id") or ""
        if only is not None and cid not in only:
            continue
        if scope is not None and cid not in scope:
            continue

        # ① 封面描述
        d = wp.cover_desc(c)
        probs = []
        if d.strip():
            if not CJK.search(d):
                probs.append("英文")
            if len(d) > COVER_MAX:
                probs.append("超长%d字" % len(d))
            if MONEY.search(d):
                probs.append("含金额")
            if FILLER.search(d):
                probs.append("营销套话")
        else:
            probs.append("空")
        if probs:
            cov_bad.append((cid, "/".join(probs), len(d), d,
                            "cover_desc" if (c.get("cover_desc") or "").strip()
                            else "one_liner兜底"))

        # ③ 标题（头条上限最紧，且是硬切，先查它）
        t = tp.make_toutiao_title(c)
        n = len(t)
        if n >= tp.TITLE_MAX:
            ttl_bad.append((cid, "头条卡30字(硬切)", n, t))
        elif n > 26:
            ttl_bad.append((cid, "头条接近上限", n, t))

        xt = xhs.make_xhs_title(c)
        if len(xt) > xhs.TITLE_MAX:
            ttl_bad.append((cid, "小红书超%d字" % xhs.TITLE_MAX, len(xt), xt))

    if "--list" in args:
        ids = sorted({c for c, *_ in cov_bad})
        print(",".join(ids))
        return 0

    n_scope = len(scope) if scope is not None else len(cases)
    print("体检范围：%d 条%s" % (n_scope, "（已构建）" if scope is not None else "（全部）"))
    print()
    print("① 封面描述问题：%d 条" % len(cov_bad))
    for cid, why, n, d, src in cov_bad:
        print("   %-42s %-14s %2d字 %-12s %s"
              % (cid, why, n, src, d[:40]))
    print()
    print("③ 标题过长/贴上限：%d 条" % len(ttl_bad))
    for cid, why, n, t in ttl_bad:
        print("   %-42s %-16s %2d字 %s" % (cid, why, n, t[:40]))
    print()
    ok = not cov_bad and not ttl_bad
    print("✅ 全部合规" if ok else "⚠ 需处理：封面 %d + 标题 %d"
          % (len(cov_bad), len(ttl_bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
