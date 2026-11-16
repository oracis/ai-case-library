#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补记 2026-11-16 全平台发布一轮里被中断丢写的台账（六家）。

背景：本轮九家里腾讯云/阿里云/博客园 由发布脚本自己写了台账，另外六家
（CSDN、知乎、B站二号、小红书、B站一号、公众号）虽然都已按**远端独立回读**
确认（列表按标题命中 + 公开页正文），但本地 `data/*.json` 与 SQLite
`platform_posts` 里一行都没有 —— 上一次会话被中断时丢了写。

本脚本只补账，不重发、不改稿。每条 remote_id / 时点都来自远端：

  · CSDN     170843268                 列表 09:08:48 命中 + 公开页 5 词
  · 知乎     2105588389919580273        草稿箱首条 2485 字（终点即草稿）
  · B站二号  53930055                   space API publish_time 1794792107 = 09:21
  · 小红书   6afa5b8f000000002203a199    创作中心 09:25
  · B站一号  53930235                   space API publish_time 1794792548 = 09:29
  · 公众号   2247485058                 公开页 var ct = 1794793101 = 09:38

⚠ B站有草稿/已发两本文件：一号 homy 的草稿 aid=343830 留在 bili_drafts.json，
  已发 aid=53930235 追加到 bili_published.json —— 不搬、不覆盖。

