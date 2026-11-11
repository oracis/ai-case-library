#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时探针：点「导入MD」后弹出的结构。"""
import sys, time, json
sys.path.insert(0, ".")
from juejin_publish import _CDP

cdp = _CDP(9228)
cdp.open("https://cloud.tencent.com/developer/article/write-new")
time.sleep(4)


def click_text(text):
    pos = cdp.eval(r"""
(function(){
  var bs = document.querySelectorAll('button,[role=button],div,span,a');
  var best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent||'').trim() !== %s) continue;
    if (b.offsetParent === null) continue;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var a = r.width * r.height;
    if (!best || a < best.a) best = {b: b, a: a,
      x: r.x + r.width/2, y: r.y + r.height/2};
  }
  if (!best) return 'no';
  return JSON.stringify({x: best.x, y: best.y});
})()""" % json.dumps(text))
    print("click %s ->" % text, pos, flush=True)
    if not pos or pos == "no":
        return False
    p = json.loads(pos)
    for tp, b in (("mousePressed", 1), ("mouseReleased", 0)):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": p["x"], "y": p["y"], "button": "left",
                  "clickCount": 1, "buttons": b})
        time.sleep(0.08)
    return True


click_text("导入MD")
time.sleep(4)

info = cdp.eval(r"""
JSON.stringify({
  dialogs: [...document.querySelectorAll('[class*=dialog],[class*=modal],[class*=popup],[role=dialog]')]
    .filter(e => e.offsetParent !== null)
    .map(e => ({cls: (e.className||'').toString().slice(0,60),
                txt: (e.textContent||'').replace(/\s+/g,' ').slice(0,200)})),
  textareas: [...document.querySelectorAll('textarea')].map(t => ({
    id: t.id, cls: (t.className||'').toString().slice(0,50),
    ph: t.placeholder, val: (t.value||'').slice(0,40)})),
  fileInputs: [...document.querySelectorAll('input[type=file]')].length,
  newButtons: [...document.querySelectorAll('button,span,div,a')]
    .filter(e => /(确认|确定|导入|提交|保存)/.test(e.textContent||'')
              && (e.textContent||'').trim().length <= 8
              && e.offsetParent !== null)
    .map(e => (e.textContent||'').trim()).slice(0,15)
})""")
print("after 导入MD:", info, flush=True)
cdp.bws.close()
