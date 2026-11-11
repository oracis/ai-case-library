# -*- coding: utf-8 -*-
"""一次性探针 6：只传 1 张图（不删任何图）→ 等 20s → 显式暂存 → 重开。

回答「含新上传的图，保存到底能不能落进记录」。
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
    n, _ = s.wait_settled()
    print("开编辑器：%d 张" % n)

    rows = F.load_plan([d])
    path = rows[0]["local"][0]
    up, fail = s.upload([path])
    print("上传：%s（失败 %d）" % (len(up), len(fail)))
    loaded = s.wait_new_loaded(1)
    print("新图加载：%s，等 20s..." % loaded)
    time.sleep(20)

    nid, coord = xp.shadow_publish_btn(s.sub, cls="white")
    if nid and coord:
        xp._click_shadow_btn(s.sub, coord)
    print("已点暂存，等 12s...")
    time.sleep(12)

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
    print("复检：%d 张，裂 %d 张   （保存前 %d 张；+1 好图 = %d）"
          % (total, broken, n, n + 1))
    s.leave(save=False)
finally:
    s.close()
