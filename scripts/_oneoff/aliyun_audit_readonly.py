#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云只读复核（PRD #108 第 ② 项）。

单进程 / 单个 9228 / 一条前台命令内跑完：
  1. 登录态只读自检（复用 yun_login_keep.probe_page / ticket_state）
  2. 两篇「auditing」文章的公开页带登录态回读（title / meta date / 正文 / 审核横幅）
  3. 「我的文章」列表 DOM（tabs / rows / /article/<id> 链接），产三篇状态对照表

⛔ 全程只读：不点击按钮、不输入、不提交、不删除、不写台账。
用法：python -X utf8 tmp/aliyun_audit_readonly.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y          # noqa: E402
import yun_login_keep as K         # noqa: E402

EVIDENCE = os.path.join(ROOT, "tmp", "aliyun_audit_platform_evidence.json")
TABLE = os.path.join(ROOT, "tmp", "aliyun_audit_3cases_table.json")
MY_ARTICLE = "https://developer.aliyun.com/my#/article"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")

# (case_id, aid, 期望标题片段, 正文关键词) —— 关键词挑只可能出现在本文里的短语
PAGES = [
    ("insect-bite-id", "1780780", "识别虫咬类型应用：基于云资源与成本结构的工程实践",
     ["拍照识别虫咬类型", "置信度", "护理建议"]),
    ("markdown-structure", "1776574", "同一份 Markdown 分发到 4 个技术社区",
     ["标题层级", "mditor", "tiptap", "metaWeblog"]),
    ("cod-dex", "1778138", "交互式编程课程的运行时设计",
     ["代码执行沙箱", "事件溯源", "Codédex"]),
    ("gojiberryai", "1780408", "GojiberryAI",
     ["GojiberryAI"]),
]

# 三篇被驳回稿：按截图标题 / 产品名在列表里找，不靠猜 aid
CASES = [
    ("gojiberryai", "GojiberryAI 的信号管道：职业事件采集、实体归一与触达幂等",
     ["GojiberryAI", "获客引擎"]),
    ("cod-dex", "在线编程课的弹性账：突发执行、沙箱隔离与内容静态化 —— Codédex",
     ["Codédex", "Cod", "编程课程", "250 小时", "交互式编程"]),
    ("chessigma", "Chessigma：百万用户级实时对弈的负载形状与算力成本",
     ["Chessigma", "对弈"]),
]

PAGE_JS = r"""
JSON.stringify({
  href: location.href,
  title: document.title,
  date: (document.querySelector('meta[name="date"]')||{}).content || '',
  modified: (document.querySelector('meta[name="last-modified"]')||{}).content || '',
  desc: (document.querySelector('div[class*="article-desc"]')||{}).innerText || '',
  text: (document.body ? (document.body.innerText||'') : '').slice(0, 20000),
  banner: /内容审核中|审核中|审核未通过/.test(
      document.body ? (document.body.innerText||'') : '')
})
"""

LIST_JS = r"""
JSON.stringify({
  url: location.href,
  tabs: [].slice.call(document.querySelectorAll('[class*=tab],[role=tab],li,a'))
    .map(function(e){ return (e.innerText||'').trim().replace(/\s+/g,' '); })
    .filter(function(t){ return t && t.length <= 12
      && /草稿|文章|发布|审核|全部|未通过/.test(t); }).slice(0, 25),
  rows: [].slice.call(document.querySelectorAll('tr,[class*=item],[class*=card],li'))
    .map(function(e){ return (e.innerText||'').trim().replace(/\s+/g,' '); })
    .filter(function(t){ return t && t.length > 8 && t.length < 300; })
    .slice(0, 80),
  links: [].slice.call(document.querySelectorAll('a[href*="/article/"]'))
    .map(function(a){ return {href: a.getAttribute('href'),
      txt: (a.innerText||'').trim().replace(/\s+/g,' ').slice(0, 80)}; })
    .filter(function(o){ return /\/article\/\d+/.test(o.href||''); })
    .slice(0, 80),
  head: (document.body ? (document.body.innerText||'') : '')
    .replace(/\s+/g,' ').slice(0, 1200)
})
"""


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def eval_json(cdp, js):
    raw = cdp.eval(js) or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {"__parse_error__": str(raw)[:300]}


