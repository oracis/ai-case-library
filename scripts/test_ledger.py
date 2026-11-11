#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`ledger.py` 回归锁：一条记录一个事实、远端 ID 当主键、格式不动。

钉死的四件事：
  1. round-trip 不丢字段、不改形状（替换调用方时 git diff 必须干净）；
  2. 状态迁移是一次原子写（draft 文件里消失 + published 里出现）；
  3. 没有远端 ID 的「已发布」必须降级成 unverified（假成功的闸门）；
  4. 平台自己的 ID 字段名要写对（note_id / gid / appmsgid）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402


class _TmpData(unittest.TestCase):
    """每个用例一个干净的 data/ 目录（绝不碰真台账）。"""

    def setUp(self):
        self._old = L.DATA
        self.dir = tempfile.mkdtemp(prefix="ledger_")
        L.DATA = self.dir

    def tearDown(self):
        L.DATA = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, obj, indent=1):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8",
                  newline="") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=indent))

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return json.load(f)


class TestRoundTrip(_TmpData):
    def test_list_layout_untouched(self):
        self.write("toutiao_published.json",
                   [{"id": "a", "at": "2026-09-28 20:40",
                     "note": "旧口径『月收$1K：某物』"}])
        self.write("toutiao_drafts.json", [{"id": "b", "at": "2026-10-13 23:41"}])
        recs = L.load("toutiao")
        L.save("toutiao", recs)
        self.assertEqual(self.read("toutiao_published.json")[0]["note"],
                         "旧口径『月收$1K：某物』")
        self.assertEqual([x["id"] for x in self.read("toutiao_drafts.json")], ["b"])

    def test_dict_layout_untouched(self):
        self.write("wechat_published.json",
                   {"a": {"title": "T", "published_at": "2026-09-01 10:00",
                          "status": "draft", "file": "x.html"}},
                   indent=1)
        L.save("wechat", L.load("wechat"))
        v = self.read("wechat_published.json")["a"]
        self.assertEqual(v["status"], "draft")
        self.assertEqual(v["file"], "x.html")
        self.assertEqual(v["title"], "T")

    def test_idempotent_twice(self):
        self.write("xhs_published.json",
                   [{"id": "a", "at": "t", "note_id": "123", "title": "标题"}])
        self.write("xhs_drafts.json", [])
        for _ in range(2):
            L.save("xiaohongshu", L.load("xiaohongshu"))
        self.assertEqual(self.read("xhs_published.json"),
                         [{"id": "a", "at": "t", "note_id": "123", "title": "标题"}])

    def test_wechat_deleted_survives_round_trip(self):
        """公众号 `status=deleted` 必须能存住（2026-11-10）。

        出处：`wx-100000514`（appmsgid=2247485037）平台 `is_deleted=true`。
        改之前 `_rec_from_wechat` 写死 `PUBLISHED if st == PUBLISHED else DRAFT`，
        `deleted` 一 `load` 就变 draft，`save` 又把 `status` 写回 draft ⇒
        实测掉成草稿。后果不是显示问题：FIFO 去重与档期都按状态判，
        一篇**已删除**的文章会被当成「没发过的草稿」再发一次。
        """
        self.write("wechat_published.json",
                   {"wx-del": {"title": "已删稿", "status": "deleted",
                               "published_at": None,
                               "deleted_at": "2026-11-10 17:25",
                               "appmsgid": "100000514"}})
        rec = L.load("wechat")["wx-del"]
        self.assertEqual(rec["state"], "deleted", "load 阶段就不许降级")
        L.save("wechat", L.load("wechat"))
        v = self.read("wechat_published.json")["wx-del"]
        self.assertEqual(v["status"], "deleted", "save 阶段也不许归一成草稿/已发")
        self.assertEqual(v["deleted_at"], "2026-11-10 17:25", "对账时点不能丢")
        self.assertIsNone(v["published_at"])


