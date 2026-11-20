# -*- coding: utf-8 -*-
"""wechat_backfill_appmsgid.py 的离线回归锁（2026-11-20）。

公众号群发之后 appmsgid 会变（本地常是草稿 id 100000xxx，线上是
2247xxxxxx），只能按标题认亲。这里钉死：
  · title_match 唯一命中才算，重复候选/标题被后台改过 ⇒ 进 unclaimed；
  · 已被别的 case 占用的 appmsg_id 不许抢；
  · --apply 才写盘，写完必须独立回读，回读不一致要返回 1。

CDP 与远端列表全部用桩替换，不联网、不碰真台账。
"""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import wechat_backfill_appmsgid as W  # noqa: E402


def _rec(state, rid="", title="", note=""):
    return {"id": "a", "state": state, "remote_id": rid, "title": title,
            "note": note, "at": "2026-11-01 10:00:00"}


def _remote(aid, title):
    return {"appmsg_id": aid, "title": title}


class TestGates(unittest.TestCase):
    def _run(self, recs, remote=None, ok=True, tok="TOKEN", argv=None,
             after_load=None):
        """跑一次 main，返回 (rc, stdout, save_mock, cdp_mock)。"""
        remote = remote if remote is not None else []
        after_load = after_load if after_load is not None else recs
        save = MagicMock()
        cdp = MagicMock()
        loads = [recs, after_load]
        with patch.object(W.L, "load", side_effect=lambda p: loads.pop(0)), \
                patch.object(W.L, "save", save), \
                patch.object(W.wp, "CDP", return_value=cdp) as cdp_ctor, \
                patch.object(W.wp, "_connect_mp", return_value=(1, tok)), \
                patch.object(W.ws, "fetch_published_remote",
                             return_value=(remote, ok)), \
                patch.object(sys, "argv", argv or ["x"]), \
                contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = W.main()
        return rc, buf.getvalue(), save, cdp_ctor

    def test_no_missing_id_short_circuits_before_cdp(self):
        rc, out, save, cdp = self._run(
            {"a": _rec(L.PUBLISHED, rid="22470001", title="T")})
        self.assertEqual(rc, 0)
        self.assertIn("缺 appmsgid 0 条", out)
        cdp.assert_not_called()
        save.assert_not_called()

    def test_no_token_returns_two(self):
        rc, out, save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="T")}, tok="")
        self.assertEqual(rc, 2)
        self.assertIn("拿不到 token", out)
        save.assert_not_called()

    def test_unreadable_remote_returns_two(self):
        rc, out, save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="T")}, ok=False)
        self.assertEqual(rc, 2)
        self.assertIn("读不到", out)
        save.assert_not_called()

    def test_unique_hit_is_dry_run_by_default(self):
        rc, out, save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="标题")},
            remote=[_remote("22470002", "标题")])
        self.assertEqual(rc, 0)
        self.assertIn("[可补] 1 条", out)
        self.assertIn("[干跑]", out)
        save.assert_not_called()

    def test_multiple_candidates_are_not_guessed(self):
        rc, out, _save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="同一标题")},
            remote=[_remote("1", "同一标题"), _remote("2", "同一标题")])
        self.assertEqual(rc, 0)
        self.assertIn("[不敢认] 1 条", out)

    def test_title_changed_in_backend_is_not_claimed(self):
        """visualizee-ai 后台改过标题：相近不是唯一命中，宁可不认。"""
        rc, out, _save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="Visualizee：老标题")},
            remote=[_remote("3", "Visualizee：新标题完全不同的一篇稿")])
        self.assertEqual(rc, 0)
        self.assertIn("[不敢认] 1 条", out)

    def test_appmsgid_already_taken_is_not_reused(self):
        recs = {"a": _rec(L.PUBLISHED, title="同一标题"),
                "b": _rec(L.PUBLISHED, rid="22470003", title="另一篇")}
        rc, out, _save, _cdp = self._run(
            recs, remote=[_remote("22470003", "同一标题")])
        self.assertEqual(rc, 0)
        self.assertIn("[不敢认] 1 条", out)

    def test_apply_writes_note_and_readback_ok(self):
        recs = {"a": _rec(L.PUBLISHED, title="标题", note="旧注记")}
        rc, out, save, _cdp = self._run(
            recs, remote=[_remote("22470004", "标题")],
            argv=["x", "--apply"])
        self.assertEqual(rc, 0)
        save.assert_called_once_with("wechat", recs)
        self.assertEqual(recs["a"]["remote_id"], "22470004")
        self.assertIn("旧注记", recs["a"]["note"])
        self.assertIn("22470004", recs["a"]["note"])
        self.assertIn("全部对上", out)

    def test_readback_mismatch_is_a_failure(self):
        recs = {"a": _rec(L.PUBLISHED, title="标题")}
        stale = {"a": _rec(L.PUBLISHED, rid="旧的", title="标题")}
        rc, out, _save, _cdp = self._run(
            recs, remote=[_remote("22470005", "标题")],
            argv=["x", "--apply"], after_load=stale)
        self.assertEqual(rc, 1)
        self.assertIn("失败", out)

    def test_prefix_tolerance_accepts_platform_suffix(self):
        rc, out, _save, _cdp = self._run(
            {"a": _rec(L.PUBLISHED, title="古代文明 RTS：读 0 A.D.")},
            remote=[_remote("22470006", "古代文明 RTS：读 0 A.D.-公众号")])
        self.assertEqual(rc, 0)
        self.assertIn("[可补] 1 条", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
