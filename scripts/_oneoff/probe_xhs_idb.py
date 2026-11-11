# -*- coding: utf-8 -*-
"""一次性探针 5：读 draft-database-v1 里 CheckVibe 的草稿记录（保存前后对比）。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

JS_STORES = """(async function(){
  function open(){return new Promise(function(res,rej){
    var rq=indexedDB.open('draft-database-v1');
    rq.onsuccess=function(){res(rq.result);}; rq.onerror=function(){rej(rq.error);};});}
  var db=await open();
  var names=[].slice.call(db.objectStoreNames);
  return JSON.stringify(names);})()"""

JS_DUMP_TMPL = """(async function(){
  function open(){return new Promise(function(res,rej){
    var rq=indexedDB.open('draft-database-v1');
    rq.onsuccess=function(){res(rq.result);}; rq.onerror=function(){rej(rq.error);};});}
  var db=await open();
  return await new Promise(function(res,rej){
    var tx=db.transaction(%(store)s,'readonly');
    var st=tx.objectStore(%(store)s);
    var out=[];
    var rq=st.openCursor();
    rq.onsuccess=function(ev){
      var cur=ev.target.result;
      if(!cur){ res(JSON.stringify(out)); return; }
      var v=cur.value;
      var s=''; try{ s=JSON.stringify(v); }catch(e){ s=String(v); }
      if (s.indexOf(%(needle)s)>=0)
        out.push({key:cur.key, len:s.length, val:v});
      cur.continue();
    };
    rq.onerror=function(){rej(rq.error);};
  });})()"""


def dump_store(store, needle):
    js = JS_DUMP_TMPL % {"store": json.dumps(store), "needle": json.dumps(needle)}
    r = s.js(js)
    return r


s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    stores = s.js(JS_STORES)
    print("stores：%s" % stores)
    for st in (stores if isinstance(stores, list) else []):
        hits = dump_store(st, "CheckVibe")
        if isinstance(hits, list) and hits:
            print("== store=%s 命中 %d 条 ==" % (st, len(hits)))
            with open("tmp/draft_db_dump.json", "w", encoding="utf-8") as f:
                json.dump(hits, f, ensure_ascii=False, indent=2)
            for h in hits:
                v = h["val"]
                print("  key=%s len=%s" % (h["key"], h["len"]))
                print("  内容键：%s" % list(v.keys()) if isinstance(v, dict)
                      else "  类型：%s" % type(v))
                # 打出跟图有关的部分
                blob = json.dumps(v, ensure_ascii=False)
                print("  预览：%s" % blob[:800])
finally:
    s.close()
