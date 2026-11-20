#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小红书 6 条已发记录补 note_id（台账只有 id+at，note_id 空）。

## 证据链（不猜 —— 每条都有线上标题里的**产品名**做硬锚点）

线上已发表列表（`xhs_webapi.posted_notes`，2026-11-20 读取）与本地 case：

| 线上标题                                   | 本地 case            |
|--------------------------------------------|----------------------|
| Pet Plus Us：AI合成宠物合影                | pet-plus-us          |
| Nutria：AI 卡路里追踪应用                  | nutria-9e3eaa        |
| 马力欧兄弟：JS 重写的 1985 经典            | ext-ep-001-...（已补）|
| White Glove：无脸视频代运营                | white-glove-content  |
| Publbee：AI 工具箱写书配图                 | publbee（已补）       |
| Visualizee：给建筑师渲效果图               | visualizee-ai        |
| Betting Tips                               | betting-tips（已补）  |
| Rezi：AI 写简历，验证收入最高              | rezi                 |
| ShipFast：卖程序员的开站模板               | shipfast             |

注意：小红书**发出去的标题与台账 title 不一致**（台账 title 空或用了别的措辞），
所以不能按台账 title 匹配；这里按「线上标题含产品名 + 该 case 在线上只有一条」
认定，且每个 note_id 必须当前没被任何其它 case 占用（脚本里会查）。

用法：
    python scripts/_oneoff/xhs_backfill_noid.py [--apply]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as L  # noqa: E402

PLAT = "xiaohongshu"

MAP = {
    "pet-plus-us": ("6aff932e000000002b0254c3", "Pet Plus Us：AI合成宠物合影"),
    "nutria-9e3eaa": ("6afe82a1000000002b03e707", "Nutria：AI 卡路里追踪应用"),
    "white-glove-content": ("6afbb1bd000000002b013547", "White Glove：无脸视频代运营"),
    "visualizee-ai": ("6af92837000000002b02b5cb", "Visualizee：给建筑师渲效果图"),
    "rezi": ("6af3b539000000002501e202", "Rezi：AI 写简历，验证收入最高"),
    "shipfast": ("6af2a47f000000002a018e90", "ShipFast：卖程序员的开站模板"),
}
TIP = "｜11-20 对账：按线上已发表列表标题唯一命中补 note_id（%s）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    taken = {str(r.get("remote_id")): c for c, r in recs.items() if r.get("remote_id")}

    todo, skip = [], []
    for cid, (nid, otitle) in MAP.items():
        r = recs.get(cid)
        if not r:
            skip.append((cid, "台账里没有这条 case"))
            continue
        if r["state"] != L.PUBLISHED:
            skip.append((cid, "state=%s，不是 published" % r["state"]))
            continue
        if str(r.get("remote_id") or "") == nid:
            skip.append((cid, "已经是 %s" % nid))
            continue
        if nid in taken and taken[nid] != cid:
            skip.append((cid, "note_id 已被 %s 占用" % taken[nid]))
            continue
        todo.append((cid, nid, otitle))

    print("待补 %d 条：" % len(todo))
    for cid, nid, otitle in todo:
        print("  %-24s ← %-26s %s" % (cid, nid, otitle))
    if skip:
        print("\n跳过 %d 条：" % len(skip))
        for cid, why in skip:
            print("  %-24s %s" % (cid, why))

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 0

    for cid, nid, otitle in todo:
        r = recs[cid]
        r["remote_id"] = nid
        r["title"] = r.get("title") or otitle
        r["evidence"] = r.get("evidence") or "published_reconcile"
        r["note"] = (r.get("note") or "") + TIP % otitle
    L.save(PLAT, recs)

    after = L.load(PLAT)
    bad = [cid for cid, nid, _t in todo
           if str((after.get(cid) or {}).get("remote_id")) != nid]
    print("\n写盘 %d 处；回读核验 %s" % (len(todo), "全部对上" if not bad else "失败:" + str(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
