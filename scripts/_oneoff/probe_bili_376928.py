#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针 2：B站 376928 到底去哪了。

1) 打印全部已投稿标题（找 ClawLink 是否换标题发过）
2) 试几个「专栏管理/回收站」候选端点，只看 code/msg，不写盘
3) 草稿箱里按关键词搜 ClawLink
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"

import bilibili_publish as bp          # noqa: E402
import bili_draft_api as bia           # noqa: E402
import wechat_publish as wp            # noqa: E402

CANDIDATES = [
    "https://member.bilibili.com/x/web/article/list?pn=1&ps=100",
    "https://member.bilibili.com/x/web/article/index?pn=1&ps=100",
    "https://member.bilibili.com/x/web/article/recycle?pn=1&ps=100",
    "https://member.bilibili.com/x/web/article/delbox?pn=1&ps=100",
    "https://member.bilibili.com/x/web/article/list?pn=1&ps=100&state=-1",
]


def try_get(cdp, url):
    js = """(async () => {
      try {
        const r = await fetch(%s, {credentials: 'include'});
        const txt = await r.text();
        let j = null;
        try { j = JSON.parse(txt); } catch (e) {}
        if (!j) return {http: r.status, raw: txt.slice(0, 160)};
        return {http: r.status, code: j.code, msg: j.message,
                keys: j.data ? Object.keys(j.data) : []};
      } catch (e) { return {err: String(e).slice(0, 120)}; }
    })()""" % json.dumps(url)
    try:
        return cdp.eval(js, refresh_context=True)
    except Exception as e:                              # noqa: BLE001
        return {"err": str(e)[:120]}


def main():
    cdp = wp.CDP(bp.PUB_PORT)
    bia._bili_tab(cdp)

    print("=== 已投稿全量标题 ===")
    pubs = bia.list_published_articles(cdp, max_page=5) or []
    for i, a in enumerate(pubs, 1):
        print("  %2d. %-10s %s" % (i, a.get("id"), (a.get("title") or "")[:48]))

    print("\n=== 候选端点 ===")
    for u in CANDIDATES:
        r = try_get(cdp, u)
        print("  %-72s %s" % (u.replace("https://member.bilibili.com", ""),
                              json.dumps(r, ensure_ascii=False)[:150]))

    print("\n=== 草稿箱关键词 ===")
    for kw in ("Claw", "claw"):
        ds = bia.list_drafts(cdp, keyword=kw) or []
        print("  keyword=%-6s → %d 条" % (kw, len(ds)))
        for d in ds[:5]:
            print("     %-10s %s" % (d.get("article_id"), (d.get("title") or "")[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
