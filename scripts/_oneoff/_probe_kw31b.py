#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针 2：拿消息关键词页的表格模板 / cgiData，并实测 keyword 数据接口。"""
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
KW_URL = ("https://mp.weixin.qq.com/misc/messageanalysis"
          "?action=keyword&t=statistics/msg_keyword&type=-1&lang=zh_CN&token=%s")


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


TPL_JS = r"""
(function(){
  var out={};
  try{ out.cgiData = window.cgiData ? JSON.stringify(window.cgiData).slice(0,1200) : null; }catch(e){ out.cgiData='ERR '+e; }
  var t=document.getElementById('js_keyword_tpl');
  out.tpl = t ? t.innerHTML.replace(/\s+/g,' ').slice(0,1200) : null;
  var ths=document.querySelectorAll('.js_keyword_table th, table th');
  var hs=[]; for(var i=0;i<ths.length&&i<12;i++) hs.push((ths[i].textContent||'').trim());
  out.th = hs;
  return JSON.stringify(out);
})()
"""

FETCH_JS = r"""
(async function(){
  var tok=(location.href.match(/[?&]token=(\d+)/)||[])[1]||'';
  function u(d1,d2){
    return '/misc/messageanalysis?type=-1&action=keyword&begin_date='+d1+'&end_date='+d2
      +'&token='+tok+'&lang=zh_CN&f=json&ajax=1';
  }
  var out={};
  try{
    var r=await fetch(u('2026-09-23','2026-10-22'),{credentials:'include'});
    out.range30 = (await r.text()).slice(0,900);
  }catch(e){ out.range30='ERR '+e; }
  try{
    var r2=await fetch(u('2026-10-22','2026-10-22'),{credentials:'include'});
    out.day1 = (await r2.text()).slice(0,900);
  }catch(e){ out.day1='ERR '+e; }
  return JSON.stringify(out);
})()
"""


def main():
    proc = launch()
    cdp = wp.CDP(PORT)
    try:
        tid, token = wp._connect_mp(cdp)
        print("[probe] token=%s" % token)
        cdp.send("Page.enable")
        t = cdp.new_target(KW_URL % token)
        tid2 = t.get("id") or t.get("targetId")
        cdp.connect_target(tid2)
        time.sleep(8)

        raw = cdp.eval(TPL_JS, refresh_context=True)
        print("\n### 模板 / cgiData / 表头")
        try:
            info = json.loads(raw)
            print("cgiData : %s" % info.get("cgiData"))
            print("th      : %s" % info.get("th"))
            print("tpl     : %s" % info.get("tpl"))
        except Exception:
            print(str(raw)[:800])

        print("\n### 实测接口（30日区间 + 单日）")
        raw2 = cdp.eval(FETCH_JS, refresh_context=True)
        try:
            d = json.loads(raw2)
            print("[30日] %s" % d.get("range30"))
            print("[单日] %s" % d.get("day1"))
        except Exception:
            print(str(raw2)[:800])
    finally:
        try:
            cdp.close_browser()
        except Exception:
            pass
        if proc:
            proc.terminate()
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("\n[probe] done, chrome cleaned")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
