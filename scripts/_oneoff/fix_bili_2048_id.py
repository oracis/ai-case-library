#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修 B站 ext-ep-002-2048 的已发 id：53996900（误发后已删除）→ 53985214。

真因：manifest 镜像 11-20 已改对，但 `bili_published.json` 与 `platform_posts`
两处写入口仍留着被删的 id，体检/去重读 JSON 或 DB 时会认成「发过一条已不存在的」。

用法：python scripts/_oneoff/fix_bili_2048_id.py [--apply]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as L  # noqa: E402

CASE = "ext-ep-002-2048"
PLAT = "bilibili"
OLD, NEW = "53996900", "53985214"
TIP = "｜11-20 修正：53996900 系误重复发布后删除，真实已发 id=%s" % NEW


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    rec = L.get(PLAT, CASE) or {}
    cur = str(rec.get("remote_id") or rec.get("article_id") or "")
    print("当前 %s/%s remote_id=%s state=%s at=%s"
          % (PLAT, CASE, cur, rec.get("state"), rec.get("at")))
    if cur == NEW:
        print("已是目标值，无需修改")
        return 0
    if cur != OLD:
        print("⛔ 当前值既不是 %s 也不是 %s ⇒ 拒绝盲改，先人工核" % (OLD, NEW))
        return 2
    note = rec.get("note") or ""
    if TIP not in note:
        note += TIP
    print("计划：remote_id %s → %s；note 追加留痕" % (OLD, NEW))
    if not a.apply:
        print("[干跑] 加 --apply 才写盘")
        return 0
    # ⚠ 不走 L.mark()：mark 会把 verified_at 刷成现在（红线：快照日不许用
    #   now 覆盖），这里只改 id 与 note，别的字段原样带过。
    recs = L.load(PLAT)
    r = recs[CASE]
    r["remote_id"] = NEW
    r["note"] = note
    L.save(PLAT, recs)
    after = L.get(PLAT, CASE) or {}
    ok = str(after.get("remote_id")) == NEW and after.get("state") == L.PUBLISHED
    print("回读：remote_id=%s state=%s at=%s ⇒ %s"
          % (after.get("remote_id"), after.get("state"), after.get("at"),
             "OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
