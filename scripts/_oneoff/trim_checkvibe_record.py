# -*- coding: utf-8 -*-
"""一次性清理：把 CheckVibe 草稿记录里实验残留的多余图片条目裁回 5 张。

今天反复试跑给记录攒了 3 条多余条目（2 条 card-1 重复 + 1 条没存完的
card-2 半成品）。只删 imgList 尾部多余项，其余字段一律不动；
裁前先在 tmp/ 留完整备份。
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

JS = """(async function(){
  function open(){return new Promise(function(res,rej){
    var rq=indexedDB.open('draft-database-v1');
    rq.onsuccess=function(){res(rq.result);}; rq.onerror=function(){rej(rq.error);};});}
  var db=await open();
  return await new Promise(function(res,rej){
    var tx=db.transaction('image-draft','readwrite');
    var st=tx.objectStore('image-draft');
    var rq=st.openCursor();
    rq.onsuccess=function(ev){
      var cur=ev.target.result;
      if(!cur){ res('notfound'); return; }
      var v=cur.value; var s='';
      try{ s=JSON.stringify(v); }catch(e){}
      if (s.indexOf('CheckVibe')<0){ cur.continue(); return; }
      var before = v.content.draftStore.imgList.length;
      var keep = %(keep)d;
      if (before <= keep){ res('already ' + before); return; }
      var removed = v.content.draftStore.imgList.splice(keep);
      v.content.draftStore.imgList = v.content.draftStore.imgList.slice(0, keep);
      var r=cur.update(v);
      r.onsuccess=function(){res('trimmed ' + before + '->' + keep +
        ' removed=' + JSON.stringify(removed.map(function(x){
          return (x.fileId||'').slice(0,40);})));};
      r.onerror=function(){res('puterr');};
    };
    rq.onerror=function(){rej(rq.error);};
  });})()"""

s = F.Session()
s.open(fresh=True)
try:
    # 备份
    dump = s.js("""(async function(){
      function open(){return new Promise(function(res,rej){
        var rq=indexedDB.open('draft-database-v1');
        rq.onsuccess=function(){res(rq.result);};
        rq.onerror=function(){rej(rq.error);};});}
      var db=await open();
      return await new Promise(function(res,rej){
        var tx=db.transaction('image-draft','readonly');
        var rq=tx.objectStore('image-draft').openCursor();
        rq.onsuccess=function(ev){
          var cur=ev.target.result;
          if(!cur){ res('[]'); return; }
          var v=cur.value; var str='';
          try{ str=JSON.stringify(v); }catch(e){}
          if (str.indexOf('CheckVibe')>=0){ res(JSON.stringify(v)); return; }
          cur.continue();
        };
        rq.onerror=function(){rej(rq.error);};
      });})()""")
    rec = dump if isinstance(dump, dict) else json.loads(dump)
    os.makedirs("tmp", exist_ok=True)
    with open("tmp/draft_db_backup_checkvibe.json", "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print("备份：tmp/draft_db_backup_checkvibe.json（imgList %d 条）"
          % len(rec["content"]["draftStore"]["imgList"]))
    r = s.js(JS % {"keep": 5})
    print("裁剪：%s" % r)
finally:
    s.close()
