#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 草稿更新：走页面「保存草稿」同款 payload（id + status:2），不发表。

2026-11-04 实测依据：编辑器点「保存草稿」发出的请求是
  POST /blog-console-api/v3/mdeditor/saveArticle
  body {id:<aid>, title, markdowncontent, content, readType:"public",
        status:2, type:"original", tags, source:"pc_mdeditor",
        cover_images:[...], cover_type:1, is_new:1, not_auto_saved:"1"}
  → data.url = blog.csdn.net/<user>/article/details/<aid>，文章仍在草稿箱。

⚠ 与 csdn_publish.save_article 的区别：那个函数写死 pubStatus="publish"、status=0，
  调用即发表（计入日配额）；本函数 status=2，只落草稿。
⚠ 必须带 id（不是 articleId），否则会新建一篇。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import csdn_publish as C               # noqa: E402
import csdn_rules as R                 # noqa: E402


def save_draft(article_id, title, md_body, cover_images=None, cover_type=1,
               tags="人工智能", via="auto", description=""):
    """把标题/正文/封面写回指定草稿。返回 (article_id, url)。"""
    payload = {
        "id": str(article_id),
        "title": title,
        "markdowncontent": md_body,
        "content": C.md_to_html(md_body),
        "readType": "public",
        "level": "0",
        "tags": tags,
        "status": 2,                      # ← 草稿态；0/publish 才是发表
        "categories": "",
        "type": "original",
        "original_link": "",
        "authorized_status": False,
        "Description": description,
        "resource_url": "",
        "not_auto_saved": "1",
        "source": "pc_mdeditor",
        "cover_images": list(cover_images or []),
        "cover_type": cover_type,
        "is_new": 1,
        "vote_id": 0,
    }
    st, body = C.api("/blog-console-api/v3/mdeditor/saveArticle",
                     data=payload, via=via)
    if not (isinstance(body, dict) and body.get("code") == 200):
        raise RuntimeError("saveArticle 未返回 200：%s"
                           % json.dumps(body, ensure_ascii=False)[:300])
    d = body.get("data") or {}
    return d.get("id") or article_id, d.get("url", "")


def list_drafts(via="browser"):
    """草稿箱列表：[(articleId, title, coverImage0, status)]

    ⚠ 走 `csdn_list_drafts.list_status()`（页面点 tab + 抓包），**不是**直接
    调 `/article/list` —— 那个 GET 的浏览器内签名 2026-11-04 试过三版全
    `HMAC signature does not match`，至今未解。`via` 参数保留只为兼容旧调用。
    """
    import csdn_list_drafts as LD
    return [(it["id"], it["title"], it["cover"], it["status"])
            for it in LD.list_status("draft")]


def main():
    ap = argparse.ArgumentParser(description="更新 CSDN 草稿（不发表）")
    ap.add_argument("article_id")
    ap.add_argument("md", help="markdown 稿（首行 # 标题）")
    ap.add_argument("--cover", default="", help="封面图 URL（i-blog.csdnimg.cn）")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    title, body = R.read_md(a.md)
    body = R.clean(body)
    r = R.scan(title, body)
    print("标题：%s" % title)
    print("正文：%d 字" % len(body))
    for it in r["block"]:
        print("✗ %s（%s，%d 处）" % (it["rule"], it["spec"], it["count"]))
    for it in r["warn"]:
        print("⚠ %s（%d 处）" % (it["rule"], it["count"]))
    if r["block"]:
        print("→ 合规闸未过，拒绝写入")
        return 1
    if a.dry:
        print("干跑：不写盘")
        return 0

    covers = [a.cover] if a.cover else []
    aid, url = save_draft(a.article_id, title, body, cover_images=covers)
    print("✓ 已写入草稿 %s  %s" % (aid, url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
