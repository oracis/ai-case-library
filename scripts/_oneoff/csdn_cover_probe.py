# -*- coding: utf-8 -*-
"""CSDN 封面修复探针：打开新编辑器 → 抓封面控件 → hook 网络 → dump。

一次性排障脚本，不进常规流程。
"""
import sys, os, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from yun_tidy import Tab

PORT = int(os.environ.get("CDP_CSDN_PORT") or 9224)
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def targets():
    return [t for t in json.loads(OP.open("http://127.0.0.1:%d/json/list" % PORT,
                                          timeout=8).read())
            if t.get("type") == "page"]


def browser_ws():
    return json.loads(OP.open("http://127.0.0.1:%d/json/version" % PORT,
                              timeout=8).read())["webSocketDebuggerUrl"]


def open_target(url):
    from wechat_publish import CDP
    c = CDP(PORT)
    return c.new_target(url)["id"]


def tab_for(url_part):
    for t in targets():
        if url_part in (t.get("url") or ""):
            return Tab(t["webSocketDebuggerUrl"]), t
    return None, None


HOOK = r"""
(function(){
  if (window.__net) return 'already';
  window.__net = [];
  var of = window.fetch;
  window.fetch = function(){
    var a = arguments, url = (typeof a[0] === 'string') ? a[0] : (a[0] && a[0].url);
    var init = a[1] || {};
    var rec = {k:'fetch', url: String(url), method: (init.method||'GET').toUpperCase(),
               body: (typeof init.body === 'string' ? init.body.slice(0,600) : null),
               status: null, resp: null, ts: Date.now()};
    window.__net.push(rec);
    return of.apply(this, a).then(function(r){
      rec.status = r.status;
      try { r.clone().text().then(function(t){ rec.resp = t.slice(0,800); })
              .catch(function(){}); } catch(e){}
      return r;
    });
  };
  var ox = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u){
    this.__rec = {k:'xhr', url:String(u), method:String(m).toUpperCase(), body:null,
                  status:null, resp:null, ts:Date.now()};
    window.__net.push(this.__rec);
    return ox.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b){
    if (this.__rec) {
      this.__rec.body = (typeof b === 'string') ? b.slice(0,600) : (b ? '[binary]' : null);
      var self = this;
      this.addEventListener('load', function(){
        self.__rec.status = self.status;
        try { self.__rec.resp = String(self.responseText || '').slice(0,800); } catch(e){}
      });
    }
    return os.apply(this, arguments);
  };
  return 'hooked';
})()
"""

if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else "probe"
    AID = "169653810"

    if act == "open":
        tid = open_target("https://mp.csdn.net/mp_blog/creation/editor/%s" % AID)
        print("新 tab:", tid)
        time.sleep(12)
        tb, t = tab_for("creation/editor/%s" % AID)
        if not tb:
            print("没找到 tab"); raise SystemExit(1)
        print("URL:", tb.eval("location.href"))
        print("标题:", tb.eval("document.title"))
        print("hook:", tb.eval(HOOK))
        print(tb.eval(r"""
(function(){
  var hits = [];
  document.querySelectorAll('*').forEach(function(e){
    if (e.children.length) return;
    var t = (e.textContent||'').trim();
    if (t && t.length <= 14 && /封面|上传|自动生成|无封面/.test(t)) {
      var r = e.getBoundingClientRect();
      hits.push(t + ' <' + e.tagName + '> y=' + Math.round(r.y) + ' x=' + Math.round(r.x));
    }
  });
  return JSON.stringify(hits.slice(0, 20), null, 1);
})()
"""))

    elif act == "net":
        tb, t = tab_for("creation/editor/%s" % AID)
        print(tb.eval("JSON.stringify((window.__net||[]).map(function(r){"
                      "return r.method+' '+r.url+' ['+(r.status||'-')+'] '+(r.body||'').slice(0,120);"
                      "}), null, 1)"))

    elif act == "shot":
        import base64
        tb, t = tab_for("creation/editor/%s" % AID)
        tb.send("Page.bringToFront")
        r = tb.send("Page.captureScreenshot", {"format": "png"})
        p = os.path.join(ROOT, "tmp", "csdn_editor.png")
        open(p, "wb").write(base64.b64decode(r["result"]["data"]))
        print("截图:", p)
