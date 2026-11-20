#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：查 B站 53683188 / 53786328 / 376928 在线上到底是什么状态。

- 已投稿列表（空间公开接口）拿 id + 标题
- 文章详情接口 /x/article/viewinfo 拿 publish_time（判断谁先发）
- 草稿箱接口拿草稿列表（判断 376928 是否还在草稿箱）

不写任何盘。用法：python scripts/_oneoff/probe_bili_mario_clawlink.py
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

WANT = {"53683188", "53786328", "376928"}
KEY = ("ario", "游戏厅", "ClawLink", "clawlink")


def viewinfo(cdp, aid):
    js = """(async () => {
      const r = await fetch('https://api.bilibili.com/x/article/viewinfo?id=%s',
        {credentials: 'include'});
      const j = await r.json();
      if (j.code !== 0) return {err: j.code, msg: j.message};
      const d = j.data || {};
      return {id: d.id, title: d.title, publish_time: d.publish_time,
              ctime: d.ctime, mtime: d.mtime, state: d.state,
              reason: d.reason, stats: d.stats};
    })()""" % str(aid)
    try:
        return cdp.eval(js, refresh_context=True)
    except Exception as e:                             # noqa: BLE001
        return {"err": "eval", "msg": str(e)[:120]}


def main():
    cdp = wp.CDP(bp.PUB_PORT)
    tid = bia._bili_tab(cdp)
    print("tab:", tid)

    print("\n=== 已投稿列表（空间公开接口，最多 5 页）===")
    pubs = bia.list_published_articles(cdp, max_page=5) or []
    print("共 %d 条" % len(pubs))
    hits = []
    for a in pubs:
        t = a.get("title") or ""
        if str(a.get("id")) in WANT or any(k in t for k in KEY):
            hits.append(a)
    for a in hits:
        print("  %-10s %s" % (a.get("id"), a.get("title")))
    print("  (命中 %d / 全量 %d)" % (len(hits), len(pubs)))

    print("\n=== 详情（发布时间）===")
    for aid in ("53683188", "53786328", "376928"):
        d = viewinfo(cdp, aid)
        if isinstance(d, dict) and d.get("err") is not None:
            print("  %-10s ⛔ %s" % (aid, json.dumps(d, ensure_ascii=False)[:120]))
        else:
            print("  %-10s publish_time=%-12s ctime=%-12s state=%s  %s"
                  % (aid, d.get("publish_time"), d.get("ctime"),
                     d.get("state"), (d.get("title") or "")[:34]))

    print("\n=== 草稿箱 ===")
    drafts = bia.list_drafts(cdp) or []
    print("共 %d 条" % len(drafts))
    for d in drafts:
        aid = str(d.get("article_id"))
        t = d.get("title") or ""
        if aid in WANT or any(k in t for k in KEY):
            print("  article_id=%-10s %s" % (aid, t[:44]))
    print("  (明细前 8 条)")
    for d in drafts[:8]:
        print("    %-10s %s" % (d.get("article_id"), (d.get("title") or "")[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
