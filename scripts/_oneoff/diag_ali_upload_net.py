#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：阿里云编辑页「传图」时，图片地址最终交给了哪个接口。

为什么需要（2026-11-16）
------------------------
`upload_image()` 现在能稳定拿到 ucc 地址（日志实测），但没有任何公开位
能回读到「文章有封面」：
  · 公开页无封面字段（任何文章都没有，`og:image` 恒为默认图标）
  · `draftDetail.coverUrl` 对已发布文章读不到
  · 「我的文章」是 SSR 纯文字列表，无 XHR、无缩略图
  · 编辑页「文章图片」列表不持久化（重载即空）
所以只剩一条路：看**传图这一步本身**到底调了哪些接口、带了什么字段。
本脚本只上传、不点发布（不会触发审核），全程只读。

用法：python -X utf8 scripts/_oneoff/diag_ali_upload_net.py \
        --aid 1783930 --cover tmp/supertux_cover_aliyun.jpg
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--aid", required=True)
ap.add_argument("--cover", required=True)
a = ap.parse_args()

EDIT = "https://developer.aliyun.com/article/new?publish=%s" % a.aid
LOCKED_JS = ("(function(){var b=document.body?document.body.innerText:'';"
             "return /内容审核中|暂不支持编辑/.test(b)?'LOCKED':'EDITABLE';})()")

# 记录**全部**非日志类请求（请求体 + 响应体），存 localStorage：
# 发布/跳转会让 window 丢，localStorage 不会。
HOOK = r"""(function(){
  try { localStorage.removeItem('wb_net2'); } catch(e) {}
  var SKIP = /log-global|\/track|_fetcher_=notifications|arms|beacon|spm/;
  function rec(o){
    try {
      if (SKIP.test(o.u || '')) return;
      if (o.r && o.r.length > 3000) o.r = o.r.slice(0, 3000);
      var arr = JSON.parse(localStorage.getItem('wb_net2') || '[]');
      if (arr.length > 40) arr = arr.slice(-30);
      arr.push(o);
      localStorage.setItem('wb_net2', JSON.stringify(arr));
    } catch(e) {}
  }
  var of = window.fetch;
  window.fetch = function(u, o){
    var url = String((u && u.url) || u || '');
    var m = ((o && o.method) || 'GET').toUpperCase();
    var b = (o && o.body) ? String(o.body).slice(0, 2000) : '';
    var p = of.apply(this, arguments);
    p.then(function(r){
      r.clone().text().then(function(t){ rec({k:'fetch', u:url, m:m, b:b, r:String(t)}); }).catch(function(){});
    }).catch(function(){});
    return p;
  };
  var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u){ this.__m=m; this.__u=String(u); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b){
    var self = this;
    this.addEventListener('load', function(){
      var t=''; try { t = String(self.responseText||''); } catch(e){}
      rec({k:'xhr', u:self.__u, m:(self.__m||'').toUpperCase(), b: b?String(b).slice(0,2000):'', r:t});
    });
    return os.apply(this, arguments);
  };
  if (navigator.sendBeacon) {
    var ob = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function(u, d){
      rec({k:'beacon', u:String(u||''), m:'POST', b: d?String(d).slice(0,2000):'', r:'(beacon)'});
      return ob(u, d);
    };
  }
  return 'HOOKED';
})()"""

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起", flush=True)
cdp.open(EDIT)
time.sleep(9)
F._close_disclaimer(cdp)
st = str(cdp.eval(LOCKED_JS) or "")
print("编辑页状态:", st, flush=True)
if st != "EDITABLE":
    print("⇒ 仍在审核锁定，不能传图（等解锁后再跑）", flush=True)
    sys.exit(0)

print("钩子:", cdp.eval(HOOK) or "(脚本已注入)", flush=True)
url = F.upload_image(cdp, a.cover)
print("上传结果:", (url or "(空)")[:110], flush=True)
time.sleep(6)

raw = cdp.eval("localStorage.getItem('wb_net2') || '[]'")
calls = json.loads(str(raw))
print("\n抓到非日志请求 %d 条：" % len(calls), flush=True)
for i, c in enumerate(calls):
    body = c.get("b") or ""
    resp = c.get("r") or ""
    print("\n[%d] %s %s" % (i, c.get("m"), (c.get("u") or "")[:150]), flush=True)
    if body:
        print("     req : %s" % body.replace("\n", " ")[:220], flush=True)
    if resp:
        print("     resp: %s" % resp.replace("\n", " ")[:220], flush=True)

low_all = "\n".join((c.get("r") or "") for c in calls).lower()
print("\n汇总：响应里出现 coverUrl=%d 次，firstImg=%d 次，ucc=%d 次"
      % (low_all.count("coverurl"), low_all.count("firstimg"),
         low_all.count("ucc")), flush=True)
