#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云平台侧两项授权操作（PRD #110）：两篇就地改稿存草稿 + 删 cod-dex 驳回旧稿。

单进程 / 单个 9228 / 一条前台命令内跑完（⛔ 本机后台任务会被整组回收）。

  --dry  只读：登录态 / 草稿箱基线 / 文章列表基线 / 两个编辑入口的旧稿全文
         / 新稿校验 / 全量按钮（确认「存为草稿」存在）→ 出证据与计划
  --go   前置重新现场回读 → 备份三篇旧稿 → 两篇注入标题+正文+摘要
         → 只点「存为草稿」（⛔ 绝不点「发布文章」）→ 删除 1777853（确认弹窗）
         → 双回读（列表 / 草稿箱 / 匿名 GET）→ 出报告

⛔ 不改 SQLite 台账；标题镜像同步由 tmp/aliyun_title_sync.py 单独按证据执行。
用法：python -X utf8 tmp/aliyun_platform_ops.py [--dry|--go]
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
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
OUT_EV = os.path.join(ROOT, "tmp", "aliyun_ops_evidence.json")
OUT_PLAN = os.path.join(ROOT, "tmp", "aliyun_ops_plan.json")
OUT_REPORT = os.path.join(ROOT, "tmp", "aliyun_ops_report.json")
BAK = os.path.join(ROOT, "tmp", "aliyun_ops_backup_%s.json" % TS)
MY_ARTICLE = "https://developer.aliyun.com/my#/article"

# 两项：就地改稿（只存草稿） + 删除驳回旧稿
REWRITE = [
    ("gojiberryai", "1780408", "/article/new/ai?publish=1780408",
     "drafts/newbatch/aliyun/gojiberryai.md"),
    ("chessigma", "1777851", "/article/new/cloudnative?publish=1777851",
     "drafts/newbatch/aliyun/chessigma.md"),
]
DELETE = ("cod-dex", "1777853", "免费学 250 小时")
KEEP_PUBLISHED = ("cod-dex", "1778138")

EP_DRAFTS = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
LIST_DRAFTS_JS = """
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_DRAFTS)

LINKS_JS = r"""
JSON.stringify([].slice.call(document.querySelectorAll('a[href*="/article/"]'))
  .map(function(a){ return {href:a.getAttribute('href'),
    txt:(a.innerText||'').trim().replace(/\s+/g,' ').slice(0,140)}; })
  .filter(function(o){ return /\/article\/\d+/.test(o.href||''); }))
"""

BTNS_JS = r"""
JSON.stringify([].slice.call(document.querySelectorAll('button,a,[role=button]'))
  .filter(function(e){ return e.offsetParent!==null
    && (e.innerText||'').trim() && (e.innerText||'').trim().length<14; })
  .map(function(e){ return (e.innerText||'').trim(); })
  .filter(function(t,i,a){ return a.indexOf(t)===i; }).slice(0,40))
