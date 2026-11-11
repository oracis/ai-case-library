# -*- coding: utf-8 -*-
"""11-11 全平台发布收尾：三条台账脏行单行补正（先备份 db）。

证据见 PRD #134 / .workbuddy/memory/2026-11-11.md。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
DB = os.path.join(ROOT, "data", "ledger.db")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row


def one(sql, params=()):
    cur = conn.execute(sql, params)
    assert cur.rowcount == 1, "rowcount=%s sql=%s" % (cur.rowcount, sql[:80])
    return cur


# ① 知乎终点是草稿 ⇒ published_at 必须为空
r = conn.execute(
    "select id,published_at from platform_posts where case_id='autoreels-ai' "
    "and platform='zhihu'"
).fetchall()
assert len(r) == 1 and r[0]["published_at"]
one("update platform_posts set published_at=NULL, "
    "note=note||' ｜ [11-11 收尾] 终点=草稿，published_at 清零' where id=?",
    (r[0]["id"],))
print(" zhihu/autoreels-ai: published_at → NULL")

# ② CSDN 实际已发表（列表 170484691 + 公开页 8025 字 / 5 词全中），
#    但脚本只写了 draft ⇒ 不补会被 dedupe 明天再发一遍
r = conn.execute(
    "select id,state,remote_id,published_at from platform_posts "
    "where case_id='autoreels-ai' and platform='csdn'"
).fetchall()
assert len(r) == 1 and r[0]["state"] == "draft" and not r[0]["remote_id"]
one("update platform_posts set state='published', remote_id=?, url=?, "
    "note=note||' ｜ [11-11 收尾] 独立双回读：列表 170484691 按标题命中 + "
    "公开页 HTTP 200 / 8025 字 / 5 关键词全中 ⇒ draft→published' where id=?",
    ("170484691",
     "https://blog.csdn.net/sinat_32396037/article/details/170484691",
     r[0]["id"]))
print(" csdn/autoreels-ai: draft → published / 170484691")

# ③ B站一号 FIFO 补账沿用了建稿时点（2026-10-14），改成真发时点
r = conn.execute(
    "select id,published_at from platform_posts where case_id='podawaa' "
    "and platform='bilibili'"
).fetchall()
assert len(r) == 1 and r[0]["published_at"] == "2026-10-14 12:09"
one("update platform_posts set published_at=?, "
    "note=note||' ｜ [11-11 收尾] published_at 由建稿时点改回真发时点' where id=?",
    ("2026-11-11 08:26", r[0]["id"]))
print(" bilibili/podawaa: published_at 2026-10-14 12:09 → 2026-11-11 08:26")

conn.commit()

bad = conn.execute(
    "select count(*) from platform_posts where state!='published' "
    "and published_at is not null and published_at!=''"
).fetchone()[0]
print(" 非 published 却带 published_at：", bad)
assert bad == 0

import publish_slot as SLOT  # noqa: E402
print(" 档期复核：", {p: (SLOT.decide(p).get("today_count"),
                        SLOT.decide(p).get("quota"))
                 for p in ("cnblogs", "xhs", "bilibili", "bilibili2",
                           "zhihu", "csdn", "tencentcloud", "aliyun", "wechat")})

import publish_guard as G  # noqa: E402
for p, c in (("csdn", "autoreels-ai"), ("cnblogs", "ext-ep-006-nethack"),
             ("bilibili", "podawaa"), ("wechat", "wx-100000530"),
             ("tencentcloud", "klar"), ("aliyun", "chessigma")):
    try:
        print(" check_dupe", p, c, "→", G.check_dupe(p, c))
    except Exception as e:  # noqa: BLE001
        print(" check_dupe", p, c, "签名不符，跳过：", type(e).__name__)