用法：python -X utf8 scripts/_oneoff/backfill_20261116.py [--go]
"""
import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

DATA = os.path.join(ROOT, "data")
DB = os.path.join(DATA, "ledger.db")

NOW = "2026-11-16 09:40"
EV = "2026-11-16 全平台一轮：远端独立回读命中后补账（中断丢写）"

# (platform, case_id, state, remote_id, published_at, title, url)
ROWS = [
    ("csdn", "ext-ep-011-minetest", "published", "170843268",
     "2026-11-16 09:08", "Minetest，低配也能跑的开源我的世界",
     "https://blog.csdn.net/sinat_32396037/article/details/170843268"),
    ("zhihu", "ext-ep-008-mindustry", "draft", "2105588389919580273",
     None, "Mindustry，物流即防御的塔防",
     "https://zhuanlan.zhihu.com/p/2105588389919580273"),
    ("bilibili2", "ext-ep-012-openra", "published", "53930055",
     "2026-11-16 09:21", "OpenRA：红警引擎复刻的 Mod 与单位配置",
     "https://www.bilibili.com/read/cv53930055"),
    ("xhs", "publbee", "published", "6afa5b8f000000002203a199",
     "2026-11-16 09:25", "Publbee：AI 工具箱写书配图", ""),
    ("bilibili", "homy", "published", "53930235",
     "2026-11-16 09:29", "Homy：Mac 上更快的文件搜索",
     "https://www.bilibili.com/read/cv53930235"),
    ("wechat", "growingupvideo", "published", "2247485058",
     "2026-11-16 09:38", "GrowingUpVideo：5分钟内将照片变成电影级AI变脸视频",
     "https://mp.weixin.qq.com/s/BBR-d6E6bAXhKuYdArrD5w"),
]

ap = argparse.ArgumentParser()
ap.add_argument("--go", action="store_true")
a = ap.parse_args()


def db_snapshot():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    out = {}
    for p, cid, *_ in ROWS:
        r = c.execute(
            "select id,state,remote_id,published_at from platform_posts "
            "where case_id=? and platform=?", (cid, p)).fetchone()
        out[(p, cid)] = dict(r) if r else None
    c.close()
    return out


def write_db():
    c = sqlite3.connect(DB)
    for p, cid, state, rid, pat, title, url in ROWS:
        r = c.execute(
            "select id from platform_posts where case_id=? and platform=?",
            (cid, p)).fetchone()
        # B站一号草稿 aid 要留在 draft_id 里，别被已发 aid 顶掉
        draft_id = "343830" if (p, cid) == ("bilibili", "homy") else None
        if r:
            c.execute(
                "update platform_posts set state=?, remote_id=?, url=?, title=?, "
                "published_at=?, updated_at=?, "
                "draft_id=coalesce(nullif(draft_id,''), ?), "
                "note=coalesce(note,'')||? where id=?",
                (state, rid, url, title, pat, NOW, draft_id,
                 "｜[2026-11-16 补账] " + EV, r[0]))
        else:
            c.execute(
                "insert into platform_posts (case_id,platform,state,remote_id,url,"
                "title,draft_id,published_at,updated_at,note) "
                "values (?,?,?,?,?,?,?,?,?,?)",
                (cid, p, state, rid, url, title, draft_id, pat, NOW,
                 "[2026-11-16 补账] " + EV))
    c.commit()
    c.close()


def write_jsons():
    # CSDN
    atomicio.update_json(os.path.join(DATA, "csdn_drafts.json"), lambda d: (
        d.__setitem__("ext-ep-011-minetest", {
            "article_id": "170843268",
            "title": "Minetest，低配也能跑的开源我的世界",
            "url": "https://blog.csdn.net/sinat_32396037/article/details/170843268",
            "ts": "2026-11-16 09:08:48"}) or d), indent=2)
    # 知乎（终点就是草稿）
    atomicio.update_json(os.path.join(DATA, "zhihu_drafts.json"), lambda d: (
        d.__setitem__("ext-ep-008-mindustry", {
            "article_id": "2105588389919580273",
            "title": "Mindustry，物流即防御的塔防",
            "url": "https://zhuanlan.zhihu.com/p/2105588389919580273",
            "status": "draft",
            "ts": "2026-11-16 09:12:00"}) or d), indent=2)
    # B站一号已发
    def _bili(d):
        if not any(x.get("id") == "homy" for x in d):
            d.append({"id": "homy", "at": "2026-11-16 09:29",
                      "article_id": "53930235",
                      "title": "Homy：Mac 上更快的文件搜索",
                      "verified_at": NOW, "evidence": "space-article-list",
                      "note": "｜" + EV})
        return d
    atomicio.update_json(os.path.join(DATA, "bili_published.json"), _bili, indent=1)
    # 小红书
    def _xhs(d):
        if not any(x.get("id") == "publbee" for x in d):
            d.append({"id": "publbee", "at": "2026-11-16 09:25",
                      "title": "Publbee：AI 工具箱写书配图",
                      "note": EV})
        return d
    atomicio.update_json(os.path.join(DATA, "xhs_published.json"), _xhs, indent=1)
    # 公众号
    atomicio.update_json(os.path.join(DATA, "wechat_published.json"), lambda d: (
        d.__setitem__("growingupvideo", {
            "title": "GrowingUpVideo：5分钟内将照片变成电影级AI变脸视频",
            "published_at": "2026-11-16 09:38",
            "status": "published",
            "appmsgid": "2247485058",
            "file": ""}) or d), indent=2)


before = db_snapshot()
print("=== DB BEFORE ===")
for k, v in before.items():
    print(" ", k, v)

if not a.go:
    print("\n（干跑）加 --go 才写台账。")
    sys.exit(0)

write_db()
write_jsons()

after = db_snapshot()
print("\n=== DB AFTER ===")
for k, v in after.items():
    print(" ", k, v)

want = {(p, cid): rid for p, cid, _s, rid, *_ in ROWS}
bad = [k for k in after if not after[k] or after[k].get("remote_id") != want[k]]
print("\nMISMATCH", bad if bad else "none")

for fn in ("csdn_drafts.json", "zhihu_drafts.json", "bili_published.json",
           "xhs_published.json", "wechat_published.json"):
    p = os.path.join(DATA, fn)
    d = json.load(open(p, encoding="utf-8"))
    print("%-24s %s" % (fn, len(d)))
