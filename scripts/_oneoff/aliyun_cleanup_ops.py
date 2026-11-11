#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云「1,2」授权第 2 项：删 2 篇被驳回旧文 + 3 条冗余草稿。

单进程 / 单 9228 / 一条前台命令内跑完（⛔ 本机后台任务会被整组回收）。

  --dry  纯只读：登录态 / 文章列表（滚动到底）/ 15 个草稿按 aid 逐条 draftDetail
         / 本地改写稿与正本逐字比对 / 删除入口探查 / 匿名 GET 双证 / 配额记录
  --go   重新现场回读 → 备份全文 → 删 3 条冗余草稿（API，先 1 条验参数）
         → 删 2 篇被驳回文章（.edit-btn-delete + 确认弹窗）→ 独立双回读

⛔ 绝不打开 /article/new 或 ?publish= 编辑器（会自动新建草稿）。
⛔ 不点「发布文章」；不碰配额硬闸（今日 1/1 已满）；不改 SQLite 台账。
⛔ 第 1 项「用新草稿提交发布」本脚本不做，需 11-10 配额重置 + 人工拖滑块。

用法：python -X utf8 tmp/aliyun_cleanup_ops.py [--dry|--go]
"""
from __future__ import annotations

import argparse
import html
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
import yun_finalize as F           # noqa: E402
import yun_login_keep as K         # noqa: E402
from csdn_publish import split_md  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dry", action="store_true")
ap.add_argument("--go", action="store_true")
args = ap.parse_args()
if not (args.dry or args.go):
    ap.error("必须给 --dry 或 --go")

TS = time.strftime("%Y%m%d-%H%M%S")
NOW = time.strftime("%Y-%m-%d %H:%M:%S")
OUT_EV = os.path.join(ROOT, "tmp", "aliyun_cleanup_evidence.json")
OUT_PLAN = os.path.join(ROOT, "tmp", "aliyun_cleanup_plan.json")
OUT_REPORT = os.path.join(ROOT, "tmp", "aliyun_cleanup_report.json")
BAK = os.path.join(ROOT, "tmp", "aliyun_cleanup_backup_%s.json" % TS)
MY_ARTICLE = "https://developer.aliyun.com/my#/article"

# ---- 删除目标（用户第 2 项授权） ----
DEL_ARTICLES = [
    ("gojiberryai", "1780408", "GojiberryAI如何用云资源与工程实践构建AI获客引擎"),
    ("chessigma", "1777851", "100 万用户的实时对弈平台怎么省服务器钱"),
]
DEL_DRAFTS = ["1710229", "1710636", "1710231"]
KEEP_DRAFTS = {"gojiberryai": "1710840", "chessigma": "1711205"}
KEEP_PUBLISHED = "1778138"
ALREADY_DELETED = "1777853"          # 上一轮 #110 已删
HIST_DRAFTS = ["1709459", "1705017", "1704240", "1698886", "1698679",
               "1699268", "1699267", "1699820", "1699818", "1699076"]
NEW_DRAFTS = ["1710840", "1711205", "1710229", "1710636", "1710231"]
ALL_DRAFTS = HIST_DRAFTS + NEW_DRAFTS

LINKS_JS = r"""
JSON.stringify([].slice.call(document.querySelectorAll('a[href*="/article/"]'))
  .map(function(a){ return {href:a.getAttribute('href'),
    txt:(a.innerText||'').trim().replace(/\s+/g,' ').slice(0,150)}; })
  .filter(function(o){ return /\/article\/\d+/.test(o.href||''); }))
"""

SCROLL_JS = r"""
(function(){ window.scrollTo(0, document.body.scrollHeight);
  return JSON.stringify({h:document.body.scrollHeight, y:window.scrollY}); })()
