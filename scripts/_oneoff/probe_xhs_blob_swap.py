# -*- coding: utf-8 -*-
"""一次性探针 15：注销 SW → 刷新（绕开 SW）→ cors fetch 转 blob → 换 img src。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

SW_KILL = """(async function(){
  var regs = await navigator.serviceWorker.getRegistrations();
  for (var i=0;i<regs.length;i++){ await regs[i].unregister(); }
  return 'killed ' + regs.length;})()"""

BLOB_SWAP = """(async function(){
  var imgs = [].slice.call(document.querySelectorAll('.img-container img.preview'));
  var out = [];
  for (var i=0;i<imgs.length;i++){
    var im = imgs[i];
    var src = im.src || '';
    if (!src || src.indexOf('blob:')===0){ out.push(i+':skip'); continue; }
    if (im.naturalWidth > 0){ out.push(i+':ok-already'); continue; }
    try {
      var r = await fetch(src, {mode:'cors', credentials:'omit'});
      if (!r.ok){ out.push(i+':http'+r.status); continue; }
      var b = await r.blob();
      var ou = URL.createObjectURL(b);
      var test = new Image();
      var ok = await new Promise(function(res){
        var to = setTimeout(function(){res(false);}, 10000);
        test.onload = function(){clearTimeout(to);res(true);};
        test.onerror = function(){clearTimeout(to);res(false);};
        test.src = ou;
      });
      if (ok){ im.src = ou; out.push(i+':swapped'); }
      else { out.push(i+':blobfail'); }
    } catch(e) { out.push(i+':err ' + e.message); }
  }
  return JSON.stringify(out);})()"""


s = F.Session()
s.open(fresh=True)
try:
    print("注销 SW：%s" % s.js(SW_KILL))
    s.sub.send("Page.navigate", {"url": xp.PUB_URL})
    time.sleep(10)
    s.js("1")
    r = s.js("""(async function(){
      var regs = await navigator.serviceWorker.getRegistrations();
      return 'controlled=' + (!!navigator.serviceWorker.controller) +
             ' regs=' + regs.length;})()""")
    print("刷新后：%s" % r)

    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s" % d.get("title", "")[:30])
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    total, broken = s.img_health()
    print("换 src 前：%d 张，裂 %d" % (total, broken))
    r2 = s.js(BLOB_SWAP)
    print("swap 结果：%s" % r2)
    total, broken = s.img_health()
    print("换 src 后：%d 张，裂 %d" % (total, broken))
    s.leave(save=False)
finally:
    s.close()
