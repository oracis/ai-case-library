#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case_store 单测：真值在 SQLite、cases.json 是逐字节镜像。

这些测试守的是「迁移没把数据弄丢/弄形变」：
- 往返：461 条导入 → 导出，**逐字节**等于原 cases.json
- 14 个曾经缺列的字段一条不少（china_fit/solo_fit/composite/cover_desc…）
- 键序按 field_order 还原（否则每次导出都是整文件 diff）
- 写库后镜像自动重导；prune 是软删不是真删
- LEDGER_DB 隔离（测试永远不碰真库）
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

REAL_MIRROR = os.path.join(ROOT, "data", "cases.json")


def _load(path):
    """读 JSON（带 with，避免 ResourceWarning 噪音）。"""
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


class _Base(unittest.TestCase):
    """每个用例一个临时库 + 临时镜像，真库一个字节都不碰。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cs_")
        self.db_path = os.path.join(self.tmp, "t.db")
        self.mirror = os.path.join(self.tmp, "cases.json")
        self._old_env = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.db_path
        import db
        import case_store as CS
        self.db = db
        self.CS = CS
        self._old_mirror = CS.MIRROR
        CS.MIRROR = self.mirror

    def tearDown(self):
        self.CS.MIRROR = self._old_mirror
        if self._old_env is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._old_env
        self.db.close_conn()
        shutil.rmtree(self.tmp, ignore_errors=True)


class RoundTrip(_Base):
    def test_full_library_roundtrip_is_byte_identical(self):
        """461 条真数据：导入 → 导出，必须与原 cases.json 逐字节相同。"""
        if not os.path.isfile(REAL_MIRROR):
            self.skipTest("没有 data/cases.json")
        conn = self.db.get_conn()
        src = _load(REAL_MIRROR)
        for row in src:
            self.db.upsert_case(conn, dict(row))
        conn.commit()
        n = self.db.export_cases_to_json(conn, self.mirror)
        self.assertEqual(n, len(src))
        a = open(REAL_MIRROR, "rb").read()
        b = open(self.mirror, "rb").read()
        self.assertEqual(len(a), len(b), "长度不同：%d vs %d" % (len(a), len(b)))
        self.assertEqual(a, b, "导出镜像与原文件不是逐字节相同")

    def test_load_save_is_byte_stable(self):
        """读 → 原样写 → 导出，必须**字节不变**。

        这条守的是 2026-11-05 踩到的坑：`_decode` 若按 `SELECT *` 的列顺序建
        dict，`upsert_case` 就会把 `field_order` 冲成列序，导出时 461 条键全部
        错位 —— 数据一个字没变，diff 却 7748 行（跑一次 `score_solo_fit` 就能
        制造出来）。所以「幂等」的判据必须是字节，不是 json 相等。
        """
        if not os.path.isfile(REAL_MIRROR):
            self.skipTest("没有 data/cases.json")
        conn = self.db.get_conn()
        for row in _load(REAL_MIRROR):
            self.db.upsert_case(conn, dict(row))
        conn.commit()
        self.db.export_cases_to_json(conn, self.mirror)
        before = open(self.mirror, "rb").read()

        cases = self.CS.load_cases()          # 读（经过 _decode）
        self.CS.save_cases(cases)             # 原样写回
        after = open(self.mirror, "rb").read()
        self.assertEqual(len(before), len(after))
        self.assertEqual(before, after, "读→写→导出 不是字节稳定（键序被冲掉了？）")

    def test_missing_late_columns_would_lose_data(self):
        """14 个表外字段必须都能落库（否则第一次写就静默丢）。"""
        late = ["china_fit", "solo_fit", "composite", "cover_desc", "segment",
                "human_read", "human_read_at", "needs_review", "judgment"]
        conn = self.db.get_conn()
        row = {"id": "x1", "name": "X"}
        for k in late:
            row[k] = {"score": 1.5} if k in ("china_fit", "solo_fit", "composite") else "v"
        self.db.upsert_case(conn, row)
        conn.commit()
        got = self.CS.get_case("x1")
        self.assertIsNotNone(got)
        for k in late:
            self.assertIn(k, got, "字段 %s 丢了" % k)
        self.assertEqual(got["china_fit"], {"score": 1.5})

    def test_field_order_is_preserved(self):
        """键序还原：不是表列序，而是原 JSON 的键序。

        用**真实存在的列**测（name 在 id 之后、tier 在 name 之后的反例）：
        顺序必须是 name → id → tier，而不是表里的 id → name → … → tier。
        """
        conn = self.db.get_conn()
        row = {"name": "N", "id": "x2", "tier": "A"}
        self.db.upsert_case(conn, row)
        conn.commit()
        self.db.export_cases_to_json(conn, self.mirror)
        text = io.open(self.mirror, encoding="utf-8").read()
        self.assertLess(text.index('"name"'), text.index('"id"'),
                        "键序被表列顺序覆盖了（name 应在 id 之前）")
        self.assertLess(text.index('"id"'), text.index('"tier"'))

    def test_mirror_has_no_internal_columns(self):
        conn = self.db.get_conn()
        self.db.upsert_case(conn, {"id": "x3", "name": "N"})
        conn.commit()
        self.db.export_cases_to_json(conn, self.mirror)
        item = _load(self.mirror)[0]
        for k in ("type", "deleted", "created_at", "field_order"):
            self.assertNotIn(k, item, "内部记账列 %s 漏进镜像了" % k)


class SaveAndMirror(_Base):
    def test_save_cases_writes_db_and_mirror(self):
        self.CS.save_cases([{"id": "a", "name": "A"}, {"id": "b", "name": "B"}])
        self.assertEqual(self.CS.count(), 2)
        items = _load(self.mirror)
        self.assertEqual([i["id"] for i in items], ["a", "b"])

    def test_save_case_updates_single_row(self):
        self.CS.save_cases([{"id": "a", "name": "A"}])
        self.CS.save_case({"id": "a", "name": "A2"})
        self.assertEqual(self.CS.get_case("a")["name"], "A2")
        items = _load(self.mirror)
        self.assertEqual(len(items), 1)

    def test_prune_soft_deletes_missing_ids(self):
        self.CS.save_cases([{"id": "a", "name": "A"}, {"id": "b", "name": "B"}])
        self.CS.save_cases([{"id": "a", "name": "A"}], prune=True)
        self.assertEqual(self.CS.count(), 1)
        self.assertEqual([i["id"] for i in _load(self.mirror)], ["a"])
        # 软删：行还在库里，只是 deleted=1
        row = self.db.get_conn().execute(
            "SELECT deleted FROM case_library WHERE id='b'").fetchone()
        self.assertEqual(row[0], 1, "prune 应该是软删，不是真删")

    def test_export_mirror_count_matches(self):
        self.CS.save_cases([{"id": "a"}, {"id": "b"}, {"id": "c"}])
        self.assertEqual(self.CS.export_mirror(), 3)


class Isolation(_Base):
    def test_ledger_db_env_is_respected(self):
        """LEDGER_DB 指哪写哪 —— 测试永远别碰真库。"""
        self.assertEqual(os.path.abspath(self.db.current_path()),
                         os.path.abspath(self.db_path))

    def test_empty_db_falls_back_to_mirror(self):
        """空表回落读镜像，但必须**明说**（不许把「读了文件」当「读了库」）。"""
        io.open(self.mirror, "w", encoding="utf-8").write(
            json.dumps([{"id": "j1", "name": "JSON"}], ensure_ascii=False))
        cases = self.CS.load_cases()
        self.assertEqual(cases[0]["id"], "j1")

    def test_explicit_other_path_reads_that_file(self):
        """传了非默认路径（测试/离线）就读那个文件，不走库。"""
        other = os.path.join(self.tmp, "other.json")
        io.open(other, "w", encoding="utf-8").write(
            json.dumps([{"id": "o1"}], ensure_ascii=False))
        self.assertEqual(self.CS.load_cases(other)[0]["id"], "o1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