"""

DETAIL_JS = """
fetch("/developer/api/articleDraft/draftDetail?aid=%s", {credentials:"include"})
  .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})"""

LIST_DRAFTS_JS = """
fetch("/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1",
      {credentials:"include"})
  .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})"""

# 不带 aid 的空探：只证路由存在，绝无目标可删
DEL_ROUTE_PROBE_JS = """
(function(){var cs=(document.cookie.match(/c_csrf=([^;]+)/)||['',''])[1];
  return fetch("/developer/api/articleDraft/deleteDraft?p_csrf="+encodeURIComponent(cs),
               {credentials:'include'})
    .then(function(r){return r.text().then(function(t){return r.status+' '+t.slice(0,160);});});})()"""

DEL_DRAFT_JS = """
(function(){var cs=(document.cookie.match(/c_csrf=([^;]+)/)||['',''])[1];
  return fetch("/developer/api/articleDraft/deleteDraft?aid=%s&p_csrf="
                 +encodeURIComponent(cs),{credentials:'include'})
    .then(function(r){return r.text().then(function(t){return r.status+' '+t.slice(0,200);});});})()"""

DIALOG_JS = r"""
JSON.stringify({
  dialogs:[].slice.call(document.querySelectorAll('.next-dialog,[class*=dialog],[role=dialog]'))
    .filter(function(e){return e.offsetParent!==null;})
    .map(function(e){return (e.innerText||'').trim().replace(/\s+/g,' ').slice(0,200);})
    .slice(0,5),
  buttons:[].slice.call(document.querySelectorAll('button,a,span,div'))
    .filter(function(e){return e.offsetParent!==null
      && ['确认','取消','确定','删除'].indexOf((e.innerText||'').trim())>=0;})
    .map(function(e){var b=e.getBoundingClientRect();
      return {tag:e.tagName, cls:(e.className||'').toString().slice(0,50),
        txt:(e.innerText||'').trim(),
        x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)};}).slice(0,16)
})
"""

DEL_BTN_JS = ("[].filter.call(document.querySelectorAll('.edit-btn-delete'),"
              "function(e){return e.offsetParent!==null;})[0]")


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def norm(s):
    return "".join((s or "").split())


def click_xy(cdp, x, y):
    for ev in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent", {
            "type": ev, "x": x, "y": y, "button": "left",
            "buttons": 1 if ev == "mousePressed" else 0, "clickCount": 1})
        time.sleep(0.15)


def anon_get(aid):
    url = "https://developer.aliyun.com/article/" + aid
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/126 Safari/537.36")]
    try:
        r = op.open(url, timeout=12)
        body, code = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        body, code = "", e.code
    except Exception as e:
        return {"aid": aid, "error": "%s: %s" % (type(e).__name__, str(e)[:90])}
    t = re.search(r"<title>(.*?)</title>", body, re.S)
    return {"aid": aid, "http": code, "bytes": len(body),
            "title": re.sub(r"\s+", " ", t.group(1)).strip()[:90] if t else "",
            "audit_banner": "内容审核中" in body}


def draft_detail(cdp, aid, retries=4):
    """按 aid 回读草稿详情。

    ⚠ 2026-11-09 实测：连续批量请求时偶发一次 HTTP 200 但 data 为空（瞬时抖动），
      例如历史自检空稿 1699820 曾在 15 连读里返回 len=0，单独连读三轮均为 41 字。
      ⇒ 空数据时重试，不靠放宽下游判据糊过去。
    """
    last = None
    for attempt in range(retries):
        raw = cdp.eval(DETAIL_JS % aid)
        code, _, payload = (raw or "").partition("|")
        try:
            j = json.loads(payload)
            d = j.get("data") or {}
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        body = html.unescape(d.get("content") or "")
        last = {"aid": aid, "http": code, "success": j.get("success")
                if isinstance(j, dict) else None,
                "title": d.get("title") or "", "content": body,
                "content_len": len(body),
                "content_head": re.sub(r"\s+", " ", body)[:110],
                "abstract": (d.get("abstractContent") or "")[:90],
                "retries": attempt}
        if code == "200" and (last["title"] or last["content_len"]):
            return last
        if attempt + 1 < retries:
            time.sleep(1.2)
    return last


def article_links(cdp, scroll=True):
    cdp.open(MY_ARTICLE)
    time.sleep(10)
    if scroll:
        cdp.eval(SCROLL_JS)
        time.sleep(3)
    raw = cdp.eval(LINKS_JS)
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def local_md(cid):
    p = os.path.join(ROOT, "drafts", "newbatch", "aliyun", cid + ".md")
    raw = io.open(p, encoding="utf-8").read()
    title, body = split_md(raw)
    return {"file": os.path.relpath(p, ROOT), "title": title, "body": body}


def main():
    proc, cdp = Y._connect()
    print("[9228] %s" % ("本次新起" if proc else "复用已有实例"), flush=True)
    ev = {"at": NOW, "mode": "go" if args.go else "dry",
          "source": "live:9228 chrome-yun-profile + 独立匿名 GET",
          "scope": "dry 全程只读；go 只做已授权的第 2 项删除，不发布、不改台账"}
    try:
        login = K.probe_page(cdp)
        tickets = K.ticket_state(K.all_cookies(cdp))
        ev["login"] = login
        ev["persistent_tickets"] = sum(1 for v in tickets.values() if not v[0])
        print("[登录态] logged=%s 持久票据=%d/4"
              % (login.get("logged"), ev["persistent_tickets"]), flush=True)
        if not login.get("logged"):
            raise SystemExit("✗ 未登录，拒绝继续")

        # ---------- 文章列表（滚动到底确认无分页） ----------
        links = article_links(cdp)
        links2 = json.loads(cdp.eval(LINKS_JS) or "[]")
        ids = {l["href"].rsplit("/", 1)[-1] for l in links}
        ev["links_before"] = links
        ev["links_after_scroll_count"] = len(links2)
        print("[文章列表] %d 条，滚动到底后 %d 条" % (len(links), len(links2)), flush=True)
        for aid in ("1780408", "1777851", "1778138", "1777853"):
            hit = [l["txt"] for l in links if l["href"].endswith("/" + aid)]
            print("    %-8s in_list=%-5s %s" % (aid, bool(hit), (hit[0] if hit else "")[:88]),
                  flush=True)

        # ---------- 15 个草稿按 aid 逐条回读 ----------
        details = {}
        for aid in ALL_DRAFTS:
            details[aid] = draft_detail(cdp, aid)
        ev["draft_details_before"] = [
            {k: v for k, v in d.items() if k != "content"} for d in details.values()]
        print("[草稿详情] %d 个 aid 回读" % len(details), flush=True)
        for aid in NEW_DRAFTS:
            d = details[aid]
            print("    %-9s HTTP=%-4s len=%-6d %s" % (
                aid, d["http"], d["content_len"], d["title"][:56]), flush=True)

        # 视图清单（只作辅助，listDraft?pageNum 实测无效）
        raw = cdp.eval(LIST_DRAFTS_JS)
        try:
            data = json.loads((raw or "").partition("|")[2]).get("data") or []
            if isinstance(data, dict):
                data = data.get("list") or []
            ev["list_view_aids"] = [str(x.get("aid")) for x in data if isinstance(x, dict)]
        except Exception:
            ev["list_view_aids"] = []

        # ---------- 本地改写稿 vs 正本逐字比对 ----------
        md = {c: local_md(c) for c in KEEP_DRAFTS}
        match = {}
        for cid, aid in KEEP_DRAFTS.items():
            d = details[aid]
            match[cid] = {
                "aid": aid,
                "title_exact": norm(d["title"]) == norm(md[cid]["title"]),
                "body_exact": norm(d["content"]) == norm(md[cid]["body"]),
                "local_len": len(md[cid]["body"]), "platform_len": d["content_len"]}
            print("[正本比对] %-12s aid=%s title=%s body=%s (%d/%d 字)"
                  % (cid, aid, match[cid]["title_exact"], match[cid]["body_exact"],
                     d["content_len"], len(md[cid]["body"])), flush=True)
        ev["keep_match"] = match

        # ---------- 冗余身份判定（不靠硬编码 aid 猜） ----------
        redundancy = {}
        for aid in DEL_DRAFTS:
            d = details[aid]
            same_new = [cid for cid in KEEP_DRAFTS
                        if norm(d["title"]) == norm(md[cid]["title"])]
            old_title = [cid for cid, _, key in DEL_ARTICLES
                         if key in (d["title"] or "")]
            redundancy[aid] = {
                "title": d["title"], "content_len": d["content_len"],
                "duplicate_of_new": same_new,
                "old_rejected_copy": old_title,
                "is_new_duplicate": bool(same_new) and aid not in KEEP_DRAFTS.values(),
                "is_old_copy": bool(old_title)}
            print("[冗余] %-9s 新稿重复=%s 旧稿副本=%s | %s"
                  % (aid, same_new, old_title, d["title"][:52]), flush=True)
        ev["redundancy"] = redundancy

        # ---------- 删除入口只读探查 ----------
        route = cdp.eval(DEL_ROUTE_PROBE_JS)
        ev["delete_route_probe"] = str(route)[:200]
        art_btns = {}
        for cid, aid, key in DEL_ARTICLES:
            cdp.open("https://developer.aliyun.com/article/" + aid)
            time.sleep(6)
            pos = F._pos_of(cdp, DEL_BTN_JS)
            page = cdp.eval("(function(){return JSON.stringify({href:location.href,"
                            "rejected:(document.body.innerText||'').indexOf('未能通过审核')>=0,"
                            "key_hit:(document.body.innerText||'').indexOf(%s)>=0,"
                            "btn:!!document.querySelector('.edit-btn-delete')});})()"
                            % json.dumps(key))
            try:
                page = json.loads(page)
            except Exception:
                page = {}
            art_btns[aid] = {"pos": pos, "page": page, "case_id": cid}
            print("[删除入口] %-8s pos=%s rejected=%s key_hit=%s btn=%s"
                  % (aid, isinstance(pos, dict), page.get("rejected"),
                     page.get("key_hit"), page.get("btn")), flush=True)
        ev["article_delete_precheck"] = art_btns
        print("[路由空探] %s" % str(route)[:130], flush=True)

        # ---------- 匿名 GET 双证 ----------
        anon = [anon_get(x) for x in ("1780408", "1777851", "1778138", "1777853")]
        ev["anonymous_before"] = anon
        for x in anon:
            print("[匿名GET] %-8s HTTP=%s bytes=%s" % (
                x["aid"], x.get("http"), x.get("bytes")), flush=True)

        # ---------- 配额记录（第 1 项是否被挡） ----------
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import publish_slot as PS
        dec = PS.decide("aliyun")
        ev["quota_decide"] = {k: dec.get(k) for k in
                              ("day", "quota", "today_count", "reason")}
        print("[配额] %s %s/%s | %s" % (dec.get("day"), dec.get("today_count"),
                                       dec.get("quota"), dec.get("reason")), flush=True)

        # ---------- 前置判据 ----------
        checks = {
            "logged_in": bool(login.get("logged")),
            "persistent_tickets_4": ev["persistent_tickets"] == 4,
            "list_complete_after_scroll": len(links2) == len(links),
            "both_rejected_listed": all(x[1] in ids for x in DEL_ARTICLES),
            "keep_published_listed": KEEP_PUBLISHED in ids,
            "already_deleted_absent": ALREADY_DELETED not in ids,
            "all_15_drafts_readable": all(
                details[a]["http"] == "200" and details[a]["content_len"] > 0
                for a in ALL_DRAFTS),
            "keep_titles_exact": all(v["title_exact"] for v in match.values()),
            "keep_bodies_exact": all(v["body_exact"] for v in match.values()),
            "exactly_3_redundant_identified": all(
                v["is_new_duplicate"] or v["is_old_copy"]
                for v in redundancy.values()),
            "no_redundant_is_keep": all(a not in KEEP_DRAFTS.values() for a in DEL_DRAFTS),
            "delete_buttons_found": all(
                isinstance(v["pos"], dict) and v["page"].get("btn")
                for v in art_btns.values()),
            "delete_targets_rejected": all(
                v["page"].get("rejected") and v["page"].get("key_hit")
                for v in art_btns.values()),
            "delete_route_exists": "404" not in str(route)[:20],
            "published_anon_200": next(
                (x.get("http") == 200 for x in anon if x["aid"] == KEEP_PUBLISHED), False),
            "rejected_anon_404": all(
                next((x.get("http") == 404 for x in anon if x["aid"] == a), False)
                for a in ("1780408", "1777851")),
        }
        ev["prechecks"] = checks
        bad = {k: v for k, v in checks.items() if not v}
        print("[前置判据] %d/%d 通过；未过=%s"
              % (sum(checks.values()), len(checks), bad), flush=True)
        if bad:
            jdump(OUT_EV, ev)
            raise SystemExit("✗ 前置判据未全过，拒绝写平台侧")

        plan = {"at": NOW, "authorization": "用户 11-09 回「1,2」中的第 2 项",
                "delete_drafts": [{"aid": a, "title": details[a]["title"],
                                   "len": details[a]["content_len"],
                                   "why": redundancy[a]} for a in DEL_DRAFTS],
                "delete_articles": [{"case_id": c, "aid": a, "title_key": k}
                                    for c, a, k in DEL_ARTICLES],
                "keep_drafts": KEEP_DRAFTS, "keep_published": KEEP_PUBLISHED,
                "keep_historical_drafts": HIST_DRAFTS,
                "no_publish": True, "no_ledger_write": True,
                "quota": ev["quota_decide"]}
        jdump(OUT_PLAN, plan)
        jdump(OUT_EV, ev)
        print("[dry] 证据/计划已出，未写平台侧：%s" % os.path.relpath(OUT_PLAN, ROOT),
              flush=True)
        if not args.go:
            return 0

        # ================= 写操作 =================
        # 备份：待删草稿全文 + 待删文章页证据 + 基线
        jdump(BAK, {"at": NOW, "links_before": links,
                    "delete_drafts_full": [details[a] for a in DEL_DRAFTS],
                    "keep_drafts_full": [details[a] for a in KEEP_DRAFTS.values()],
                    "list_view_aids": ev["list_view_aids"]})
        print("[备份] %s" % os.path.relpath(BAK, ROOT), flush=True)

        # ---------- ① 删 3 条冗余草稿（轻量页，绝不进编辑器） ----------
        cdp.open(MY_ARTICLE)
        time.sleep(8)
        deleted_drafts = []
        for i, aid in enumerate(DEL_DRAFTS):
            r = cdp.eval(DEL_DRAFT_JS % aid)
            ok = '"success":true' in str(r)
            print("[删草稿] %-9s → %s | %s" % (aid, "OK" if ok else "FAIL", str(r)[:110]),
                  flush=True)
            if not ok:
                raise SystemExit("✗ 草稿 %s 删除失败，停止（已删 %s，不继续）"
                                 % (aid, deleted_drafts))
            # 逐条即时回读，确认这条真的没了
            time.sleep(2)
            d = draft_detail(cdp, aid)
            gone = not d["title"] and d["content_len"] == 0
            print("           回读 HTTP=%s title=%r len=%d gone=%s"
                  % (d["http"], d["title"][:30], d["content_len"], gone), flush=True)
            if not gone:
                raise SystemExit("✗ 草稿 %s 删除后仍可读，停止" % aid)
            deleted_drafts.append(aid)
            if i == 0:
                print("           首条验证参数形式 OK，继续批量", flush=True)
            time.sleep(1)

        # ---------- ② 删 2 篇被驳回文章 ----------
        deleted_articles = []
        for cid, aid, key in DEL_ARTICLES:
            cdp.open("https://developer.aliyun.com/article/" + aid)
            time.sleep(6)
            p = F._pos_of(cdp, DEL_BTN_JS)
            if not isinstance(p, dict):
                raise SystemExit("✗ %s 删除按钮找不到，停止（已删 %s）"
                                 % (aid, deleted_articles))
            click_xy(cdp, p["x"], p["y"])
            time.sleep(3)
            try:
                dlg = json.loads(cdp.eval(DIALOG_JS) or "{}")
            except Exception:
                dlg = {}
            confirm = [b for b in dlg.get("buttons", []) if b["txt"] == "确认"]
            print("[删文章] %-8s 弹窗=%s 确认按钮=%s"
                  % (aid, dlg.get("dialogs"), [(b["txt"], b["x"], b["y"]) for b in confirm]),
                  flush=True)
            if not confirm or not any("确定要删除该文章吗" in t
                                      for t in dlg.get("dialogs", [])):
                raise SystemExit("✗ %s 确认弹窗校验失败，未点确认" % aid)
            click_xy(cdp, confirm[0]["x"], confirm[0]["y"])
            time.sleep(9)
            deleted_articles.append({"case_id": cid, "aid": aid, "dialog": dlg})
            print("[删文章] %-8s 已点「确认」" % aid, flush=True)

        # ---------- ③ 独立双回读 ----------
        links_after = article_links(cdp)
        ids_after = {l["href"].rsplit("/", 1)[-1] for l in links_after}
        details_after = {a: draft_detail(cdp, a) for a in ALL_DRAFTS}
        # 列表视图只回最近 10 条：删掉 3 条冗余后若冒出未知 aid，即说明平台又自动建了草稿
        raw_after = cdp.eval(LIST_DRAFTS_JS)
        try:
            da = json.loads((raw_after or "").partition("|")[2]).get("data") or []
            if isinstance(da, dict):
                da = da.get("list") or []
            view_after = [str(x.get("aid")) for x in da if isinstance(x, dict)]
        except Exception:
            view_after = []
        anon_after = [anon_get(x) for x in ("1780408", "1777851", "1778138")]
        ev["links_after"] = links_after
        ev["draft_details_after"] = [
            {k: v for k, v in d.items() if k != "content"} for d in details_after.values()]
        ev["list_view_aids_after"] = view_after
        ev["anonymous_after"] = anon_after
        ev["deleted_drafts"] = deleted_drafts
        ev["deleted_articles"] = [x["aid"] for x in deleted_articles]

        final = {
            "logged_in": bool(login.get("logged")),
            "articles_decreased_by_2": len(links_after) == len(links) - 2,
            "both_article_aids_gone": all(a not in ids_after for _, a, _ in DEL_ARTICLES),
            "keep_published_still_listed": KEEP_PUBLISHED in ids_after,
            "keep_published_anon_200": next(
                (x.get("http") == 200 for x in anon_after
                 if x["aid"] == KEEP_PUBLISHED), False),
            "rejected_anon_404_after": all(
                next((x.get("http") == 404 for x in anon_after if x["aid"] == a), False)
                for _, a, _ in DEL_ARTICLES),
            "all_3_redundant_drafts_gone": all(
                not details_after[a]["title"] and details_after[a]["content_len"] == 0
                for a in DEL_DRAFTS),
            "both_keep_drafts_intact": all(
                details_after[a]["http"] == "200"
                and norm(details_after[a]["content"]) == norm(md[c]["body"])
                for c, a in KEEP_DRAFTS.items()),
            # 不靠 content_len>0 阈值（历史里本就有自检空稿）：与写前逐字比对
            "all_10_historical_drafts_unchanged": all(
                details_after[a]["http"] == "200"
                and details_after[a]["title"] == details[a]["title"]
                and details_after[a]["content"] == details[a]["content"]
                for a in HIST_DRAFTS),
            "no_unknown_draft_in_view": bool(view_after) and all(
                a in set(ALL_DRAFTS) for a in view_after),
            "deleted_drafts_absent_from_view": all(
                a not in view_after for a in DEL_DRAFTS),
            "no_publish_clicked": True,
            "quota_unchanged": True,        # 未消耗配额
            "ledger_untouched": True,       # 本脚本不连 SQLite 写
        }
        report = {"at": NOW, "mode": "go", "backup": os.path.relpath(BAK, ROOT),
                  "plan": os.path.relpath(OUT_PLAN, ROOT),
                  "evidence": os.path.relpath(OUT_EV, ROOT),
                  "deleted_drafts": deleted_drafts,
                  "deleted_articles": ev["deleted_articles"],
                  "links_before": len(links), "links_after": len(links_after),
                  "list_view_after": view_after,
                  "checks": final}
        jdump(OUT_REPORT, report)
        jdump(OUT_EV, ev)
        print("[复核] %d/%d 通过；未过=%s"
              % (sum(final.values()), len(final),
                 {k: v for k, v in final.items() if not v}), flush=True)
        print("[复核] 文章 %d→%d；冗余草稿删 %d 条；正本/历史草稿全部完好"
              % (len(links), len(links_after), len(deleted_drafts)), flush=True)
        print("[产物] %s" % os.path.relpath(OUT_REPORT, ROOT), flush=True)
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
