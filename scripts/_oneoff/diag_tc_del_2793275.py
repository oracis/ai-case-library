#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断 2793275 卡片删除按钮为何点不出确认框（2026-11-26）。

不点任何按钮，只 dump DOM：链接定位、父容器、删除图标坐标/inView/hit-test。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import tc_manage as TM  # noqa: E402

AID = "2793275"
proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open(TM.LIST_URL)
time.sleep(10)

js = r"""
JSON.stringify((function(id){
  const out = {id: id};
  const as = [...document.querySelectorAll('a[href*="/developer/article/' + id + '"]')];
  out.link_count = as.length;
  out.links_visible = as.filter(e => e.offsetParent !== null).map(e => ({
    href: e.getAttribute('href'), txt: (e.textContent||'').trim().slice(0,50),
    rect: (function(){const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})()
  }));
  const a = as.filter(e => e.offsetParent !== null)[0];
  if (!a) return out;
  // 逐层父容器：文本里是否含 编辑/删除/更多，删除图标几个
  out.ancestors = [];
  let box = a;
  for (let i=0;i<10 && box.parentElement;i++){
    box = box.parentElement;
    const dels = [...box.querySelectorAll('span.cdc-svg-icon-con')];
    out.ancestors.push({
      i: i, tag: box.tagName, cls: String(box.className||'').slice(0,60),
      has_act: /编辑|删除|更多/.test(box.textContent||''),
      del_count: dels.length,
      del_texts: dels.map(e=>(e.textContent||'').trim()).slice(0,10)
    });
  }
  // 按 POS_JS 同样的循环选 box，再取 del
  let b2 = a;
  for (let i=0;i<10 && b2.parentElement;i++){
    b2 = b2.parentElement;
    if (/编辑|删除|更多/.test(b2.textContent||'')) break;
  }
  out.chosen_box = {tag:b2.tagName, cls:String(b2.className||'').slice(0,60)};
  const del = [...b2.querySelectorAll('span.cdc-svg-icon-con')]
    .filter(e => e.offsetParent !== null && (e.textContent||'').trim()==='删除')[0];
  if (!del) { out.del_found=false; return out; }
  out.del_found = true;
  const r = del.getBoundingClientRect();
  out.del_rect = [r.x, r.y, r.width, r.height];
  const x = r.x + r.width/2, y = r.y + r.height/2;
  out.click_xy = [x,y];
  out.in_viewport = x>=0 && y>=0 && x<=innerWidth && y<=innerHeight;
  const hit = document.elementFromPoint(x,y);
  out.hit = hit ? (hit.tagName+'.'+String(hit.className||'').slice(0,50)+'|'+(hit.textContent||'').trim().slice(0,20)) : 'null';
  out.is_del_or_child = !!(hit && (hit===del || del.contains(hit)));
  return out;
})(%s))""" % json.dumps(AID)
r = cdp.eval(js)
print(json.dumps(json.loads(r), ensure_ascii=False, indent=2))
