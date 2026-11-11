#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：走 UI 删掉测试稿，并 hook 抓「删除」的真实请求形态。

## 为什么不能继续猜接口

`POST /mp/agw/creator_center/delete` 无论 body 对错都返 `{"code":0,"success"}`
（试过 `{type,gid}` 与补全 7 字段两种），回读目标**都在**。头条对形态不对的
请求一律宽容返回 ⇒ 只能以「回读消失」为准；要知道形态就得抓一次真实点击。

## DOM 事实（2026-10-18 调试）

    DIV.article-card-bone
      └ DIV.title-wrap → A.title「__tt_probe_send__」
      └ …「更多」= SPAN[tabindex=0]（无 class，文本「更多」）

⚠ 这是**真删**（目标就是那条测试稿），不是预演。
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

PORT = int(os.environ.get("CDP_PORT", 9223))
TITLE = "__tt_probe_send__"

HOOK_JS = r"""(function(){
  if (window.__delHook) return 'already';
  window.__delHook = true;
  window.__delRec = [];
  var F = window.fetch;
  window.fetch = function(input, init){
    try {
      var u = (typeof input === 'string') ? input : (input && input.url);
      if (u && /delete/i.test(u)) {
        var h = (init && init.headers) || {};
        var flat = {};
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
      if (this.__u && /delete/i.test(this.__u)) {
        window.__delRec.push({via:'xhr', m:this.__m, u:this.__u,
                              b:(typeof b === 'string') ? b : '', h:{}});
      }
    } catch(e){}
    return XS.apply(this, arguments);
  };
  return 'ok';
})()"""

FIND_MORE_JS = r"""(function(){
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
    return e.children.length === 0 && (e.innerText||'').trim() === '更多';
  });
  if (!c.length) return 'no-more';
  var r = c[0].getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.left + r.width/2),
                         y: Math.round(r.top + r.height/2)});
})()"""

FIND_TEXT_JS = r"""(function(){
  var label = %s;
  var all = Array.prototype.slice.call(document.querySelectorAll('*'));
  var c = all.filter(function(e){
    if (e.children.length) return false;
    if ((e.innerText||'').trim() !== label) return false;
    var r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top >= 0;
  });
  if (!c.length) return 'none';
  var r = c[0].getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.left + r.width/2),
                         y: Math.round(r.top + r.height/2),
                         tag: c[0].tagName, n: c.length});
})()"""


def click_at(cdp, x, y):
    # 先移过去再点：头条的「更多」靠 mouseenter 起菜单，只 press/release 不触发
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
    import time as _t
    _t.sleep(0.6)
    for typ in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": x, "y": y, "button": "left",
                  "clickCount": 1})


def click_label(cdp, label, pause=1.5):
    r = cdp.eval(FIND_TEXT_JS % json.dumps(label))
    print("  找 %r → %s" % (label, r))
    if r and r.startswith("{"):
        p = json.loads(r)
        click_at(cdp, p["x"], p["y"])
        time.sleep(pause)
        return True
    return False


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else TITLE
    cdp = wp.CDP(PORT)
    t = cdp.new_target(T.TT_WORKS)
    cdp.connect_target(t["id"])
    try:
        time.sleep(10)
        print("页面：%s" % cdp.eval("location.href"))
        print("装 hook：%s" % cdp.eval(HOOK_JS))

        r = cdp.eval(FIND_MORE_JS % json.dumps(title))
        print("找「更多」：%s" % r)
        if not (r and r.startswith("{")):
            return 2
        p = json.loads(r)
        click_at(cdp, p["x"], p["y"])
        time.sleep(1.8)

        if not click_label(cdp, "删除"):
            print("  ⚠ 菜单里没有「删除」，可见文本前 300 字：")
            print("  %s" % (cdp.eval("document.body.innerText") or "")[:300].replace("\n", " | "))
            return 3

        for label in ("确认删除", "确定", "删除", "继续删除"):
            if click_label(cdp, label, pause=2.0):
                break

        time.sleep(3)
        rec = json.loads(cdp.eval("JSON.stringify(window.__delRec||[])") or "[]")
        print("\nhook 记录 %d 条：" % len(rec))
        for x in rec:
            print("  [%s] %s %s" % (x["via"], x["m"], x["u"]))
            print("       body=%s" % str(x["b"])[:240])
            print("       headers=%s" % json.dumps(x["h"], ensure_ascii=False)[:160])
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
