# -*- coding: utf-8 -*-
"""知乎编辑器 autosave hook 探针（2026-10-28）。
目的：抓 zhuanlan.zhihu.com/write 编辑器「自动保存草稿」的真实请求形状。
红线：绝不点击任何发布/保存按钮——只填标题正文、等 autosave 自己触发。
用后即弃探针，不属于正式管线。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scripts"))
import chrome_cdp_launch as L          # noqa: E402
from juejin_publish import _CDP        # noqa: E402

PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-zhihu-profile")
PORT = 9227

HOOK = '''
(function(){
  if (window.__net) return;
  window.__net = [];
  var of = window.fetch;
  window.fetch = function(){
    var a = arguments;
    var url = (a[0] && a[0].url) || a[0];
    var opt = a[1] || {};
    window.__net.push({kind:'fetch', url:String(url), method:opt.method||'GET',
      body: opt.body ? String(opt.body).slice(0,1200) : null});
    return of.apply(this, a);
  };
  var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b){
    window.__net.push({kind:'xhr', url:this.__u, method:this.__m, body:b?String(b).slice(0,1200):null});
    return os.apply(this, arguments); };
})();
'''

FILL = '''
(function(){
  // 1) 标题
  var t = document.querySelector('textarea[placeholder], .WriteIndex-titleInput textarea, input[placeholder*=标题], textarea[placeholder*=标题]');
  var titleEl = t;
  if (!titleEl) {
    var cands = [].slice.call(document.querySelectorAll('textarea, input'));
    titleEl = cands.filter(function(e){ return /标题|title/i.test(e.placeholder||e.name||e.id||''); })[0];
  }
  var r1 = 'NO_TITLE';
  if (titleEl) {
    var proto = Object.getPrototypeOf(titleEl);
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    desc.set.call(titleEl, '知乎草稿端点探针请忽略');
    titleEl.dispatchEvent(new Event('input', {bubbles: true}));
    r1 = 'TITLE_SET:' + titleEl.tagName + ':' + (titleEl.placeholder || '').slice(0, 30);
  }
  // 2) 正文：Draft.js 编辑器——focus 后 execCommand insertText（合成 paste 不进内部状态）
  var editor = document.querySelector('.public-DraftEditor-content, [contenteditable="true"]');
  var r2 = 'NO_EDITOR';
  if (editor) {
    editor.focus();
    document.execCommand('insertText', false, '知乎草稿端点探针正文。auto-save 触发用，无实际内容。');
    r2 = 'BODY_SET:' + (document.querySelector('.public-DraftEditor-content') ? 'draftjs' : 'contenteditable') +
         ':len=' + (editor.textContent || '').length;
  }
  return r1 + ' | ' + r2;
})()
'''

def main():
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    proc = subprocess.Popen(L.build_args(chrome, PROFILE, port=PORT, headless=True),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    assert L.wait_cdp(proc, timeout=45, port=PORT), "CDP 没起来"
    cdp = _CDP(PORT)
    try:
        tid = cdp.open("https://zhuanlan.zhihu.com/write")
        time.sleep(10)
        print("当前 URL:", cdp.eval("location.href"))
        cdp.send("Page.enable")
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
        cdp.eval("location.reload()")
        time.sleep(12)
        print("填内容:", cdp.eval(FILL))
        # 等 autosave（知乎编辑器空闲若干秒后自动保存）
        for i in range(6):
            time.sleep(5)
            net = cdp.eval("JSON.stringify((window.__net||[]).length)")
            print("  [%ds] __net=%s" % ((i + 1) * 5, net))
        net = json.loads(cdp.eval("JSON.stringify(window.__net||[])") or "[]")
        print("捕获 %d 条，过滤写请求：" % len(net))
        for e in net:
            u = e.get("url") or ""
            if (e["method"] in ("POST", "PUT", "PATCH")
                    or "article" in u.lower() or "draft" in u.lower()):
                print(" *", e["kind"], e["method"], u[:130])
                if e.get("body"):
                    print("   body:", e["body"][:900])
        if not net:
            print("(net 空——hook 未生效或无请求)")
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass
        proc.terminate()

if __name__ == "__main__":
    main()
