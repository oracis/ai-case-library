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


class TestDuplicates(_TmpData):
    def test_same_case_in_both_files(self):
        self.write("toutiao_published.json", [{"id": "dup", "at": "t"}])
        self.write("toutiao_drafts.json", [{"id": "dup", "at": "t"},
                                           {"id": "only", "at": "t"}])
        self.assertEqual(L.duplicates("toutiao"), ["dup"])
        self.assertEqual(len(L.load("toutiao")), 2, "统一视图里只算一条")

    def test_no_draft_file_platform(self):
        self.assertEqual(L.duplicates("bilibili"), [])


class TestMissingRemoteId(_TmpData):
    def test_lists_legacy_records(self):
        self.write("toutiao_published.json",
                   [{"id": "old", "at": "t"}, {"id": "new", "at": "t", "gid": "1"}])
        self.write("toutiao_drafts.json", [])
        got = [r["id"] for r in L.missing_remote_id("toutiao")]
        self.assertEqual(got, ["old"])


if __name__ == "__main__":
    unittest.main()