def main():
    proc, cdp = Y._connect()
    print("[9228] %s" % ("本次新起" if proc else "复用已有实例"), flush=True)
    ev = {"at": NOW, "source": "live:9228 chrome-yun-profile",
          "scope": "只读；未点击/未提交/未删除/未改台账"}
    try:
        # ① 登录态自检（只读）
        login = K.probe_page(cdp)
        tickets = K.ticket_state(K.all_cookies(cdp))
        ev["login"] = login
        ev["tickets"] = {k: list(v) for k, v in tickets.items()}
        ev["persistent_tickets"] = sum(1 for v in tickets.values() if not v[0])
        print("[登录态] logged=%s url=%s 持久票据=%d/%d"
              % (login.get("logged"), login.get("url"),
                 ev["persistent_tickets"], 4), flush=True)

        # ② 公开页带登录态回读
        pages = []
        for cid, aid, title_key, kws in PAGES:
            url = "https://developer.aliyun.com/article/" + aid
            cdp.open(url)
            time.sleep(4)
            d = eval_json(cdp, PAGE_JS)
            text = d.get("text") or ""
            hits = [w for w in kws if w in text or w in (d.get("desc") or "")]
            rec = {
                "case_id": cid, "aid": aid, "url": url,
                "final_url": d.get("href"), "title": d.get("title"),
                "meta_date": d.get("date"), "meta_modified": d.get("modified"),
                "title_exact": title_key in (d.get("title") or ""),
                "keyword_hits": hits, "keyword_total": len(kws),
                "audit_banner": bool(d.get("banner")),
                "text_len": len(text),
                "desc": (d.get("desc") or "").strip()[:160],
            }
            pages.append(rec)
            print("[公开页] %-20s aid=%-8s title=%s date=%s 词=%d/%d 横幅=%s len=%d"
                  % (cid, aid, rec["title_exact"], rec["meta_date"],
                     len(hits), len(kws), rec["audit_banner"], rec["text_len"]),
                  flush=True)
        ev["pages"] = pages

        # ③ 文章管理列表（只读 DOM）
        cdp.open(MY_ARTICLE)
        time.sleep(11)
        lst = eval_json(cdp, LIST_JS)
        ev["list"] = lst
        print("[列表] url=%s tabs=%s rows=%d links=%d"
              % (lst.get("url"), " | ".join((lst.get("tabs") or [])[:8]),
                 len(lst.get("rows") or []), len(lst.get("links") or [])),
              flush=True)

        # 三篇对照表：按产品名在 rows / links 里求交
        rows = lst.get("rows") or []
        links = lst.get("links") or []
        table = {"at": NOW, "source": MY_ARTICLE,
                 "list_rows": len(rows), "list_links": len(links),
                 "tabs": lst.get("tabs"), "cases": []}
        for cid, local_h1, keys in CASES:
            hit_rows = [r for r in rows if any(k in r for k in keys)]
            hit_links = [l for l in links if any(k in (l.get("txt") or "")
                                                 for k in keys)]
            # 去重
            seen, uniq_rows = set(), []
            for r in hit_rows:
                if r not in seen:
                    seen.add(r)
                    uniq_rows.append(r)
            table["cases"].append({
                "case_id": cid, "local_h1": local_h1, "keys": keys,
                "matched_rows": uniq_rows[:12],
                "matched_article_links": hit_links[:12],
                "matched_rows_count": len(uniq_rows),
            })
            print("[对照] %-12s rows=%d links=%d" % (cid, len(uniq_rows),
                                                     len(hit_links)), flush=True)
            for r in uniq_rows[:4]:
                print("        ", r[:120], flush=True)

        jdump(EVIDENCE, ev)
        jdump(TABLE, table)
        print("[产物] %s" % os.path.relpath(EVIDENCE, ROOT), flush=True)
        print("[产物] %s" % os.path.relpath(TABLE, ROOT), flush=True)
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