"""

EDITOR_JS = r"""
JSON.stringify({
  href: location.href,
  title: (document.querySelector('#title')||{}).value || '',
  body: (document.querySelector('.mditor textarea.textarea')||{}).value || '',
  abstract: (document.querySelector('#abstractContent')||{}).value || '',
  bodyMirror: (document.querySelector('#article-editor')||{}).value || '',
  pageTitle: document.title,
  textHead: (document.body ? (document.body.innerText||'') : '')
    .replace(/\s+/g,' ').slice(0,180)
})
"""

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


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def eval_json(cdp, js, default):
    raw = cdp.eval(js)
    try:
        return json.loads(raw or "")
    except Exception:
        print("  [eval 解析失败] %s" % str(raw)[:180], flush=True)
        return default


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
    return {"aid": aid, "http": code, "bytes": len(body),
            "audit_banner": "内容审核中" in body}


def draft_rows(cdp):
    r = cdp.eval(LIST_DRAFTS_JS)
    if not isinstance(r, str) or r.startswith("ERR|"):
        raise SystemExit("✗ 草稿箱读取失败: %s" % str(r)[:180])
    payload = r.partition("|")[2]
    data = json.loads(payload).get("data") or []
    if isinstance(data, dict):
        data = data.get("list") or []
    return [{"aid": str(x.get("aid")), "title": x.get("title") or "",
             "content": html.unescape(x.get("content") or "")}
            for x in data if isinstance(x, dict)]


def article_links(cdp):
    cdp.open(MY_ARTICLE)
    time.sleep(10)
    return eval_json(cdp, LINKS_JS, [])


def load_new_md(rel):
    raw = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
    title, body = split_md(raw)
    assert title and body, "新稿标题或正文为空: " + rel
    head = body.strip().split("\n")[0][:24]
    return {"file": rel, "title": title, "body": body, "abstract": head,
            "first": body.strip().split("\n")[0][:40]}


def open_editor(cdp, href):
    cdp.open("https://developer.aliyun.com" + href)
    time.sleep(9)
    try:
        F._close_disclaimer(cdp)
    except Exception:
        pass
    Y._wait_for(cdp, "!!document.querySelector('#title')", timeout=45)
    return eval_json(cdp, EDITOR_JS, {})


def norm(s):
    return "".join((s or "").split())


def main():
    proc, cdp = Y._connect()
    print("[9228] %s" % ("本次新起" if proc else "复用已有实例"), flush=True)
    ev = {"at": NOW, "mode": "go" if args.go else "dry",
          "source": "live:9228 chrome-yun-profile + 独立匿名 GET",
          "scope": "dry 全程只读；go 只做已授权的两篇改稿存草稿 + 删除 1777853"}
    try:
        login = K.probe_page(cdp)
        tickets = K.ticket_state(K.all_cookies(cdp))
        ev["login"] = login
        ev["persistent_tickets"] = sum(1 for v in tickets.values() if not v[0])
        print("[登录态] logged=%s 持久票据=%d/4"
              % (login.get("logged"), ev["persistent_tickets"]), flush=True)
        if not login.get("logged"):
            raise SystemExit("✗ 未登录，拒绝继续")

        # ---------- 前置基线（dry / go 都现场重读） ----------
        drafts_before = draft_rows(cdp)
        links_before = article_links(cdp)
        ev["drafts_before"] = drafts_before
        ev["links_before"] = links_before
        print("[基线] 草稿 %d 条 / 文章链接 %d 条"
              % (len(drafts_before), len(links_before)), flush=True)
        ids_before = {l["href"].rsplit("/", 1)[-1] for l in links_before}
        if DELETE[1] not in ids_before or KEEP_PUBLISHED[1] not in ids_before:
            raise SystemExit("✗ 基线里缺少待删除 / 需保留的文章，拒绝继续")
        if any(x[1] not in ids_before for x in REWRITE):
            raise SystemExit("✗ 基线里缺少待改稿文章，拒绝继续")

        # ---------- 两篇编辑入口：旧稿全文 + 新稿校验 ----------
        snap = []
        for cid, aid, href, rel in REWRITE:
            d = open_editor(cdp, href)
            new = load_new_md(rel)
            btns = eval_json(cdp, BTNS_JS, [])
            rec = {"case_id": cid, "aid": aid, "href": href,
                   "final_url": d.get("href"), "old_title": d.get("title"),
                   "old_body": d.get("body"), "old_abstract": d.get("abstract"),
                   "old_body_len": len(d.get("body") or ""),
                   "buttons": btns, "new": new}
            snap.append(rec)
            print("[编辑器] %-12s aid=%-8s 旧标题=%s" % (cid, aid, d.get("title")), flush=True)
            print("           旧正文 %d 字 / 摘要 %d 字 / 按钮=%s"
                  % (rec["old_body_len"], len(d.get("abstract") or ""),
                     " | ".join(btns[:16])), flush=True)
            print("           新标题=%s" % new["title"], flush=True)
            if not d.get("title") or not d.get("body"):
                raise SystemExit("✗ %s 编辑页没加载到旧稿，拒绝改稿" % cid)
            if "存为草稿" not in btns:
                raise SystemExit("✗ %s 编辑页没有「存为草稿」按钮，拒绝改稿" % cid)
            if norm(new["title"]) == norm(d.get("title")):
                raise SystemExit("✗ %s 新标题与旧标题相同，拒绝改稿" % cid)
            if norm(d.get("body") or "")[:40] == norm(new["body"])[:40]:
                raise SystemExit("✗ %s 新正文与旧正文开头相同，稿件可能没换" % cid)
        ev["editor_snapshots"] = [
            {k: v for k, v in r.items() if k != "old_body"} | {"old_body_len": r["old_body_len"]}
            for r in snap]

        # ---------- 删除目标前置证据 ----------
        cdp.open("https://developer.aliyun.com/article/" + DELETE[1])
        time.sleep(6)
        del_pos = F._pos_of(cdp, "[].filter.call("
                                 "document.querySelectorAll('.edit-btn-delete'),"
                                 "function(e){return e.offsetParent!==null;})[0]")
        page_ok = cdp.eval("(function(){return JSON.stringify({"
                           "href:location.href, hit:(document.body.innerText||'')"
                           ".indexOf(%s)>=0, btn:!!document.querySelector"
                           "('.edit-btn-delete')});})()" % json.dumps(DELETE[2]))
        try:
            page_ok = json.loads(page_ok)
        except Exception:
            page_ok = {}
        ev["delete_precheck"] = {"pos": del_pos, "page": page_ok}
        print("[删除前置] pos=%s page=%s" % (del_pos, page_ok), flush=True)
        if not isinstance(del_pos, dict) or not page_ok.get("hit") \
                or DELETE[1] not in (page_ok.get("href") or ""):
            raise SystemExit("✗ 删除目标页校验失败，拒绝删除")

        plan = {"at": NOW, "rewrite": [
            {"case_id": c, "aid": a, "href": h, "file": r,
             "new_title": s["new"]["title"], "new_body_len": len(s["new"]["body"]),
             "old_title": s["old_title"]} for (c, a, h, r), s in zip(REWRITE, snap)],
            "delete": {"case_id": DELETE[0], "aid": DELETE[1],
                       "title_key": DELETE[2]},
            "keep_published": {"case_id": KEEP_PUBLISHED[0], "aid": KEEP_PUBLISHED[1]},
            "save_button": "存为草稿", "publish_button_forbidden": True}
        jdump(OUT_PLAN, plan)
        jdump(OUT_EV, ev)
        print("[dry] 证据/计划已出，未写平台侧：%s / %s"
              % (os.path.relpath(OUT_EV, ROOT), os.path.relpath(OUT_PLAN, ROOT)),
              flush=True)
        if not args.go:
            return 0

        # ---------- 备份（旧稿全文 + 平台基线） ----------
        jdump(BAK, {"at": NOW, "drafts_before": drafts_before,
                    "links_before": links_before,
                    "old_articles": [{"case_id": r["case_id"], "aid": r["aid"],
                                      "title": r["old_title"], "body": r["old_body"],
                                      "abstract": r["old_abstract"]} for r in snap]})
        print("[备份] %s" % os.path.relpath(BAK, ROOT), flush=True)

        # ---------- ① 两篇就地改稿：只点「存为草稿」 ----------
        rewritten = []
        for (cid, aid, href, rel), s in zip(REWRITE, snap):
            d = open_editor(cdp, href)
            if norm(d.get("title")) != norm(s["old_title"]):
                raise SystemExit("✗ %s 打开编辑页后旧标题变了，停止" % cid)
            r1 = Y._fill_text(cdp, "#title", s["new"]["title"])
            r2 = Y._fill_text(cdp, "#abstractContent", s["new"]["abstract"])
            r3 = cdp.eval("""
