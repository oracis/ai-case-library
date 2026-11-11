#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时探针：用「导入MD」文件入口把 markdown 灌进腾讯云 CodeMirror 编辑器。"""
import sys, time, json, os
sys.path.insert(0, ".")
from juejin_publish import _CDP

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "drafts", "newbatch", "tencentcloud", "chessigma.md")
CLEAN = os.path.join(ROOT, "tmp", "_tc_clean_chessigma.md")
os.makedirs(os.path.dirname(CLEAN), exist_ok=True)

# 去掉水印行，标题按首行 # 抽取
lines = open(SRC, encoding="utf-8").read().split("\n")
body = [ln for ln in lines if not ln.startswith("> 平台：")]
title = ""
for ln in lines:
    if ln.startswith("# "):
        title = ln[2:].strip()
        break
open(CLEAN, "w", encoding="utf-8").write("\n".join(body))
print("clean md ->", CLEAN, "title=", title, flush=True)

cdp = _CDP(9228)
cdp.open("https://cloud.tencent.com/developer/article/write-new")
time.sleep(4)

# 填标题
r = cdp.eval(r"""
(function(){
  var el = document.querySelector('textarea.cdc-article-editor__title-input');
  if (!el) return 'no-title';
  var setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  return 'title len=' + el.value.length;
})()""" % json.dumps(title))
print("title:", r, flush=True)

# 点「导入MD」
pos = cdp.eval(r"""
(function(){
  var bs = document.querySelectorAll('button,[role=button],div,span,a');
  var best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent||'').trim() !== '导入MD') continue;
    if (b.offsetParent === null) continue;
    var rr = b.getBoundingClientRect();
    if (rr.width <= 0 || rr.height <= 0) continue;
    var a = rr.width * rr.height;
    if (!best || a < best.a) best = {b: b, a: a,
      x: rr.x + rr.width/2, y: rr.y + rr.height/2};
  }
  if (!best) return 'no';
  return JSON.stringify({x: best.x, y: best.y});
})()""")
print("导入MD btn:", pos, flush=True)
if pos and pos != "no":
    p = json.loads(pos)
    for tp, b in (("mousePressed", 1), ("mouseReleased", 0)):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": p["x"], "y": p["y"], "button": "left",
                  "clickCount": 1, "buttons": b})
        time.sleep(0.08)
    time.sleep(2)

# 找 file input 并设文件
doc = cdp.send("DOM.getDocument")
root = doc["result"]["root"]["nodeId"]
q = cdp.send("DOM.querySelector", {"nodeId": root,
                                   "selector": "input[type=file]"})
print("fileInput nodeId:", q.get("result", {}).get("nodeId"), flush=True)
nid = q.get("result", {}).get("nodeId")
if nid:
    cdp.send("DOM.setFileInputFiles",
             {"nodeId": nid, "files": [CLEAN.replace("\\", "/")]})
    print("set file:", CLEAN, flush=True)
    time.sleep(7)
    head = cdp.eval(r"""
(function(){
  var ed = document.querySelector('.cm-content');
  return ed ? (ed.innerText||'').slice(0, 200) : '(no cm-content)';
})()""")
    print("EDITOR HEAD:", repr(head), flush=True)
    lens = cdp.eval(r"""
(function(){
  var ed = document.querySelector('.cm-content');
  return ed ? ed.innerText.length : -1;
})()""")
    print("EDITOR LEN:", lens, flush=True)

cdp.bws.close()
