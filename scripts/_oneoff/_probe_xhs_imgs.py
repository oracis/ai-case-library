# -*- coding: utf-8 -*-
"""一次性探针：编辑器里那「15 张裂 10 张」到底裂的是笔记图还是页面 UI 图。

6 条草稿跑出来的数字一模一样（都是 15 张裂 10 张），太整齐了 ——
整齐得像常量，说明 `_img_health` 数到的多半是页面 chrome（头像/图标/背景），
不是笔记内容图。列出来看一眼。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x

PROBE = """
(function(){
  var o=[];
  [].slice.call(document.querySelectorAll('img')).forEach(function(im){
    var rc=im.getBoundingClientRect();
    if(rc.width<20) return;
    var p=im.parentElement, chain=[];
    for(var i=0;i<3&&p;i++,p=p.parentElement)
      chain.push(p.tagName+'.'+String(p.className||'').slice(0,32));
    o.push({src:(im.src||'').slice(0,70), nw:im.naturalWidth,
            w:Math.round(rc.width), h:Math.round(rc.height),
            x:Math.round(rc.x), y:Math.round(rc.y),
            cls:String(im.className).slice(0,28),
            up:chain.join('<')});
  });
  return JSON.stringify(o);})()
"""


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    d, _ = x.pick_draft(drafts)
    print("目标：%s | %s" % (d.get("saved"), d.get("title")))
    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("编辑器：%s %r" % (ok, got))
    if not ok:
        return
    time.sleep(4)
    r = sub.eval(PROBE, refresh_context=True)
    imgs = json.loads(r) if r else []
    print("共 %d 张（width>=20）" % len(imgs))
    for i, im in enumerate(imgs, 1):
        print("[%2d] nw=%-5s %sx%s @(%s,%s) cls=%s" % (
            i, im["nw"], im["w"], im["h"], im["x"], im["y"], im["cls"]))
        print("     src=%s" % im["src"])
        print("     up =%s" % im["up"])


if __name__ == "__main__":
    main()
