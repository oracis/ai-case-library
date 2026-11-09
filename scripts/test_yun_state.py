#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`yun_state` 回归锁：三态裁定 + platform_posts 单行落库。

全部离线：匿名 GET 由 `fetch` 注入，SQLite 指到 tempfile，不碰真库。

跑法：cd scripts && python -X utf8 -m unittest test_yun_state -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import yun_state as YS  # noqa: E402

SCHEMA = """CREATE TABLE platform_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, platform TEXT,
    state TEXT, remote_id TEXT, url TEXT, title TEXT, draft_id TEXT,
    attempt INTEGER, published_at TEXT, updated_at TEXT, note TEXT)"""


def page(code=200, title="", banner=False, bytes_=1000):
    return {"code": code, "bytes": bytes_, "title": title, "banner": banner,
            "soft": False, "text": ""}


def soft(bytes_=987):
    """平台风控插页：HTTP 200、体积很小、没有 <title>。"""
    return {"code": 200, "bytes": bytes_, "title": "", "banner": False,
            "soft": True, "text": ""}


class Judge(unittest.TestCase):
    TITLE = "GojiberryAI 的信号管道：职业事件采集、实体归一与触达幂等"

    def test_published_needs_200_title_and_no_banner(self):
        j = YS.judge("1789999", self.TITLE,
                     fetch=lambda url, timeout=15: page(title=self.TITLE + "-阿里云"))
        self.assertEqual(j["state"], "published")
        self.assertTrue(j["title_ok"])
        self.assertIn("1789999", j["url"])

    def test_404_is_auditing(self):
        j = YS.judge("1789999", self.TITLE,
                     fetch=lambda url, timeout=15: page(code=404, title=""))
        self.assertEqual(j["state"], "auditing")

    def test_review_banner_is_auditing(self):
        j = YS.judge("1789999", self.TITLE,
                     fetch=lambda url, timeout=15: page(title=self.TITLE, banner=True))
        self.assertEqual(j["state"], "auditing")

    def test_title_mismatch_is_auditing(self):
        """200 且无横幅，但页面是别的文章 ⇒ 不许算 published（防 aid 串号）。"""
        j = YS.judge("1789999", self.TITLE,
                     fetch=lambda url, timeout=15: page(title="完全不同的一篇"))
        self.assertEqual(j["state"], "auditing")
        self.assertFalse(j["title_ok"])

    def test_empty_title_never_published(self):
        j = YS.judge("1789999", "",
                     fetch=lambda url, timeout=15: page(title="任何标题"))
        self.assertEqual(j["state"], "auditing")

    # ---- 风控插页（2026-11-09 实测：17 篇连刷后腾讯云返回 200 + 987 bytes 空标题）
    def test_throttle_page_is_unverified_and_retried(self):
        """200 + 极小页 + 空标题 = 风控插页，**不是**「未公开」的证据。

        必须重试，重试仍如此要标 unverified（调用方据此拒绝落账），
        否则刚发的文章会被记成 auditing、真 published 也会被洗成假象。
        """
        calls = {"n": 0}

        def fetch(url, timeout=15):
            calls["n"] += 1
            return soft()

        j = YS.judge("1789999", self.TITLE, fetch=fetch, backoff=0)
        self.assertTrue(j["unverified"])
        self.assertEqual(j["state"], "auditing")
        self.assertEqual(calls["n"], 3)                  # 默认重试 3 次
        self.assertEqual(j["tries"], 3)
        self.assertIn("风控", j["evidence"])

    def test_throttle_then_ok_publishes(self):
        seq = [soft(), page(title=self.TITLE)]

        def fetch(url, timeout=15):
            return seq.pop(0) if seq else page(title=self.TITLE)

        j = YS.judge("1789999", self.TITLE, fetch=fetch, backoff=0)
        self.assertEqual(j["state"], "published")
        self.assertFalse(j["unverified"])
        self.assertEqual(j["tries"], 2)


