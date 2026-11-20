# -*- coding: utf-8 -*-
"""bili_article_delete.py 的离线回归锁（2026-11-20）。

删除是不可逆动作，判据必须比「接口返回 OK」更硬：
  · 默认干跑；--show 永远不删；
  · 已投稿列表里没有目标 ⇒ 拒绝删（别拿错 aid）；
  · delete 返回 code != 0 ⇒ 失败；
  · 即使返回 OK，也要独立回读列表：目标还在 ⇒ 返回 4，不算成功。

CDP 全部用桩替换，不联网、不删任何真实稿件。
"""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bili_article_delete as D  # noqa: E402


class TestJsContracts(unittest.TestCase):
    def test_detail_queries_both_endpoints(self):
        cdp = MagicMock()
        cdp.eval.return_value = {"viewinfo": {"code": 0}}
        D.detail(cdp, "53683188")
        js = cdp.eval.call_args.args[0]
        self.assertIn("x/article/viewinfo?id=53683188", js)
        self.assertIn("x/article/cards?ids=53683188", js)
        self.assertTrue(cdp.eval.call_args.kwargs.get("refresh_context"))

    def test_delete_uses_csrf_and_both_aid_keys(self):
        cdp = MagicMock()
        cdp.eval.return_value = {"code": 0}
        D.do_delete(cdp, "53683188")
        js = cdp.eval.call_args.args[0]
        self.assertIn("bili_jct", js)
        self.assertIn("x/web/article/delete", js)
        self.assertIn("article_id", js)
        self.assertIn("53683188", js)
        self.assertTrue(cdp.eval.call_args.kwargs.get("refresh_context"))


class TestFindInList(unittest.TestCase):
    def test_hit_reports_position_and_total(self):
        bia = MagicMock()
        bia.list_published_articles.return_value = [
            {"id": 1, "title": "A"}, {"id": "53683188", "title": "B"}]
        item, pos, total = D.find_in_list(MagicMock(), bia, "53683188")
        self.assertEqual(item["title"], "B")
        self.assertEqual((pos, total), (2, 2))

    def test_miss_reports_total_without_position(self):
        bia = MagicMock()
        bia.list_published_articles.return_value = [{"id": 1, "title": "A"}]
        item, pos, total = D.find_in_list(MagicMock(), bia, "999")
        self.assertIsNone(item)
        self.assertIsNone(pos)
        self.assertEqual(total, 1)


class TestTs(unittest.TestCase):
    def test_epoch_is_formatted(self):
        import re
        self.assertRegex(D._ts(1763241600), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_empty_is_dash(self):
        self.assertEqual(D._ts(0), "-")
        self.assertEqual(D._ts(None), "-")

    def test_bad_value_is_passed_through(self):
        self.assertEqual(D._ts("not-a-number"), "not-a-number")


class TestMainGates(unittest.TestCase):
    def _run(self, argv, item=None, delete_result=None, after=None,
             after_total=None):
        calls = {"delete": 0, "list": 0}

        def fake_find(cdp, bia, aid):
            calls["list"] += 1
            if calls["list"] == 1:
                return item, (1 if item else None), 10
            total = 10 if after_total is None else after_total
            return after, (1 if after else None), total

        detail = MagicMock(return_value={"viewinfo": {"code": -404, "data": {}}})
        delete = MagicMock(return_value=delete_result or {"code": 0})
        with patch.object(D, "_cdp", return_value=(MagicMock(), MagicMock(),
                                                   MagicMock())), \
                patch.object(D, "find_in_list", side_effect=fake_find), \
                patch.object(D, "detail", detail), \
                patch.object(D, "do_delete", delete), \
                patch.object(sys, "argv", ["x"] + argv), \
                contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = D.main()
        return rc, buf.getvalue(), delete, calls

    def test_show_never_deletes_even_with_yes(self):
        rc, out, delete, calls = self._run(
            ["--aid", "1", "--show", "--yes"], item={"id": 1, "title": "A"})
        self.assertEqual(rc, 0)
        delete.assert_not_called()
        self.assertEqual(calls["list"], 1)
        self.assertIn("viewinfo", out)      # --show 只打详情
        self.assertNotIn("干跑", out)        # 也不走删除分支的提示

    def test_dry_run_never_deletes(self):
        rc, out, delete, calls = self._run(
            ["--aid", "1"], item={"id": 1, "title": "A"})
        self.assertEqual(rc, 0)
        delete.assert_not_called()
        self.assertIn("干跑", out)

    def test_missing_from_list_refuses_delete(self):
        rc, out, delete, _calls = self._run(["--aid", "1", "--yes"], item=None)
        self.assertEqual(rc, 2)
        delete.assert_not_called()
        self.assertIn("拒绝删除", out)

    def test_nonzero_response_is_failure(self):
        rc, out, delete, calls = self._run(
            ["--aid", "1", "--yes"], item={"id": 1, "title": "A"},
            delete_result={"code": -111, "message": "csrf"})
        self.assertEqual(rc, 3)
        self.assertIn("删除失败", out)
        delete.assert_called_once()
        self.assertEqual(calls["list"], 1, "失败后不该再回读列表")

    def test_success_but_still_online_is_failure(self):
        rc, out, _delete, calls = self._run(
            ["--aid", "1", "--yes"], item={"id": 1, "title": "A"},
            after={"id": 1, "title": "A"})
        self.assertEqual(rc, 4)
        self.assertIn("仍在", out)
        self.assertEqual(calls["list"], 2)

    def test_success_and_gone_returns_zero(self):
        rc, out, _delete, calls = self._run(
            ["--aid", "1", "--yes"], item={"id": 1, "title": "A"},
            after=None, after_total=9)
        self.assertEqual(rc, 0)
        self.assertIn("已消失", out)
        self.assertEqual(calls["list"], 2)

    def test_gone_without_count_drop_is_not_reported_as_success(self):
        """PRD #203：判据是「目标不在 + 条数 -1」，缺一条就不算成功。"""
        rc, out, _delete, calls = self._run(
            ["--aid", "1", "--yes"], item={"id": 1, "title": "A"},
            after=None, after_total=10)
        self.assertEqual(rc, 4)
        self.assertIn("条数未按预期 -1", out)
        self.assertEqual(calls["list"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
