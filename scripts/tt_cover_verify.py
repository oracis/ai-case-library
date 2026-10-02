#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立回读头条草稿封面，验证「真的换了」。

⚠ 为什么要独立回读（铁律）：
   `cover_one` 返回 "ok" 只代表上传动作没报错。头条的 `cover-status`
   那类布尔判据证明不了换图（B站已经吃过这个亏：重新上传后仍有封面，
   和没换在它眼里一模一样）。所以必须回编辑器/列表页比对**封面 URL**。

判据：
  -编辑器内 `.article-cover-images img` 的 src 与旧图不同 = 真换了
  - src 存在且非空= 有封面
用法：
    python -X utf8 scripts/tt_cover_verify.py --case quran-unlock
    python -X utf8 scripts/tt_cover_verify.py --baseline out/tt_cover_url_base.json
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402
import chrome_cdp_launch as cl  # noqa: E402
import wechat_publish as wp  # noqa: E402

BASE = os.path.join(ROOT, "out", "tt_cover_url_base.json")

JS_IMG = ("(function(){var i=document.querySelector('.article-cover-images img');"
          "return i?i.src:'';})()")


def _draft_ids():
    """草稿箱里真实存在的案例 id（精确解析，不用模糊匹配）。"""
    src = os.path.join(ROOT, "out", "tt_draftbox.txt")
    d = open(src, encoding="utf-8").read()
    items = re.findall(r"([^\n]{4,60})\n(\d{2}-\d{2} \d{2}:\d{2})\n编辑删除", d)
    dt = {t for t, _ in items}
    return [c["id"] for c in tp.load_cases() if tp.find_title(c) in dt]


def get_editor_src(cdp, title):
    """打开某草稿的编辑页，回读封面 URL。只读，不改任何东西。"""
    c, t, err = tp.open_draft_editor(title, wait=25)
    if not c:
        return None, err
    try:
        time.sleep(2)
        return c.eval(JS_IMG) or "", ""
    finally:
        try:
            c.close_target(t["id"])
        except Exception:                # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="单个案例 id")
    ap.add_argument("--baseline", action="store_true",
                    help="只回读并存基线（换图前跑一次）")
    ap.add_argument("--check", action="store_true",
                    help="与基线比对，报告哪些变了")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    wp._autostart_chrome_if_needed(tp.CDP_PORT, profile=cl.PLAT_PROFILE)

    if args.baseline:
        titles = {}
        src = os.path.join(ROOT, "out", "tt_draftbox.txt")
        d = open(src, encoding="utf-8").read()
        items = re.findall(r"([^\n]{4,60})\n(\d{2}-\d{2} \d{2}:\d{2})\n编辑删除", d)
        dt = {t for t, _ in items}
        for c in tp.load_cases():
            t = tp.find_title(c)
            if t in dt:
                titles[c["id"]] = t
        out = {}
        for i, (cid, t) in enumerate(titles.items(), 1):
            print("[%d/%d] %-22s 回读封面…" % (i, len(titles), cid), flush=True)
            src_url, err = get_editor_src(None, t)
            out[cid] = src_url
            print("    %s" % (("ERR " + err) if err else (src_url or "(空)")), flush=True)
            time.sleep(2)
        json.dump(out, open(BASE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n基线已存%s（%d 条）" % (BASE, len(out)))
        return 0

    # 单条模式：有基线就比对，没有就只回读当前值（判断"有没有封面"）
    base = json.load(open(BASE, encoding="utf-8")) if os.path.exists(BASE) else {}
    if args.case:
        ids = [args.case]
    elif args.all:
        ids = list(base) or _draft_ids()
    else:
        ids = list(base) or _draft_ids()
    changed, same, errs = [], [], []
    for i, cid in enumerate(ids, 1):
        t = base.get(cid)
        c = tp.find_case_fuzzy(cid) if hasattr(tp, "find_case_fuzzy") else None
        if c is None:
            for x in tp.load_cases():
                if x["id"] == cid:
                    c = x
                    break
        title = tp.find_title(c)
        print("[%d/%d] %-22s 回读…" % (i, len(ids), cid), flush=True)
        new, err = get_editor_src(None, title)
        if err:
            errs.append((cid, err))
            print("    ERR %s" % err, flush=True)
            continue
        old = base.get(cid, "")
        if not base:
            print("    现值 %s（无基线，仅记录）" % (new or "(空)"), flush=True)
        elif old and new and old != new:
            changed.append(cid)
            print("    ✅ 已换 %s → %s" % (old[-28:], new[-28:]), flush=True)
        elif not new:
            same.append((cid, "(空)"))
            print("    ⚠ 封面为空", flush=True)
        else:
            same.append((cid, "未变"))
            print("    ➖ 未变 %s" % new[-28:], flush=True)
        time.sleep(2)
    print("\n==== 换成功 %d / 未变或空 %d / 报错 %d ===="
          % (len(changed), len(same), len(errs)))
    if same:
        print("未变：", "、".join("%s(%s)" % x for x in same))
    if errs:
        print("报错：", "、".join("%s(%s)" % x for x in errs))
    return 0


if __name__ == "__main__":
    sys.exit(main())