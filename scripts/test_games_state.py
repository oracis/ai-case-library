#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`games_state.record_published` + 回填脚本的契约测试（离线，不碰真台账）。

钉住 2026-11-06 补写游戏仓台账这件事上最容易「假绿」的五处：

1. 补写必须让**游戏仓自己的** `ledger.blocks_republish()` 与
   `publish_guard._games_dup()` 同时生效 —— 只写进本仓 db 等于没修。
2. 台账读坏了**绝不写盘**（长期 dirty、人也会手改，吞异常会把历史整本覆盖）。
3. 回填不许拿 `now()` 冒充发布时间；草稿 aid → 发布 aid 要留痕。
4. 重跑幂等：已经有 published 就 skipped，文件字节不许变。
5. 格式与游戏仓 `ledger.save()` 同形（LF / indent=2 / 末尾换行），
   写出来的记录要能过游戏仓 `ledger.validate()`。

所有用例都显式传 `path=` 到临时目录（同 `candidate_store` 那条纪律）。
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili2_ledger_backfill as BF        # noqa: E402
import games_state as G                   # noqa: E402
import publish_guard as PG                # noqa: E402

GAMES_ROOT = os.environ.get("GAMES_ROOT", r"D:/open-source-games")


def _drafted(slug="ep-004-quake", aid="376917"):
    return {
        "schema_version": 1,
        "updated_at": "2026-10-31 21:49:23",
        "articles": {
            slug: {
                "title": "游戏厅：Quake",
                "platforms": {"bilibili2": {
                    "state": "drafted", "remote_id": aid, "url": "",
                    "attempted_at": "2026-10-31 21:49:23",
                    "verified_at": "", "note": "draft 退出 0（未真发）",
                }},
            },
        },
    }


