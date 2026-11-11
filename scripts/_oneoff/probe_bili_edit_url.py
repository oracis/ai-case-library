#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：B站草稿正文能不能从**编辑器**读出全文（而不是 draft/view 的预览残文）。

背景（实测已定，别再重复踩）
------------------------
`draft/view.content` 是编辑器**扁平化预览残文**：`<p>` 块 0 个、末尾无闭合标签、
`<h2>` 被压成 `▶ 我的判断`、块间是全角空格。既不能回写，也不能当「写进去的是全文」。

`view_draft` 返回的 `edit_url` 字段实测**是空串**，所以只能用 `bili_draft_api` 注释里
记的路由：`https://member.bilibili.com/york/read-editor?aid=<aid>`。
编辑器在同域 iframe 里（`read-draft` / `read-editor`），同域可主文档直读
`iframe.contentDocument`，不必切 CDP context。

要看的三件事
----------
  1. 直接导航 read-editor?aid= 能不能加载出编辑器（不靠点击草稿箱列表）
  2. `.tiptap.ProseMirror.eva3-editor` 的 innerText 有多少字
  3. 与 `draft/view` 预览残文、母版纯文本三方对得上吗

⛔ 只读：只开 tab + 读 DOM，不点按钮、不输入、不保存。
用法：
    python -X utf8 tmp/probe_bili_edit_url.py --cid bustem
    python -X utf8 tmp/probe_bili_edit_url.py --cid bustem --mode drafts
