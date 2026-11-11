# -*- coding: utf-8 -*-
"""一次性探针 4：把浏览器本地的草稿记录 dump 出来，看保存前后写了什么。

小红书草稿页自己说「草稿存储于当前使用的浏览器本地」。把 localStorage 和
IndexedDB 里跟标题相关的记录翻出来，对比保存前后，确定改动到底写没写。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

DUMP_LS = """(function(){
  var out=[];
  for (var i=0;i<localStorage.length;i++){
    var k=localStorage.key(i); var v=String(localStorage.getItem(k));
    if (v.indexOf('CheckVibe')>=0 || /draft|note|image/i.test(k))
      out.push({key:k, len:v.length, head:v.slice(0,400)});
  }
  return JSON.stringify(out);})()"""

DUMP_DB_LIST = """(async function(){
  if (!indexedDB.databases) return 'no-databases-api';
  var dbs = await indexedDB.databases();
  return JSON.stringify(dbs.map(function(d){return d.name;}));})()"""


def dump(label):
    ls = s.js(DUMP_LS)
    print("== %s localStorage 命中 ==" % label)
    for it in (ls if isinstance(ls, list) else []):
        print("  key=%s len=%s\n    head=%s" % (it["key"], it["len"],
                                                it["head"][:300]))
    dbs = s.js(DUMP_DB_LIST)
    print("== IndexedDB 库：%s ==" % dbs)
    return ls, dbs


s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    dump("打开抽屉后")
finally:
    s.close()
