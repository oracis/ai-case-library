#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删腾讯云 easymix 第四次驳回稿 2794871、清幂等闸（2026-11-26）。

用户裁定：easymix 这篇稿不再发（四次驳回 2792910/2793275/2794842/2794871
均因「广告/引流」，去品牌名/换写法无效）。删 2794871 进回收站，回读创作中心
同标题应为 0，清掉 publish_tencent 的标题幂等闸。

复用滚动版删稿逻辑（tc_delete_easymix_scroll_20261126.py）：删除图标可能在
视口外（y=832 点击打空），先 scrollIntoView 再取 rect + hit-test；确认框只认
文案「确定删除」（cdc-btn--primary 是「取消」）。

同一次调用内起 9228 CDP + 打开创作中心，避免 DETACHED 回收。默认 dry；--go 真删。
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import tc_manage as TM  # noqa: E402

AID = "2794871"
TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"

ap = argparse.ArgumentParser()
ap.add_argument("--go", action="store_true")
a = ap.parse_args()

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open(TM.LIST_URL)
time.sleep(10)

scroll_js = r"""
JSON.stringify((function(id){
  const a = [...document.querySelectorAll('a[href*="/developer/article/' + id + '"]')]
    .filter(e => e.offsetParent !== null)[0];
  if (!a) return {err:'no-link'};
  let box = a;
  for (let i=0;i<10 && box.parentElement;i++){
    box = box.parentElement;
    if (/编辑|删除|更多/.test(box.textContent||'')) break;
  }
  const del = [...box.querySelectorAll('span.cdc-svg-icon-con')]
    .filter(e => e.offsetParent !== null && (e.textContent||'').trim()==='删除')[0];
  if (!del) return {err:'no-del-btn'};
  del.scrollIntoView({block:'center', inline:'center'});
  const r = del.getBoundingClientRect();
  const x = r.x + r.width/2, y = r.y + r.height/2;
  const hit = document.elementFromPoint(x,y);
  return {x:x, y:y,
          inView: x>=0 && y>=0 && x<=innerWidth && y<=innerHeight,
          hit: hit ? (hit.tagName+'.'+String(hit.className||'').slice(0,40)) : 'null',
          isDel: !!(hit && (hit===del || del.contains(hit)))};
})(%s))""" % json.dumps(AID)
pos = json.loads(cdp.eval(scroll_js) or "{}")
print("滚动后删除图标:", json.dumps(pos, ensure_ascii=False))
if not (pos.get("inView") and pos.get("isDel")):
    raise SystemExit("✗ 删除图标仍不可点（inView/isDel 不满足），停手")

# 删前只读确认同标题命中恰为 2794871
rows = TM.fetch_list(cdp, pages=4)
norm = lambda s: re.sub(r"\s+", "", str(s or ""))
hits = [r for r in rows if norm(r.get("title")) == norm(TITLE)]
print("删前同标题:", [(r.get("id"), r.get("state")) for r in hits])
if [str(r.get("id")) for r in hits] != [AID]:
    raise SystemExit("✗ 删前同标题命中不是唯一 2794871，停手检查")

if not a.go:
    print("\n（干跑）加 --go 才真删")
    sys.exit(0)

for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
    cdp.send("Input.dispatchMouseEvent",
             {"type": tp, "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
    time.sleep(0.3)
time.sleep(2)

btns = json.loads(cdp.eval(TM.CONFIRM_JS) or "[]")
hit = [b for b in btns if b["w"] > 20 and b["txt"] == "确定删除"]
print("确认框按钮:", [(b["txt"], b["w"]) for b in btns])
if not hit:
    raise SystemExit("✗ 没弹「确定删除」确认框，停手")
for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
    cdp.send("Input.dispatchMouseEvent",
             {"type": tp, "x": hit[0]["x"], "y": hit[0]["y"], "button": "left", "clickCount": 1})
    time.sleep(0.3)
time.sleep(3)

# 回读：重新打开创作中心，确认同标题为 0
cdp.open(TM.LIST_URL)
time.sleep(10)
after = TM.fetch_list(cdp, pages=4)
left = [r for r in after if norm(r.get("title")) == norm(TITLE)]
print("\n删后同标题剩余:", [(r.get("id"), r.get("state")) for r in left])
ok = not left
print("✓ 2794871 已删进回收站、幂等闸已清" if ok else "✗ 仍有同标题文章，停手")
sys.exit(0 if ok else 1)
