#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决定性探针：抓「头条编辑器页面自己」发出的草稿保存请求 + 响应。

背景（11-11）
------------
`save_draft_api` 全量重提恒 `code=5009 发布失败，请重试`（新建诊断稿也 5009），
DOM/execCommand/真实 Backspace 改正文都不落库，footer 长期「草稿保存中...」。
两种可能必须分清：
  A) 我方重放链路失效（URL / body / header 已被服务端改掉）
  B) **账号/平台侧保存草稿整体被拒**（页面自己也 5009）
⇒ 不猜参数，直接 hook 页面自己的 fetch/XHR，记录 URL + headers + body + 响应。

只读观察；触发自动保存时会临时改标题，finally 里用真输入把标题改回来，
并独立回读标题 + 注记状态，确认没污染平台草稿。

用法（一条命令内完成，Chrome 必须同一会话起，见 _launch_9223.py 的坑）：
    CHROME_HEADED=1 /c/Python315/python.exe -X utf8 scripts/_launch_9223.py &&
    /c/Python315/python.exe -X utf8 tmp/probe_tt_selfsave.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

GID = "7699904114575753737"          # chatbase，见 tmp/tt16_map.json
PUB = "https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=" + GID
OUT = os.path.join(ROOT, "tmp", "ttselfsave.json")

JS_HOOK = r"""(function(){
  if (window.__ttProbe) return 'already';
  window.__ttProbe = true;
  window.__ttRec = [];
  var F = window.fetch;
  window.fetch = function(input, init){
    var u = (typeof input === 'string') ? input : (input && input.url);
    var rec = {via:'fetch', url:String(u||''), method:(init&&init.method)||'GET',
               headers:{}, body:'', status:null, resp:''};
    try { if (init && init.body) rec.body = String(init.body).slice(0,900); } catch(e) {}
    try {
      var h = (init && init.headers) || {};
      if (typeof Headers !== 'undefined' && h instanceof Headers) {
        h.forEach(function(v,k){ rec.headers[k] = String(v); });
      } else { for (var k in h) rec.headers[k] = String(h[k]); }
    } catch(e) {}
    window.__ttRec.push(rec);
    var p = F.apply(this, arguments);
    try {
      p.then(function(r){ rec.status = r.status; return r.clone().text(); })
       .then(function(t){ rec.resp = String(t).slice(0,500); })
       .catch(function(e){ rec.resp = 'ERR:' + e.message; });
    } catch(e) {}
    return p;
  };
  var XO = XMLHttpRequest.prototype.open;
  var XS = XMLHttpRequest.prototype.send;
  var XSH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(m,u){
    this.__u = u; this.__m = m; return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(k,v){
    try { if (!this.__h) this.__h = {}; this.__h[k] = String(v); } catch(e) {}
    return XSH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b){
    var rec = {via:'xhr', url:String(this.__u||''), method:this.__m||'',
               headers:this.__h||{}, body:String(b||'').slice(0,900),
               status:null, resp:''};
    window.__ttRec.push(rec);
    try {
      var self = this;
      this.addEventListener('loadend', function(){
        rec.status = self.status;
        try { rec.resp = String(self.responseText||'').slice(0,500); } catch(e) {}
      });
    } catch(e) {}
    return XS.apply(this, arguments);
  };
  return 'hooked';
})()"""

JS_STATE = r"""(function(){
  var t = document.querySelector("textarea[placeholder*='文章标题'],input[placeholder*='文章标题']");
  var eds = [].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  var body = eds.length ? (eds[0].innerText||'') : '';
  return JSON.stringify({title:(t&&t.value)||'', bodylen:body.length,
                         mark:body.indexOf('我去核了一遍')>=0,
                         tip:(document.body.innerText||'').indexOf('草稿保存中...')>=0,
                         recs:(window.__ttRec||[]).length});
})()"""

JS_RECS = "(function(){return JSON.stringify(window.__ttRec||[]);})()"


