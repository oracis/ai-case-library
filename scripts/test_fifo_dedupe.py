#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`fifo_dedupe.py` 的离线单测：临时库 + 临时台账，不碰真库真数据。

守的是 2026-11-07 两起重复稿的架构级根因（PRD #90）：
FIFO 绕过 11 闸，各入口自己用「标题」认亲 ⇒ 标题改过就认不出 ⇒ 重发。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fifo_dedupe as FD  # noqa: E402


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        self.db_path = os.path.join(self.tmp, "ledger.db")
        c = sqlite3.connect(self.db_path)
        c.execute("CREATE TABLE platform_posts (case_id TEXT, platform TEXT, "
                  "state TEXT, remote_id TEXT)")
        c.commit()
        c.close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def add_post(self, cid, platform, state, remote_id=""):
        c = sqlite3.connect(self.db_path)
        c.execute("INSERT INTO platform_posts VALUES (?,?,?,?)",
                  (cid, platform, state, remote_id))
        c.commit()
        c.close()

    def put(self, name, obj):
        with open(os.path.join(self.data, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def ids(self, platform, **kw):
        return FD.published_case_ids(
            platform, db_path=self.db_path, data_dir=self.data, **kw)


class TestNorm(_Env):
    def test_ext前缀归一到游戏仓同一期(self):
        self.assertEqual(FD.norm("ext-ep-003-freedoom"), "ep-003-freedoom")
        self.assertEqual(FD.norm("  ext-ep-001-x "), "ep-001-x")
        self.assertEqual(FD.norm(""), "")

    def test_普通id不受影响(self):
        self.assertEqual(FD.norm("divine-widgets"), "divine-widgets")


class TestUnion(_Env):
    def test_db和JSON取并集而不是只认db(self):
        self.add_post("db-only", "xhs", "published")
        self.put("xhs_published.json",
                 [{"id": "json-only"}, {"id": "divine-widgets"}])
        self.assertEqual(self.ids("xhs"),
                         {"db-only", "json-only", "divine-widgets"})

    def test_两套id归一后算同一个案例(self):
        self.add_post("ext-ep-003-freedoom", "wechat", "published")
        self.put("wechat_published.json", {"ep-003-freedoom":
                                          {"status": "published"}})
        self.assertEqual(self.ids("wechat"), {"ep-003-freedoom"})

    def test_只拦published不拦draft(self):
        self.add_post("draft-case", "xhs", "draft")
        # 草稿记录也带 remote_id（草稿 id），不许据此判「已发表」
        self.add_post("draft-with-remote", "xhs", "draft", "100001090")
        self.put("wechat_published.json",
                 {"pending": {"status": "draft"},
                  "published-one": {"status": "published"}})
        self.assertNotIn("draft-case", self.ids("xhs"))
        self.assertNotIn("draft-with-remote", self.ids("xhs"))
        self.assertEqual(self.ids("wechat"), {"published-one"})

    def test_草稿带remote_id不许误杀FIFO候选(self):
        """公众号实跑抓到：status=draft + appmsgid 非空 ⇒ 不能算已发。"""
        self.add_post("verse", "wechat", "draft", "100001090")
        self.add_post("ai-smart-decor", "wechat", "draft", "100001079")
        self.put("wechat_published.json",
                 {"verse": {"status": "draft"},
                  "ai-smart-decor": {"status": "draft"}})
        self.assertEqual(self.ids("wechat"), set())

    def test_manifest里published但db没有也算已发(self):
        self.put("publish_manifest.json",
                 {"outrank": {"bilibili": {"state": "published"}},
                  "other": {"bilibili": {"state": "draft"}}})
        self.assertEqual(self.ids("bilibili"), {"outrank"})

    def test_extra补旁证(self):
        self.assertEqual(self.ids("bilibili2", extra=["ext-ep-004-quake"]),
                         {"ep-004-quake"})

    def test_库不存在时不炸也不许凭空建表(self):
        """读函数不许写盘：库不存在时返回空集，且不能凭空造出库文件。"""
        missing = os.path.join(self.tmp, "nope.db")
        self.assertEqual(FD.published_case_ids(
            "xhs", db_path=missing, data_dir=self.data), set())
        self.assertFalse(os.path.exists(missing))


class TestFilterRows(_Env):
    ROWS = [
        {"title": "Divine：给站长卖网站挂件"},   # 标题改过，按 case 仍要拦
        {"title": "最久的未发稿"},
        {"title": "认不出的孤儿稿"},
    ]

    def _case_of(self, r):
        return {"Divine：给站长卖网站挂件": "divine-widgets",
                "最久的未发稿": "coral"}.get(r["title"], "")

    def test_已发案例的旧标题稿被跳过(self):
        kept, skipped = FD.filter_rows(
            self.ROWS, "xhs", self._case_of,
            case_ids={"divine-widgets"}, verbose=False)
        self.assertEqual([r["title"] for r in kept],
                         ["最久的未发稿", "认不出的孤儿稿"])
        self.assertEqual([c for c, _ in skipped], ["divine-widgets"])

    def test_孤儿稿不被拦死(self):
        kept, _ = FD.filter_rows(
            [self.ROWS[2]], "xhs", self._case_of,
            case_ids={"divine-widgets", "coral"}, verbose=False)
        self.assertEqual(len(kept), 1)

    def test_全是已发案例时返回空且不假绿(self):
        kept, skipped = FD.filter_rows(
            self.ROWS[:2], "xhs", self._case_of,
            case_ids={"divine-widgets", "coral"}, verbose=False)
        self.assertEqual(kept, [])
        self.assertEqual(len(skipped), 2)

    def test_传入case_ids时不重复读盘(self):
        with mock.patch.object(FD, "published_case_ids") as pub:
            FD.filter_rows(self.ROWS, "xhs", self._case_of,
                           case_ids={"divine-widgets"}, verbose=False)
        self.assertFalse(pub.called)

    def test_case_is_published认不出孤儿返回False(self):
        with mock.patch.object(FD, "published_case_ids",
                               return_value={"divine-widgets"}):
            self.assertTrue(FD.case_is_published("xhs", "divine-widgets"))
            self.assertFalse(FD.case_is_published("xhs", "never-heard-of"))
            self.assertFalse(FD.case_is_published("xhs", ""))

    def test_批次键台账不许被当成已发案例(self):
        """longtail_sync / yun_finalize 的键不是「已发表案例」清单。"""
        self.put("longtail_sync.json", {"shipfast-2026-11-04-1": {"ok": True}})
        self.put("yun_finalize.json",
                 {"chessigma": {"tc_state": "draft"}})
        self.assertEqual(self.ids("tencentcloud"), set())
        self.assertEqual(self.ids("aliyun"), set())


class TestXhsSameCaliber(unittest.TestCase):
    """小红书先修的并集读口与共用读口在真库上不许漂移。"""

    def test_两份读口对xhs的已发集合一致(self):
        import xhs_publish as X
        a = {FD.norm(c) for c in X._published_case_ids_union() if c}
        b = FD.published_case_ids("xhs")
        self.assertTrue(a.issubset(b),
                        "共用读口漏了 xhs 已发案例：%s" % sorted(a - b)[:5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
