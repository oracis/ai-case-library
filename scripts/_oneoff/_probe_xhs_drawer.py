# -*- coding: utf-8 -*-
"""一次性探针：小红书草稿箱抽屉为什么点不开 / 怎么点得开。

不是产品代码，跑完看结论就删。
"""
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x


def wait_ready(sub, key, timeout=40):
    for i in range(int(timeout / 2)):
        t = sub.eval("document.body.innerText", refresh_context=True) or ""
        if key in t:
            return i * 2
    return None


def main():
    sub = x._draft_cdp()
    t0 = wait_ready(sub, "草稿箱(")
    print("页面就绪（出现「草稿箱(」）：%s 秒" % t0)
    if t0 is None:
        print(sub.eval("document.body.innerText")[:300])
        return

    info = x._js(sub, """
      var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
        return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()) &&
               x.getBoundingClientRect().width>0; });
      if(!es.length) return null;
      var e=es[es.length-1], rc=e.getBoundingClientRect();
      var chain=[], p=e;
      for(var i=0;i<4&&p;i++,p=p.parentElement)
        chain.push(p.tagName+'.'+String(p.className||'').slice(0,40));
      return JSON.stringify({cx:Math.round(rc.x+rc.width/2),
                             cy:Math.round(rc.y+rc.height/2), chain:chain});
    """)
    print("抽屉入口：%s" % info)
    if not info:
        return
    cx, cy = info["cx"], info["cy"]

    # A) 合成事件
    x._js(sub, "(function(){%s\n" % x.JS_CLICK_CHAIN + """
      var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
        return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()); });
      jclick(es[es.length-1]); return 'clicked';})()""")
    t = wait_ready(sub, "保存于", 12)
    print("A) 合成事件 jclick：%s" % ("开（%.0fs）" % t if t is not None else "没开"))
    if t is not None:
        return

    # B) 真实鼠标
    for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
        sub.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": cx, "y": cy, "button": "left",
                  "clickCount": 1})
        time.sleep(0.25)
    t = wait_ready(sub, "保存于", 12)
    print("B) 真实鼠标 (%d,%d)：%s" % (cx, cy, "开（%.0fs）" % t
                                       if t is not None else "没开"))


if __name__ == "__main__":
    main()
