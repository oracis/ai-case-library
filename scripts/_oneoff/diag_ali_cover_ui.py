#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：阿里云编辑页里跟「封面/首图」相关的控件到底有哪些。

2026-11-16 背景：给已发布文章补首图，上传能拿到 ucc 地址，但发布后
任何公开位都读不到（公开页 / draftDetail / 我的文章列表三个判据全部证伪）。
在再发一次之前，先确认编辑页里是不是有**专门的首图控件**被我们漏掉了
（我们一直只用「上传图片」按钮，那可能只是往正文插图）。

用法：python -X utf8 scripts/_oneoff/diag_ali_cover_ui.py --aid 1783930
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--aid", required=True)
a = ap.parse_args()

FIND_JS = r"""(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('*')).forEach(function(e){
    if (e.children.length) return;
    var t = (e.textContent || '').trim();
    if (t && t.length <= 16 && /封面|首图/.test(t)) {
      out.push({tag: e.tagName, text: t, cls: (e.className || '').slice(0, 70)});
    }
  });
  var btns = [].slice.call(document.querySelectorAll('button'))
    .filter(function(b){ return b.offsetWidth || b.offsetHeight; })
    .map(function(b){ return (b.textContent || '').trim(); })
    .filter(function(t){ return t; }).slice(0, 30);
  var ups = [].slice.call(document.querySelectorAll('.next-upload-list-item')).length;
  return JSON.stringify({hits: out.slice(0, 12), btns: btns, upload_items: ups});
})()"""

proc, cdp = Y._connect()
cdp.open("https://developer.aliyun.com/article/new/cloudnative?publish=%s" % a.aid)
time.sleep(12)
F._close_disclaimer(cdp)
print("HREF", cdp.eval("location.href"))
print(cdp.eval(FIND_JS))
