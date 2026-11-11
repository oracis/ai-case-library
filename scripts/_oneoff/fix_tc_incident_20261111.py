# -*- coding: utf-8 -*-
"""11-11 腾讯云误发事故：按公开页真实内容认账，清掉假 published 行。

证据：匿名 GET https://cloud.tencent.com/developer/article/2781011
  HTTP 200 / 54810 bytes / <title> 为 klar 旧标题逐字 /
  Klar 6、课程材料 17、闪卡 10、预测卷 10；Mindustry 0、塔防 0、沙盒 0。
"""
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
DB = os.path.join(ROOT, "data", "ledger.db")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
now = time.strftime("%Y-%m-%d %H:%M:%S")

klar = conn.execute(
    "select * from platform_posts where case_id='klar' and platform='tencentcloud'"
).fetchall()
fake = conn.execute(
    "select * from platform_posts where case_id='ext-ep-008-mindustry' "
    "and platform='tencentcloud'"
).fetchall()
assert len(klar) == 1 and len(fake) == 1, "前置行数不对，拒绝写"
assert klar[0]["state"] == "draft" and klar[0]["draft_id"] == "338080"
assert fake[0]["state"] == "published" and fake[0]["remote_id"] == "2781011"

note = (klar[0]["note"] or "") + (
    " ｜ [11-11 事故认账] 草稿 338080 被发布脚本直接提交，公开页 2781011 "
    "实为本行 klar 旧稿（标题逐字、正文 Klar/课程材料/闪卡命中，"
    "Mindustry 0 命中）⇒ 按真实内容认领为已发表；属非计划内发布，"
    "当天不再补发第二篇。"
)
cur = conn.execute(
    "update platform_posts set state='published', remote_id=?, published_at=?, "
    "url=?, note=?, updated_at=? where id=?",
    ("2781011", now,
     "https://cloud.tencent.com/developer/article/2781011",
     note, now, klar[0]["id"]),
)
assert cur.rowcount == 1, "klar 更新行数不为 1"
cur = conn.execute("delete from platform_posts where id=?", (fake[0]["id"],))
assert cur.rowcount == 1, "假行删除行数不为 1"
conn.commit()

print(" klar → published / 2781011 /", now)
print(" 假行 ext-ep-008-mindustry@tencentcloud 已删除")
bad = conn.execute(
    "select count(*) from platform_posts where state!='published' "
    "and published_at is not null and published_at!=''"
).fetchone()[0]
print(" 非 published 却带 published_at：", bad)
for r in conn.execute(
    "select case_id,state,remote_id,published_at from platform_posts "
    "where platform='tencentcloud' and case_id in ('klar','ext-ep-008-mindustry')"
):
    print(" ", dict(r))

import publish_slot as SLOT  # noqa: E402
d = SLOT.decide("tencentcloud")
print(" decide(tencentcloud):", {k: d.get(k) for k in
      ("kind", "today_count", "quota", "blocked")})
