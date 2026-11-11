# -*- coding: utf-8 -*-
"""一次性探针：诊断删图为什么删不动。

只开一条草稿的编辑器，把删图链路的每一步状态打出来：
hover 前后 close-btn 的可见性、elementFromPoint 命中、真点之后张数变没变。
不动任何数据（最后直接离开，不点删除之外的东西）。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # scripts/ 目录（wechat_publish 等都在那）
sys.path.insert(0, _HERE)

import wechat_publish as wp
import xhs_publish as xp
import xhs_draft_fix_images as F

s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    print("草稿 %d 条" % len(drafts))
    d = drafts[0]
    print("目标：%s（%s）" % (d.get("title", "")[:30], d.get("saved", "")))
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, settled = s.wait_settled()
    print("图 %d 张（settled=%s）" % (n, settled))

    info = s.img_info()
    for i, it in enumerate(info):
        print("  [%d] nw=%s src=%s pos=(%d,%d) close=(%d,%d)"
              % (i, it.get("nw"), (it.get("src") or "")[:60],
                 it["x"], it["y"], it["cx"], it["cy"]))

    # 逐张试 hover → 读 close-btn 状态
    for idx in range(min(3, len(info))):
        it = info[idx]
        s.hover(4, 4)
        time.sleep(0.3)
        s.hover(it["x"], it["y"])
        time.sleep(0.6)
        d1 = s.js("(%s)(%d)" % (F.JS_CLOSE_AT, idx))
        print("hover图[%d] → %s" % (idx, d1))
        # 再直接 hover 到 close-btn 坐标上
        if isinstance(d1, dict):
            s.hover(d1["cx"], d1["cy"])
            time.sleep(0.6)
            d2 = s.js("(%s)(%d)" % (F.JS_CLOSE_AT, idx))
            print("hover按钮[%d] → %s" % (idx, d2))
        # 顺便把 close-btn 的内联可见性查一下（可能不止 visibility 一种隐藏法）
        probe = s.js("""(function(i){
          var its=[].slice.call(document.querySelectorAll('.img-container'));
          var it=its[i]; if(!it) return null;
          var cb=it.querySelector('.close-btn'); if(!cb) return 'no-btn';
          var cs=getComputedStyle(cb); var r=cb.getBoundingClientRect();
          var masks=[].slice.call(it.querySelectorAll('*')).map(function(e){
            var c=getComputedStyle(e);
            if(c.visibility==='hidden'||c.opacity==='0'||c.display==='none')
              return e.className+'|v='+c.visibility+'|o='+c.opacity+'|d='+c.display;
            return null;}).filter(Boolean);
          return JSON.stringify({w:r.width,h:r.height,vis:cs.visibility,
            opacity:cs.opacity, display:cs.display, pointer:cs.pointerEvents,
            hiddenDeps:masks.slice(0,6)});})(%d)""" % idx)
        print("按钮样式[%d] → %s" % (idx, probe))
        # 真点一下第一张，看张数变不变
        if idx == 0:
            if not isinstance(d1, dict):
                print("JS_CLOSE_AT 返回 %r —— 取点链路没通，不盲点" % (d1,))
            else:
                before = len(s.img_info())
                hit = s.click_at(d1["cx"], d1["cy"], "删图试射")
                time.sleep(1.2)
                after = len(s.img_info())
                print("点击后 %d → %d 张（click=%s）" % (before, after, hit))
                if after == before:
                    # 试试合成点击兜底
                    s.js("(function(){var its=[].slice.call("
                         "document.querySelectorAll('.img-container'));"
                         "var cb=its[0]?its[0].querySelector('.close-btn'):null;"
                         "if(!cb) return 0; %s jclick(cb); return 1;})()"
                         % F.JS_CLICK_CHAIN)
                    time.sleep(1.2)
                    print("jclick 兜底后 → %d 张" % len(s.img_info()))
    s.leave()
finally:
    s.close()
