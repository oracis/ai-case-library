#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：抓阿里云「我的文章」列表接口的原始响应，找封面字段。

为什么需要（2026-11-16）
------------------------
给已发布文章 1783930 补首图后，三条候选判据全被对照文章证伪：
  · 公开页首图 —— 任何文章都是 0 处（`og:image` 恒为平台默认 200x200 图标）
  · `draftDetail.coverUrl` —— 对已发布文章整体读不到
  · 「我的文章」卡片缩略图 —— 纯文字卡片
但「对照文章」本身当初也是用同一个坏掉的上传逻辑发的，**很可能全平台
都没有封面** ⇒ 上面三条不是「证伪」，是「这个位置本来就不展示」。
所以不能再靠 DOM 猜，直接看平台自己返回的数据。

做法：在 `/my` 页面装钩子（window 级，hash 跳转不丢），把 fetch/XHR 的
请求体与响应体记进 localStorage，再切到 `#/article`，最后把命中
article 关键字的响应原文打出来。

用法：python -X utf8 scripts/_oneoff/diag_ali_myapi.py [--aid 1783930]
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--aid", default="1783930")
ap.add_argument("--keep", action="store_true", help="不关浏览器")
a = ap.parse_args()

HOOK = r"""(function(){
  try { localStorage.removeItem('wb_net'); } catch(e) {}
  function rec(o){
    try {
      var arr = JSON.parse(localStorage.getItem('wb_net') || '[]');
      if (arr.length > 60) arr = arr.slice(-40);
      arr.push(o);
      localStorage.setItem('wb_net', JSON.stringify(arr));
    } catch(e) {}
  }
  var of = window.fetch;
  window.fetch = function(u, o){
    var url = String((u && u.url) || u || '');
    var m = (o && o.method) || 'GET';
    var body = (o && o.body) ? String(o.body).slice(0, 1500) : '';
    var p = of.apply(this, arguments);
    p.then(function(r){
      r.clone().text().then(function(t){ rec({k:'fetch', u:url, m:m, b:body, r:String(t).slice(0,4000)}); }).catch(function(){});
    }).catch(function(){});
    return p;
  };
  var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u){ this.__m=m; this.__u=String(u); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b){
    var self = this;
    this.addEventListener('load', function(){
      var t=''; try { t = String(self.responseText||'').slice(0,4000); } catch(e){}
      rec({k:'xhr', u:self.__u, m:self.__m, b: b?String(b).slice(0,1500):'', r:t});
    });
    return os.apply(this, arguments);
  };
  return 'HOOKED';
})()"""

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起", flush=True)
cdp.open("https://developer.aliyun.com/my")
time.sleep(8)
print("钩子:", cdp.eval(HOOK), flush=True)

# hash 路由，window 不重建 ⇒ 钩子还在
cdp.eval("location.hash = '#/article'")
time.sleep(10)

raw = cdp.eval("localStorage.getItem('wb_net') || '[]'")
try:
    calls = json.loads(str(raw))
except (ValueError, TypeError):
    calls = []
print("抓到请求 %d 条" % len(calls), flush=True)

KEY = ("cover", "firstimg", "imgurl", "coverurl", "image")
hits = 0
for i, c in enumerate(calls):
    body = (c.get("r") or "")
    low = body.lower()
    if not any(k in low for k in KEY):
        continue
    hits += 1
    print("\n--- [%d] %s %s" % (i, c.get("m"), (c.get("u") or "")[:140]), flush=True)
    # 只打含 cover 的片段，避免刷屏
    for k in ("coverurl", "firstimg", "cover"):
        p = low.find(k)
        while p >= 0:
            print("   …%s…" % body[max(0, p - 90):p + 170].replace("\n", " "),
                  flush=True)
            p = low.find(k, p + 1)
            if p > 0 and k != "coverurl":
                break
    if a.aid in body:
        print("   [含 %s]" % a.aid, flush=True)

print("\n含封面字段的响应: %d 条" % hits, flush=True)

if not a.keep and proc is not None:
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
