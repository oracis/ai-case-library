# -*- coding: utf-8 -*-
"""一次性探针 11：列出 Service Worker 的 Cache Storage，找 sns-creator-preview
的缓存条目和它们的状态码。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

JS = """(async function(){
  var names = await caches.keys();
  var out = {caches: names, hits: []};
  for (var i=0;i<names.length;i++){
    var c = await caches.open(names[i]);
    var keys = await c.keys();
    out['n_' + names[i]] = keys.length;
    for (var j=0;j<keys.length;j++){
      var u = keys[j].url;
      if (u.indexOf('sns-creator-preview')>=0 || u.indexOf('spectrum')>=0){
        var r = await c.match(keys[j]);
        out.hits.push({cache:names[i], url:u.slice(0,140),
                       status: r ? r.status : null});
      }
    }
  }
  return JSON.stringify(out);})()"""

s = F.Session()
s.open(fresh=True)
try:
    r = s.js(JS)
    print(r)
finally:
    s.close()