class TestTransition(_TmpData):
    def test_promote_moves_record_atomically(self):
        self.write("toutiao_published.json", [])
        self.write("toutiao_drafts.json", [{"id": "c", "at": "2026-10-13 23:41"}])
        L.promote("toutiao", "c", remote_id="7694", title="C：标题")
        self.assertEqual(self.read("toutiao_drafts.json"), [],
                         "草稿侧必须同时消失，否则又是两本账")
        pub = self.read("toutiao_published.json")
        self.assertEqual(len(pub), 1)
        self.assertEqual(pub[0]["gid"], "7694")
        self.assertEqual(pub[0]["at"], "2026-10-13 23:41", "原时间不能丢")

    def test_no_remote_id_degrades_to_unverified(self):
        self.write("toutiao_published.json", [])
        self.write("toutiao_drafts.json", [])
        rec = L.mark("toutiao", "d", "published")       # 没给 ID
        self.assertEqual(rec["state"], L.UNVERIFIED,
                         "无远端 ID 的『已发布』必须降级，否则假成功进账")
        self.assertIn("unverified", rec["note"])

    def test_remove(self):
        self.write("toutiao_published.json", [{"id": "a", "at": "t"}])
        self.write("toutiao_drafts.json", [])
        self.assertTrue(L.remove("toutiao", "a"))
        self.assertEqual(self.read("toutiao_published.json"), [])
        self.assertFalse(L.remove("toutiao", "a"))


class TestPlatformIdField(_TmpData):
    def test_never_uses_plain_id(self):
        """远端 ID 字段绝不能叫 `id` —— list 布局里 `id` 是 **case id**。

        2026-10-19 踩过：给 B站加了个 "id" 候选，结果把 case id "rezi"
        当成文章 ID 写进台账，之后每一轮巡检都会拿它去比对线上。
        """
        for plat, fields in L.REMOTE_ID_FIELD.items():
            self.assertNotIn("id", fields,
                             "%s 的远端 ID 字段不能含 'id'" % plat)

    def test_bilibili_article_id(self):
        self.write("bili_published.json", [])
        L.promote("bilibili", "rezi", remote_id="53385991")
        row = self.read("bili_published.json")[0]
        self.assertEqual(row["article_id"], "53385991")
        self.assertEqual(row["id"], "rezi")

    def test_xhs_uses_note_id(self):
        self.write("xhs_published.json", [])
        self.write("xhs_drafts.json", [])
        L.promote("xiaohongshu", "x", remote_id="6abb39e8")
        row = self.read("xhs_published.json")[0]
        self.assertEqual(row["note_id"], "6abb39e8")
        self.assertNotIn("remote_id", row, "内部字段名不许漏到盘上")

    def test_wechat_uses_appmsgid(self):
        self.write("wechat_published.json", {})
        L.promote("wechat", "w", remote_id="100000121", title="W")
        v = self.read("wechat_published.json")["w"]
        self.assertEqual(v["appmsgid"], "100000121")
        self.assertEqual(v["status"], "published")


class TestDraftLedger(_TmpData):
    """B站草稿账（2026-10-19 建）—— 线上 49 条草稿曾经一条本地记录都没有。"""

    def _blank(self):
        self.write("bili_published.json", [])
        self.write("bili_drafts.json", [])

    def test_set_draft_id_writes_draft_file(self):
        self._blank()
        L.set_draft_id("bilibili", "bustem", "342372", title="Bustem：打假")
        row = self.read("bili_drafts.json")[0]
        self.assertEqual(row["id"], "bustem")
        self.assertEqual(row["article_id"], "342372",
                         "B站文章的远端 ID 字段名是 article_id（不是 bvid）")
        self.assertEqual(self.read("bili_published.json"), [])
        self.assertEqual(L.load("bilibili")["bustem"]["state"], L.DRAFT)

    def test_no_internal_names_leak_to_disk(self):
        self._blank()
        L.set_draft_id("bilibili", "x", "1")
        L.promote("bilibili", "x", remote_id="53460777", title="T")
        for name in ("bili_drafts.json", "bili_published.json"):
            for row in self.read(name):
                self.assertNotIn("remote_id", row)
                self.assertNotIn("draft_remote_id", row,
                                 "内部字段名漏到盘上 ⇒ 下次 round-trip 多一个字段")

    def test_draft_and_published_coexist(self):
        """发过文的 case 又存了一条草稿 ⇒ 两个 ID 都在，状态不被拉回去。

        这是 set_draft_id 存在的理由：`mark(..., DRAFT)` 会把已发布记成草稿，
        等于宣布一篇发出去的文章没发。
        """
        self._blank()
        L.promote("bilibili", "trustmrr", remote_id="53460777", title="T")
        L.set_draft_id("bilibili", "trustmrr", "342372")
        rec = L.load("bilibili")["trustmrr"]
        self.assertEqual(rec["state"], L.PUBLISHED)
        self.assertEqual(rec["remote_id"], "53460777")
        self.assertEqual(rec["draft_remote_id"], "342372")
        self.assertEqual(self.read("bili_published.json")[0]["article_id"],
                         "53460777")
        self.assertEqual(self.read("bili_drafts.json")[0]["article_id"],
                         "342372")

    def test_draft_row_survives_repeated_save(self):
        """反复 save 不能把「已发布 + 草稿还在」那一行吃掉。

        旧规则是 `state != DRAFT ⇒ 不写草稿文件`，那样下次任何一次 save
        都会静默删掉线上真实存在的一条草稿 —— 账本凭空少一行，且不可恢复。
        """
        self._blank()
        L.promote("bilibili", "mort", remote_id="53280245")
        L.set_draft_id("bilibili", "mort", "342369")
        for _ in range(3):
            L.save("bilibili", L.load("bilibili"))
        drafts = self.read("bili_drafts.json")
        self.assertEqual(len(drafts), 1, "草稿行被 save 弄丢了")
        self.assertEqual(drafts[0]["article_id"], "342369")
        rec = L.load("bilibili")["mort"]
        self.assertEqual(rec["state"], L.PUBLISHED)
        self.assertEqual(rec["draft_remote_id"], "342369")

    def test_promote_preserves_draft_id(self):
        self._blank()
        L.set_draft_id("bilibili", "voklit", "342350")
        L.promote("bilibili", "voklit", remote_id="53375028")
        rec = L.load("bilibili")["voklit"]
        self.assertEqual(rec["draft_remote_id"], "342350",
                         "升已发布后草稿 ID 必须留着（线上那条草稿还在）")


