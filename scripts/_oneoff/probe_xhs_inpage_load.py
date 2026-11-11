# -*- coding: utf-8 -*-
"""一次性探针 9：在编辑器页内 new Image() 加载同款 URL —— 页内到底能不能加载。"""
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
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    info = s.img_info()
    print("图 %d 张（裂 %d）" % (n, sum(1 for i in info if i["nw"] == 0)))
    src = info[0].get("src") or ""
    print("测试 src：%s" % src)

    r = s.js("""(function(){
      return new Promise(function(res){
        var im = new Image();
        var done = false;
        var to = setTimeout(function(){ if(!done){done=true;res('timeout');} }, 15000);
        im.onload = function(){ if(!done){done=true;clearTimeout(to);
          res('loaded ' + im.naturalWidth + 'x' + im.naturalHeight);} };
        im.onerror = function(e){ if(!done){done=true;clearTimeout(to);
          res('error ' + (e && e.message || ''));} };
        im.referrerPolicy = 'no-referrer';
        im.src = %s;
      });})()""" % json.dumps(src))
    print("页内加载结果：%s" % r)

    # 再试无 referer 差异：默认 referrerPolicy
    r2 = s.js("""(function(){
      return new Promise(function(res){
        var im = new Image();
        var to = setTimeout(function(){res('timeout');}, 15000);
        im.onload = function(){clearTimeout(to);res('loaded ' + im.naturalWidth);};
        im.onerror = function(){clearTimeout(to);res('error');};
        im.src = %s;
      });})()""" % json.dumps(src))
    print("默认 referer 加载：%s" % r2)

    # fetch 看状态码（带 cookie）
    r3 = s.js("""(function(){
      return fetch(%s, {credentials:'include'}).then(function(r){
        return 'fetch ' + r.status + ' type=' + r.type;
      }).catch(function(e){ return 'fetch-err ' + e.message; });})()"""
             % json.dumps(src))
    print("页内 fetch：%s" % r3)
finally:
    s.close()
