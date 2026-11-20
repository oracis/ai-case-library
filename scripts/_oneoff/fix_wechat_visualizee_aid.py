#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号 visualizee-ai 补 appmsgid（唯一一条自动认不出的）。

## 为什么自动认不出

线上标题被后台改过：台账记的是「Visualizee.ai：建筑与室内设计的效果图渲染工具」，
线上是「Visualizee.ai：给建筑师和室内设计师做效果图渲染，两年卡在 $150/月，
一次转型跳到 $8.6K」。归一化后既非相等也非前缀，`title_match` 必然失败
（这条正是 `wechat_publish.py` 注释里记的那个历史案例）。

## 凭什么敢认（不是猜标题）

1. 已发表列表（2026-11-20 读，共 60 条）里含 "Visualizee" 的**只有一条**：
   `2247484219`，`is_deleted=false`
2. 那条的主题「效果图渲染 / 建筑师」与本地 case 一致
3. 该 appmsgid 当前没有被任何其它 case 占用

用法：
    python scripts/_oneoff/fix_wechat_visualizee_aid.py [--apply]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as L  # noqa: E402

PLAT = "wechat"
CID = "visualizee-ai"
AID = "2247484219"
ONLINE_TITLE = ("Visualizee.ai：给建筑师和室内设计师做效果图渲染，"
                "两年卡在 $150/月，一次转型跳到 $8.6K")
TIP = "｜11-20 对账：后台改过标题（线上《%s》），按产品名唯一命中补 appmsgid=%s" % (
    ONLINE_TITLE, AID)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    r = recs.get(CID)
    if not r:
        print("⛔ 台账里没有 %s" % CID)
        return 2
    print("当前：state=%s remote_id=%r title=%s"
          % (r["state"], r.get("remote_id"), r.get("title")))
    if str(r.get("remote_id") or "") == AID:
        print("已是目标值，无需修改")
        return 0
    if r.get("remote_id"):
        print("⛔ 已有别的 remote_id=%s ⇒ 拒绝盲改" % r["remote_id"])
        return 2
    taken = [c for c, x in recs.items() if str(x.get("remote_id") or "") == AID]
    if taken:
        print("⛔ %s 已被 %s 占用" % (AID, taken))
        return 2

    print("计划：%s 补 appmsgid=%s（线上《%s》）" % (CID, AID, ONLINE_TITLE))
    if not a.apply:
        print("[干跑] 加 --apply 才写盘")
        return 0

    r["remote_id"] = AID
    r["note"] = (r.get("note") or "") + TIP
    L.save(PLAT, recs)

    after = L.get(PLAT, CID) or {}
    ok = str(after.get("remote_id")) == AID
    print("回读：remote_id=%s ⇒ %s" % (after.get("remote_id"), "OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
