# -*- coding: utf-8 -*-
"""bili_published_reconcile.py 的离线回归锁（2026-11-20）。

钉死三条会出事故的判据：
  · STALE 先算，算出的真 id 必须进 local_rid，否则同一篇稿会被 ORPHAN 再补一条；
  · 线上重复稿只进 unclaimed，绝不覆盖已有在线 id；
  · 台账记 draft 而线上已发 ⇒ PROMOTE（FIFO 只认 published，不修就会重发）。

plan 是纯函数；apply 路径用内存桩替代 L.load/L.save，不碰真台账。
"""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import bili_published_reconcile as R  # noqa: E402


def _rec(state, rid="", did="", title="", note=""):
    r = {"id": "a", "state": state, "remote_id": rid, "title": title,
         "note": note, "at": "2026-11-01 10:00:00"}
    if did:
        r["draft_remote_id"] = did
    return r


class TestPlan(unittest.TestCase):
    def test_stale_remap_also_blocks_orphan_for_same_article(self):
        recs = {"a": _rec(L.PUBLISHED, rid="342371", title="已发但记了草稿 id")}
        stale, orphan, promote, ghost, unclaimed = R.plan(
            recs, {}, {"53460777": "已发但记了草稿 id"})
        self.assertEqual(stale, [("a", "342371", "53460777", "已发但记了草稿 id")])
        self.assertEqual(orphan, [], "STALE 认领后不许再补一条 ORPHAN")
        self.assertEqual(unclaimed, [])

    def test_stale_ambiguous_is_left_for_human(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="同一标题")}
        stale, orphan, _p, _g, unclaimed = R.plan(
            recs, {}, {"222": "同一标题", "333": "同一标题"})
        self.assertEqual((stale, orphan), ([], []))
        self.assertEqual(unclaimed[0][0], "STALE")
        self.assertIn("2", unclaimed[0][3])

    def test_stale_with_no_title_candidate_is_unclaimed(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="旧标题")}
        stale, _o, _p, _g, unclaimed = R.plan(recs, {}, {"222": "完全不同的标题"})
        self.assertEqual(stale, [])
        self.assertEqual(unclaimed[0][0], "STALE")

    def test_orphan_unique_hit(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="这篇在线")}
        stale, orphan, _p, _g, unclaimed = R.plan(
            recs, {}, {"111": "这篇在线", "222": "线上多出来的一篇"})
        # 222 标题与本地任何一条都对不上 ⇒ 只报告，不许拿 111 的 case 硬认
        self.assertEqual(orphan, [])
        self.assertEqual(unclaimed[0][0], "ORPHAN")
        self.assertIn("本地候选 0 个", unclaimed[0][3])

        recs = {}
        _s, orphan, _p, _g, unclaimed = R.plan(recs, {}, {"222": "本地漏登的稿"})
        self.assertEqual(orphan, [])
        self.assertEqual(unclaimed[0][0], "ORPHAN",
                         "本地没有 case 可认领时只报告，不造 stub")

    def test_orphan_fills_case_without_remote_id(self):
        recs = {"a": _rec(L.PUBLISHED, title="本地漏登的稿")}
        _s, orphan, _p, _g, unclaimed = R.plan(recs, {}, {"222": "本地漏登的稿"})
        self.assertEqual(orphan, [("a", "222", "本地漏登的稿")])
        self.assertEqual(unclaimed, [])

    def test_promote_when_ledger_still_says_draft(self):
        recs = {"a": _rec(L.DRAFT, rid="342371", did="342371", title="已发表")}
        _s, _o, promote, _g, unclaimed = R.plan(recs, {"342371": "已发表"},
                                                {"53460777": "已发表"})
        self.assertEqual(promote, [("a", "342371", "53460777", "已发表")])
        self.assertEqual(unclaimed, [])

    def test_ghost_draft_id_only_when_published_and_missing_online(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", did="342371", title="T"),
                "b": _rec(L.DRAFT, rid="", did="999999", title="D")}
        _s, _o, _p, ghost, _u = R.plan(recs, {"999999": "D"}, {"111": "T"})
        self.assertEqual(ghost, [("a", "342371")],
                         "草稿 id 还在草稿箱就不该清；草稿态也不算幽灵")

    def test_duplicate_publication_never_overwrites_live_id(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="同一标题")}
        _s, orphan, _p, _g, unclaimed = R.plan(
            recs, {}, {"111": "同一标题", "999": "同一标题"})
        self.assertEqual(orphan, [])
        self.assertEqual(unclaimed[0][0], "DUP_PUB")

    def test_prefix_tolerance_is_shared_with_toutiao(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="NetHack，地牢模拟器")}
        _s, _o, _p, _g, unclaimed = R.plan(
            recs, {}, {"222": "NetHack，地牢模拟器-哔哩哔哩"})
        self.assertEqual(unclaimed, [], "同一套 12 字前缀容差，别两边漂移")


class TestApply(unittest.TestCase):
    def test_apply_updates_id_clears_ghost_and_keeps_note(self):
        recs = {"a": _rec(L.PUBLISHED, rid="342371", did="342371",
                          title="标题", note="旧注记")}
        saved = []
        with patch.object(R, "pull", return_value=({}, {"53460777": "标题"})), \
                patch.object(L, "load", return_value=recs), \
                patch.object(L, "save", side_effect=lambda p, r: saved.append((p, r))), \
                patch.object(sys, "argv", ["x", "--apply"]), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = R.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(saved), 1)
        self.assertEqual(recs["a"]["remote_id"], "53460777")
        self.assertNotIn("draft_remote_id", recs["a"])
        self.assertIn("旧注记", recs["a"]["note"])
        self.assertIn("53460777", recs["a"]["note"])

    def test_dry_run_never_saves(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="标题")}
        with patch.object(R, "pull", return_value=({}, {"111": "标题"})), \
                patch.object(L, "load", return_value=recs), \
                patch.object(L, "save") as save, \
                patch.object(sys, "argv", ["x"]), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = R.main()
        self.assertEqual(rc, 0)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