"""
import argparse
import html
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import bili_draft_api as api            # noqa: E402
import bilibili_publish as bp           # noqa: E402
import toutiao_publish as tt            # noqa: E402
import wechat_publish as wp             # noqa: E402

MARK = "我去核了一遍"
EDITOR_URL = "https://member.bilibili.com/york/read-editor?aid=%s"

# 主文档里穿过同域 iframe 读编辑器；同时把顶层也读一遍（顶层直开时编辑器就在 document）
JS_READ = r"""(function(){
  var M = '\u6211\u53bb\u6838\u4e86\u4e00\u904d';
  var SEL = '.tiptap.ProseMirror.eva3-editor';
  function txt(doc){
    if(!doc) return null;
    var el = doc.querySelector(SEL);
    if(!el) return null;
    var t = (el.innerText || '');
    return {len: t.replace(/\s/g,'').length, mark: t.indexOf(M) >= 0,
            head: t.replace(/\s+/g,' ').slice(0,60),
            tail: t.replace(/\s+/g,' ').slice(-60)};
  }
  var out = {url: location.href, docTitle: document.title,
             top: txt(document), frames: [], frameCount: 0};
  var fs = [].slice.call(document.querySelectorAll('iframe'));
  out.frameCount = fs.length;
  fs.forEach(function(f, i){
    var rec = {i: i, src: (f.src||'').slice(0,90)};
    try {
      var d = f.contentDocument;
      rec.hasDoc = !!d;
      rec.url = d ? (d.location ? d.location.href.slice(0,90) : '') : '';
      var r = txt(d);
      if (r) rec.editor = r;
      else rec.eds = d ? d.querySelectorAll('[contenteditable]').length : -1;
      // 二跳：iframe 里再套 iframe
      var inner = d ? [].slice.call(d.querySelectorAll('iframe')) : [];
      if (inner.length) {
        rec.innerFrames = inner.map(function(g, j){
          try {
            var r2 = txt(g.contentDocument);
            return r2 ? {j: j, editor: r2} : {j: j, eds: g.contentDocument ?
                     g.contentDocument.querySelectorAll('[contenteditable]').length : -1};
          } catch (e) { return {j: j, err: String(e)}; }
        });
      }
    } catch (e) { rec.err = String(e); }
    out.frames.push(rec);
  });
  var b = document.body ? (document.body.innerText || '') : '';
  out.bodyLen = b.replace(/\s/g,'').length;
  out.bodyHead = b.replace(/\s+/g,' ').slice(0, 140);
  out.canvas = [].slice.call(document.querySelectorAll(
      '.title-input__inner, .new-creation_button')).map(function(e){
        return e.tagName + '.' + ((e.className||'').toString().slice(0,40)); });
  return JSON.stringify(out);
})()"""


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def build_body(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return tt.blocks_to_html(tt.md_to_blocks(md), footer=False)


def read_editor(sub, settle):
    for _ in range(settle):
        time.sleep(1)
    raw = sub.eval(JS_READ, refresh_context=True)
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:                                       # noqa: BLE001
        print("✗ 探针返回非 JSON：%r" % (str(raw)[:300],))
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="bustem")
    ap.add_argument("--aid", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--settle", type=int, default=8)
    ap.add_argument("--mode", default="editor",
                    choices=["editor", "drafts"],
                    help="editor=直开 read-editor?aid=；drafts=开草稿箱列表页")
    a = ap.parse_args()
    port = a.port or bp.PUB_PORT

    md = os.path.join(ROOT, "out", "articles", a.cid + ".md")
    mplain = norm(build_body(md))
    print("母版：纯文本 %d 字" % len(mplain))

    aid = a.aid
    if not aid:
        import sqlite3
        conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
        cur = conn.cursor()
        cur.execute("SELECT remote_id, draft_id FROM platform_posts "
                    "WHERE platform='bilibili' AND state='draft' AND case_id=?",
                    (a.cid,))
        r = cur.fetchone()
        conn.close()
        aid = str((r[0] or r[1]) if r else "")
    print("aid=%s" % aid)

    cdp = wp.CDP(port)
    api._bili_tab(cdp)
    d = api.view_draft(cdp, aid) or {}
    preview = d.get("content") or ""
    print("预览残文：纯文本 %d 字（含注记=%s）"
          % (len(norm(preview)), MARK in preview))

    url = (EDITOR_URL % aid) if a.mode == "editor" else api.DRAFT_PAGE
    print("打开：%s" % url)

    tid, sub = None, None
    try:
        tid = cdp.new_target(url)["id"]
        sub = wp.CDP(port)
        if not sub.connect_target(tid):
            print("✗ 连不上新 tab")
            return 1
        sub.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                  "mobile": False})
        j = read_editor(sub, a.settle)
        print("\n--- 页面 ---")
        print("location   : %s" % j.get("url"))
        print("title      : %s" % j.get("docTitle"))
        print("body       : %s 字 ｜ head=%s" % (j.get("bodyLen"), j.get("bodyHead")))
        print("iframe     : %d 个" % (j.get("frameCount") or 0))
        print("入口元素   : %s" % json.dumps(j.get("canvas") or [], ensure_ascii=False))
        top = j.get("top")
        if top:
            print("顶层编辑器 : %d 字 ｜ 含注记=%s" % (top["len"], top["mark"]))
        got = None
        for f in (j.get("frames") or []):
            print("  frame[%s] %s hasDoc=%s editor=%s"
                  % (f.get("i"), f.get("src"), f.get("hasDoc"),
                     json.dumps(f.get("editor"), ensure_ascii=False) if f.get("editor")
                     else ("eds=%s" % f.get("eds"))))
            if f.get("editor") and (not got or f["editor"]["len"] > got["len"]):
                got = f["editor"]
            for g in (f.get("innerFrames") or []):
                print("      inner[%s] %s" % (g.get("j"),
                      json.dumps(g.get("editor"), ensure_ascii=False)
                      if g.get("editor") else "eds=%s" % g.get("eds")))
                if g.get("editor") and (not got or g["editor"]["len"] > got["len"]):
                    got = g["editor"]
        if top and (not got or top["len"] > got["len"]):
            got = top

        print("\n--- 裁定 ---")
        if not got:
            print("⇒ ✗ 读不到 .tiptap.ProseMirror.eva3-editor，编辑器全文这条不通")
            return 2
        cov = got["len"] / len(mplain) if mplain else 0
        print("编辑器正文 %d 字 ｜ 母版 %d 字 ｜ 覆盖率 %.2f ｜ 含注记=%s"
              % (got["len"], len(mplain), cov, got["mark"]))
        print("head: %s" % got["head"])
        print("tail: %s" % got["tail"])
        if cov >= 0.95:
            print("⇒ ✓ 编辑器可读全文，能当回写验收真值")
        else:
            print("⇒ 编辑器可读但明显短于母版（%.0f%%），需查是否缺块" % (cov * 100))
    finally:
        for conn in (sub, cdp):
            try:
                if conn is not None:
                    conn.close()
            except Exception:                               # noqa: BLE001
                pass
        if tid:
            try:
                wp.CDP(port).close_target(tid)
            except Exception:                               # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
