#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时探针：确认腾讯云新编辑器如何切回脚本可识别的旧编辑器。"""
import sys, time, json
sys.path.insert(0, ".")
from juejin_publish import _CDP

cdp = _CDP(9228)
cdp.open("https://cloud.tencent.com/developer/article/write-new")
time.sleep(4)


def dump(tag):
    info = cdp.eval(r"""
JSON.stringify({
  inputs: [...document.querySelectorAll('input')].map(i => ({
    ph: i.placeholder, name: i.name, id: i.id,
    cls: (i.className||'').toString().slice(0,40),
    val: (i.value||'').slice(0,30)})),
  textareas: [...document.querySelectorAll('textarea')].map(t => ({
    id: t.id, cls: (t.className||'').toString().slice(0,40),
    ph: t.placeholder})),
  editables: [...document.querySelectorAll('[contenteditable=true]')].map(e => ({
    cls: (e.className||'').toString().slice(0,40), tag: e.tagName})),
  hasRich: !!document.querySelector('#cdc-rich-editor'),
  hasTitle: !!document.querySelector('textarea.cdc-article-editor__title-input'),
  impMD: [...document.querySelectorAll('button,span,div,a')]
    .filter(e => (e.textContent||'').trim() === '导入MD').length
})""")
    print("[%s] %s" % (tag, info), flush=True)


dump("before")

# 尝试点击「使用旧版编辑器」
pos = cdp.eval(r"""
(function(){
  var els = [...document.querySelectorAll('*')];
  var e = els.find(x => (x.textContent||'').trim() === '使用旧版编辑器'
                       && x.children.length === 0);
  if (!e) return 'no';
  var r = e.getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
print("oldEditorBtn:", pos, flush=True)
if pos and pos != "no":
    p = json.loads(pos)
    for tp, b in (("mousePressed", 1), ("mouseReleased", 0)):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": p["x"], "y": p["y"], "button": "left",
                  "clickCount": 1, "buttons": b})
        time.sleep(0.08)
    time.sleep(6)
    dump("after-old-editor")

cdp.bws.close()
