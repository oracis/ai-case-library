#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 CSDN 文章列表（草稿箱 / 已发布 / 回收站），走页面自身请求。

为什么不用接口：`browser_api()` 的 GET 签名试过三版全 mismatch
（`article/list?status=draft` → `HMAC signature does not match`），POST 才稳。
这里的做法是让页面自己去请求 —— 打开创作中心 → 点对应 tab → 从注入的
fetch/XHR hook 里读回响应，等于「照抓包读」，不碰签名。

用法：
    python -X utf8 scripts/csdn_list_drafts.py            # 草稿箱
    python -X utf8 scripts/csdn_list_drafts.py --status all_v3
    python -X utf8 scripts/csdn_list_drafts.py --json      # 机器可读

2026-11-04 实测：draft 三条、coverImage 全空那次就是靠它看出来的。
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import chrome_cdp_launch as L          # noqa: E402
import csdn_publish as C               # noqa: E402
import wechat_publish as wp            # noqa: E402

PORT = 9224
URL = "https://mp.csdn.net/mp_blog/manage/article?spm=1010.2135.3001.4476"

HOOK = r"""(function(){
  if (window.__net_hooked) return;
  window.__net_hooked = 1; window.__net = [];
  var of = window.fetch;
  window.fetch = function(){
    var u = (arguments[0] && arguments[0].url) || arguments[0] || '';
    var p = of.apply(this, arguments);
    try { p.then(function(r){ r.clone().text().then(function(t){ window.__net.push({u:String(u), t:t}); }).catch(function(){}); }).catch(function(){}); } catch(e){}
    return p;
  };
  var oo = XMLHttpRequest.prototype.open, os_ = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m,u){ this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(){
    var self = this;
    this.addEventListener('load', function(){ window.__net.push({u:String(self.__u||''), t:self.responseText}); });
    return os_.apply(this, arguments);
  };
})();"""

TAB_NAMES = {"draft": "草稿箱", "all_v3": "全部", "deleted": "回收站",
             "publish": "已发布", "audit": "审核中"}

RECT_JS = r"""(function(want){
  var els = document.querySelectorAll('div,span,li,a,button');
  for (var i=0;i<els.length;i++){
    var t=(els[i].innerText||'').trim();
    if (t.indexOf(want)===0 && t.length < 12 && els[i].getBoundingClientRect().height>0){
      var r = els[i].getBoundingClientRect();
      return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
    }
  }
  return 'NOT_FOUND';
})"""

PICK_JS = r"""(function(st){
  var out = [];
  (window.__net||[]).forEach(function(x){
    if ((x.u||'').indexOf('status=' + st) < 0) return;
    try {
      var d = JSON.parse(x.t).data || {};
      (d.list||[]).forEach(function(it){
        out.push({id: String(it.articleId), status: String(it.status),
                  title: it.title, cover: (it.coverImage||[])[0] || '',
                  views: it.viewCount});
      });
    } catch(e){}
  });
  return JSON.stringify(out);
})"""


def list_status(status="draft", timeout=45):
    """返回 [{id,status,title,cover,views}]；没抓到就返回 []。"""
    L.clear_proxy()
    L.remove_lock(C.LOGIN_PROFILE)
    chrome, _ = L.pick_chrome()
    args = L.build_args(chrome, C.LOGIN_PROFILE, port=PORT, headless=False)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        if not L.wait_cdp(proc, timeout=timeout, port=PORT):
            raise SystemExit("✗ Chrome CDP 没起来（端口 %d）" % PORT)
        time.sleep(2)
        cdp = wp.CDP(PORT)
        tid = cdp.new_target(URL)["id"]
        cdp.connect_target(tid)
        cdp.send("Page.enable", {})
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
        cdp.eval("location.reload()")
        time.sleep(22)
        r = str(cdp.eval("(%s)(%s)" % (RECT_JS, json.dumps(TAB_NAMES.get(status, "草稿箱")))))
        if r == "NOT_FOUND":
            return []
        p = json.loads(r)
        for t in ("mousePressed", "mouseReleased"):
            cdp.send("Input.dispatchMouseEvent", {"type": t, "x": p["x"], "y": p["y"],
                                                  "button": "left", "clickCount": 1})
        time.sleep(9)
        return json.loads(str(cdp.eval("(%s)(%s)" % (PICK_JS, json.dumps(status)))))
    finally:
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description="读 CSDN 文章列表（走页面请求）")
    ap.add_argument("--status", default="draft",
                    choices=sorted(TAB_NAMES), help="draft/all_v3/deleted/publish/audit")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    a = ap.parse_args()
    rows = list_status(a.status)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("%-10s %-4s %-46s %-7s %s" % ("ID", "ST", "标题", "阅读", "封面"))
    for it in rows:
        print("%-10s %-4s %-46s %-7s %s" % (it["id"], it["status"], (it["title"] or "")[:46],
                                            it.get("views") or "-", it["cover"][:52] or "✗ 无"))
    print("\n共 %d 条（status=%s）" % (len(rows), a.status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
