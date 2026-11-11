#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：定位作品管理页「更多」按钮的 DOM 位置（调试用）。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import toutiao_publish as T  # noqa: E402
import wechat_publish as wp  # noqa: E402

JS = r"""(function(){
  var title = %s;
  var els = Array.prototype.slice.call(document.querySelectorAll('a.title, .title'));
  var hit = els.filter(function(e){ return (e.innerText||'').trim() === title; });
  if (!hit.length) return JSON.stringify({err: 'no-title', n: els.length});
  var box = hit[0], chain = [];
  for (var i = 0; i < 8 && box.parentElement; i++) {
    box = box.parentElement;
    chain.push(box.tagName + '.' + String(box.className).slice(0, 28) +
               ' | ' + (box.innerText||'').slice(0, 36).replace(/\n/g, '~'));
    if (box.innerText && box.innerText.indexOf('更多') >= 0) break;
  }
  var cands = Array.prototype.slice.call(box.querySelectorAll('*')).filter(function(e){
    return e.children.length === 0 && (e.innerText||'').trim() === '更多';
  });
  var out = {chain: chain, cands: cands.length};
  if (cands[0]) {
    var r = cands[0].getBoundingClientRect();
    out.tag = cands[0].tagName;
    out.cls = String(cands[0].className).slice(0, 60);
    out.rect = [r.left, r.top, r.width, r.height];
    out.html = cands[0].outerHTML.slice(0, 160);
  }
  return JSON.stringify(out);
})()"""


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "__tt_probe_send__"
    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_WORKS)
    cdp.connect_target(t["id"])
    try:
        time.sleep(10)
        print(cdp.eval(JS % json.dumps(title)))
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
