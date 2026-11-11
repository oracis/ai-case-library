#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：在有头调试 Chrome 里打开「自动回复 → 关键词回复」页，留给用户手配。

只导航、只读，不点保存类按钮；**不结束 Chrome**（用户要用）。
"""
import json
import os
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import wechat_publish as wp          # noqa: E402

PORT = 9222

LINKS_JS = r"""
(function(){
  var out=[],as=document.querySelectorAll('a[href]');
  for(var i=0;i<as.length;i++){
    var h=as[i].getAttribute('href')||'';
    if(/autoreply|reply/i.test(h)){
      out.push(((as[i].textContent||'').replace(/\s+/g,' ').trim()||'(无文字)').slice(0,20)+' | '+h.slice(0,150));
    }
  }
  return JSON.stringify(out.slice(0,20));
})()
"""

INFO_JS = r"""
(function(){
  function t(e){return (e&&e.innerText||'').replace(/\s+/g,' ').trim();}
  var tabs=[],as=document.querySelectorAll('.tab_nav a, .tabs a, .ui-tab a, [class*=tab] a');
  for(var i=0;i<as.length&&i<10;i++){var x=t(as[i]); if(x&&x.length<12) tabs.push(x+' | '+(as[i].getAttribute('href')||''));}
  return JSON.stringify({href:location.href.slice(0,200), title:document.title,
    body:t(document.body).slice(0,700), tabs:tabs});
})()
"""


def main():
    cdp = wp.CDP(PORT)
    tid, token = wp._connect_mp(cdp)
    print("[mp] token=%s" % token)
    if not token:
        print("✗ 拿不到 token，登录态可能要人工扫码")
        return 2
    raw = cdp.eval(LINKS_JS, refresh_context=True)
    print("[links] %s" % raw)

    base = "https://mp.weixin.qq.com/advanced/autoreply?t=ivr/reply"
    cands = [
        base + "&action=keyword&lang=zh_CN&token=" + token,
        base + "&action=beadded&lang=zh_CN&token=" + token,
        base + "&action=message&lang=zh_CN&token=" + token,
    ]
    for url in cands:
        t = cdp.new_target(url)
        tid2 = t.get("id") or t.get("targetId")
        if not cdp.connect_target(tid2):
            print("[x] connect 失败 %s" % url)
            continue
        time.sleep(4)
        raw2 = cdp.eval(INFO_JS, refresh_context=True)
        try:
            d = json.loads(raw2)
        except Exception:
            print("[?] %s" % str(raw2)[:300])
            continue
        print("\nURL  : %s" % d["href"])
        print("TITLE: %s" % d["title"])
        print("TABS : %s" % d["tabs"])
        print("BODY : %s" % d["body"][:500])
        if "自动回复" in d["body"] and "关键词" in d["body"]:
            print("\n[ok] 停在这个 tab 不关，留给人工配置")
            # 把窗口提到前台（有头模式下用户能直接看到）
            try:
                cdp.send("Page.bringToFront")
            except Exception:
                pass
            return 0
        cdp.close_target(tid2)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
