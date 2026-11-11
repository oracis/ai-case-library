# -*- coding: utf-8 -*-
"""一次性探针：B站编辑器里「发布」按钮到底能不能点。

要回答的问题只有一个：**加上封面之后，发布按钮是不是就不再 disabled**。
之前的结论是「headless 下恒 disabled（封面校验）」，但那个结论是在
`cover_via_api` 落地**之前**得出的，没验过「有封面的草稿」。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import bilibili_publish as bp          # noqa: E402
import bili_draft_api as bapi          # noqa: E402
import wechat_publish as wp           # noqa: E402


def oldest_unpublished():
    cdp = wp.CDP(bp.PUB_PORT)
    bp._api_tab(cdp)
    drafts = bapi.list_drafts(cdp)
    m = bp._load_manifest()
    done = set()
    for _c, d in m.items():
        b = d.get("bilibili") or {}
        if b.get("state") == "published" and b.get("remote_id"):
            done.add(str(b["remote_id"]))
    pool = [d for d in drafts if str(d.get("article_id")) not in done]
    pool.sort(key=lambda d: d.get("mtime") or 0)
    print("草稿 %d 条，清单已标 published %d 条，未发布候选 %d 条"
          % (len(drafts), len(done), len(pool)))
    for d in pool[:5]:
        print("   aid=%s mtime=%s cover=%s《%s》"
              % (d.get("article_id"), d.get("mtime"),
                 "有" if bp._cover_of(d) else "无", (d.get("title") or "")[:36]))
    return pool[0] if pool else None


JS_BTN = """
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(f){return (f.src||'').indexOf('read-editor')>=0;});
  var d=f&&f.contentDocument?f.contentDocument:null;
  if(!d) return JSON.stringify({err:'no-iframe'});
  var out=[];
  [].slice.call(d.querySelectorAll('button,[role=button],a')).forEach(function(b){
    var t=(b.innerText||'').trim();
    if(!t || t.length>8) return;
    var rc=b.getBoundingClientRect();
    if(rc.width<4) return;
    out.push({t:t, cls:String(b.className).slice(0,60),
              dis:b.disabled===true,
              aria:b.getAttribute('aria-disabled'),
              x:Math.round(rc.x), y:Math.round(rc.y),
              w:Math.round(rc.width), h:Math.round(rc.height)});
  });
  return JSON.stringify(out);})()
"""

JS_HINT = """
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(f){return (f.src||'').indexOf('read-editor')>=0;});
  var d=f&&f.contentDocument?f.contentDocument:null;
  if(!d) return JSON.stringify({err:'no-iframe'});
  var cov=!!d.querySelector('.selected-cover');
  var txt=(d.body.innerText||'');
  var bad=[];
  ['未设置','请设置','必填','请选择','封面'].forEach(function(k){
    if(txt.indexOf(k)>=0) bad.push(k);
  });
  return JSON.stringify({cover:cov, hints:bad, tail:txt.slice(-260)});})()
"""


def main():
    d = oldest_unpublished()
    if not d:
        print("没有未发布草稿")
        return
    aid = d.get("article_id")
    cdp = wp.CDP(bp.PUB_PORT)
    bp._close_stale_bili_tabs(cdp)
    tid = cdp.new_target(bp.BILI_EDIT_URL % aid)["id"]
    cdp.connect_target(tid)
    try:
        ok = False
        for attempt in (1, 2):
            time.sleep(7 if attempt == 1 else 4)
            if bp._wait_iframe_ready(cdp, "read-editor", bp.SEL["body"], 30):
                ok = True
                break
            print("  iframe 未就绪，重试 %d" % attempt)
        if not ok:
            print("  编辑器 iframe 没起来")
            return
        time.sleep(3)
        print("\n== 封面状态 ==")
        print("   封面已经在 DOM 里：", bp._cover_present(cdp, tid))
        print("   体检:", json.dumps(json.loads(cdp.eval(JS_HINT) or "{}"),
                                    ensure_ascii=False)[:500])
        print("\n== 可见按钮 ==")
        btns = json.loads(cdp.eval(JS_BTN) or "[]")
        for b in btns:
            mark = ""
            if b.get("t") == "发布":
                mark = "  <<<< 发布"
            print("   %-6s cls=%-40s dis=%s aria=%s @(%s,%s)%s"
                  % (b.get("t"), b.get("cls"), b.get("dis"), b.get("aria"),
                     b.get("x"), b.get("y"), mark))
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


if __name__ == "__main__":
    main()
