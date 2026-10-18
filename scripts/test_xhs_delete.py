# -*- coding: utf-8 -*-
"""小红书删除笔记逻辑回归锁（2026-10-18 打通「纯接口可删」后补）。

锁四件事，都不依赖 9223 是否在跑：

1. **参数形状**：删除端点的字段名是单数 `note_id`（复数 `noteIds` 回 400
   `required param check: note_id`），URL / method 不能被改成别的。
2. **风控不误判**：461/471 时 body 也可能是 `{"code":0}` 假象，只有
   `status==200` 且 `code==0` 才算删成功（与 publish 探测同一套口径）。
3. **台账只留线上真实存在的**：删完必须移除条目，且移除前留 `.bak-*`。
4. **快照追加不覆盖**：`xhs_deleted.json` 是审计流水，第二次删不能把第一条冲掉。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_delete as D  # noqa: E402


def _load(path):
    """读 JSON 并关文件（直接 json.load(open(...)) 会留 ResourceWarning）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


NOTE = {"note_id": "6abbc7d8000000001b02c26b", "title": "月收$128K：销售挖客户线索的工具",
        "time": "2026-09-29 22:14", "likes": 1, "view_count": 50,
        "collected_count": 0, "shared_count": 1, "comments_count": 0,
        "images_list": [{"url": "http://x/1.jpg"}]}


class TempLedger:
    """把模块级 LEDGER / ARCHIVE 指到临时文件，跑完还原。"""

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="xhsdel_")
        self.ledger = os.path.join(self.dir, "xhs_published.json")
        self.archive = os.path.join(self.dir, "xhs_deleted.json")
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump(self.rows, f, ensure_ascii=False)
        self._orig = (D.LEDGER, D.ARCHIVE)
        D.LEDGER, D.ARCHIVE = self.ledger, self.archive
        return self

    def __exit__(self, *exc):
        D.LEDGER, D.ARCHIVE = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class TestEndpoint(unittest.TestCase):
    def test_delete_url_is_capa_postgw(self):
        # capa/postgw 与发布用的 web_api/sns/v2/note 是两个服务，前者不过 461
        self.assertEqual(D.DELETE_URL,
                         "https://edith.xiaohongshu.com/web_api/sns/capa/postgw/note/delete")

    def test_payload_uses_singular_note_id(self):
        seen = {}

        def fake_full(cdp, url, method="GET", body=None, signed=True):
            seen.update(url=url, method=method, body=body)
            return 200, {"code": 0, "msg": "成功"}, "", {}

        with mock.patch.object(D.X, "fetch_full", fake_full):
            r = D.delete_note(object(), NOTE["note_id"])
        self.assertEqual(seen["method"], "POST")
        # 复数 noteIds 实测回 400 required param check: note_id
        self.assertEqual(list(seen["body"]), ["note_id"])
        self.assertEqual(seen["body"]["note_id"], NOTE["note_id"])
        self.assertEqual(r["code"], 0)

    def test_verify_codes_are_risk_control(self):
        self.assertEqual(D.NEED_VERIFY, (460, 461, 471))


class TestFind(unittest.TestCase):
    def test_find_hit(self):
        self.assertIs(D.find_note([NOTE], NOTE["note_id"]), NOTE)

    def test_find_miss(self):
        self.assertIsNone(D.find_note([NOTE], "deadbeef"))

    def test_find_empty(self):
        self.assertIsNone(D.find_note([], NOTE["note_id"]))


class TestLedgerDrop(unittest.TestCase):
    ROWS = [
        {"id": "prosp", "at": "2026-09-29 12:09", "note_id": "aaa"},
        {"id": "prosp", "at": "2026-09-29 22:14", "note_id": "bbb"},
        {"id": "kibu", "at": "2026-10-05 22:03", "note_id": "ccc"},
    ]

    def test_hits_only_matching_note_id(self):
        with TempLedger(self.ROWS):
            hits = D.ledger_hits("bbb")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0][1]["at"], "2026-09-29 22:14")

    def test_drop_removes_one_and_keeps_others(self):
        with TempLedger(self.ROWS) as t:
            n = D.drop_from_ledger("bbb")
            self.assertEqual(n, 1)
            left = _load(t.ledger)
            self.assertEqual([e["note_id"] for e in left], ["aaa", "ccc"])

    def test_drop_writes_backup(self):
        with TempLedger(self.ROWS) as t:
            D.drop_from_ledger("bbb")
            baks = [f for f in os.listdir(t.dir) if ".bak-" in f]
            self.assertEqual(len(baks), 1)

    def test_drop_unknown_is_noop_without_backup(self):
        with TempLedger(self.ROWS) as t:
            self.assertEqual(D.drop_from_ledger("zzz"), 0)
            self.assertEqual([f for f in os.listdir(t.dir) if ".bak-" in f], [])


class TestArchive(unittest.TestCase):
    def test_append_does_not_overwrite(self):
        with TempLedger([]) as t:
            self.assertEqual(D.archive(NOTE, NOTE["note_id"], True), 1)
            self.assertEqual(D.archive(NOTE, "other", True), 2)
            rows = _load(t.archive)
            self.assertEqual([r["note_id"] for r in rows],
                             [NOTE["note_id"], "other"])

    def test_snapshot_keeps_images(self):
        with TempLedger([]) as t:
            D.archive(NOTE, NOTE["note_id"], True)
            rows = _load(t.archive)
            self.assertEqual(len(rows[0]["snapshot"]["images_list"]), 1)
            self.assertEqual(rows[0]["applied"], True)


if __name__ == "__main__":
    unittest.main()
