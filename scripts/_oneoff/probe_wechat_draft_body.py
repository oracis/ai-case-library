#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测：公众号**草稿**正文到底能不能读到、用哪条路读最稳。

只读，不改任何稿、不发任何东西。目的只有一个：
`wechat_fifo.py` 过形态闸时想传 `text=<平台草稿实际正文>`（现在没传，
所以闸验的是本地产物，本地干净 / 平台脏时会放过）。

候选读法（同一页面依次试）：
  A. 编辑页 Vue `pageData.content`（编辑器真实数据源）
  B. `#js_content` innerText（旧编辑器 DOM）
  C. `.ProseMirror` innerText（新版编辑器 DOM）
  D. 草稿列表接口自带的 digest（只有摘要，量级差太远，仅作对照）

判据：谁返回的文本**同时**含正文特征（长度 > 500 且含标题里的词），
就用谁；只返回 HTML 的也算可用（闸内部会剥标签）。
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

READERS = {
    # A. Vue pageData.content（草稿编辑页；不同版本字段名不一致，都试）
    "vue_pageData_content": r"""(function(){
      var all=document.querySelectorAll('body *'),seen=[],stack=[],hits=[];
      for(var i=0;i<all.length;i++) if(all[i].__vue__) stack.push(all[i].__vue__);
      while(stack.length){
        var vm=stack.pop(); if(!vm||seen.indexOf(vm)>=0) continue; seen.push(vm);
        var pd=vm.pageData;
        if(pd){
          ['content','content_noencode','contentNoEncode'].forEach(function(k){
            if(typeof pd[k]==='string' && pd[k]) hits.push([k,pd[k].length]);
          });
          if(hits.length) { window.__pd=pd; }
        }
        var ch=vm.$children||[];
        for(var j=0;j<ch.length;j++) stack.push(ch[j]);
        if(vm.$parent) stack.push(vm.$parent);
      }
      if(!window.__pd) return JSON.stringify({hit:false});
      var pd=window.__pd,best='',key='';
      ['content','content_noencode','contentNoEncode'].forEach(function(k){
        if(typeof pd[k]==='string' && pd[k].length>best.length){best=pd[k];key=k;}
      });
      return JSON.stringify({hit:!!best,key:key,len:best.length,
        keys:Object.keys(pd).filter(function(k){return typeof pd[k]==='string';}).slice(0,40),
        head:best.slice(0,160)});
    })()""",
    # B. 旧版编辑器 DOM
    "js_content_innerText": r"""(function(){
      var el=document.querySelector('#js_content');
      if(!el) return JSON.stringify({hit:false});
      var t=el.innerText||'';
      return JSON.stringify({hit:true,len:t.length,html_len:(el.innerHTML||'').length,
        head:t.slice(0,160)});
    })()""",
    # C. 新版编辑器 DOM
    "prosemirror_innerText": r"""(function(){
      var el=document.querySelector('.ProseMirror')||document.querySelector('[contenteditable="true"]');
      if(!el) return JSON.stringify({hit:false});
      var t=el.innerText||'';
      return JSON.stringify({hit:true,len:t.length,html_len:(el.innerHTML||'').length,
        head:t.slice(0,160)});
    })()""",
}


def main():
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    port = wp.CDP_PORT
    wp._autostart_chrome_if_needed(port=port)
    cdp = wp.CDP(port)
    tid, tok = wp._connect_mp(cdp)
    print("token: %s…  home_tid=%s" % (str(tok)[:6], tid))

    drafts, ok = ws.fetch_draft_remote(cdp, tok)
    print("草稿箱读取 ok=%s 共 %d 条" % (ok, len(drafts)))
    if not drafts:
        print("✗ 草稿箱为空，无法探测")
        return 2
    rows = sorted(drafts.items(),
                  key=lambda kv: int(kv[1].get("update_time") or 0))
    aid, item = rows[0]
    print("探测对象：appmsgid=%s 《%s》 digest_len=%d"
          % (aid, item.get("title") or "", len(item.get("digest") or "")))

    etid = wp._open_draft_editor(cdp, aid, tok, timeout=45, strict=True)
    print("编辑页已就绪 tid=%s" % etid)
    try:
        print("\n页面标题: %r" % (cdp.eval("document.title") or ""))
        for name, expr in READERS.items():
            try:
                raw = cdp.eval(expr, refresh_context=True)
                d = json.loads(raw) if isinstance(raw, str) else {"raw": str(raw)[:200]}
            except Exception as e:                       # noqa: BLE001
                d = {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}
            print("\n[%s]" % name)
            print("  " + json.dumps(d, ensure_ascii=False)[:700])
    finally:
        try:
            cdp.close_target(etid)
            print("\n已关闭编辑页 tab")
        except Exception as e:                           # noqa: BLE001
            print("关 tab 失败：%s" % str(e)[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
