# -*- coding: utf-8 -*-
"""一次性探针 10：查 service worker 注册、COEP 状态、no-cors 加载。"""
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
      var out = {};
      try {
        var regs = await navigator.serviceWorker.getRegistrations();
        out.sw = regs.map(function(x){return x.scope + ' active=' + !!x.active;});
      } catch(e) { out.sw = 'err ' + e.message; }
      out.coep = self.crossOriginIsolated;
      return JSON.stringify(out);})()""")
    print("SW / isolated：%s" % r)

    # no-cors 图片加载（Image 本来就是 no-cors，这里试 fetch no-cors）
    src = "https://sns-creator-preview.xhscdn.com/spectrum/RDLE58MTVDTayl6SA8ftyLW5MPblBIv4DishNsROU96Ytj0?sign=e4b67d8114e266100630e6f7f7611185&t=6ad6f275"
    r2 = s.js("""(function(){
      return fetch(%s, {mode:'no-cors', credentials:'omit'})
        .then(function(r){ return 'no-cors fetch: ' + r.type + ' ' + r.status; })
        .catch(function(e){ return 'no-cors err: ' + e.message; });})()"""
             % json.dumps(src))
    print(r2)

    # 换一张完全没出现过的 URL 签名（重新让编辑器生成？直接用记录里的老签名）
    src2 = "https://sns-creator-preview.xhscdn.com/spectrum/ZUSbV2BB5Ltu2etP_ayFWXMrSzEx-I1qtn72aDtBHjOSvhc?sign=3095af7f7cbabb57fa5f550ad6881da7&t=6ad6f18e"
    r3 = s.js("""(function(){
      return new Promise(function(res){
        var im = new Image();
        var to = setTimeout(function(){res('timeout');}, 12000);
        im.onload = function(){clearTimeout(to);res('loaded ' + im.naturalWidth);};
        im.onerror = function(){clearTimeout(to);res('error');};
        im.src = %s;
      });})()""" % json.dumps(src2))
    print("老签名 URL 页内加载：%s" % r3)

    # 静态资源对照：加载一个确定存在的 xhscdn 资源
    r4 = s.js("""(function(){
      return new Promise(function(res){
        var im = new Image();
        var to = setTimeout(function(){res('timeout');}, 12000);
        im.onload = function(){clearTimeout(to);res('loaded ' + im.naturalWidth);};
        im.onerror = function(){clearTimeout(to);res('error');};
        im.src = 'https://fe-platform.xhscdn.com/platform/104101l031udehquhiq06chlqrg900000000001asvebiq.png';
      });})()""")
    print("fe-platform 对照加载：%s" % r4)
finally:
    s.close()
