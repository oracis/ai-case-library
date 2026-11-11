# -*- coding: utf-8 -*-
"""一次性探针：`ce-btn bg-red` 是不是藏在 shadow DOM 里。

抽屉编辑器里主 document 一个 ce-btn 都没有，但按钮肯定存在（用户从
devtools 复制到了真实 HTML）。最后一个可能：**shadow DOM** ——
document.querySelectorAll 不穿透 shadow root。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x

PROBE = """
(function(){
  var out={here:location.href, ce:[], pub:[], shadows:0, html_len:0};
  function walk(root, path){
    var list=[];
    try { list = [].slice.call(root.querySelectorAll('*')); } catch(e){ return; }
    list.forEach(function(e){
      if(e.shadowRoot){
        out.shadows++;
        walk(e.shadowRoot, path + '>' + e.tagName + '#shadow');
      }
      var c = String(e.className||'');
      var t = (e.innerText||e.textContent||'').trim();
      if(c.indexOf('bg-red')>=0 || c.indexOf('ce-btn')>=0){
        var rc={};
        try { var r=e.getBoundingClientRect();
              rc={x:Math.round(r.x), y:Math.round(r.y),
                  w:Math.round(r.width), h:Math.round(r.height)}; } catch(err){}
        out.ce.push({tag:e.tagName, c:c.slice(0,50), t:t.slice(0,10),
                     path:path, rect:rc,
                     da:e.getAttribute?e.getAttribute('aria-disabled'):null,
                     bu:e.getAttribute?e.getAttribute('aria-busy'):null});
      }
      if(e.tagName==='BUTTON' && t.indexOf('发布')>=0){
        var r2=e.getBoundingClientRect();
        out.pub.push({c:String(e.className).slice(0,50), t:t.slice(0,10),
                      path:path, x:Math.round(r2.x), y:Math.round(r2.y),
                      w:Math.round(r2.width), h:Math.round(r2.height),
                      da:e.getAttribute('aria-disabled'),
                      bu:e.getAttribute('aria-busy')});
      }
    });
  }
  walk(document, 'doc');
  out.html_len = document.documentElement.outerHTML.length;
  return JSON.stringify(out);})()
"""


def show(sub, tag):
    time.sleep(4)
    sub.eval("1", refresh_context=True)
    r = sub.eval(PROBE, refresh_context=True)
    print("=== %s" % tag)
    try:
        o = json.loads(r)
    except (TypeError, ValueError):
        print("  eval 异常：%r" % (r,))
        return
    print("  URL:", o["here"], "| html 长度:", o["html_len"],
          "| shadow root 数:", o["shadows"])
    print("  ce-btn/bg-red:", json.dumps(o["ce"], ensure_ascii=False))
    print("  含「发布」button:", json.dumps(o["pub"], ensure_ascii=False))


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    d, why = x.pick_draft(drafts)
    print("目标草稿：%s | %s" % (d.get("saved"), d.get("title")))
    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("抽屉编辑器：%s %r" % (ok, got))
    if ok:
        show(sub, "抽屉里点「编辑」打开的编辑器（穿透 shadow DOM）")


if __name__ == "__main__":
    main()
