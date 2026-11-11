# -*- coding: utf-8 -*-
"""一次性探针 12（终极实验）：注销 SW + 清缓存 → 刷新 → 看图是否复活。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

s = F.Session()
s.open(fresh=True)
try:
    r = s.js("""(async function(){
      var regs = await navigator.serviceWorker.getRegistrations();
      for (var i=0;i<regs.length;i++){ await regs[i].unregister(); }
      var names = await caches.keys();
      for (var j=0;j<names.length;j++){ await caches.delete(names[j]); }
      return 'unregistered ' + regs.length + ', cleared ' + names.length;})()""")
    print(r)

    # 刷新页面
    s.sub.send("Page.navigate", {"url": xp.PUB_URL})
    time.sleep(10)
    s.js("1")

    # 检查 SW 是否被重新注册
    r2 = s.js("""(async function(){
      var regs = await navigator.serviceWorker.getRegistrations();
      return 'sw after reload: ' + regs.length;})()""")
    print(r2)

    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s" % d.get("title", "")[:30])
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, settled = s.wait_settled()
    total, broken = s.img_health()
    print("结果：%d 张，裂 %d 张（settled=%s）" % (total, broken, settled))
    s.leave(save=False)
finally:
    s.close()
