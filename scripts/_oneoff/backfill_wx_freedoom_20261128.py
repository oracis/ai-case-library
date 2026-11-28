#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号 Freedoom 已发侧漏登补账（2026-11-28）。

创作中心已发表列表唯一命中「游戏厅：Freedoom」
appmsg_id=2247484936 / content_url=https://mp.weixin.qq.com/s/2sNJBuoxyLEPSBIlhbVrnQ；
公开页 CDP 回读标题+H1+正文命中，页面时间 Oct 31, 2026, 11:03 PM 江苏
⇒ published_at=2026-10-31 23:03（历史稿，绝不按 now 计入今日档期）。

⛔ 只做定点新增：JSON append 一条 + db 单行 INSERT，写前备份。
用法：python scripts/_oneoff/backfill_wx_freedoom_20261128.py [--apply]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ledger as L  # noqa: E402

CID = "ext-ep-003-freedoom"
TITLE = "游戏厅：Freedoom"
PUB_ID = "2247484936"
URL = "https://mp.weixin.qq.com/s/2sNJBuoxyLEPSBIlhbVrnQ"
PUB_AT = "2026-10-31 23:03"
PATH = os.path.join(ROOT, "data", "wechat_published.json")
DB = os.path.join(ROOT, "data", "ledger.db")
NOTE = "｜11-28 对账：创作中心已发表列表唯一命中 + 公开页标题/正文命中（页面时间 2026-10-31 23:03），本地漏登补账，不再重发"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    raw = open(PATH, encoding="utf-8").read()
    data = json.loads(raw)
    rows = list(data.values()) if isinstance(data, dict) else data
    hit = [r for r in rows if r.get("title") == TITLE or r.get("appmsgid") == PUB_ID]
    conn = sqlite3.connect(DB)
    db_hit = conn.execute(
        "select * from platform_posts where case_id=? and platform='wechat'", (CID,)
    ).fetchone()
    print("BEFORE json_hits=%d db_hit=%s" % (len(hit), bool(db_hit)))
    if not a.apply:
        return 0
    ts = time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(PATH, os.path.join(ROOT, "tmp", "backup_wx_published_%s.json" % ts))
    shutil.copy2(DB, os.path.join(ROOT, "tmp", "backup_ledger_%s.db" % ts))
    print("备份后缀 %s" % ts)

    rec = {
        "title": TITLE,
        "published_at": PUB_AT,
        "status": "published",
        "appmsgid": PUB_ID,
        "published_appmsgid": PUB_ID,
        "file": "",
        "publish_type": "publish",
        "sent_via": "reconcile",
        "send_reason": "线上已发表但本地漏登，按标题唯一命中补账",
        "note": NOTE,
        "verified_at": "2026-11-28 11:00",
    }
    if isinstance(data, dict):
        data[CID] = rec
    else:
        data.append(rec)
    _, _, nl, ind = L._read_file(PATH)
    with open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=ind).replace("\n", nl))
    cur = conn.execute(
        "insert into platform_posts(case_id,platform,state,remote_id,url,title,"
        "draft_id,attempt,published_at,updated_at,note) values(?,?,?,?,?,?,?,?,?,?,?)",
        (CID, "wechat", "published", PUB_ID, URL, TITLE, "", 0, PUB_AT, PUB_AT, NOTE),
    )
    conn.commit()
    print("db INSERT rowcount=%d id=%s" % (cur.rowcount, cur.lastrowid))
    after_data = json.loads(open(PATH, encoding="utf-8").read())
    after = [r for r in (after_data.values() if isinstance(after_data, dict) else after_data)
             if r.get("appmsgid") == PUB_ID]
    print("AFTER json_hits=%d" % len(after))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
