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
            "text": ""}


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


class Record(unittest.TestCase):
    TITLE = "Chessigma：百万用户级实时对弈的负载形状与算力成本"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="yun_state_")
        self.db = os.path.join(self.tmp, "ledger.db")
        c = sqlite3.connect(self.db)
        c.execute(SCHEMA)
        c.commit()
        c.close()
        self.fetch_ok = lambda url, timeout=15: page(title=self.TITLE)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