class TencentJudge(unittest.TestCase):
    """腾讯云（2026-11-09 #115）：同一套判据，只换 URL 模板与横幅文案。"""

    TITLE = "批量生成短视频的流水线：任务编排、素材幂等与配额回退"

    def test_url_is_public_article_page(self):
        j = YS.judge("2779407", self.TITLE, platform="tencentcloud",
                     fetch=lambda url, timeout=15: page(title=self.TITLE))
        self.assertEqual(j["url"],
                         "https://cloud.tencent.com/developer/article/2779407")
        self.assertNotIn("write-new", j["url"])          # ⛔ 编辑器地址不是证据
        self.assertEqual(j["state"], "published")

    def test_suffix_title_hits(self):
        """公开页标题带「-腾讯云开发者社区-腾讯云」后缀，包含匹配要命中。"""
        j = YS.judge("2779407", self.TITLE, platform="tencentcloud",
                     fetch=lambda url, timeout=15: page(
                         title=self.TITLE + "-腾讯云开发者社区-腾讯云"))
        self.assertTrue(j["title_ok"])
        self.assertEqual(j["state"], "published")

    def test_review_banner_is_auditing(self):
        j = YS.judge("2779407", self.TITLE, platform="tencentcloud",
                     fetch=lambda url, timeout=15: page(
                         title=self.TITLE, banner=True))
        self.assertEqual(j["state"], "auditing")

    def test_404_is_auditing(self):
        j = YS.judge("2779407", self.TITLE, platform="tencentcloud",
                     fetch=lambda url, timeout=15: page(code=404))
        self.assertEqual(j["state"], "auditing")

    def test_unknown_platform_rejected(self):
        with self.assertRaises(ValueError):
            YS.judge("1", "x", platform="csdn",
                     fetch=lambda url, timeout=15: page())


