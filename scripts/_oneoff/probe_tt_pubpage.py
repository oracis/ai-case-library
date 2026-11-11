#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针：直接开 `graphic/publish?pgc_id=` 能不能读到草稿正文。

只读，不开编辑器不改任何东西。报告：最终 URL、标题框/编辑器是否存在、
各 contenteditable 的 innerText 长度、iframe 数量、页面首屏文字。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

GID = sys.argv[1] if len(sys.argv) > 1 else "7696901580452348454"
URL = "https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=%s" % GID

JS = """(function(){
  var o={url:location.href,title:'',nCE:0,ce:[],iframes:0,head:''};
  var ta=document.querySelector("textarea[placeholder*='文章标题'],"
                               "input[placeholder*='文章标题'],"
                               "textarea[placeholder*='标题'],input[placeholder*='标题']");
  o.title = ta ? (''+ta.value).slice(0,60) : '(no title box)';
  var eds=[].slice.call(document.querySelectorAll("[contenteditable='true']"));
  o.nCE = eds.length;
  eds.forEach(function(e,i){
    o.ce.push({i:i, cls:(''+e.className).slice(0,60),
               len:(''+(e.innerText||'')).length});
  });
  o.iframes = document.querySelectorAll('iframe').length;
  o.head = (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,180);
  return JSON.stringify(o);
})()"""


def main():
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=tt.CDP_PORT)
    cdp, box = tt._open("https://mp.toutiao.com/profile_v4/manage/draft", wait=8)
    print("草稿箱页:", cdp.eval("location.href"))
    t = cdp.new_target(URL)
    print("新 tab:", t["id"])
    cdp.connect_target(t["id"])
    for i in range(8):
        time.sleep(2)
        try:
            r = json.loads(cdp.eval(JS, refresh_context=True) or "{}")
        except Exception as e:                                 # noqa: BLE001
            print("  [%d] eval 失败 %s" % (i, e))
            continue
        print("  [%d] url=%s" % (i, r.get("url", "")[:90]))
        print("      title=%r nCE=%s iframes=%s"
              % (r.get("title"), r.get("nCE"), r.get("iframes")))
        for c in r.get("ce", [])[:5]:
            print("      ce#%d len=%d cls=%s" % (c["i"], c["len"], c["cls"]))
        print("      head=%s" % r.get("head", "")[:120])
    cdp.close_target(t["id"])
    cdp.close_target(box["id"])


if __name__ == "__main__":
    main()
