#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对照探针：让页面自己「新建」一篇空白草稿，看是否同样 5009。

`probe_tt_selfsave.py` 已证明「带 pgc_id 更新旧草稿」页面自己也 5009。
但还有一种可能：5009 只针对**更新旧稿**（服务端对这 12 条老草稿拒写），
新建仍能成功 ⇒ 那就还有「删旧建新」的路可走。故必须做这一组对照。

只读观察 + 一次性探针稿；若探针稿真建成（code:0），finally 里必删并核对
草稿箱条数复原。

    /c/Python315/python.exe -X utf8 tmp/probe_tt_newblank.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tmp"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402
from probe_tt_selfsave import JS_HOOK, JS_RECS               # noqa: E402

PUB = "https://mp.toutiao.com/profile_v4/graphic/publish"
PROBE_TITLE = "__probe_selfsave__1111"
OUT = os.path.join(ROOT, "tmp", "ttnewblank.json")

JS_READY = r"""(function(){
  var t=document.querySelector("textarea[placeholder*='文章标题'],input[placeholder*='文章标题']");
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  return JSON.stringify({title:!!t, editors:eds.length,
                         value:(t&&t.value)||'', href:location.href});
})()"""

JS_BODY_CLICK = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  if(!eds.length) return 'notfound';
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  var el=eds[0]; el.scrollIntoView({block:'center'});
  var b=el.getBoundingClientRect();
  return JSON.stringify({x:b.x+Math.min(30,b.width/2),y:b.y+Math.min(20,b.height/2)});
})()"""

JS_STATE = r"""(function(){
  var t=document.querySelector("textarea[placeholder*='文章标题'],input[placeholder*='文章标题']");
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  return JSON.stringify({title:(t&&t.value)||'',
                         bodylen:eds.length?(eds[0].innerText||'').length:0,
                         tip:(document.body.innerText||'').indexOf('草稿保存中...')>=0,
                         recs:(window.__ttRec||[]).length});
})()"""


def insert_body(cdp, text):
    r = cdp.eval(JS_BODY_CLICK)
    if not r or r == "notfound":
        return False
    pos = json.loads(r)
    for typ in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": pos["x"], "y": pos["y"],
                  "button": "left", "clickCount": 1})
    time.sleep(0.4)
    cdp.send("Input.insertText", {"text": text})
    time.sleep(1.0)
    return True


def count_drafts(cdp):
    try:
        rows = tt.fetch_draft_api_raw(cdp)
        if rows is None:
            return -1, []
        return len(rows), rows
    except Exception as e:                                       # noqa: BLE001
        print("  草稿箱读取失败: %s" % e)
        return -1, []


def main():
    # 先用一个独立 CDP 连接读草稿箱基线（必须在开新 tab 之前）
    base = wp.CDP(tt.CDP_PORT)
    t0 = base.new_target("https://mp.toutiao.com/mp/agw/creator_center/draft_list"
                         "?type=0&count=200&app_id=1231")
    if not t0 or not base.connect_target(t0["id"]):
        print("✗ 开不了草稿箱 tab")
        return 1
    n0, rows0 = count_drafts(base)
    print("草稿箱基线：%d 条" % n0, flush=True)
    base.close_target(t0["id"])

    c = wp.CDP(tt.CDP_PORT)
    t = c.new_target("about:blank")
    if not t or not c.connect_target(t["id"]):
        print("✗ 开不了新建页 tab")
        return 1
    made_pid = ""
    try:
        c.send("Emulation.setDeviceMetricsOverride",
               {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                "mobile": False})
        c.send("Page.navigate", {"url": PUB})
        ready = {}
        for _ in range(20):
            time.sleep(1.5)
            try:
                ready = json.loads(c.eval(JS_READY, refresh_context=True) or "{}")
            except Exception:                                    # noqa: BLE001
                continue
            if ready.get("title") and ready.get("editors", 0) > 0:
                break
        print("新建页载入：%s" % json.dumps(ready, ensure_ascii=False), flush=True)
        if not ready.get("title"):
            print("✗ 编辑器没就绪")
            return 1

        print("hook:", c.eval(JS_HOOK), flush=True)
        # 真输入填标题
        from probe_tt_selfsave import type_replace_title
        ok_t = type_replace_title(c, PROBE_TITLE)
        ok_b = insert_body(c, "探针正文一句话，用于验证新建草稿能否保存。")
        print("填标题=%s 填正文=%s" % (ok_t, ok_b), flush=True)
        time.sleep(22)

        recs = json.loads(c.eval(JS_RECS, refresh_context=True) or "[]")
        pubs = [r for r in recs if "/mp/agw/article/publish" in r.get("url", "")]
        print("\n===== 新建页自己发出的 publish 请求 =====", flush=True)
        for r in pubs:
            print("  status=%s resp=%s" % (r.get("status"),
                                           (r.get("resp") or "")[:220]), flush=True)
            resp = r.get("resp") or ""
            if '"code":0' in resp:
                try:
                    made_pid = str((json.loads(resp).get("data") or {}).get("pgc_id") or "")
                except Exception:                                # noqa: BLE001
                    pass
        st = json.loads(c.eval(JS_STATE, refresh_context=True) or "{}")
        print("页内最终：%s" % json.dumps(st, ensure_ascii=False), flush=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"ready": ready, "state": st, "publish": pubs,
                       "made_pid": made_pid}, f, ensure_ascii=False, indent=2)
        print("已写 %s" % OUT, flush=True)
    finally:
        c.close_target(t["id"])
        # 核对草稿箱：若真建成探针稿，按标题找到并删掉
        b2 = wp.CDP(tt.CDP_PORT)
        t2 = b2.new_target("https://mp.toutiao.com/mp/agw/creator_center/draft_list"
                           "?type=0&count=200&app_id=1231")
        if t2 and b2.connect_target(t2["id"]):
            n1, rows1 = count_drafts(b2)
            print("草稿箱收尾：%d 条（基线 %d）" % (n1, n0), flush=True)
            leftovers = [r for r in rows1
                         if PROBE_TITLE in str(r.get("title") or "")]
            print("探针稿残留：%d 条" % len(leftovers), flush=True)
            for r in leftovers:
                gid = str(r.get("gid") or r.get("pgc_id") or "")
                print("  清理 %s -> %s" % (gid, tt.delete_draft_api(b2, gid)),
                      flush=True)
            n2, _ = count_drafts(b2)
            print("清理后草稿箱：%d 条" % n2, flush=True)
            b2.close_target(t2["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
