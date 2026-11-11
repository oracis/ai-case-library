#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云三处错账裁定前的深度只读复核（PRD #108 后续）。

单进程 / 单个 9228 / 一条前台命令内跑完：
  1. 登录态自检（只读）
  2. 「我的文章」列表：tabs / 全量链接 / 分页控件 / 滚动加载前后对比
  3. 三篇被驳回文章页：审核横幅与「未通过原因」文案（若平台有显示）
  4. cod-dex 已发布文章页：标题 / meta date / 正文关键词
  5. 两个草稿编辑页 ?edit=<draftId>：是否还存在（只读打开，不输入不保存）
  6. 独立匿名 GET 双证（HTTP / title / meta date / 审核横幅）

⛔ 全程只读：不点击、不输入、不提交、不删除、不写台账、不写平台侧。
用法：python -X utf8 tmp/aliyun_3cases_readonly_v2.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y          # noqa: E402
import yun_login_keep as K         # noqa: E402

OUT = os.path.join(ROOT, "tmp", "aliyun_3cases_evidence_v2.json")
MY_ARTICLE = "https://developer.aliyun.com/my#/article"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")

# 三篇被驳回稿 + 同 case 已发布稿
ARTICLES = [
    ("gojiberryai", "1780408", "GojiberryAI如何用云资源与工程实践构建AI获客引擎",
     ["GojiberryAI", "获客"]),
    ("cod-dex-rejected", "1777853", "免费学 250 小时",
     ["Codédex", "9.99", "250 小时"]),
    ("chessigma-rejected", "1777851", "100 万用户的实时对弈平台",
     ["Chessigma", "长连接"]),
    ("cod-dex-published", "1778138", "交互式编程课程的运行时设计",
     ["代码执行沙箱", "事件溯源", "Codédex"]),
]

# 台账里的草稿 id：只读取证，确认草稿是否还在
DRAFTS = [
    ("gojiberryai", "1708661"),
    ("chessigma", "1700270"),
    ("cod-dex", "1701663"),
]

PAGE_JS = r"""
JSON.stringify({
  href: location.href,
  title: document.title,
  date: (document.querySelector('meta[name="date"]')||{}).content || '',
  desc: (document.querySelector('div[class*="article-desc"]')||{}).innerText || '',
  text: (document.body ? (document.body.innerText||'') : '').slice(0, 20000),
  auditLines: (document.body ? (document.body.innerText||'') : '')
    .split('\n').map(function(s){return s.trim();})
    .filter(function(s){return s && s.length<160 &&
      /审核|未通过|驳回|违规|原因|修改建议|内容规范|广告|推广/.test(s);})
    .slice(0, 24)
})
"""

LIST_JS = r"""
JSON.stringify({
  url: location.href,
  tabs: [].slice.call(document.querySelectorAll('[class*=tab],[role=tab],li,a'))
    .map(function(e){ return (e.innerText||'').trim().replace(/\s+/g,' '); })
    .filter(function(t){ return t && t.length <= 14
      && /草稿|文章|发布|审核|全部|未通过/.test(t); }).slice(0, 30),
  rows: [].slice.call(document.querySelectorAll('tr,[class*=item],[class*=card],li'))
    .map(function(e){ return (e.innerText||'').trim().replace(/\s+/g,' '); })
    .filter(function(t){ return t && t.length > 8 && t.length < 300; })
    .slice(0, 120),
  links: [].slice.call(document.querySelectorAll('a[href*="/article/"]'))
    .map(function(a){ return {href: a.getAttribute('href'),
      txt: (a.innerText||'').trim().replace(/\s+/g,' ').slice(0, 120)}; })
    .filter(function(o){ return /\/article\/\d+/.test(o.href||''); })
    .slice(0, 120),
  pagers: [].slice.call(document.querySelectorAll('[class*=pag],[class*=page],button'))
    .map(function(e){ return (e.innerText||'').trim().replace(/\s+/g,' '); })
    .filter(function(t){ return t && t.length < 40; }).slice(0, 30),
  scroll: {docH: document.documentElement.scrollHeight,
           bodyH: document.body ? document.body.scrollHeight : 0,
           y: window.scrollY, inner: window.innerHeight},
  tail: (document.body ? (document.body.innerText||'') : '')
    .replace(/\s+/g,' ').slice(-900)
})
"""

LINKS_JS = r"""
JSON.stringify([].slice.call(document.querySelectorAll('a[href*="/article/"]'))
  .map(function(a){ return {href: a.getAttribute('href'),
    txt: (a.innerText||'').trim().replace(/\s+/g,' ').slice(0,120)}; })
  .filter(function(o){ return /\/article\/\d+/.test(o.href||''); }))
"""

SCROLL_JS = r"""
(function(){
  window.scrollTo(0, document.documentElement.scrollHeight);
  var n=0;
  [].slice.call(document.querySelectorAll('div,section,main')).forEach(function(e){
    if (e.scrollHeight > e.clientHeight + 80 && e.clientHeight > 200) {
      e.scrollTop = e.scrollHeight; n++;
    }
  });
  return 'scrolled_containers=' + n;
})()
"""

