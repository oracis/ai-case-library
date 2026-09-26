# -*- coding: utf-8 -*-
"""publish_both 的纯函数测试（不连浏览器、不跑子进程）。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_both as pb          # noqa: E402


def fake_case(cid, name=None):
    return {"id": cid, "name": name or cid}


class TestArticleHtml(unittest.TestCase):
    def test_no_article(self):
        """没稿 → False（find_article 要打桩，它扫的是 wechat_publish 自己的 ROOT）。"""
        old = pb.wp.find_article
        self.addCleanup(setattr, pb.wp, "find_article", old)
        pb.wp.find_article = lambda *a, **k: None
        # 用库里肯定没有的 id，别撞上 out/articles 里真实存在的稿
        self.assertFalse(pb.article_html("zzz-nonexistent-case-xyz"))

    def test_root_article_wins(self):
        pb.wp.find_article = lambda cid, name=None: "真实/公众号-%s拆解.html" % cid
        self.assertTrue(pb.article_html("nitra"))

    def test_articles_dir_counts(self):
        with tempfile.TemporaryDirectory() as d:
            old = pb.ROOT
            pb.ROOT = d
            os.makedirs(os.path.join(d, "out", "articles"))
            open(os.path.join(d, "out", "articles", "nitra.html"), "w").close()
            try:
                self.assertTrue(pb.article_html("nitra"))
            finally:
                pb.ROOT = old


class TestWechatDoneIds(unittest.TestCase):
    def test_set(self):
        pb.wp.load_published = lambda: {"a": {}, "b": {}}
        self.assertEqual(pb._wechat_done_ids(), {"a", "b"})

    def test_list(self):
        pb.wp.load_published = lambda: [{"id": "a"}, {"id": "b"}]
        self.assertEqual(pb._wechat_done_ids(), {"a", "b"})

    def test_empty(self):
        pb.wp.load_published = lambda: {}
        self.assertEqual(pb._wechat_done_ids(), set())


class _PendingCase(unittest.TestCase):
    """共同准备：打桩掉 find_article（一律有稿）。

    所有打桩都必须 addCleanup 还原 —— 否则会污染同进程里
    test_xhs_publish 的用例（2026-09-26 实测踩过：它拿错了 load_cases）。
    """

    def setUp(self):
        self._patches = []
        self.patch(pb.wp, "find_article", lambda *a, **k: "/fake/公众号-x拆解.html")
        self.patch(pb.x, "load_cases",
                   lambda: [fake_case("a"), fake_case("b"), fake_case("c")])
        self.patch(pb.wp, "load_published", lambda: {})
        self.patch(pb.x, "_drafted_ids", lambda: [])
        self.patch(pb.x, "_published_ids", lambda: [])

    def patch(self, module, name, value):
        old = getattr(module, name)
        self._patches.append((module, name, old))
        setattr(module, name, value)

    def tearDown(self):
        for module, name, old in reversed(self._patches):
            setattr(module, name, old)


class TestBothPending(_PendingCase):
    def test_skips_both_sides(self):
        """两边都记过 → 出队；只有一边记过 → 留着。"""
        pb.wp.load_published = lambda: {"a": {}}
        pb.x._drafted_ids = lambda: ["b"]
        self.assertEqual([c["id"] for c in pb.both_pending()], ["c"])

    def test_voklit_never_requeued(self):
        """voklit 已真发过，不在小红书草稿记录里，队列也不能捞它。"""
        pb.x.load_cases = lambda: [fake_case("voklit"), fake_case("a")]
        pb.x._drafted_ids = lambda: []
        self.assertEqual([c["id"] for c in pb.both_pending()], ["a"])

    def test_requires_article(self):
        """公众号没稿 → 不出队（不自动现造，免得发空页）。"""
        self.patch(pb.wp, "find_article", lambda *a, **k: None)
        self.assertEqual(pb.both_pending(), [])

    def test_order_latest_first(self):
        # load_cases 正序，both_pending 里 reversed → 最新在前
        self.assertEqual([c["id"] for c in pb.both_pending()], ["c", "b", "a"])


class TestPick(_PendingCase):
    def test_near_limits(self):
        cases, mode = pb.pick(type("A", (), {"case": None, "near": 2})())
        self.assertEqual(mode, "near")
        self.assertEqual([c["id"] for c in cases], ["c", "b"])

    def test_case_matches_fuzzy(self):
        self.patch(pb.x, "find_case_fuzzy", lambda q: fake_case(q, "Bustem"))
        cases, mode = pb.pick(type("A", (), {"case": "bus", "near": 0})())
        self.assertEqual(mode, "case")
        self.assertEqual(cases[0]["id"], "bus")


if __name__ == "__main__":
    unittest.main()
