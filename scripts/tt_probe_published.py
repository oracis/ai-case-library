#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：头条那 20 条不在草稿箱的案例，到底是「已发布」还是「标题不一致」。

背景（2026-10-02）：草稿箱只19 条，39 案例里有 20 条匹配不上。
看着像「金额相同、描述不同」（例：草稿箱「月收$1.6K：零配置跑起自己的 AI」
vs 本地voklit「月收$1.6K：给跨境团队搭云通信」）⇒ 必须分清是
  (a) 已发布（草稿自然不在草稿箱，改封面要找作品管理页）
  (b) 标题被改过（草稿还在，只是标题对不上）
这两种的处理方式完全不同，不能混。

⚠ 严格只读：不点任何按钮、不导航到编辑页。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402
import chrome_cdp_launch as cl  # noqa: E402
import wechat_publish as wp  # noqa: E402

CONTENT = "https://mp.toutiao.com/profile_v4/manage/content/all"
DRAFT = "https://mp.toutiao.com/profile_v4/manage/draft"


def _norm(s):
    """归一化标题用于比对：去空白、去标点。"""
    return re.sub(r"[\s:：,，.。、!！?？~～\-—_]+", "", s or "")


def main():
    cases = list(tp.load_cases())
    wp._autostart_chrome_if_needed(tp.CDP_PORT, profile=cl.PLAT_PROFILE)
    cdp = wp.CDP(tp.CDP_PORT)

    # 草稿箱
    cdp.new_target(DRAFT)
    import time
    time.sleep(8)
    box = None
    for t in cdp.list_targets():
        if "/manage/draft" in t.get("url", ""):
            box = t
            break
    if not box:
        print("!! 草稿箱没打开")
        return 1
    cdp.connect_target(box["id"])
    time.sleep(3)
    tp._load_all_drafts(cdp, max_rounds=20, pause=1.5)
    draft_txt = cdp.eval("(document.body.innerText||'')") or ""
    open(os.path.join(ROOT, "out", "tt_draftbox.txt"), "w",
         encoding="utf-8").write(draft_txt)

    # 作品管理（已发布）
    cdp.new_target(CONTENT)
    time.sleep(10)
    pub = None
    for t in cdp.list_targets():
        if "/manage/content/all" in t.get("url", ""):
            pub = t
            break
    if not pub:
        print("!! 作品管理页没打开")
        return 1
    cdp.connect_target(pub["id"])
    time.sleep(4)
    # 展开已发布列表
    for _ in range(20):
        cdp.eval("window.scrollTo(0,document.body.scrollHeight)")
        time.sleep(1.2)
    pub_txt = cdp.eval("(document.body.innerText||'')") or ""
    open(os.path.join(ROOT, "out", "tt_published.txt"), "w",
         encoding="utf-8").write(pub_txt)

    # 判定
    dn, pn = _norm(draft_txt), _norm(pub_txt)
    rows = []
    for c in cases:
        t = tp.find_title(c)
        n = _norm(t)
        in_d = n in dn
        # 金额相同但描述不同 → 疑似改过标题
        m = re.search(r"[\$￥]?\d[\d,.]*[KkMm万]?", t)
        amt = m.group(0) if m else ""
        cand = ""
        if not in_d and amt:
            for other in cases:
                if other["id"] == c["id"]:
                    continue
                ot = tp.find_title(other)
                om = re.search(r"[\$￥]?\d[\d,.]*[KkMm万]?", ot)
                if om and om.group(0) == amt and _norm(ot) in dn:
                    cand = other["id"]
                    break
        rows.append({
            "id": c["id"], "title": t,
            "in_draft": in_d, "in_published": n in pn,
            "dup_of": cand,
        })

    print("=" * 74)
    print("%-22s %-9s %-11s %s" % ("id", "草稿箱", "已发布", "备注"))
    print("-" * 74)
    for r in sorted(rows, key=lambda x: (x["in_draft"], x["id"])):
        note = ""
        if r["dup_of"]:
            note = "⚠ 金额与 %s 相同，疑似同稿改标题" % r["dup_of"]
        elif not r["in_draft"] and not r["in_published"]:
            note = "❓ 两边都没有"
        print("%-22s %-9s %-11s %s" % (
            r["id"], "有" if r["in_draft"] else "无",
            "有" if r["in_published"] else "无", note))
    n_d = sum(1 for r in rows if r["in_draft"])
    print("-" * 74)
    print("草稿箱 %d / 已发布 %d / 两边都没有 %d（共 %d）" % (
        n_d,
        sum(1 for r in rows if r["in_published"]),
        sum(1 for r in rows if not r["in_draft"] and not r["in_published"]),
        len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())