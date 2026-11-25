#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在腾讯云**新建**一份草稿（write-new 从零填标题+正文+存草稿），拿到 draftId。

为什么需要：`yun_finalize.publish_tencent()` 只接受已有 draftId，不注入正文
（设计如此，见 rewrite_yun_draft.py 文档）。被驳回的稿件已从草稿箱移走，
要重发必须先建一份新草稿。

判据（照 #136 的口径，全部走「离开编辑器页的独立列箱」）：
  1. 点「存草稿」后导航到 write-new 首页，重新拉草稿列表两次，两次一致
  2. 用**标题精确匹配**认领新行（不看数量：打开新编辑器本身可能先建空草稿）
  3. plain 里必须含新正文首句（归一化后比较，平台会把空格全压掉）

红线：不认编辑器页内的乐观回读；拿不到 draftId 就停，不猜。

用法：
    python scripts/_oneoff/tc_new_draft_20261125.py \
        --file drafts/newbatch/tencentcloud/easymix.md --case easymix [--go]
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y          # noqa: E402
from csdn_publish import split_md  # noqa: E402

LIST_TC = """
fetch("/developer/api/article/getUserArticleDrafts?_="+Date.now(),
      {method:"POST", credentials:"include", cache:"no-store",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""

HOOK_SAVES = """
(function(){
  window.__wb_saves=[];
  var O=window.fetch;
  window.fetch=function(u,o){
    var p=O.apply(this,arguments);
    try{
      var url=String((u && u.url) || u || '');
      if(/save|draft|article/i.test(url)){
        p.then(function(r){ try{ r.clone().text().then(function(t){
          window.__wb_saves.push({via:'fetch',url:url,status:r.status,
                                 body:String(t).slice(0,400)});}); }catch(e){} });
      }
    }catch(e){}
    return p;
  };
  return 'HOOKED';
})()"""

SEL_TITLE = "textarea.cdc-article-editor__title-input"
norm = lambda s: "".join((s or "").split()).lstrip(">")


SAVE_BTN_JS = """
(function(){
  var want = '存草稿';
  var bs = document.querySelectorAll('button,[role=button]');
  var best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent || '').trim() !== want) continue;
    if (b.offsetParent === null) continue;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var a = r.width * r.height;
    if (!best || a < best.a) best = {b: b, a: a};
  }
  if (!best) return '';
  // 🚨 按钮常被滚出视口，elementFromPoint 就返回 null、坐标点击必然打空。
  //   先滚到视口中央，再重新取 rect（rect 是相对视口的，滚动后必须重算）。
  best.b.scrollIntoView({block: 'center', inline: 'center'});
  var r2 = best.b.getBoundingClientRect();
  var x = r2.x + r2.width / 2, y = r2.y + r2.height / 2;
  var hit = document.elementFromPoint(x, y);
  return JSON.stringify({
    x: x, y: y,
    inView: (x >= 0 && y >= 0 && x <= innerWidth && y <= innerHeight),
    covered: !(hit && (best.b === hit || best.b.contains(hit))),
    hit: hit ? (hit.tagName + '.' + String(hit.className || '').slice(0, 40)) : 'null'
  });
})()"""


def click_save(cdp):
    """滚到视口中央后发真实鼠标事件；失败返回 False。"""
    pos = cdp.eval(SAVE_BTN_JS)
    if not pos:
        print("    ⚠ 没找到「存草稿」按钮")
        return False
    pos = json.loads(pos)
    print("  存草稿按钮: x=%.0f y=%.0f inView=%s covered=%s hit=%s"
          % (pos["x"], pos["y"], pos["inView"], pos["covered"], pos["hit"]))
    for tp, buttons in (("mouseMoved", 0), ("mousePressed", 1),
                        ("mouseReleased", 0)):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": pos["x"], "y": pos["y"], "button": "left",
                  "clickCount": 1, "buttons": buttons})
        time.sleep(0.1)
    return True


def wait_draft_id(cdp, seconds=90):
    """点保存后轮询 URL：拿到 write-new?draftId=xxx 才算落库。"""
    for i in range(seconds // 3):
        u = cdp.eval("location.href") or ""
        m = re.search(r"draftId=(\d+)", u)
        if m:
            print("  ✓ URL 出现 draftId=%s（第 %d 次轮询）" % (m.group(1), i + 1))
            return m.group(1)
        time.sleep(3)
    return ""


def list_rows(cdp):
    r = cdp.eval(LIST_TC)
    if not isinstance(r, str) or r.startswith("ERR|"):
        raise SystemExit("腾讯云草稿箱读失败: %s" % str(r)[:200])
    d = json.loads(r)
    items = d.get("list") or d.get("data") or []
    if isinstance(items, dict):
        items = items.get("list") or []
    return [(str(x.get("draftId")), x.get("title") or "", x.get("plain") or "")
            for x in items if isinstance(x, dict)]


def independent_rows(cdp):
    """离开编辑器页再列箱（两次一致才算服务端真值，#136）。"""
    cdp.open("https://cloud.tencent.com/developer/article/write-new")
    time.sleep(8)
    r1 = list_rows(cdp)
    time.sleep(2)
    r2 = list_rows(cdp)
    if r1 != r2:
        raise SystemExit("✗ 独立列箱两次不一致 ⇒ 服务端状态不稳，判 FAIL")
    print("独立列箱: %d 条（两次一致）" % len(r1))
    return r1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--port", type=int, default=9228)
    ap.add_argument("--go", action="store_true", help="真建草稿（缺省干跑）")
    a = ap.parse_args()

    src = a.file if os.path.isabs(a.file) else os.path.join(ROOT, a.file)
    raw = open(src, encoding="utf-8").read()
    title, body = split_md(raw)
    if not title or not body:
        raise SystemExit("✗ 稿件标题或正文为空：%s" % src)
    head = body.strip().split("\n")[0][:24]
    print("标题:", title)
    print("正文: %d 字符 ｜ 首句: %r" % (len(body), head))

    proc, cdp = Y._connect()
    print("9228:", "复用常驻" if proc is None else "本次新起")

    before = independent_rows(cdp)
    hit = [r for r in before if r[1].strip() == title.strip()]
    if hit:
        print("⚠ 草稿箱里已有同标题草稿：draftId=%s" % hit[0][0])
        print("   直接复用（不新建）：--draft %s" % hit[0][0])
        return 0
    print("改前草稿箱 %d 条，无同标题" % len(before))

    if not a.go:
        print("\n（干跑）加 --go 才真建草稿。")
        return 0

    cdp.open("https://cloud.tencent.com/developer/article/write-new")
    time.sleep(7)
    cdp.eval(HOOK_SAVES)
    Y._wait_for(cdp, "!!document.querySelector(%s)" % json.dumps(SEL_TITLE),
                timeout=60)

    print("标题注入:", Y._fill_text(cdp, SEL_TITLE, title))
    r = cdp.eval("""
(function(){
  var e = document.querySelector('.cm-content');
  if (!e) return JSON.stringify({err:'no cm-content'});
  e.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, %s);
  var t = e.innerText || '';
  return JSON.stringify({len:t.length, head:t.slice(0,70), tail:t.slice(-70)});
})()""" % json.dumps(body))
    got = json.loads(r) if isinstance(r, str) and r.startswith("{") else {}
    print("同帧回读: len=%s head=%r" % (got.get("len"), (got.get("head") or "")[:40]))
    if head[:12] not in (got.get("head") or ""):
        raise SystemExit("✗ 正文没进编辑器，停止（未点存草稿）")

    click_save(cdp)
    did = wait_draft_id(cdp, 90)
    print("保存请求:", str(cdp.eval("JSON.stringify(window.__wb_saves||[])"))[:900])
    if not did:
        raise SystemExit("✗ 点存草稿后 URL 没出现 draftId ⇒ 没落库（不猜 id）")

    rows = independent_rows(cdp)
    new = ([r for r in rows if r[0] == did]
           or [r for r in rows if r[1].strip() == title.strip()])
    if not new:
        raise SystemExit("✗ 独立列箱找不到新草稿（标题 %r）⇒ 保存未落库" % title)
    did, rtitle, rplain = new[0]
    body_ok = norm(head[:12]) in norm(rplain)
    print("✓ draftId=%s ｜ 标题命中 ｜ 正文首句命中=%s" % (did, body_ok))
    if not body_ok:
        raise SystemExit("✗ 草稿正文首句不在 plain 里 ⇒ 正文没落库，draftId=%s 不可用" % did)
    print("\n下一步：python -X utf8 scripts/yun_finalize.py --case %s --file %s "
          "--publish --i-said-publish i-said-publish --tc-draft %s"
          % (a.case, a.file, did))
    return 0


if __name__ == "__main__":
    sys.exit(main())
