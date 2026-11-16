#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：阿里云「我的文章」列表卡片里到底有没有缩略图。

为什么需要（2026-11-16）
------------------------
给已发布文章补首图后，找不到任何可信的回读判据：
  · 公开页没有首图（已知有图的 1780571 也没有，`og:image` 恒为平台默认图标）
  · `draftDetail.coverUrl` 对已发布文章整体返回空（连 1780571 也 `retry-failed`）
  · 编辑页的「文章图片」列表**本来就不持久化**（重载即空，看 upload_image 文档）
唯一还没验的是「我的文章」列表卡片 —— 那是首图真正的展示位。
本脚本把命中文章的卡片 outerHTML 打出来，判断有没有 <img>/background-image。

用法：python -X utf8 scripts/_oneoff/diag_ali_card.py --aid 1783930 [--aid 1780571]
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--aid", action="append", required=True)
a = ap.parse_args()

CARD_JS = r"""(function(aid){
  var links = [].slice.call(document.querySelectorAll('a[href*="/article/"]'));
  var hit = links.filter(function(e){
    return (e.getAttribute('href') || '').indexOf('/article/' + aid) >= 0;
  })[0];
  if (!hit) return JSON.stringify({found:false, links:links.length});
  var b = hit;
  for (var k = 0; k < 8 && b.parentElement; k++) {
    b = b.parentElement;
    if (b.querySelector('img')) break;
  }
  var imgs = [].slice.call(b.querySelectorAll('img')).map(function(i){
    return {src:(i.src||'').slice(0,120), w:i.naturalWidth, h:i.naturalHeight};
  });
  var bgs = [];
  [].slice.call(b.querySelectorAll('*')).forEach(function(e){
    var v = getComputedStyle(e).backgroundImage || '';
    var m = v.match(/url\("?([^")]+)"?\)/);
    if (m && m[1].indexOf('alicdn') >= 0) bgs.push(m[1].slice(0,120));
  });
  return JSON.stringify({
    found:true, imgs:imgs.slice(0,5), bgs:bgs.slice(0,5),
    text:(b.innerText||'').replace(/\s+/g,' ').slice(0,120),
    html:(b.outerHTML||'').slice(0,900)
  });
})"""

proc, cdp = Y._connect()
cdp.open("https://developer.aliyun.com/my#/article")
time.sleep(12)
print("HREF", cdp.eval("location.href"))
for aid in a.aid:
    raw = cdp.eval(CARD_JS + "(%s)" % repr(aid).replace("'", '"'))
    print("\n=== %s ===" % aid)
    print(str(raw)[:1200])
