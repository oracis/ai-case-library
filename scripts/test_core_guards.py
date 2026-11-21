#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心发布闸的回归锁（离线，临时库隔离，不碰真值）。

补的是三个「判错不报错、只会静默出事」的纯逻辑模块——它们此前无同名测试：
  · db.upsert_post     状态只前进 + 空字段回填 + draft 不带 published_at
                       （PRD #82/#203：draft 盖 published 会触发重发，重磅）
  · link_hygiene.scan  自家域名/导流话术命中，封面资源地址豁免
                       （头条 11-02 禁言 30 天的根因闸）
  · compliance_check   禁售类目按目标平台判高危，提示项不误伤技术上下文

跑法：python -m unittest scripts.test_core_guards
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


class _DbBase(unittest.TestCase):
    """每个用例一个临时库；用 LEDGER_DB 钉住，绝不碰 data/ledger.db。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cg_")
        self.db_path = os.path.join(self.tmp, "t.db")
        self._old = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.db_path
        import importlib
        import db
        importlib.reload(db)                     # 让它重读 LEDGER_DB
        self.db = db
        self.conn = db.get_conn()

    def tearDown(self):
        self.db.close_conn()
        if self._old is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class UpsertPostStateMachine(_DbBase):
    def _post(self, state, **kw):
        row = {"state": state}
        row.update(kw)
        self.db.upsert_post(self.conn, "case-x", "wechat", row)

    def test_published_not_overwritten_by_draft(self):
        """已发表被草稿盖掉 ⇒ publish_slot 当成没发过 ⇒ 重磅重发。"""
        self._post("published", remote_id="REAL_ID", published_at="2026-01-01")
        self._post("draft", remote_id="DRAFT_ID")
        got = self.db.get_post(self.conn, "case-x", "wechat")
        self.assertEqual(got["state"], "published", "draft 不许盖掉 published")
        self.assertEqual(got["published_at"], "2026-01-01", "发表时点不许被冲掉")

    def test_empty_fields_backfilled(self):
        """后写没带字段时，空值不许冲掉已有 remote_id/url。"""
        self._post("draft_saved", remote_id="R1", url="https://x/p", title="T")
        self._post("draft_saved", remote_id="", url="", note="")
        got = self.db.get_post(self.conn, "case-x", "wechat")
        self.assertEqual(got["remote_id"], "R1")
        self.assertEqual(got["url"], "https://x/p")
        self.assertEqual(got["title"], "T")

    def test_draft_has_no_published_at(self):
        """draft 带 published_at 会被 quota 判成「今日已发」（11-20 实测）。"""
        self._post("draft", draft_id="D1")
        got = self.db.get_post(self.conn, "case-x", "wechat")
        self.assertFalse(got["published_at"], "draft 不该有 published_at")

    def test_force_state_allows_deliberate_reset(self):
        self._post("published", remote_id="REAL")
        self._post("deleted", force_state=True)
        got = self.db.get_post(self.conn, "case-x", "wechat")
        self.assertEqual(got["state"], "deleted", "显式 force 应允许改状态")

    def test_write_commits_across_connections(self):
        """写完关连接，另开连接必须查得到（否则静默丢写）。"""
        self._post("published", remote_id="COMMIT")
        self.db.close_conn()
        import sqlite3
        conn2 = sqlite3.connect(self.db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT remote_id FROM platform_posts WHERE case_id='case-x'").fetchone()
        conn2.close()
        self.assertIsNotNone(row, "upsert_post 没提交 ⇒ 静默丢写")
        self.assertEqual(row["remote_id"], "COMMIT")


class LinkHygiene(unittest.TestCase):
    def setUp(self):
        import link_hygiene as L
        self.L = L

    def kinds(self, text, **kw):
        return [k for k, _ in self.L.scan(text, **kw)]

    def test_self_host_hit(self):
        self.assertIn("self_host",
                      self.kinds("欢迎来 https://case.ydtgo.top 看全文"))

    def test_self_host_asset_exempt(self):
        """封面资源地址不是导流，别误伤（封面白边事故同类的假阳性）。"""
        kinds = self.kinds("封面 https://case.ydtgo.top/covers/x.png 见下方")
        self.assertNotIn("self_host", kinds)

    def test_leadout_toggle(self):
        text = "加群 加微信 联系我 领取资料"
        self.assertTrue(self.kinds(text, leadout=True))
        self.assertFalse([k for k in self.kinds(text, leadout=False)
                          if k == "leadout"], "leadout=False 应只查域名")

    def test_clean_text_passes(self):
        self.assertEqual(self.kinds("这是一段讲订阅制商业模式的正常正文"), [])

    def test_assert_clean_raises_on_hit(self):
        with self.assertRaises(SystemExit):
            self.L.assert_clean("来 https://case.ydtgo.top 看更多")


class ComplianceCheck(unittest.TestCase):
    def setUp(self):
        import compliance_check as C
        self.C = C

    def test_code_blocks_excluded(self):
        """代码块里提到禁售词不算（strip_code_and_style 的职责）。"""
        text = "```python\n# 博彩 site example\nprint(1)\n```\n正文正常"
        stripped = self.C.strip_code_and_style(text)
        self.assertNotIn("博彩", stripped)

    def test_hit_context_returns_snippet(self):
        ctx = self.C.hit_context("前面一段话 微信 后面一段话", r"微信")
        self.assertTrue(ctx and any("微信" in c for c in ctx))

    def test_check_returns_two_lists(self):
        fatal, warns = self.C.check("这是一段讲 AI 工具的正常正文", "wechat")
        self.assertIsInstance(fatal, list)
        self.assertIsInstance(warns, list)
        self.assertEqual(fatal, [], "正常正文不该有高危项")


if __name__ == "__main__":
    unittest.main(verbosity=2)
