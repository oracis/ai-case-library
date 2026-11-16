#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：阿里云个人主页（文章卡片）里，首图到底长什么样。

为什么需要（2026-11-16）
------------------------
`scripts/yun_finalize.py` 的 `upload_image()` 早已修好（能稳定拿到 ucc 地址），
但一直找不到「文章有封面」的可回读位：
  · 文章公开页：无 coverUrl/firstImg 字段，`og:image` 恒为平台默认图标
  · `draftDetail.coverUrl`：对**已发布**文章读不到
  · 「我的文章」：SSR 纯文字列表，无缩略图
  · 编辑页「文章图片」：页面态，不持久化
剩下唯一可能是**个人主页的文章卡片**（平台对外展示文章的地方）。
本脚本把卡片 DOM 原样打出来，用对照文章判断「有图」长什么样。

用法：python -X utf8 scripts/_oneoff/diag_ali_profile_card.py [--uid 32o2bc7dqh5k6]
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
ap.add_argument("--uid", default="32o2bc7dqh5k6")
ap.add_argument("--aid", default="1783930")
a = ap.parse_args()

CARD_JS = """
(function(aid){
  var links = [].slice.call(document.querySelectorAll('a[href*="/article/"]'));
  var out = {n_links: links.length, hit: null};
  var hit = links.filter(function(e){
    return (e.getAttribute('href') || '').indexOf('/article/' + aid) >= 0;
  })[0];
  if (!hit) { out.hrefs = links.map(function(e){ return e.getAttribute('href'); }).slice(0, 20); return JSON.stringify(out); }
  var b = hit;
  for (var k = 0; k < 8 && b.parentElement; k++) {
    b = b.parentElement;
    if (b.querySelector('img')) break;
  }
  var imgs = [].slice.call(b.querySelectorAll('img')).map(function(i){
    return {src: (i.src || '').slice(0, 130), cls: i.className,
            w: i.offsetWidth, h: i.offsetHeight,
            ds: (i.getAttribute('data-src') || '').slice(0, 130)};
  });
  var bgs = [];
  [].slice.call(b.querySelectorAll('*')).forEach(function(e){
    var v = getComputedStyle(e).backgroundImage || '';
    var m = v.match(/url\\(["']?([^"')]+)["']?\\)/);
    if (m && /alicdn/.test(m[1])) bgs.push(m[1].slice(0, 130));
  });
  out.hit = {text: (b.innerText || '').replace(/\\s+/g, ' ').slice(0, 160),
             imgs: imgs, bgs: bgs.slice(0, 3),
             html: b.outerHTML.replace(/\\s+/g, ' ').slice(0, 900)};
  return JSON.stringify(out);
})('%s')
""" % a.aid

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起", flush=True)
cdp.open("https://developer.aliyun.com/profile/%s" % a.uid)
time.sleep(12)
print("HREF", cdp.eval("location.href"), flush=True)
raw = cdp.eval(CARD_JS)
try:
    d = json.loads(str(raw))
except (ValueError, TypeError):
    print("PARSE_FAIL", str(raw)[:300])
    sys.exit(1)

print("页面上文章链接数:", d.get("n_links"), flush=True)
if not d.get("hit"):
    print("没找到该文章，页面上的链接：", flush=True)
    for h in (d.get("hrefs") or []):
        print("   ", h, flush=True)
    sys.exit(0)

h = d["hit"]
print("\n卡片文字:", h["text"], flush=True)
print("\n<img> 列表:")
for i in h["imgs"]:
    print("   src=%s | %dx%d | cls=%s | data-src=%s"
          % (i["src"] or "(空)", i["w"], i["h"], i["cls"], i["ds"] or "-"), flush=True)
if h["bgs"]:
    print("\n背景图:", h["bgs"], flush=True)
print("\n卡片 HTML（截断）:\n", h["html"], flush=True)
