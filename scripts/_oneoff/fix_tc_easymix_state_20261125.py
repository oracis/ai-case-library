#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 easymix 腾讯云那一行的脏状态（2026-11-25）。

现象：`yun_finalize._record_publish_state()` 旧逻辑「有 articleId 就当 published」
先把它乐观写成 `state=published / published_at=''`，随后 `record_publish()` 按匿名
公开页判出 auditing 时撞上「已 published 不许降级」硬闸，整行没写进去。
结果：台账 published、公开页 404 —— 三态里最不能出现的组合。

修法：显式 `allow_downgrade=True` 让匿名页裁定生效（404 ⇒ auditing），
`published_at` / `url` 清空，note 追加一段说明。单行 UPDATE，不碰其他行。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db                # noqa: E402
import yun_state as Y    # noqa: E402

CASE = "easymix"
PLAT = "tencentcloud"
AID = "2793275"
DRAFT = "352180"
TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"


def row():
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("select case_id,state,remote_id,draft_id,url,published_at "
                "from platform_posts where platform=? and case_id=?", (PLAT, CASE))
    r = cur.fetchone()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    print("改前:", row())
    if not a.yes:
        print("\n（干跑）加 --yes 才写库")
        return 0
    res = Y.record_publish(PLAT, CASE, DRAFT, AID, TITLE, allow_downgrade=True)
    print("裁定:", res["state"], "｜证据:", res["evidence"])
    print("改后:", row())
    return 0


if __name__ == "__main__":
    sys.exit(main())
