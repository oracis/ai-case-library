#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""只读：回读公众号已发表 Coral 的标题 + 修改页上限/剩余次数。

不调 submit、不消耗修改次数、不写任何平台数据。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LOCALAPPDATA",
                      os.path.join(os.path.expanduser("~"), "AppData", "Local"))
os.environ.setdefault("ProgramFiles", r"C:\Program Files")

import wechat_publish as wp          # noqa: E402
import wechat_sync as ws             # noqa: E402
import wechat_set_digest as wsd      # noqa: E402

APPMSGID = "2247485052"
TITLE_OLD = "Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统——卖的是「配置」，不是模型"

VM_JS = r"""(function(){
  var all=document.querySelectorAll('body *'), seen=[], stack=[], vm=null;
  for(var i=0;i<all.length;i++) if(all[i].__vue__) stack.push(all[i].__vue__);
  while(stack.length){
    var v=stack.pop(); if(!v||seen.indexOf(v)>=0) continue; seen.push(v);
    if(v.pageData && typeof v.pageData.digest==='string' && typeof v.request==='function'){vm=v;break;}
    var ch=v.$children||[]; for(var j=0;j<ch.length;j++) stack.push(ch[j]);
    if(v.$parent) stack.push(v.$parent);
  }
  if(!vm) return JSON.stringify({found:false, url:location.href, body:(document.body.innerText||'').slice(0,300)});
  var d=vm.$data||{}, o={found:true, url:location.href};
  ['title','pageTitle','maxModifyTitleCount','maxModifyTextCount','currModifyTitleCount',
   'currModifyContentCount','remainModifyCount','remain_modify_count','maxDigestTextCount',
   'digestTextCount','modifyStatus','modify_status','appmsg_modified'].forEach(function(k){
     var val=vm[k];
     if(val!==undefined) o[k]=String(val).slice(0,200);
  });
  var keys=Object.keys(d).filter(function(k){return /title|modify|count|status/i.test(k);});
  o.data_keys=keys.slice(0,50);
  o.data_vals={}; keys.slice(0,30).forEach(function(k){
    try{o.data_vals[k]=String(d[k]).slice(0,120);}catch(e){}
  });
  return JSON.stringify(o);
})()"""


def jload(raw):
    try:
        return json.loads(raw if isinstance(raw, str) else str(raw or ""))
    except Exception:
        return {}


def main():
    wp._autostart_chrome_if_needed(port=9222)
    cdp = wp.CDP(9222)
    tid, token = wp._connect_mp(cdp)
    if not token:
        print("✗ 拿不到 token（可能要扫码登录）")
        return 2
    print("token 前缀", str(token)[:6])
    cdp.connect_target(tid)
    cdp.send("Page.enable")

    pubs, ok = ws.fetch_published_remote(cdp, token)
    print("已发表列表条数", len(pubs), "fetch_ok", ok)
    hits = [p for p in pubs
            if "2247485052" in str(p.get("appmsg_id") or p.get("appmsgid") or "")
            or "Coral" in str(p.get("title") or "")]
    for p in hits[:3]:
        print("HIT", {k: p.get(k) for k in
                      ("title", "appmsg_id", "appmsgid", "update_time", "create_time")})
    print("旧标题逐字命中列表?", any(str(p.get("title") or "") == TITLE_OLD for p in pubs))

    row = wsd.remote_row(cdp, token, APPMSGID)
    if row:
        keys = [k for k in row if any(x in k.lower()
                                      for x in ("title", "modify", "status", "digest", "appmsgid"))]
        print("remote_row 关键字段", json.dumps({k: str(row[k])[:160] for k in keys[:20]},
                                            ensure_ascii=False))
    else:
        print("remote_row 未命中", APPMSGID)

    url = wsd.modify_url(APPMSGID, token)
    print("修改页", url.replace(token, "<token>"))
    cdp.send("Page.navigate", {"url": url})
    raw = None
    for i in range(6):
        time.sleep(5)
        raw = cdp.eval(VM_JS)
        if raw and '"found":true' in str(raw):
            break
    print("VM", str(raw)[:2200])


if __name__ == "__main__":
    sys.exit(main() or 0)