(function(){
  var real = document.querySelector('.mditor textarea.textarea');
  if (!real) return 'no real textarea';
  var setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(real, %s);
  real.dispatchEvent(new Event('input', {bubbles:true}));
  return 'ok len=' + real.value.length;
})()""" % json.dumps(s["new"]["body"]))
            time.sleep(2)
            chk = eval_json(cdp, EDITOR_JS, {})
            ok_title = norm(chk.get("title")) == norm(s["new"]["title"])
            ok_body = norm(chk.get("body")) == norm(s["new"]["body"])
            ok_old_gone = norm(s["old_body"])[:30] not in norm(chk.get("body"))
            print("[改稿] %-12s title=%s abstract=%s body=%s | 回读 title=%s body=%s 旧文已去=%s"
                  % (cid, r1, r2, r3, ok_title, ok_body, ok_old_gone), flush=True)
            if not (ok_title and ok_body and ok_old_gone):
                raise SystemExit("✗ %s 编辑器内回读不通过，停止（不点存为草稿）" % cid)
            if not Y._click_by_text(cdp, "存为草稿"):
                raise SystemExit("✗ %s 找不到「存为草稿」按钮，停止" % cid)
            time.sleep(10)
            final_url = cdp.eval("location.href")
            rewritten.append({"case_id": cid, "aid": aid, "final_url": final_url,
                              "editor_readback": {"title": ok_title, "body": ok_body,
                                                 "old_gone": ok_old_gone},
                              "new_title": s["new"]["title"]})
            print("[改稿] %-12s 已点「存为草稿」 final=%s" % (cid, final_url), flush=True)
        ev["rewritten"] = rewritten

        # ---------- ② 删除 1777853 ----------
        cdp.open("https://developer.aliyun.com/article/" + DELETE[1])
        time.sleep(6)
        p = F._pos_of(cdp, "[].filter.call("
                           "document.querySelectorAll('.edit-btn-delete'),"
                           "function(e){return e.offsetParent!==null;})[0]")
        if not isinstance(p, dict):
            raise SystemExit("✗ 删除按钮找不到，停止")
        click_xy(cdp, p["x"], p["y"])
        time.sleep(3)
        dlg = eval_json(cdp, DIALOG_JS, {})
        confirm = [b for b in dlg.get("buttons", []) if b["txt"] == "确认"]
        ev["delete_dialog"] = dlg
        print("[删除] 弹窗=%s 确认按钮=%s"
              % (dlg.get("dialogs"), [(b["txt"], b["x"], b["y"]) for b in confirm]),
              flush=True)
        if not confirm or not any("确定要删除该文章吗" in t
                                  for t in dlg.get("dialogs", [])):
            raise SystemExit("✗ 删除确认弹窗校验失败，停止（未确认删除）")
        click_xy(cdp, confirm[0]["x"], confirm[0]["y"])
        time.sleep(9)
        print("[删除] 已点「确认」，等待回读", flush=True)
        ev["delete_clicked"] = True

        # ---------- ③ 双回读 ----------
        links_after = article_links(cdp)
        drafts_after = draft_rows(cdp)
        anon = [anon_get(x) for x in (DELETE[1], KEEP_PUBLISHED[1],
                                     REWRITE[0][1], REWRITE[1][1])]
        ev["links_after"] = links_after
        ev["drafts_after"] = drafts_after
        ev["anonymous_after"] = anon
        ids_after = {l["href"].rsplit("/", 1)[-1] for l in links_after}
        titles_after = " ".join(l["txt"] for l in links_after)

        checks = {
            "logged_in": bool(login.get("logged")),
            "persistent_tickets_4": ev["persistent_tickets"] == 4,
            "baseline_links_12": len(links_before) == 12,
            "delete_target_gone": DELETE[1] not in ids_after,
            "links_decreased_by_1": len(links_after) == len(links_before) - 1,
            "keep_published_still_listed": KEEP_PUBLISHED[1] in ids_after,
            "keep_published_anon_200": next(
                (a.get("http") == 200 for a in anon if a["aid"] == KEEP_PUBLISHED[1]), False),
            "delete_target_anon_404": next(
                (a.get("http") == 404 for a in anon if a["aid"] == DELETE[1]), False),
            "no_publish_clicked": True,   # 代码路径上从未调用发布按钮
            "both_editors_readback_ok": all(
                all(r["editor_readback"].values()) for r in rewritten),
        }
        # 标题是否真的落到平台侧（决定台账能否同步）
        title_on_platform = {}
        for r in rewritten:
            key = norm(r["new_title"])[:18]
            in_list = key in norm(titles_after)
            in_drafts = any(key in norm(d["title"]) for d in drafts_after)
            in_content = any(key in norm(d["content"]) for d in drafts_after)
            title_on_platform[r["case_id"]] = {
                "list": in_list, "draft_title": in_drafts, "draft_content": in_content}
        ev["title_on_platform"] = title_on_platform
        checks["rewrite_titles_visible_on_platform"] = all(
            any(v.values()) for v in title_on_platform.values())
        checks["draft_box_unchanged_or_explainable"] = (
            len(drafts_after) >= len(drafts_before))
        report = {"at": NOW, "mode": "go", "backup": os.path.relpath(BAK, ROOT),
                  "plan": os.path.relpath(OUT_PLAN, ROOT),
                  "evidence": os.path.relpath(OUT_EV, ROOT),
                  "checks": checks, "title_on_platform": title_on_platform,
                  "rewritten": rewritten,
                  "links_before": len(links_before), "links_after": len(links_after),
                  "drafts_before": len(drafts_before), "drafts_after": len(drafts_after),
                  "draft_aids_added": sorted(
                      {d["aid"] for d in drafts_after} - {d["aid"] for d in drafts_before}),
                  "anonymous_after": anon}
        jdump(OUT_REPORT, report)
        jdump(OUT_EV, ev)
        print("[复核] %d/%d 通过：%s"
              % (sum(checks.values()), len(checks),
                 {k: v for k, v in checks.items() if not v}), flush=True)
        print("[复核] 文章 %d→%d 草稿 %d→%d 新增草稿 aid=%s"
              % (len(links_before), len(links_after), len(drafts_before),
                 len(drafts_after), report["draft_aids_added"]), flush=True)
        print("[复核] 平台侧新标题可见性=%s" % title_on_platform, flush=True)
        print("[产物] %s" % os.path.relpath(OUT_REPORT, ROOT), flush=True)
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
