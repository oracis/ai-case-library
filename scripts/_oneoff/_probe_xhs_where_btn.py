# -*- coding: utf-8 -*-
"""一次性探针：`ce-btn bg-red`「发布」按钮到底长在哪个页面上。

抽屉里的编辑器（URL 还是 target=draft）实测一个 ce-btn 都没有，
所以要么按钮在 iframe 里，要么用户截的是**新建发布页**。两个都查。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x

PROBE = """
(function(){
  var o={here:location.href, iframes:[], red:[], pub:[]};
  [].slice.call(document.querySelectorAll('iframe')).forEach(function(f){
    var rc=f.getBoundingClientRect();
    o.iframes.push({src:(f.src||'').slice(0,80),
                    w:Math.round(rc.width), h:Math.round(rc.height)});
  });
  [].slice.call(document.querySelectorAll('[class*=bg-red],[class*=ce-btn]'))
    .forEach(function(e){
      var rc=e.getBoundingClientRect();
      o.red.push({tag:e.tagName, c:String(e.className).slice(0,40),
                  t:(e.innerText||'').trim().slice(0,10),
                  x:Math.round(rc.x), y:Math.round(rc.y),
                  w:Math.round(rc.width), h:Math.round(rc.height),
                  da:e.getAttribute('aria-disabled'),
                  bu:e.getAttribute('aria-busy')});
    });
  [].slice.call(document.querySelectorAll('button')).forEach(function(b){
    var t=(b.innerText||'').trim();
    if(t.indexOf('发布')<0) return;
    var rc=b.getBoundingClientRect();
    o.pub.push({c:String(b.className).slice(0,40), t:t.slice(0,10),
                x:Math.round(rc.x), y:Math.round(rc.y),
                w:Math.round(rc.width), h:Math.round(rc.height),
                da:b.getAttribute('aria-disabled'),
                bu:b.getAttribute('aria-busy')});
  });
  return JSON.stringify(o);})()
"""


def show(sub, tag):
    time.sleep(4)
    sub.eval("1", refresh_context=True)
    r = sub.eval(PROBE, refresh_context=True)
    print("=== %s" % tag)
    try:
        o = json.loads(r)
    except (TypeError, ValueError):
        print("  eval 返回异常：%r" % (r,))
        return None
    print("  URL:", o["here"])
    print("  iframe:", json.dumps(o["iframes"], ensure_ascii=False))
    print("  bg-red/ce-btn:", json.dumps(o["red"], ensure_ascii=False))
    print("  含「发布」的 button:", json.dumps(o["pub"], ensure_ascii=False))
    return o


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    d, _ = x.pick_draft(drafts)
    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("抽屉编辑器：%s %r" % (ok, got))
    show(sub, "A) 抽屉里点「编辑」打开的编辑器")

    # B) 新建图文发布页
    sub.send("Page.navigate", {"url": x.PUB_URL})
    show(sub, "B) 新建图文发布页 source=official")


if __name__ == "__main__":
    main()
