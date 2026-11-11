#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：自动保存抓到的 token 到底能不能用。

`_HOOK_ANTI_TOKEN` 在 `fetch()` **发出前**就把 token 记下来了，
所以「页面自动保存被服务端拒绝」时，抓到的仍是一个**无效 token**。
这能解释「上传封面成功、存草稿恒 7050」——上传是另一条链路。

本脚本装一个增强 hook：同时记录 token 与那次请求的**响应体**，
然后触发一次自动保存，直接看页面自己存得成不成。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as T          # noqa: E402
import wechat_publish as wp          # noqa: E402

HOOK_REPLY = r"""
(function(){
  if (window.__ttAntiHook2) { return 'already'; }
  window.__ttAntiHook2 = true;
  window.__diag = [];
  var F = window.fetch;
  window.fetch = function(input, init){
    var tok = null, u = (typeof input === 'string') ? input : (input && input.url);
    try {
      if (u && /article\/publish/.test(u)) {
        var h = (init && init.headers) || {}, flat = {};
        try {
          if (typeof Headers !== 'undefined' && h instanceof Headers) {
            h.forEach(function(v, k){ flat[k] = String(v); });
          } else { for (var k in h) { flat[k] = String(h[k]); } }
        } catch (e) { flat = {}; }
        tok = flat['tt-anti-token'] || null;
      }
    } catch (e) {}
    var p = F.apply(this, arguments);
    if (tok) {
      p.then(function(r){
        return r.clone().text().then(function(txt){
          window.__diag.push({tok: tok, status: r.status, body: String(txt).slice(0, 200), via: 'fetch'});
        });
      }).catch(function(e){
        window.__diag.push({tok: tok, status: -1, body: 'ERR:' + e.message, via: 'fetch'});
      });
    }
    return p;
  };
  var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send,
      XH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(m, u){ this.__u = u; return XO.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function(k, v){
    try { if (!this.__h) { this.__h = {}; } this.__h[k] = String(v); } catch (e) {}
    return XH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b){
    var self = this;
    try {
      if (self.__u && /article\/publish/.test(self.__u) &&
          self.__h && self.__h['tt-anti-token']) {
        var tk = self.__h['tt-anti-token'];
        self.addEventListener('load', function(){
          window.__diag.push({tok: tk, status: self.status,
                              body: String(self.responseText || '').slice(0, 200),
                              via: 'xhr'});
        });
      }
    } catch (e) {}
    return XS.apply(this, arguments);
  };
  return 'hooked';
})()
"""


def main():
    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_EDITOR)
    cdp.connect_target(t["id"])
    time.sleep(10)
    print("页面：%s" % (cdp.eval("location.href") or "")[:90])

    print("\n① 检查页面是否有风控/验证提示")
    for pat in ("argus", "验证", "异常", "risk"):
        js = ("(function(){return (document.body.innerText||'')"
              ".indexOf(%s)>=0;})()" % json.dumps(pat))
        if str(cdp.eval(js)).strip() == "true":
            print("  命中关键词：%s" % pat)
    print("  （以上无输出即未见提示）")

    print("\n② 装增强 hook 并触发自动保存")
    print("  hook:", cdp.eval(HOOK_REPLY))
    for i in range(3):
        T._focus_and_type(cdp, T.SEL["title"], "__diag_tok_%d__" % i)
        print("  第 %d 轮填标题后等 12s…" % (i + 1))
        time.sleep(12)
        rec = json.loads(cdp.eval("JSON.stringify(window.__diag||[])") or "[]")
        if rec:
            break
        # 再敲一次正文，逼一次保存
        T._focus_and_type(cdp, T.SEL["title"], "__diag_tok_%d_b__" % i)
        time.sleep(8)
        rec = json.loads(cdp.eval("JSON.stringify(window.__diag||[])") or "[]")
        if rec:
            break

    print("\n③ 自动保存的完整记录（%d 条）" % len(rec))
    for r in rec[-3:]:
        print("  token   = %s…" % (r.get("tok") or "")[:20])
        print("  via     = %s   status=%s" % (r.get("via"), r.get("status")))
        print("  response= %s" % (r.get("body") or "")[:150])
        print("  ---")

    print("\n=== 判定 ===")
    if not rec:
        print("  ⚠ 页面没触发自动保存（无头/时序），抓 token 这条路本身就不稳")
    else:
        b = (rec[-1].get("body") or "")
        if '"code":0' in b or '"code": 0' in b:
            print("  ✅ 页面自动保存成功 ⇒ 抓到的 token 有效")
            print("     ⇒ 我方 save 仍 7050 的话，问题在请求构造差异（header/body）")
        elif "7050" in b:
            print("  ❌ 页面自动保存也被 7050 拒 ⇒ 抓到的 token 本身就是废的")
            print("     ⇒ 说明当前会话写操作被限（账号/环境级），与代码无关")
        else:
            print("  ⚠ 响应既不是 0 也不是 7050：%s" % b[:120])

    cdp.close_target(t["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
