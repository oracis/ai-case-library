# -*- coding: utf-8 -*-
"""toutiao_published_reconcile.py 的离线回归锁（2026-11-20）。

钉死头条特有的两件事：
  · 早期台账 title 是空的，标题真值在 out/toutiao/<case>/meta.json —— 不能
    只看台账就说「线上候选 0 个」；
  · 已发布但 gid 空（NO_ID）与 gid 记错（STALE）是两种病，都要唯一命中才修；
    线上又冒出同标题文章时只报 DUP_PUB，绝不覆盖在线真 gid。

不联网、不碰真台账；输入法桩替换 L.load/L.save 与 tr.pull_*。
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import toutiao_reconcile as tr  # noqa: E402
import toutiao_published_reconcile as R  # noqa: E402


def _rec(state, rid="", title="", note=""):
    return {"id": "a", "state": state, "remote_id": rid, "title": title,
            "note": note, "at": "2026-11-01 10:00:00"}


class TestTitleOf(unittest.TestCase):
    def setUp(self):
        # 与 ROOT 同盘：避免 Windows 跨盘路径问题
        self.tmp = tempfile.mkdtemp(
            prefix="tt_rc_", dir=os.path.join(os.path.dirname(HERE), "tmp"))
        self._old = R.PACK_DIR
        R.PACK_DIR = self.tmp

    def tearDown(self):
        R.PACK_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ledger_title_wins(self):
        self.assertEqual(R._title_of("a", {"title": "台账标题"}), "台账标题")

    def test_fallback_to_meta_json(self):
        os.makedirs(os.path.join(self.tmp, "a"))
        with open(os.path.join(self.tmp, "a", "meta.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"title": "产物标题"}, f)
        self.assertEqual(R._title_of("a", {"title": ""}), "产物标题")

    def test_bad_meta_json_is_empty_not_exception(self):
        os.makedirs(os.path.join(self.tmp, "a"))
        with open(os.path.join(self.tmp, "a", "meta.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(R._title_of("a", {"title": ""}), "")

    def test_missing_pack_is_empty(self):
        self.assertEqual(R._title_of("a", {"title": ""}), "")


class TestPlan(unittest.TestCase):
    def test_no_id_remapped_by_unique_title(self):
        recs = {"a": _rec(L.PUBLISHED, title="已发但没记 gid")}
        noid, stale, orphan, promote, unclaimed = R.plan(
            recs, {"71234567": "已发但没记 gid"})
        self.assertEqual(noid, [("a", "", "71234567", "已发但没记 gid")])
        self.assertEqual((stale, orphan, promote, unclaimed), ([], [], [], []))

    def test_stale_gid_replaced(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="标题")}
        _n, stale, _o, _p, unclaimed = R.plan(recs, {"222": "标题"})
        self.assertEqual(stale, [("a", "111", "222", "标题")])
        self.assertEqual(unclaimed, [])

    def test_ambiguous_title_is_left_for_human(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="同一标题")}
        noid, stale, _o, _p, unclaimed = R.plan(
            recs, {"222": "同一标题", "333": "同一标题"})
        self.assertEqual((noid, stale), ([], []))
        self.assertEqual(unclaimed[0][0], "STALE")
        self.assertEqual(unclaimed[0][2], "同一标题")

    def test_duplicate_publication_not_overwritten(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="同一标题")}
        _n, _s, orphan, _p, unclaimed = R.plan(
            recs, {"111": "同一标题", "999": "同一标题"})
        self.assertEqual(orphan, [])
        self.assertEqual(unclaimed[0][0], "DUP_PUB")

    def test_promote_when_draft_is_already_published(self):
        recs = {"a": _rec(L.DRAFT, title="草稿其实发了")}
        _n, _s, _o, promote, unclaimed = R.plan(recs, {"888": "草稿其实发了"})
        self.assertEqual(promote, [("a", "888", "草稿其实发了")])
        self.assertEqual(unclaimed, [])

    def test_orphan_for_other_state_without_id(self):
        recs = {"a": _rec(L.UNVERIFIED, title="线上有这篇")}
        _n, _s, orphan, _p, unclaimed = R.plan(recs, {"777": "线上有这篇"})
        self.assertEqual(orphan, [("a", "777", "线上有这篇")])
        self.assertEqual(unclaimed, [])

    def test_candidate_cannot_reuse_another_cases_gid(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="同一标题"),
                "b": _rec(L.PUBLISHED, rid="222", title="其它")}
        _n, _s, _o, _p, unclaimed = R.plan(
            recs, {"222": "同一标题"})
        # 222 已被 b 占着，不能拿它给 a 改名；候选 0 个 ⇒ 只报告
        self.assertEqual(unclaimed[0][0], "STALE")

    def test_prefix_tolerance_shared_with_bilibili(self):
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="Quake，开源雷神之锤")}
        _n, stale, _o, _p, unclaimed = R.plan(
            recs, {"222": "Quake，开源雷神之锤-今日头条"})
        self.assertEqual(stale[0][2], "222")
        self.assertEqual(unclaimed, [])


class TestPullAndApply(unittest.TestCase):
    def test_pull_only_uses_published_gids(self):
        with patch.object(tr, "pull_drafts", return_value=[
                {"gid": "111", "title": "草稿"}]), \
                patch.object(tr, "pull_published", return_value=[
                    {"gid": "222", "title": "已发"},
                    {"title": "没 gid 的"}]):
            drafts, pubs = R.pull()
        self.assertEqual(drafts, {"111": "草稿"})
        self.assertEqual(pubs, {"222": "已发"})

    def test_apply_writes_id_and_note_then_readback(self):
        recs = {"a": _rec(L.PUBLISHED, title="已发但没记 gid", note="旧注记")}
        saved = []
        with patch.object(R, "pull", return_value=({}, {"71234567": "已发但没记 gid"})), \
                patch.object(L, "load", return_value=recs), \
                patch.object(L, "save", side_effect=lambda p, r: saved.append((p, r))), \
                patch.object(sys, "argv", ["x", "--apply"]), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = R.main()
        self.assertEqual(rc, 0)
        self.assertEqual(saved[0][0], "toutiao")
        self.assertEqual(recs["a"]["remote_id"], "71234567")
        self.assertIn("旧注记", recs["a"]["note"])
        self.assertIn("71234567", recs["a"]["note"])

    def test_dry_run_never_saves(self):
        recs = {"a": _rec(L.PUBLISHED, title="标题")}
        with patch.object(R, "pull", return_value=({}, {"1": "标题"})), \
                patch.object(L, "load", return_value=recs), \
                patch.object(L, "save") as save, \
                patch.object(sys, "argv", ["x"]), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = R.main()
        self.assertEqual(rc, 0)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