class _DbFixture(unittest.TestCase):
    """临时 SQLite 夹具（不碰真库）；子类只写明自己的 TITLE。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="yun_state_")
        self.db = os.path.join(self.tmp, "ledger.db")
        c = sqlite3.connect(self.db)
        c.execute(SCHEMA)
        c.commit()
        c.close()
        self.fetch_ok = lambda url, timeout=15: page(title=self.TITLE or "")

    def tearDown(self):
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def _ins(self, **kw):
        cols = ("case_id", "platform", "state", "remote_id", "url", "title",
                "draft_id", "published_at", "updated_at", "note")
        vals = tuple(kw.get(c) for c in cols)
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO platform_posts (%s) VALUES (%s)"
                  % (",".join(cols), ",".join("?" * len(cols))), vals)
        c.commit()
        c.close()

    def _rows(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        r = [dict(x) for x in c.execute("select * from platform_posts order by id")]
        c.close()
        return r


class Record(_DbFixture):
    TITLE = "Chessigma：百万用户级实时对弈的负载形状与算力成本"

    def test_published_writes_all_and_only_that_row(self):
        self._ins(case_id="chessigma", platform="aliyun", state="draft",
                  draft_id="1711205", title="旧镜像", published_at=None)
        self._ins(case_id="other", platform="aliyun", state="published",
                  remote_id="111", title="别动我", published_at="2026-01-01 00:00:00")
        r = YS.record_aliyun_publish("chessigma", "1711205", "1789999",
                                     self.TITLE, db_path=self.db, now="2026-11-11 10:00:00",
                                     fetch=self.fetch_ok)
        self.assertEqual(r["state"], "published")
        rows = {x["case_id"]: x for x in self._rows()}
        self.assertEqual(rows["chessigma"]["state"], "published")
        self.assertEqual(rows["chessigma"]["remote_id"], "1789999")
        self.assertEqual(rows["chessigma"]["published_at"], "2026-11-11 10:00:00")
        self.assertIn("/article/1789999", rows["chessigma"]["url"])
        self.assertEqual(rows["other"]["title"], "别动我")          # 未受牵连
        self.assertEqual(rows["other"]["published_at"], "2026-01-01 00:00:00")

    def test_auditing_keeps_published_at_null_and_url_empty(self):
        self._ins(case_id="chessigma", platform="aliyun", state="draft",
                  draft_id="1711205", title="旧镜像", published_at=None)
        r = YS.record_aliyun_publish(
            "chessigma", "1711205", "1789999", self.TITLE, db_path=self.db,
            now="2026-11-11 10:00:00",
            fetch=lambda url, timeout=15: page(code=404))
        self.assertEqual(r["state"], "auditing")
        row = self._rows()[0]
        self.assertEqual(row["state"], "auditing")
        self.assertEqual(row["remote_id"], "1789999")
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["url"], "")       # 公开页 404，不许写链接

    def test_unverified_evidence_refuses_to_write(self):
        """取证失败（风控插页）⇒ 拒绝落账，不是「记成 auditing」。

        2026-11-09 实测：连刷 17 篇公开页后，腾讯云开始返回 200 + 987 bytes 空标题。
        若把它当 auditing 写进去，刚发出的文章会被记成未公开（且清掉 published_at）。
        """
        self._ins(case_id="chessigma", platform="aliyun", state="draft",
                  draft_id="1711205", title="旧镜像", published_at=None)
        with self.assertRaises(RuntimeError) as ctx:
            YS.record_aliyun_publish(
                "chessigma", "1711205", "1789999", self.TITLE, db_path=self.db,
                backoff=0, fetch=lambda url, timeout=15: soft())
        self.assertIn("没取到有效证据", str(ctx.exception))
        row = self._rows()[0]
        self.assertEqual(row["state"], "draft")        # 一个字都没改
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["url"] or "", "")

    def test_insert_when_row_missing(self):
        r = YS.record_aliyun_publish("brandnew", "1790000", "1790000",
                                     self.TITLE, db_path=self.db,
                                     now="2026-11-11 10:00:00", fetch=self.fetch_ok)
        self.assertEqual(r["state"], "published")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["case_id"], "brandnew")
        self.assertEqual(rows[0]["draft_id"], "1790000")

    def test_two_matching_rows_aborts_and_rolls_back(self):
        """唯一索引被绕过时（历史脏数据）必须停下，不许批量覆盖。"""
        for _ in range(2):
            self._ins(case_id="chessigma", platform="aliyun", state="draft",
                      draft_id="1711205", title="旧镜像", published_at=None)
        with self.assertRaises(RuntimeError):
            YS.record_aliyun_publish("chessigma", "1711205", "1789999",
                                     self.TITLE, db_path=self.db, fetch=self.fetch_ok)
        self.assertEqual({x["state"] for x in self._rows()}, {"draft"})

    def test_empty_title_keeps_old_title(self):
        self._ins(case_id="chessigma", platform="aliyun", state="draft",
                  draft_id="1711205", title="旧镜像", published_at=None)
        YS.record_aliyun_publish("chessigma", "1711205", "1789999", "",
                                 db_path=self.db, fetch=self.fetch_ok)
        self.assertEqual(self._rows()[0]["title"], "旧镜像")

    def test_refuses_downgrade_published_to_auditing(self):
        """状态只前进：网络抖动/风控 404 不许把真 published 洗成 auditing。"""
        self._ins(case_id="chessigma", platform="aliyun", state="published",
                  remote_id="1780000", draft_id="1711205", title=self.TITLE,
                  url="https://developer.aliyun.com/article/1780000",
                  published_at="2026-11-01 00:00:00")
        with self.assertRaises(RuntimeError):
            YS.record_aliyun_publish(
                "chessigma", "1711205", "1780000", self.TITLE, db_path=self.db,
                fetch=lambda url, timeout=15: page(code=404))
        row = self._rows()[0]
        self.assertEqual(row["state"], "published")
        self.assertEqual(row["published_at"], "2026-11-01 00:00:00")

    def test_allow_downgrade_when_explicit(self):
        self._ins(case_id="chessigma", platform="aliyun", state="published",
                  remote_id="1780000", draft_id="1711205", title=self.TITLE,
                  url="https://developer.aliyun.com/article/1780000",
                  published_at="2026-11-01 00:00:00")
        r = YS.record_aliyun_publish(
            "chessigma", "1711205", "1780000", self.TITLE, db_path=self.db,
            fetch=lambda url, timeout=15: page(code=404), allow_downgrade=True)
        self.assertEqual(r["state"], "auditing")
        self.assertIsNone(r["after"]["published_at"])



class TencentRecord(_DbFixture):
    """腾讯云落库：复用 Record 的临时库夹具，断言平台列与 url 形态。"""

    TITLE = "批量生成短视频的流水线：任务编排、素材幂等与配额回退"

    def test_published_writes_public_url(self):
        self._ins(case_id="autoreels-ai", platform="tencentcloud",
                  state="draft", draft_id="342800", title="旧镜像",
                  url="https://cloud.tencent.com/developer/article/"
                      "write-new?draftId=342800", published_at=None)
        r = YS.record_tencent_publish(
            "autoreels-ai", "342800", "2779407", self.TITLE, db_path=self.db,
            now="2026-11-09 20:00:00", fetch=self.fetch_ok)
        self.assertEqual(r["state"], "published")
        row = self._rows()[0]
        self.assertEqual(row["remote_id"], "2779407")
        self.assertEqual(row["url"],
                         "https://cloud.tencent.com/developer/article/2779407")
        self.assertEqual(row["published_at"], "2026-11-09 20:00:00")

    def test_auditing_keeps_url_empty_even_if_old_url_was_editor(self):
        """旧行存着 write-new 编辑器地址 ⇒ auditing 时必须清空，不许留假链接。"""
        self._ins(case_id="autoreels-ai", platform="tencentcloud",
                  state="draft", draft_id="342800", title="旧镜像",
                  url="https://cloud.tencent.com/developer/article/"
                      "write-new?draftId=342800", published_at=None)
        YS.record_tencent_publish(
            "autoreels-ai", "342800", "2779407", self.TITLE, db_path=self.db,
            fetch=lambda url, timeout=15: page(code=404))
        row = self._rows()[0]
        self.assertEqual(row["state"], "auditing")
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["url"], "")

    def test_rejected_needs_explicit_downgrade(self):
        """被驳回 = 登录态卡片证据；已 published 的行不许静默降级。"""
        self._ins(case_id="autoreels-ai", platform="tencentcloud",
                  state="published", remote_id="2779407", draft_id="342800",
                  title=self.TITLE,
                  url="https://cloud.tencent.com/developer/article/2779407",
                  published_at="2026-11-01 00:00:00")
        with self.assertRaises(RuntimeError):
            YS.record_tencent_publish(
                "autoreels-ai", "342800", "2779407", self.TITLE, db_path=self.db,
                verdict="rejected", reason="技术无关内容")
        self.assertEqual(self._rows()[0]["state"], "published")

    def test_rejected_lands_draft_with_reason(self):
        self._ins(case_id="autoreels-ai", platform="tencentcloud",
                  state="published", remote_id="2779407", draft_id="342800",
                  title=self.TITLE, url="", published_at="2026-11-01 00:00:00")
        r = YS.record_tencent_publish(
            "autoreels-ai", "342800", "2779407", self.TITLE, db_path=self.db,
            verdict="rejected", reason="你的文章已驳回,原因: 技术无关内容",
            allow_downgrade=True)
        self.assertEqual(r["state"], "draft")
        row = self._rows()[0]
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["url"], "")
        self.assertIn("技术无关内容", row["note"])

    def test_tencent_row_not_touched_by_aliyun_record(self):
        """同 case 两平台互不串行：写腾讯云不许动阿里云那行。"""
        self._ins(case_id="autoreels-ai", platform="aliyun", state="published",
                  remote_id="1780000", title="阿里云那篇",
                  url="https://developer.aliyun.com/article/1780000",
                  published_at="2026-10-01 00:00:00")
        YS.record_tencent_publish(
            "autoreels-ai", "342800", "2779407", self.TITLE, db_path=self.db,
            fetch=self.fetch_ok)
        rows = {x["platform"]: x for x in self._rows()}
        self.assertEqual(rows["aliyun"]["remote_id"], "1780000")
        self.assertEqual(rows["aliyun"]["published_at"], "2026-10-01 00:00:00")
        self.assertEqual(rows["tencentcloud"]["remote_id"], "2779407")


if __name__ == "__main__":
    unittest.main(verbosity=2)