class TestDuplicates(_TmpData):
    def test_same_case_in_both_files(self):
        self.write("toutiao_published.json", [{"id": "dup", "at": "t"}])
        self.write("toutiao_drafts.json", [{"id": "dup", "at": "t"},
                                           {"id": "only", "at": "t"}])
        self.assertEqual(L.duplicates("toutiao"), ["dup"])
        self.assertEqual(len(L.load("toutiao")), 2, "统一视图里只算一条")

    def test_no_draft_file_platform(self):
        # 公众号是 dict 布局、状态在 value 里，天然没有第二本账
        self.assertEqual(L.duplicates("wechat"), [])


class TestMissingRemoteId(_TmpData):
    def test_lists_legacy_records(self):
        self.write("toutiao_published.json",
                   [{"id": "old", "at": "t"}, {"id": "new", "at": "t", "gid": "1"}])
        self.write("toutiao_drafts.json", [])
        got = [r["id"] for r in L.missing_remote_id("toutiao")]
        self.assertEqual(got, ["old"])


class TestDbSyncDraftHasNoPublishedAt(unittest.TestCase):
    """2026-11-10：`_db_sync` 把草稿的 `at`（建稿时间）写成 `published_at`。

    实测残留 82 行（bilibili 46 + wechat 36）`state='draft'` 却带
    `published_at`；wechat 那批 note 里还留着「[11-09 错账清理] 原
    published_at 已迁出」——说明 11-09 清过，之后每次 `L.save()` 全本
    upsert 又刷回旧值（`set_draft_id(at=…)` / `mark(DRAFT)` 都把草稿时间
    放在 `at` 里）。

    修法：与 `db.upsert_post` 2026-11-07 同一口径 —— 显式 draft 时置 None。
    """

    def _mem_db(self):
        """内存库 + patch 掉 ledger 的 db 出口（照 test_publish_slot 的先例）。"""
        import sqlite3
        import db
        conn = db.get_conn()
        schemas = conn.execute(
            "select sql from sqlite_master where type='table' "
            "and name in ('cases','platform_posts')").fetchall()
        db.close_conn()
        mem = sqlite3.connect(':memory:')
        for (sql,) in schemas:
            mem.execute(sql)
        return mem, db

    def test_draft_sync_leaves_published_at_null(self):
        from unittest.mock import patch
        mem, db = self._mem_db()
        rec = {"id": "probe-draft", "state": "draft", "at": "2026-10-25 01:21",
               "remote_id": "100001090", "draft_remote_id": "100001090",
               "title": "草稿标题", "note": "｜按远端草稿箱回填"}
        with patch.object(L, "DATA", os.path.join(L.ROOT, "data")), \
             patch.object(db, "get_conn", return_value=mem), \
             patch.object(db, "close_conn", return_value=None):
            L._db_sync("wechat", rec)
        row = mem.execute(
            "select state,published_at,remote_id from platform_posts "
            "where case_id='probe-draft'").fetchone()
        self.assertEqual(row[0], "draft")
        self.assertIsNone(row[1], "草稿行被写了 published_at（会按「已发」算档期）")
        self.assertEqual(row[2], "100001090", "草稿的远端 ID 仍要留着")

    def test_published_sync_keeps_published_at(self):
        from unittest.mock import patch
        mem, db = self._mem_db()
        rec = {"id": "probe-pub", "state": "published", "at": "2026-11-03 13:42",
               "remote_id": "345869", "title": "已发标题"}
        with patch.object(L, "DATA", os.path.join(L.ROOT, "data")), \
             patch.object(db, "get_conn", return_value=mem), \
             patch.object(db, "close_conn", return_value=None):
            L._db_sync("bilibili", rec)
        row = mem.execute(
            "select state,published_at from platform_posts "
            "where case_id='probe-pub'").fetchone()
        self.assertEqual(row, ("published", "2026-11-03 13:42"),
                         "已发布行的真实发表时点不能被清")

    def test_draft_sync_does_not_wipe_existing_published_at_when_already_published(self):
        """先 published 后 draft：状态不许回退，published_at 也不许被清。"""
        from unittest.mock import patch
        mem, db = self._mem_db()
        mem.execute("insert into cases (id,type,status) values ('probe-mix','case','ok')")
        mem.execute(
            "insert into platform_posts (case_id,platform,state,remote_id,published_at,updated_at) "
            "values ('probe-mix','bilibili','published','345869','2026-11-03 13:42','2026-11-03 13:42')")
        rec = {"id": "probe-mix", "state": "draft", "at": "2026-11-10 09:00",
               "remote_id": "345869"}
        with patch.object(L, "DATA", os.path.join(L.ROOT, "data")), \
             patch.object(db, "get_conn", return_value=mem), \
             patch.object(db, "close_conn", return_value=None):
            L._db_sync("bilibili", rec)
        row = mem.execute(
            "select state,published_at from platform_posts "
            "where case_id='probe-mix'").fetchone()
        self.assertEqual(row, ("published", "2026-11-03 13:42"),
                         "draft 的同步不得把已发布行打回未发")

    def test_deleted_sync_leaves_published_at_null(self):
        """平台已删的行，`published_at` 也必须为空（2026-11-10 补）。

        出处：公众号 `wx-100000514`（appmsgid=2247485037）平台 `is_deleted=true`，
        对账时 `state` 降 `deleted`、`published_at` 置 NULL。但 JSON 台账里
        是可能留着发表时间的（那确实是历史事实），一旦 `L.save()` 全本 upsert，
        `_db_sync` 就会把 pt 写回 —— 和那 82 行草稿脏数据同一个出口。
        库里既有 deleted 行（`magicslides-app/toutiao`）就是 NULL + note 记原值。
        """
        from unittest.mock import patch
        mem, db = self._mem_db()
        rec = {"id": "probe-del", "state": "deleted", "at": "2026-11-10 12:29",
               "remote_id": "100000514", "title": "已删标题",
               "note": "｜[11-10 对账] is_deleted=true"}
        with patch.object(L, "DATA", os.path.join(L.ROOT, "data")), \
             patch.object(db, "get_conn", return_value=mem), \
             patch.object(db, "close_conn", return_value=None):
            L._db_sync("wechat", rec)
        row = mem.execute(
            "select state,published_at from platform_posts "
            "where case_id='probe-del'").fetchone()
        self.assertEqual(row[0], "deleted")
        self.assertIsNone(row[1], "已删行被写了 published_at（会按「已发」算档期）")

    def test_deleted_is_not_downgraded_by_later_draft(self):
        """deleted 是终态（rank=5）：后续 draft 同步不许把状态打回、也不许补 pt。"""
        from unittest.mock import patch
        mem, db = self._mem_db()
        mem.execute("insert into cases (id,type,status) values ('probe-del2','case','ok')")
        mem.execute(
            "insert into platform_posts (case_id,platform,state,remote_id,published_at,updated_at) "
            "values ('probe-del2','wechat','deleted','100000514',NULL,'2026-11-10 17:25')")
        rec = {"id": "probe-del2", "state": "draft", "at": "2026-11-10 18:00",
               "remote_id": "100000514"}
        with patch.object(L, "DATA", os.path.join(L.ROOT, "data")), \
             patch.object(db, "get_conn", return_value=mem), \
             patch.object(db, "close_conn", return_value=None):
            L._db_sync("wechat", rec)
        row = mem.execute(
            "select state,published_at from platform_posts "
            "where case_id='probe-del2'").fetchone()
        self.assertEqual(row, ("deleted", None),
                         "已删行被 draft 同步打回或补了 pt")


if __name__ == "__main__":
    unittest.main()