class TestRecordPublished(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "publish_state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, obj):
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _raw(self):
        with open(self.path, "rb") as f:
            return f.read()

    def test_新建台账_字段齐且顶层结构合法(self):
        action, entry = G.record_published(
            "bilibili2", "ext-ep-004-quake-b2", remote_id="53790001",
            title="Quake：把渲染管线拆开看", at="2026-11-07 10:30", path=self.path)
        self.assertEqual(action, "written")
        self.assertEqual(set(entry) >= {"state", "remote_id", "url",
                                        "attempted_at", "verified_at", "note"}, True)
        d = self._read()
        self.assertEqual(d["schema_version"], 1)
        self.assertTrue(d["updated_at"])
        art = d["articles"]["ep-004-quake"]
        self.assertEqual(art["title"], "Quake：把渲染管线拆开看")   # 新建时设主标题
        self.assertEqual(art["platforms"]["bilibili2"]["state"], "published")

    def test_drafted升级_不覆盖主标题_旧aid留痕(self):
        self._seed(_drafted())
        action, entry = G.record_published(
            "bilibili2", "ext-ep-004-quake-b2", remote_id="53790001",
            title="Quake：把渲染管线拆开看", at="2026-11-07 10:30",
            note="send-draft 回读命中", path=self.path)
        self.assertEqual(action, "written")
        self.assertEqual(entry["attempted_at"], "2026-11-07 10:30:00")
        self.assertEqual(entry["verified_at"], "2026-11-07 10:30:00")
        self.assertIn("376917 → 发布 aid 53790001", entry["note"])
        art = self._read()["articles"]["ep-004-quake"]
        self.assertEqual(art["title"], "游戏厅：Quake")       # 别污染该期主标题
        self.assertEqual(art["platforms"]["bilibili2"]["title"],
                         "Quake：把渲染管线拆开看")          # 二号变体记在 entry
        self.assertEqual(G.entries("bilibili2", path=self.path)[0]["title"],
                         "Quake：把渲染管线拆开看")

    def test_幂等_重复调用skipped且字节不变(self):
        self._seed(_drafted())
        G.record_published("bilibili2", "ep-004-quake", remote_id="53790001",
                           at="2026-11-07 10:30", path=self.path)
        raw = self._raw()
        action, entry = G.record_published(
            "bilibili2", "ext-ep-004-quake-b2", remote_id="53790001",
            at="2026-11-08 09:00", path=self.path)
        self.assertEqual(action, "skipped")
        self.assertEqual(entry["state"], "published")
        self.assertEqual(self._raw(), raw)                   # 连 updated_at 都不许动

    def test_坏JSON绝不写盘(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"articles": {"ep-004-quake": ')        # 半截 JSON
        raw = self._raw()
        with self.assertRaises(ValueError):
            G.record_published("bilibili2", "ep-004-quake",
                               remote_id="53790001", path=self.path)
        self.assertEqual(self._raw(), raw)

    def test_顶层不是对象也不写盘(self):
        self._seed(["不是对象"])
        raw = self._raw()
        with self.assertRaises(ValueError):
            G.record_published("bilibili2", "ep-004-quake",
                               remote_id="53790001", path=self.path)
        self.assertEqual(self._raw(), raw)

    def test_参数校验_不许记无ID的published(self):
        for kw in ({"platform": "bilibili"},               # 一号不进游戏仓
                   {"case": ""}, {"remote_id": ""}):
            args = {"platform": "bilibili2", "case": "ep-004-quake",
                    "remote_id": "53790001", "path": self.path}
            args.update(kw)
            with self.subTest(**kw), self.assertRaises(ValueError):
                G.record_published(**args)
        self.assertFalse(os.path.exists(self.path))

    def test_时间不许拿now冒充_非法格式直接抛(self):
        self.assertEqual(G._stamp("2026-11-07 10:30"), "2026-11-07 10:30:00")
        self.assertEqual(G._stamp("2026-11-07 10:30:05"), "2026-11-07 10:30:05")
        self.assertRegex(G._stamp(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        with self.assertRaises(ValueError):
            G._stamp("昨天下午")

    def test_格式与游戏仓ledger_save同形(self):
        G.record_published("bilibili2", "ep-004-quake", remote_id="53790001",
                           at="2026-11-07 10:30", path=self.path)
        raw = self._raw()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertIn(b'\n  "articles": {', raw)            # indent=2

    @unittest.skipUnless(os.path.isdir(GAMES_ROOT), "游戏仓不在本机")
    def test_写出来的记录能过游戏仓自己的validate(self):
        sys.path.insert(0, GAMES_ROOT)
        try:
            import publish.ledger as L
        except ImportError:
            self.skipTest("游戏仓 publish.ledger 不可用")
        self._seed(_drafted())
        G.record_published("bilibili2", "ep-004-quake", remote_id="53790001",
                           at="2026-11-07 10:30", path=self.path)
        self.assertEqual(L.validate(path=self.path), [])

    @unittest.skipUnless(os.path.isdir(GAMES_ROOT), "游戏仓不在本机")
    def test_补写后游戏仓blocks_republish生效(self):
        sys.path.insert(0, GAMES_ROOT)
        try:
            import publish.ledger as L
        except ImportError:
            self.skipTest("游戏仓 publish.ledger 不可用")
        self._seed(_drafted())
        old = L.LEDGER_PATH
        L.LEDGER_PATH = self.path
        try:
            self.assertFalse(L.blocks_republish("ep-004-quake", "bilibili2"))
            G.record_published("bilibili2", "ep-004-quake", remote_id="53790001",
                               at="2026-11-07 10:30", path=self.path)
            self.assertTrue(L.blocks_republish("ep-004-quake", "bilibili2"))
        finally:
            L.LEDGER_PATH = old

    def test_补写后本仓判重第三路生效(self):
        hit, _warn = PG._games_dup("bilibili2", "ext-ep-004-quake",
                                   path=self.path)
        self.assertIsNone(hit)                              # 补写前不拦
        G.record_published("bilibili2", "ep-004-quake", remote_id="53790001",
                           at="2026-11-07 10:30", path=self.path)
        hit, _warn = PG._games_dup("bilibili2", "ext-ep-004-quake-b2",
                                   path=self.path)
        self.assertIsNotNone(hit)
        self.assertFalse(hit["ok"])
        self.assertEqual(hit["level"], "games")


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "publish_state.json")
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            json.dump(_drafted(), f, ensure_ascii=False, indent=2)
            f.write("\n")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _posts():
        return [{"case_id": "ext-ep-004-quake", "state": "published",
                 "remote_id": "53790001", "title": "Quake：技术向",
                 "published_at": "2026-11-07 10:30", "updated_at": ""}]

    def test_gaps找出缺口_go回填后第二次零缺口(self):
        with mock.patch.object(BF.db, "get_posts_by_platform",
                               return_value=self._posts()):
            posts, todo = BF.gaps(self.path)
            self.assertEqual((len(posts), len(todo)), (1, 1))
            old_argv = sys.argv
            try:
                sys.argv = ["x", "--go", "--state-path", self.path]
                with contextlib.redirect_stdout(io.StringIO()):
                    BF.main()
                sys.argv = old_argv
                # 第二次：游戏仓已 published ⇒ 零缺口、不写盘
                _posts, todo2 = BF.gaps(self.path)
                self.assertEqual(todo2, [])
            finally:
                sys.argv = old_argv
        self.assertEqual(G.find("bilibili2", "ep-004-quake",
                                path=self.path)["state"], "published")

    def test_无ID或无发布时间的记录不回填(self):
        bad = self._posts()[0]
        bad["remote_id"] = ""
        with mock.patch.object(BF.db, "get_posts_by_platform",
                               return_value=[bad]):
            self.assertEqual(BF.gaps(self.path)[1], [])
        bad["remote_id"] = "53790001"
        bad["published_at"] = ""
        with mock.patch.object(BF.db, "get_posts_by_platform",
                               return_value=[bad]):
            self.assertEqual(BF.gaps(self.path)[1], [])

    def test_干跑不写盘(self):
        with open(self.path, "rb") as f:
            raw = f.read()
        with mock.patch.object(BF.db, "get_posts_by_platform",
                               return_value=self._posts()):
            old_argv = sys.argv
            try:
                sys.argv = ["x", "--state-path", self.path]
                with contextlib.redirect_stdout(io.StringIO()):
                    BF.main()
            finally:
                sys.argv = old_argv
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), raw)


if __name__ == "__main__":
    unittest.main()
