# -*- coding: utf-8 -*-
"""小红书草稿箱抽屉打不开的现场诊断：入口元素、命中测试、点击前后 body 文本。"""
from __future__ import annotations

import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chrome_cdp_launch as cl  # noqa: E402
import wechat_publish as wp  # noqa: E402

PORT = 9223
PUB_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"

JS_ENTRIES = """(function(){
  var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()); });
  return JSON.stringify(es.map(function(e){
    var r=e.getBoundingClientRect();
    var cs=getComputedStyle(e);
    return {tag:e.tagName, cls:(e.className||'').toString().slice(0,60),
            text:(e.innerText||'').trim().slice(0,30),
            x:Math.round(r.x), y:Math.round(r.y),
            w:Math.round(r.width), h:Math.round(r.height),
            vis:cs.visibility, disp:cs.display, pe:cs.pointerEvents};
  }));})()"""

JS_HIT = """(function(){
  var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()) &&
           x.getBoundingClientRect().width>0; });
  if(!es.length) return 'no-entry';
  var e=es[es.length-1], r=e.getBoundingClientRect();
  var cx=Math.round(r.x+r.width/2), cy=Math.round(r.y+r.height/2);
  var hit=document.elementFromPoint(cx,cy);
  var chain=[];
  var n=hit;
  for(var i=0;i<4 && n;i++){
    chain.push(n.tagName+'.'+((n.className||'').toString().slice(0,40)));
    n=n.parentElement;
  }
  return JSON.stringify({pt:[cx,cy], chain:chain,
     isSelf: hit===e || e.contains(hit) || hit.contains(e)});})()"""

JS_BODY = """(function(){
  var t=(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300);
  return JSON.stringify({url:location.href, title:document.title, body:t});})()"""

JS_DRAWER_UP = ("(function(){return (document.body.innerText||'')"
                ".indexOf('图文笔记(')>=0 ? 1 : 0;})()")


def js(sub, expr):
    r = sub.eval(expr, refresh_context=True)
    if isinstance(r, str):
        try:
            return __import__("json").loads(r)
        except Exception:
            return r
    return r


def main():
    wp._autostart_chrome_if_needed(PORT, profile=cl.PLAT_PROFILE)
    cdp = wp.CDP(PORT)
    tid = cdp.new_target(PUB_URL)["id"]
    sub = wp.CDP(PORT)
    sub.connect_target(tid)
    sub._own_tid = tid
    time.sleep(8)
    print("== 页面状态 ==")
    print(js(sub, JS_BODY))
    print("== 草稿箱入口元素 ==")
    print(js(sub, JS_ENTRIES))
    print("== 入口中心点命中 ==")
    print(js(sub, JS_HIT))
    print("== 抽屉已开? ==", js(sub, JS_DRAWER_UP))

    # 视口尺寸 + 加宽视口后重测
    print("== 视口 ==", js(sub, "(function(){return JSON.stringify({w:window.innerWidth,h:window.innerHeight,dpr:window.devicePixelRatio,screen:screen.width+'x'+screen.height});})()"))
    sub.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2)
    print("== 加宽后视口 ==", js(sub, "(function(){return JSON.stringify({w:window.innerWidth,h:window.innerHeight});})()"))
    print("== 加宽后入口 ==", js(sub, JS_ENTRIES))
    print("== 加宽后命中 ==", js(sub, JS_HIT))
    print("== 加宽后抽屉已开? ==", js(sub, JS_DRAWER_UP))

    # 真实鼠标点一次入口（moved → pressed → released）
    hit = js(sub, JS_HIT)
    if isinstance(hit, dict) and hit.get("pt"):
        cx, cy = hit["pt"]
        print("== 真实点击 (%d,%d) ==" % (cx, cy))
        sub.send("Input.dispatchMouseEvent",
                 {"type": "mouseMoved", "x": cx, "y": cy})
        time.sleep(0.2)
        sub.send("Input.dispatchMouseEvent",
                 {"type": "mousePressed", "x": cx, "y": cy,
                  "button": "left", "clickCount": 1})
        time.sleep(0.12)
        sub.send("Input.dispatchMouseEvent",
                 {"type": "mouseReleased", "x": cx, "y": cy,
                  "button": "left", "clickCount": 1})
        time.sleep(4)
        print("点击后抽屉已开?", js(sub, JS_DRAWER_UP))
        print("点击后 body:", js(sub, JS_BODY))
    try:
        op = __import__("urllib.request", fromlist=["x"])
        import urllib.request
        op2 = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        op2.open("http://127.0.0.1:%d/json/close/%s" % (PORT, tid), timeout=3).read()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