EDITOR_JS = r"""
JSON.stringify({
  href: location.href,
  title: document.title,
  text: (document.body ? (document.body.innerText||'') : '').slice(0, 3000),
  fields: [].slice.call(document.querySelectorAll('input,textarea,[contenteditable=true]'))
    .map(function(e){ return {tag: e.tagName,
      ph: e.getAttribute('placeholder') || '',
      val: (e.value || e.innerText || '').trim().slice(0, 160)}; })
    .filter(function(o){ return o.ph || o.val; }).slice(0, 16)
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


def anon_get(aid):
    """独立匿名 GET：不复用 CDP 的登录态。"""
    url = "https://developer.aliyun.com/article/" + aid
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/126 Safari/537.36")]
    try:
        r = op.open(url, timeout=12)
        body = r.read().decode("utf-8", "replace")
        code, final = r.status, r.geturl()
    except urllib.error.HTTPError as e:
        body, code, final = "", e.code, url
    except Exception as e:
        return {"aid": aid, "error": "%s: %s" % (type(e).__name__, str(e)[:100])}
    t = re.search(r"<title>(.*?)</title>", body, re.S)
    d = re.search(r'<meta[^>]*name=["\']date["\'][^>]*content=["\']([^"\']+)', body)
    return {
        "aid": aid, "http": code, "bytes": len(body), "final": final,
        "title": re.sub(r"\s+", " ", t.group(1)).strip() if t else "",
        "meta_date": d.group(1) if d else "",
        "audit_banner": "内容审核中" in body,
    }


def main():
    proc, cdp = Y._connect()
    print("[9228] %s" % ("本次新起" if proc else "复用已有实例"), flush=True)
    ev = {"at": NOW, "source": "live:9228 chrome-yun-profile + 独立匿名 GET",
          "scope": "只读；未点击/未输入/未提交/未删除/未写台账"}
    try:
        # ① 登录态
        login = K.probe_page(cdp)
        tickets = K.ticket_state(K.all_cookies(cdp))
        ev["login"] = login
        ev["persistent_tickets"] = sum(1 for v in tickets.values() if not v[0])
        print("[登录态] logged=%s 持久票据=%d/4"
              % (login.get("logged"), ev["persistent_tickets"]), flush=True)

        # ② 文章列表（含分页 / 滚动完整性）
        cdp.open(MY_ARTICLE)
        time.sleep(11)
        lst = eval_json(cdp, LIST_JS)
        before = lst.get("links") or []
        scrolled = cdp.eval(SCROLL_JS)
        time.sleep(4)
        raw_after = cdp.eval(LINKS_JS)
        try:
            after = json.loads(raw_after or "[]")
        except Exception:
            after = []
        ev["list"] = {k: v for k, v in lst.items() if k != "links"}
        ev["list"]["links"] = before
        ev["list"]["links_after_scroll"] = after
        ev["list"]["scroll_js"] = scrolled
        ev["list"]["complete"] = len(after) <= len(before) + 2
        print("[列表] links=%d 滚动后=%d tabs=%s pagers=%s scroll=%s"
              % (len(before), len(after), " | ".join((lst.get("tabs") or [])[:8]),
                 " | ".join((lst.get("pagers") or [])[:8]), lst.get("scroll")),
              flush=True)
        print("[列表] tail=%s" % (lst.get("tail") or "")[:220], flush=True)

        # ③ 四篇文章页（带登录态）
        pages = []
        for cid, aid, title_key, kws in ARTICLES:
            cdp.open("https://developer.aliyun.com/article/" + aid)
            time.sleep(4)
            d = eval_json(cdp, PAGE_JS)
            text = d.get("text") or ""
            hits = [w for w in kws if w in text or w in (d.get("desc") or "")]
            rec = {
                "case_id": cid, "aid": aid, "final_url": d.get("href"),
                "title": d.get("title"), "meta_date": d.get("date"),
                "title_exact": title_key in (d.get("title") or ""),
                "keyword_hits": hits, "keyword_total": len(kws),
                "text_len": len(text), "audit_lines": d.get("auditLines") or [],
                "desc": (d.get("desc") or "").strip()[:160],
            }
            pages.append(rec)
            print("[文章页] %-22s aid=%-8s title=%-5s date=%-20s 词=%d/%d 审核行=%d"
                  % (cid, aid, rec["title_exact"], rec["meta_date"],
                     len(hits), len(kws), len(rec["audit_lines"])), flush=True)
            for line in rec["audit_lines"][:5]:
                print("          · %s" % line[:110], flush=True)
        ev["pages"] = pages

        # ④ 草稿编辑页（只读打开）
        drafts = []
        for cid, did in DRAFTS:
            url = "https://developer.aliyun.com/article/new?edit=%s#/" % did
            cdp.open(url)
            time.sleep(5)
            d = eval_json(cdp, EDITOR_JS)
            rec = {"case_id": cid, "draft_id": did, "url": url,
                   "final_url": d.get("href"), "title": d.get("title"),
                   "fields": d.get("fields") or [],
                   "text_head": (d.get("text") or "")[:600]}
            drafts.append(rec)
            print("[草稿页] %-12s did=%-8s 字段=%d head=%s"
                  % (cid, did, len(rec["fields"]),
                     rec["text_head"].replace("\n", " ")[:90]), flush=True)
            for f in rec["fields"][:4]:
                print("          · %s ph=%r val=%r"
                      % (f["tag"], f["ph"][:30], f["val"][:60]), flush=True)
        ev["drafts"] = drafts

        # ⑤ 独立匿名 GET 双证
        anon = [anon_get(a[1]) for a in ARTICLES]
        ev["anonymous"] = anon
        for a in anon:
            print("[匿名GET] %-8s HTTP=%s bytes=%-7s date=%-20s banner=%s title=%s"
                  % (a.get("aid"), a.get("http"), a.get("bytes"),
                     a.get("meta_date"), a.get("audit_banner"),
                     (a.get("title") or "")[:48]), flush=True)

        jdump(OUT, ev)
        print("[产物] %s" % os.path.relpath(OUT, ROOT), flush=True)
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
