# -*- coding: utf-8 -*-
"""一次性探针 2：删一张图 → 显式「暂存离开」→ 重开，看删除到底存没存。

顺带把保存前后页面的弹窗/报错文案抓出来。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s（%s）" % (d.get("title", "")[:30], d.get("saved", "")))
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, settled = s.wait_settled()
    print("开编辑器：图 %d 张" % n)

    # 删第 0 张（裂图）
    if not s.delete_index(0):
        raise SystemExit("删图失败")
    print("删图后：%d 张" % len(s.img_info()))

    # 点「暂存离开」前先看有没有弹窗
    def snap(label):
        t = s.js("(function(){return (document.body.innerText||'')"
                 ".slice(0,600);})()")
        print("--- 页面文案（%s）---\n%s\n---" % (label, (t or "")[:400]))

    # 显式暂存
    st = xp.publish_btn_state(s.sub)
    print("宿主属性：%s" % json.dumps(st.get("host", {}), ensure_ascii=False))
    nid, coord = xp.shadow_publish_btn(s.sub, cls="white")
    print("暂存按钮：%s" % (coord,))
    if nid and coord:
        xp._click_shadow_btn(s.sub, coord)
    time.sleep(3)
    snap("点击暂存后3s")
    time.sleep(7)
    snap("点击暂存后10s")
    print("URL：%s" % (s.js("location.href") or "",))
    sig = s.js(xp._JS_DRAFT_SIGNAL) or {}
    print("暂存信号：%s" % sig)

    # 重开复检
    time.sleep(5)
    if not s.open_drawer():
        print("[err] 抽屉打不开")
        sys.exit(0)
    cur = s.list_drafts()
    want = xp.norm_title(d.get("title") or "")
    now = next((x for x in cur
                if xp.norm_title(x.get("title") or "") == want), None)
    if not now:
        print("草稿箱里找不到这条了！")
        sys.exit(0)
    print("重开（saved=%s）" % now.get("saved", ""))
    ok, _ = s.open_editor(now.get("title"))
    if not ok:
        print("[err] 编辑器没打开")
        sys.exit(0)
    n2, _ = s.wait_settled()
    total, broken = s.img_health()
    print("复检：%d 张，裂 %d 张" % (total, broken))
    s.leave()
finally:
    s.close()
