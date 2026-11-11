#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探针：摸清公众号「消息分析 / 消息关键词」页的 URL、表格结构与取数接口。

只读：只导航 + 读 DOM / performance 资源，不点任何按钮。
用完即删（_ 前缀约定 = 一次性脚本）。
"""
import json
import os
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import chrome_cdp_launch as L          # noqa: E402
import wechat_publish as wp            # noqa: E402

PORT = 9222
PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP")


def probe_port():
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % PORT, timeout=3) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def launch():
    L.clear_proxy()
    if probe_port():
        print("[probe] 9222 已在运行，复用")
        return None
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    args = L.build_args(chrome, PROFILE, port=PORT, headless=True)
    print("[probe] launch %s (headless)" % label, flush=True)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        raise SystemExit("Chrome CDP 没起来")
    return proc


DUMP_JS = r"""
(function(){
  function txt(el){return (el&&el.innerText||'').replace(/\s+/g,' ').trim();}
  var tables=[];
  var ts=document.querySelectorAll('table');
  for(var i=0;i<ts.length&&i<6;i++){
    var t=ts[i], rows=[];
    var trs=t.querySelectorAll('tr');
    for(var j=0;j<trs.length&&j<12;j++){
      var cells=[];
      var tds=trs[j].querySelectorAll('th,td');
      for(var k=0;k<tds.length&&k<10;k++) cells.push(txt(tds[k]).slice(0,40));
      if(cells.length) rows.push(cells);
    }
    tables.push({cls:(t.className||'').slice(0,60), rows:rows});
  }
  var res=[];
  try{
    var es=performance.getEntriesByType('resource')||[];
    for(var m=0;m<es.length;m++){
      var n=es[m].name||'';
      if(/useranalysis|keyword|statistic|analysis|msgdata|cgi-bin/i.test(n))
        res.push(n.slice(0,220));
    }
  }catch(e){}
  return JSON.stringify({
    href:(location.href||'').slice(0,300),
    title:(document.title||'').slice(0,80),
    body:txt(document.body).slice(0,1500),
    tables:tables,
    xhr:res.slice(-25)
  });
})()
"""

SIDEBAR_JS = r"""
(function(){
  var out=[], seen={};
  var as=document.querySelectorAll('a,div[role=menuitem],li');
  for(var i=0;i<as.length;i++){
    var a=as[i], t=(a.textContent||'').replace(/\s+/g,' ').trim();
    var h=a.getAttribute?a.getAttribute('href')||'':'';
    if(!t||t.length>20) continue;
    if(!/消息|统计|分析|关键词|数据|用户/.test(t)) continue;
    var key=t+'|'+h;
    if(seen[key]) continue; seen[key]=1;
    out.push({t:t.slice(0,20), h:h.slice(0,200)});
  }
  return JSON.stringify(out.slice(0,60));
})()
"""


def dump(cdp, tag):
    raw = cdp.eval(DUMP_JS, refresh_context=True)
    try:
        info = json.loads(raw)
    except Exception:
        print("[%s] eval 非 JSON：%s" % (tag, str(raw)[:300]))
        return None
    print("\n" + "=" * 70)
    print("### %s" % tag)
    print("URL   : %s" % info["href"])
    print("TITLE : %s" % info["title"])
    body = info["body"]
    print("BODY  : %s" % (body[:900] if body else "(空)"))
    for ti, t in enumerate(info["tables"]):
        print("TABLE[%d] cls=%s rows=%d" % (ti, t["cls"], len(t["rows"])))
        for r in t["rows"][:8]:
            print("   | " + " | ".join(r))
    if info["xhr"]:
        print("XHR/RES:")
        for u in info["xhr"]:
            print("   " + u)
    print("=" * 70)
    return info


def main():
    proc = launch()
    cdp = wp.CDP(PORT)
    try:
        tid, token = wp._connect_mp(cdp)
        print("[probe] token=%s tid=%s" % (token, tid))
        cdp.send("Page.enable")

        # 1) 侧边栏链接
        raw = cdp.eval(SIDEBAR_JS, refresh_context=True)
        print("\n### 侧边栏候选链接")
        try:
            for it in json.loads(raw):
                print("   %-14s %s" % (it["t"], it["h"]))
        except Exception:
            print("   (非 JSON) %s" % str(raw)[:500])

        # 2) 打真实的「消息关键词」页（msg_top.js 里抠到的 tab URL）
        base = "https://mp.weixin.qq.com/misc/messageanalysis"
        cands = [
            ("msg_keyword",
             base + "?action=keyword&t=statistics/msg_keyword&type=-1"
                    "&lang=zh_CN&token=" + token),
            ("msg_keyword nolang",
             base + "?action=keyword&t=statistics/msg_keyword&type=-1&token=" + token),
        ]
        for tag, url in cands:
            t = cdp.new_target(url)
            tid2 = t.get("id") or t.get("targetId")
            if not cdp.connect_target(tid2):
                print("\n[%s] connect 失败" % tag)
                continue
            time.sleep(8)
            dump(cdp, tag)
            cdp.close_target(tid2)
    finally:
        try:
            cdp.close_browser()
        except Exception:
            pass
        if proc:
            proc.terminate()
        # 兜底：确保无残留 chrome
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("\n[probe] done, chrome cleaned")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
