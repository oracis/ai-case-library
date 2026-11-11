# -*- coding: utf-8 -*-
"""一次性探针 3：删一张图 → 等 20s → 显式暂存 → 重开，看删除能不能保住。"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import xhs_publish as xp
import xhs_draft_fix_images as F

WAIT = int(os.environ.get("PROBE_WAIT", "20"))

s = F.Session()
s.open(fresh=True)
try:
    if not s.open_drawer():
        raise SystemExit("抽屉打不开")
    drafts = s.list_drafts()
    d = drafts[0]
    print("目标：%s（%s），等待 %ds" % (d.get("title", "")[:30],
                                       d.get("saved", ""), WAIT))
    ok, _ = s.open_editor(d.get("title"))
    if not ok:
        raise SystemExit("编辑器没打开")
    n, _ = s.wait_settled()
    info = s.img_info()
    print("开编辑器：%d 张（裂 %d）" % (n, sum(1 for i in info if i["nw"] == 0)))

    if not s.delete_index(0):
        raise SystemExit("删图失败")
    print("删图后：%d 张，等 %ds 让应用收尾..." % (len(s.img_info()), WAIT))
    time.sleep(WAIT)

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
    print("复检：%d 张，裂 %d 张   （删除前是 %d 张）" % (total, broken, n))
    s.leave()
finally:
    s.close()
