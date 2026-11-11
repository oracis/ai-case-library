# -*- coding: utf-8 -*-
"""一次性探针：删图按钮「坐标对、点了没反应」到底卡在哪。

诊断显示 close-btn `visible`、`elementFromPoint` 命中的就是它里面的 path，
坐标也对，但张数不降。要区分两件事：
  ① 鼠标事件压根没送达（CDP 事件问题）
  ② 事件送达了，但 Vue 不认（要 pointer 事件 / 要 mousedown 序列）
所以：先在按钮上挂监听器记录收到了什么事件，再点，看记录。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import xhs_draft_fix_images as fx


def main():
    s = fx.Session()
    s.open(fresh=True)
    print("tab:", s.tid)
    s.open_drawer()
    drafts = s.list_drafts()
    print("草稿 %d 条" % len(drafts))
    title = sys.argv[1] if len(sys.argv) > 1 else drafts[0]["title"]
    print("打开：%s" % title)
    if not s.open_editor(title):
        print("编辑器没打开")
        return
    time.sleep(3)
    info = s.img_info()
    print("图 %d 张：%s" % (len(info), json.dumps(info, ensure_ascii=False)))
    if not info:
        return

    # 1) close-btn 结构 + 计算样式
    print("\n--- close-btn 结构")
    print(s.js("""
(function(){
  var it=document.querySelector('.img-container');
  if(!it) return 'no .img-container';
  var cb=it.querySelector('.close-btn');
  if(!cb) return 'no .close-btn';
  var cs=getComputedStyle(cb);
  return JSON.stringify({
    html: cb.outerHTML.slice(0,300),
    parent: it.outerHTML.slice(0,400),
    vis: cs.visibility, display: cs.display, opacity: cs.opacity,
    pe: cs.pointerEvents, z: cs.zIndex, pos: cs.position
  });})()
"""))

    # 2) 挂监听器，记录来了什么事件
    s.js("""
(function(){
  window.__LOG=[];
  var cb=document.querySelector('.img-container .close-btn');
  if(!cb) return 'no btn';
  ['mousedown','mouseup','click','pointerdown','pointerup','touchstart']
    .forEach(function(t){
      cb.addEventListener(t, function(e){
        window.__LOG.push({on:cb.tagName, t:t,
                           trust:e.isTrusted, target:e.target.tagName});
      }, true);
    });
  var par=cb.parentElement;
  ['mousedown','click','pointerdown'].forEach(function(t){
    par.addEventListener(t, function(e){
      window.__LOG.push({on:'PARENT.'+String(par.className).slice(0,20), t:t,
                         trust:e.isTrusted, target:e.target.tagName});
    }, true);
  });
  return 'listeners ok';})()
""")

    # 3) hover 出按钮，再点
    it = info[0]
    s.hover(4, 4)
    time.sleep(0.3)
    s.hover(it["x"], it["y"])
    time.sleep(0.6)
    d = s.js(fx.JS_CLOSE_AT)
    print("\n--- close-btn 位置：%s" % json.dumps(d, ensure_ascii=False))
    if not isinstance(d, dict) or not d.get("onBtn"):
        return
    cx, cy = d["cx"], d["cy"]
    s.hover(cx, cy)
    time.sleep(0.3)
    s.click_at(cx, cy, "删图")
    time.sleep(1.5)
    print("--- 事件记录：%s" % json.dumps(s.js("JSON.stringify(window.__LOG)"),
                                          ensure_ascii=False))
    print("--- 点击后张数：%d" % len(s.img_info()))

    # 4) 换一招：React/Vue 合成事件三连 + 直接调 .click()
    print("\n--- 试试 element.click()")
    s.js("""
(function(){
  var cb=document.querySelector('.img-container .close-btn');
  if(!cb) return 'none';
  cb.click();
  return 'clicked';})()
""")
    time.sleep(1.5)
    print("张数：%d" % len(s.img_info()))
    print("事件记录：%s" % json.dumps(s.js("JSON.stringify(window.__LOG)"),
                                      ensure_ascii=False))


if __name__ == "__main__":
    main()
