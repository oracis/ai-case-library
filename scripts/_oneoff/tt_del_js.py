#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：用 JS 原生 click 打开「更多」菜单，抓删除请求。

CDP 派发 mousePressed/Released 打不开菜单（可能是遮罩或坐标问题），
但 `el.click()` 会让 React 的 onClick 正常触发。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import toutiao_publish as T  # noqa: E402
import wechat_publish as wp  # noqa: E402

HOOK_JS = r"""(function(){
  if (window.__delHook) return 'already';
  window.__delHook = true;
  window.__delRec = [];
  var F = window.fetch;
  window.fetch = function(input, init){
    try {
      var u = (typeof input === 'string') ? input : (input && input.url);
      if (u && /(delete|remove)/i.test(u)) {
        var h = (init && init.headers) || {}, flat = {};
        try {
          if (typeof Headers !== 'undefined' && h instanceof Headers) {
            h.forEach(function(v,k){ flat[k] = String(v); });
          } else { for (var k in h) { flat[k] = String(h[k]); } }
        } catch(e) {}
        window.__delRec.push({via:'fetch', m:(init&&init.method)||'GET',
                              u:u, b:(init&&init.body)||'', h:flat});
      }
    } catch(e){}
    return F.apply(this, arguments);
  };
  var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; return XO.apply(this,arguments); };
  XMLHttpRequest.prototype.send = function(b){
    try {
      if (this.__u && /(delete|remove)/i.test(this.__u)) {
        window.__delRec.push({via:'xhr', m:this.__m, u:this.__u,
                              b:(typeof b === 'string') ? b : '', h:{}});
      }
    } catch(e){}
    return XS.apply(this, arguments);
  };
  return 'ok';
})()"""

JS_CLICK_MORE = r"""(function(){
  var title = %s;
  var els = Array.prototype.slice.call(document.querySelectorAll('a.title, .title'));
  var hit = els.filter(function(e){ return (e.innerText||'').trim() === title; });
  if (!hit.length) return 'no-title';
  var box = hit[0];
  for (var i = 0; i < 8 && box.parentElement; i++) {
    box = box.parentElement;
    if (box.innerText && box.innerText.indexOf('更多') >= 0) break;
  }
  var c = Array.prototype.slice.call(box.querySelectorAll('span')).filter(function(e){
    return (e.innerText||'').trim() === '更多';
  });
  if (!c.length) return 'no-more';
  c[0].click();
  return 'clicked:' + c[0].tagName;
})()"""

JS_CLICK_LABEL = r"""(function(){
  var label = %s;
  var all = Array.prototype.slice.call(document.querySelectorAll('*'));
  var c = all.filter(function(e){
    if (e.children.length) return false;
    return (e.innerText||'').trim() === label;
  });
  if (!c.length) return 'none';
  c[c.length - 1].click();
  return 'clicked:' + c[c.length - 1].tagName;
})()"""

JS_MENU_TEXT = r"""(function(){
  var all = Array.prototype.slice.call(document.querySelectorAll('*'));
  var out = [];
  all.forEach(function(e){
    if (e.children.length) return;
    var t = (e.innerText||'').trim();
    if (!t || t.length > 6) return;
    var r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.push(t);
  });
  return JSON.stringify(Array.from(new Set(out)).slice(0, 60));
})()"""


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "__tt_probe_send__"
    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_WORKS)
    cdp.connect_target(t["id"])
    try:
        time.sleep(10)
        print("hook: %s" % cdp.eval(HOOK_JS))
        print("点更多: %s" % cdp.eval(JS_CLICK_MORE % json.dumps(title)))
        time.sleep(2)
        print("菜单可见文本: %s" % cdp.eval(JS_MENU_TEXT))

        print("点删除: %s" % cdp.eval(JS_CLICK_LABEL % json.dumps("删除")))
        time.sleep(2)
        print("弹窗后文本: %s" % cdp.eval(JS_MENU_TEXT))

        for label in ("确定", "确认删除", "继续删除"):
            r = cdp.eval(JS_CLICK_LABEL % json.dumps(label))
            print("点 %s: %s" % (label, r))
            if r.startswith("clicked"):
                time.sleep(2.5)
                break

        time.sleep(3)
        rec = json.loads(cdp.eval("JSON.stringify(window.__delRec||[])") or "[]")
        print("\nhook %d 条" % len(rec))
        for x in rec:
            print("  [%s] %s %s" % (x["via"], x["m"], x["u"]))
            print("     body=%s" % str(x["b"])[:260])
            print("     hdr=%s" % json.dumps(x["h"], ensure_ascii=False)[:150])
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
