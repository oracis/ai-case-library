#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测二：编辑页正文到底在哪个容器里（只读）。

上一轮结果：`.ProseMirror` 命中的是**标题**（49 字），`#js_content` 与
Vue `pageData.content` 都没命中。可能原因：
  ① 正文在 iframe（UEditor 的 `#ueditor_0`）里；
  ② 正文容器是别的选择器；
  ③ 页面还没渲染完（ready 只判了 `#title`）。
这里把结构 dump 出来，不再猜。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LOCALAPPDATA",
                      os.path.join(os.path.expanduser("~"), "AppData", "Local"))
os.environ.setdefault("ProgramFiles", r"C:\Program Files")

import wechat_publish as wp      # noqa: E402
import wechat_sync as ws         # noqa: E402

DUMP_JS = r"""(function(){
  var out={};
  out.title = (document.title||'').slice(0,60);
  out.iframes=[];
  var ifs=document.querySelectorAll('iframe');
  for(var i=0;i<ifs.length;i++){
    var f=ifs[i], d=null, tlen=0, txt='';
    try{ d=f.contentDocument;
      if(d){ var b=d.body; txt=b?(b.innerText||''):'';
        tlen=txt.length; }
    }catch(e){ txt='<cross-origin>'; }
    out.iframes.push({id:f.id||'', cls:f.className||'', src:(f.src||'').slice(0,90),
                      html_len:(d&&d.body)?(d.body.innerHTML||'').length:0,
                      text_len:tlen, head:txt.slice(0,100)});
  }
  out.editable=[];
  var eds=document.querySelectorAll('[contenteditable="true"],[contenteditable=""]');
  for(var j=0;j<eds.length;j++){
    var e=eds[j], tx=e.innerText||'';
    out.editable.push({tag:e.tagName, id:e.id||'', cls:(e.className||'').slice(0,80),
      len:tx.length, html_len:(e.innerHTML||'').length, head:tx.slice(0,80)});
  }
  // 正文候选容器：按 innerText 长度降序取前 6 个 div
  var divs=document.querySelectorAll('div,section,article'), best=[];
  for(var k=0;k<divs.length;k++){
    var dv=divs[k], tt=dv.innerText||'';
    if(tt.length>300) best.push([tt.length, (dv.id||''), (dv.className||'').toString().slice(0,70), tt.slice(0,60)]);
  }
  best.sort(function(a,b){return b[0]-a[0];});
  out.big_text_blocks = best.slice(0,6);
  // Vue pageData 键名
  try{
    var all=document.querySelectorAll('body *'),seen=[],stack=[],pds=[];
    for(var m=0;m<all.length;m++) if(all[m].__vue__) stack.push(all[m].__vue__);
    while(stack.length){
      var vm=stack.pop(); if(!vm||seen.indexOf(vm)>=0) continue; seen.push(vm);
      if(vm.pageData) pds.push(Object.keys(vm.pageData).slice(0,50).join(','));
      var ch=vm.$children||[];
      for(var n=0;n<ch.length;n++) stack.push(ch[n]);
      if(vm.$parent) stack.push(vm.$parent);
    }
    out.pageData_keys = pds.slice(0,4);
  }catch(e){ out.pageData_keys=['ERR:'+e.message]; }
  return JSON.stringify(out);
})()"""


def main():
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    port = wp.CDP_PORT
    wp._autostart_chrome_if_needed(port=port)
    cdp = wp.CDP(port)
    _tid, tok = wp._connect_mp(cdp)
    drafts, ok = ws.fetch_draft_remote(cdp, tok)
    rows = sorted(drafts.items(), key=lambda kv: int(kv[1].get("update_time") or 0))
    aid, item = rows[0]
    print("探测 appmsgid=%s 《%s》" % (aid, item.get("title") or ""))

    etid = wp._open_draft_editor(cdp, aid, tok, timeout=45, strict=True)
    try:
        # 再等 8 秒让富文本编辑器把正文挂上
        import time
        time.sleep(8)
        raw = cdp.eval(DUMP_JS, refresh_context=True)
        d = json.loads(raw) if isinstance(raw, str) else {"raw": str(raw)[:300]}
        print("\n--- iframes (%d) ---" % len(d.get("iframes") or []))
        for f in d.get("iframes") or []:
            print("  " + json.dumps(f, ensure_ascii=False))
        print("\n--- contenteditable (%d) ---" % len(d.get("editable") or []))
        for f in d.get("editable") or []:
            print("  " + json.dumps(f, ensure_ascii=False))
        print("\n--- 最大文本块 ---")
        for b in d.get("big_text_blocks") or []:
            print("  %6d  id=%r cls=%r head=%r" % (b[0], b[1], b[2], b[3]))
        print("\n--- pageData keys ---")
        for k in d.get("pageData_keys") or []:
            print("  " + k)
    finally:
        try:
            cdp.close_target(etid)
        except Exception:                                # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