def type_replace_title(cdp, text):
    """全选标题框后用真输入替换（Vue 受控输入框认 Input.insertText）。"""
    r = cdp.eval("""(function(){
      var el=document.querySelector("textarea[placeholder*='文章标题'],input[placeholder*='文章标题']");
      if(!el) return 'notfound';
      el.scrollIntoView({block:'center'});
      var b=el.getBoundingClientRect();
      return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});
    })()""")
    if not r or r == "notfound":
        return False
    pos = json.loads(r)
    for typ in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": pos["x"], "y": pos["y"],
                  "button": "left", "clickCount": 1})
    time.sleep(0.4)
    # Ctrl+A（modifiers=2）
    for typ in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent",
                 {"type": typ, "key": "a", "code": "KeyA",
                  "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
                  "modifiers": 2})
    time.sleep(0.3)
    cdp.send("Input.insertText", {"text": text})
    time.sleep(1.0)
    return True


def dump(cdp, tag, allrecs):
    try:
        recs = json.loads(cdp.eval(JS_RECS, refresh_context=True) or "[]")
    except Exception as e:                                       # noqa: BLE001
        print("%s 读 rec 失败: %s" % (tag, e))
        return
    allrecs[tag] = recs
    print("\n===== %s：共 %d 条请求 =====" % (tag, len(recs)))
    for i, r in enumerate(recs):
        u = r.get("url", "")
        interesting = ("publish" in u or "draft" in u or "save" in u
                       or r.get("method", "").upper() == "POST")
        if not interesting:
            continue
        print("[%d] %s %s via=%s status=%s" %
              (i, r.get("method"), u[:140], r.get("via"), r.get("status")))
        hk = {k: (v[:24] + "..." if len(v) > 24 else v)
              for k, v in (r.get("headers") or {}).items()}
        print("    headers=%s" % json.dumps(hk, ensure_ascii=False)[:400])
        print("    body=%s" % (r.get("body") or "")[:260].replace("\n", " "))
        print("    resp=%s" % (r.get("resp") or "")[:300])


def main():
    tab = wp.CDP(tt.CDP_PORT)
    t = tab.new_target("about:blank")
    if not t or not tab.connect_target(t["id"]):
        print("✗ 开不了 tab")
        return 1
    try:
        tab.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                  "mobile": False})
        tab.send("Page.navigate", {"url": PUB})
        state = {}
        for _ in range(20):
            time.sleep(1.5)
            try:
                state = json.loads(tab.eval(JS_STATE, refresh_context=True) or "{}")
            except Exception:                                    # noqa: BLE001
                continue
            if state.get("bodylen", 0) > 200:
                break
        print("载入：title=%r bodylen=%s mark=%s save_pending=%s" %
              (state.get("title"), state.get("bodylen"),
               state.get("mark"), state.get("tip")), flush=True)
        if not state.get("title"):
            print("✗ 标题没读到，编辑器可能没加载")
            return 1
        orig = state["title"]

        print("hook:", tab.eval(JS_HOOK), flush=True)
        allrecs = {}

        # 阶段 1：纯观察 20s，看页面有没有自发保存
        time.sleep(20)
        dump(tab, "A_纯观察20s", allrecs)

        # 阶段 2：改标题触发页面自己的自动保存
        probe_title = orig + "·"
        ok = type_replace_title(tab, probe_title)
        print("\n改标题触发保存：ok=%s -> %r" % (ok, probe_title), flush=True)
        time.sleep(20)
        dump(tab, "B_改标题后20s", allrecs)

        # 阶段 3：把标题改回原值（真输入），再观察一次
        type_replace_title(tab, orig)
        time.sleep(12)
        dump(tab, "C_标题还原后12s", allrecs)

        st = json.loads(tab.eval(JS_STATE, refresh_context=True) or "{}")
        print("\n页内最终：title=%r bodylen=%s mark=%s save_pending=%s" %
              (st.get("title"), st.get("bodylen"), st.get("mark"),
               st.get("tip")), flush=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"state": st, "recs": allrecs}, f,
                      ensure_ascii=False, indent=2)
        print("已写 %s" % OUT, flush=True)
        return 0
    finally:
        tab.close_target(t["id"])


if __name__ == "__main__":
    sys.exit(main())
