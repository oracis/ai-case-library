# -*- coding: utf-8 -*-
"""一次性探针 14：验证「换 fe-platform URL」显示修复方案。

① 页内 cors fetch（不带凭证）抓图转 blob → 能否显示（保底方案）
② 直接改 IDB 记录 entry[0] 的 url/previewUrl → fe-platform 地址 →
   重开编辑器看第 0 张是否加载（fileId 不动，发布不受影响）
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

FE_URL = "https://fe-platform.xhscdn.com/platform/104101l031udehquhiq06chlqrg900000000001asvebiq.png"

DUMP_ONE = """(async function(){
  function open(){return new Promise(function(res,rej){
    var rq=indexedDB.open('draft-database-v1');
    rq.onsuccess=function(){res(rq.result);}; rq.onerror=function(){rej(rq.error);};});}
  var db=await open();
  return await new Promise(function(res,rej){
    var tx=db.transaction('image-draft','readonly');
    var rq=tx.objectStore('image-draft').openCursor();
    rq.onsuccess=function(ev){
      var cur=ev.target.result;
      if(!cur){ res(null); return; }
      var s=''; try{ s=JSON.stringify(cur.value); }catch(e){}
      if (s.indexOf('CheckVibe')>=0){ res(JSON.stringify({k:cur.key,
        url: cur.value.content.draftStore.imgList[0].url,
        previewUrl: cur.value.content.draftStore.imgList[0].previewUrl})); return; }
      cur.continue();
    };
    rq.onerror=function(){rej(rq.error);};
  });})()"""

PATCH = """(async function(){
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
      var s=''; try{ s=JSON.stringify(cur.value); }catch(e){}
      if (s.indexOf('CheckVibe')>=0){
        var im=cur.value.content.draftStore.imgList[0];
        im.url=%(fe)s; im.previewUrl=%(fe)s;
        var r=cur.update(cur.value);
        r.onsuccess=function(){res('patched');};
        r.onerror=function(){res('puterr');};
        return;
      }
      cur.continue();
    };
    rq.onerror=function(){rej(rq.error);};
  });})()""" % {"fe": json.dumps(FE_URL)}


def load_inpage(sub, url):
    return sub.eval("""(function(){
      return new Promise(function(res){
        var im = new Image();
        var to = setTimeout(function(){res('timeout');}, 12000);
        im.onload = function(){clearTimeout(to);res('loaded ' + im.naturalWidth);};
        im.onerror = function(){clearTimeout(to);res('error');};
        im.src = %s;
      });})()""" % json.dumps(url), refresh_context=True)


s = F.Session()
s.open(fresh=True)
try:
    # ① cors fetch + blob
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    info = s.img_info()
    src = info[0].get("src")
    r = s.js("""(async function(){
      try {
        var r = await fetch(%s, {mode:'cors', credentials:'omit'});
        var b = await r.blob();
        var ou = URL.createObjectURL(b);
        return await new Promise(function(res){
          var im = new Image();
          var to = setTimeout(function(){res('blob-load timeout');}, 12000);
          im.onload = function(){clearTimeout(to);
            res('blob-loaded ' + im.naturalWidth + 'x' + im.naturalHeight +
                ' blob=' + b.size + 'B');};
          im.onerror = function(){clearTimeout(to);res('blob-err');};
          im.src = ou;
        });
      } catch(e) { return 'cors-err ' + e.message; }})()""" % json.dumps(src))
    print("① cors+blob：%s" % r)
    s.leave(save=False)

    # ② IDB 补丁
    cur = s.js(DUMP_ONE)
    print("补丁前 entry[0] url：%s" % (cur or {}).get("url", "")[:100])
    r2 = s.js(PATCH)
    print("补丁结果：%s" % r2)
    time.sleep(2)

    # 重开编辑器看第 0 张
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    cur_drafts = s.list_drafts()
    want = xp.norm_title(d.get("title") or "")
    now = next((x for x in cur_drafts
                if xp.norm_title(x.get("title") or "") == want), None)
    ok, _ = s.open_editor(now.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    info = s.img_info()
    for i, it in enumerate(info[:3]):
        print("  [%d] nw=%s src=%s" % (i, it.get("nw"),
                                       (it.get("src") or "")[:100]))
    total, broken = s.img_health()
    print("② 结果：%d 张，裂 %d 张" % (total, broken))
    s.leave(save=False)
finally:
    s.close()
