# -*- coding: utf-8 -*-
"""一次性探针：抽屉卡片封面的 DOM 结构（img 还是 background-image？）。"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
from xhs_draft_fix_images import Session

s = Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    r = s.js("""(function(){
      var imgs = [].slice.call(document.querySelectorAll('img'));
      var broken = imgs.filter(function(im){
        return im.naturalWidth===0 && (im.src||'').indexOf('http')===0;});
      var bgs = [].slice.call(document.querySelectorAll('*')).filter(
        function(e){
          var bi = (getComputedStyle(e).backgroundImage||'');
          return bi.indexOf('sns-creator-preview')>=0;});
      var card = [].slice.call(document.querySelectorAll('*')).filter(
        function(x){ return (x.innerText||'').indexOf('保存于')>=0 &&
          (x.innerText||'').indexOf('编辑')>=0;});
      var smallest = card.filter(function(c){
        return !card.some(function(o){return o!==c && c.contains(o);});})[0];
      return JSON.stringify({
        img_total: imgs.length,
        img_broken: broken.length,
        img_broken_srcs: broken.slice(0,3).map(function(im){return im.src.slice(0,90);}),
        bg_els: bgs.length,
        bg_sample: bgs.slice(0,2).map(function(e){
          return {tag:e.tagName, cls:String(e.className).slice(0,50),
                  bi:getComputedStyle(e).backgroundImage.slice(0,120)};}),
        card_html: smallest ? smallest.outerHTML.slice(0, 900) : 'none'
      });})()""")
    print(json.dumps(r, ensure_ascii=False, indent=1) if isinstance(r, dict) else r)
finally:
    s.close()
