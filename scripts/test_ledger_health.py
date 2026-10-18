#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`ledger_health.py` 判据回归：只测「怎么下结论」，不碰浏览器、不碰真台账。

钉死的核心：**回填 ID 只认硬证据，绝不靠标题相似度猜** ——
猜错一次就会把 A 文章的 ID 记到 B 名下，之后每一轮巡检都跟着错。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L          # noqa: E402
import ledger_health as LH  # noqa: E402


class _NoIO(unittest.TestCase):
    """把 L.get 换成内存桩，避免测试读到真台账。"""

    def stub(self, rec):
        self._old = L.get
        L.get = lambda platform, cid: rec

    def tearDown(self):
        if hasattr(self, "_old"):
            L.get = self._old


class TestBackfill(_NoIO):
    REMOTE = {"pub": {"53385991": "Rezi：AI 简历生成器",
                      "53280245": "MORT：AI 求职代理"},
              "draft": {}, "draft_ok": True, "pub_ok": True}

    def test_cv_number_in_note(self):
        self.stub({"id": "rezi", "note": "P1对账: 标题直接命中(cv53385991) → Rezi"})
        gid, why = LH._backfill("bilibili", "rezi", self.REMOTE)
        self.assertEqual(gid, "53385991")
        self.assertIn("cv", why)

    def test_alias_in_note(self):
        self.stub({"id": "mort", "note": "对账: 『MORT：AI 求职代理』在册"})
        gid, _ = LH._backfill("bilibili", "mort", self.REMOTE)
        self.assertEqual(gid, "53280245")

    def test_no_guess_when_ambiguous(self):
        """标题不精确相等就不回填 —— 相似度匹配只用于诊断，不能写账。"""
        self.stub({"id": "x", "title": "Rezi：AI 简历生成器（旧口径）"})
        gid, why = LH._backfill("bilibili", "x", self.REMOTE)
        self.assertIsNone(gid)
        self.assertIn("无唯一命中", why)

    def test_empty_remote(self):
        self.stub({"id": "x", "note": "cv12345678"})
        gid, why = LH._backfill("bilibili", "x",
                                {"pub": {}, "pub_ok": False})
        self.assertIsNone(gid)
        self.assertIn("没读到", why)


class TestCheckOnline(unittest.TestCase):
    def _run(self, recs, remote):
        return {i["code"]: i for i in LH.check_online("x", recs, remote)}

    def test_stale(self):
        """本地记已发布、线上 ID 不在列表里 ⇒ STALE（可能被删，别自动重发）。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "999"}},
                        {"pub": {"111": "别的"}, "pub_ok": True})
        self.assertIn("STALE", got)

    def test_promote(self):
        """本地还是草稿、线上已发布 ⇒ PROMOTE。"""
        got = self._run({"a": {"id": "a", "state": L.DRAFT, "remote_id": "111"}},
                        {"pub": {"111": "标题"}, "pub_ok": True})
        self.assertIn("PROMOTE", got)

    def test_orphan(self):
        """线上有、本地任何记录都没指向它 ⇒ ORPHAN。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "111"}},
                        {"pub": {"111": "已登记", "222": "没登记"},
                         "pub_ok": True})
        self.assertIn("ORPHAN", got)
        self.assertEqual(got["ORPHAN"]["detail"].count("222"), 1)

    def test_note_alias_suppresses_second_article(self):
        """note 里写过的『旧篇标题』不算漏登（bustem/cometly 各两篇）。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "111",
                               "note": "旧篇『B：老标题』也在线上"}},
                        {"pub": {"111": "A：新", "222": "B：老标题"},
                         "pub_ok": True})
        self.assertNotIn("ORPHAN", got)

    def test_no_conclusion_when_unreadable(self):
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "999"}},
                        {"pub": {}, "pub_ok": False})
        self.assertNotIn("STALE", got, "读不到 ≠ 线上没有")


class TestOffline(_NoIO):
    def test_unverified_flagged(self):
        recs = {"a": {"id": "a", "state": L.UNVERIFIED, "remote_id": ""}}
        codes = [i["code"] for i in LH.check_offline("toutiao", recs)]
        self.assertIn("UNVERIFIED", codes)

    def test_no_id_flagged(self):
        recs = {"a": {"id": "a", "state": L.PUBLISHED, "remote_id": ""}}
        codes = [i["code"] for i in LH.check_offline("toutiao", recs)]
        self.assertIn("NO_ID", codes)


if __name__ == "__main__":
    unittest.main()
