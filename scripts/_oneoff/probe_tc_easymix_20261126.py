#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探查：腾讯云 easymix 重发前的两个硬闸证据（2026-11-26）。

1. 创作中心是否已有同标题文章（yun_finalize._tc_existing_article 的幂等闸会查）
2. 草稿箱里 352180 是否还在、正文是哪个版本（旧品牌名 or 新中性写法）

全程只读：只 open 页面 + fetch 列表接口，不点任何保存/发布/删除按钮。
用法::

    python scripts/_oneoff/probe_tc_easymix_20261126.py
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y  # noqa: E402
import tc_manage as TM  # noqa: E402

TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"
DRAFT = "352180"
LIST_TC = """
fetch("/developer/api/article/getUserArticleDrafts?_="+Date.now(),
      {method:"POST", credentials:"include", cache:"no-store",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""


def main():
    proc, cdp = Y._connect()
    print("9228:", "复用常驻" if proc is None else "本次新起")

    # 1) 创作中心同标题文章（幂等闸会查这个）
    cdp.open(TM.LIST_URL)
    time.sleep(8)
    rows = TM.fetch_list(cdp, pages=4)
    norm = lambda s: re.sub(r"\s+", "", str(s or ""))
    hits = [r for r in rows if norm(r.get("title")) == norm(TITLE)]
    print("\n=== 创作中心同标题文章（幂等闸视角）===")
    print("总文章数:", len(rows), "| 同标题命中:", len(hits))
    for h in sorted(hits, key=lambda r: int(r.get("id") or 0)):
        print("  id=%s state=%s title=%r why=%s" %
              (h.get("id"), h.get("state"), h.get("title"), str(h.get("why"))[:80]))
    if not hits:
        # 打印相近标题，防 norm 口径差
        near = [r for r in rows if "分轨" in str(r.get("title")) or "混音" in str(r.get("title"))]
        print("相近标题（含分轨/混音）:", [(r.get("id"), r.get("title"), r.get("state")) for r in near])

    # 2) 草稿箱 352180
    cdp.open("https://cloud.tencent.com/developer/article/write-new")
    time.sleep(8)
    r1 = cdp.eval(LIST_TC)
    time.sleep(2)
    r2 = cdp.eval(LIST_TC)
    print("\n=== 草稿箱 ===")
    if not isinstance(r1, str) or r1.startswith("ERR|"):
        print("读失败:", str(r1)[:200])
    else:
        d = json.loads(r1)
        items = d.get("list") or d.get("data") or []
        if isinstance(items, dict):
            items = items.get("list") or []
        print("两次一致:", r1 == r2, "| 草稿数:", len(items))
        for x in items:
            did = str(x.get("draftId"))
            plain = str(x.get("plain") or "")
            mark = " <== 目标" if did == DRAFT else ""
            print("  draftId=%s title=%r plain_head=%r%s" %
                  (did, x.get("title"), plain[:50], mark))

    # 3) 编辑器页 352180 正文首句（它虽不在草稿箱，编辑页可能仍能开）
    cdp.open("https://cloud.tencent.com/developer/article/write-new?draftId=%s" % DRAFT)
    time.sleep(8)
    head = cdp.eval("(document.querySelector('.cm-content')||{innerText:''}).innerText||''")
    title = cdp.eval("(document.querySelector('textarea.cdc-article-editor__title-input')||{}).value||''")
    print("\n=== 编辑器页 352180 ===")
    print("title:", repr(str(title)[:80]))
    print("body_head:", repr(str(head)[:100]))
    print("是否旧品牌名正文:", "easyMix" in str(head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
