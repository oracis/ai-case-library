#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e_platform_readback.py 单测（离线，不联网、不起浏览器）。

守的是「双回读判据不能被写松」：
  · 已发表列表必须**唯一命中**，多命中不许认领（防重发）；
  · 标题归一化用同一把尺子（title_match）；
  · 缺文件 / 空列表 = FAIL，不是 pass；
  · 第二路直接把参数原样转给 verify_page.py，不丢 --expect/--title。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e2e_platform_readback as R                          # noqa: E402


class CheckPublishedList(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="rb_")

    def _write(self, lines):
        p = os.path.join(self.d, "titles.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return p

    def test_unique_hit(self):
        # 台账标题带后缀、查询给短标题：归一化后是前缀关系且差 ≤ 12 ⇒ 命中
        p = self._write(["别的文章", "盯住5万个招聘页,ATS与去重", "第三篇"])
        ok, detail = R.check_published_list(p, "盯住 5 万个招聘页")
        self.assertTrue(ok, detail)
        self.assertIn("唯一命中", detail)

    def test_no_similarity_vote(self):
        # title_match 不按相似度投票：多出的前缀不算命中（防错配）
        p = self._write(["招聘：盯住 5 万个招聘页"])
        ok, _ = R.check_published_list(p, "盯住5万个招聘页")
        self.assertFalse(ok)

    def test_no_hit_is_fail(self):
        p = self._write(["完全无关的一篇"])
        ok, detail = R.check_published_list(p, "招聘")
        self.assertFalse(ok)
        self.assertIn("没有匹配", detail)

    def test_multiple_hits_is_fail(self):
        # 两条都在 MAX_GAP 容差内 ⇒ 不唯一，不许认领
        p = self._write(["招聘产品解析第一版", "招聘产品解析第二版"])
        ok, detail = R.check_published_list(p, "招聘产品解析")
        self.assertFalse(ok)
        self.assertIn("不唯一", detail)

    def test_missing_file_is_fail(self):
        ok, detail = R.check_published_list(os.path.join(self.d, "none.txt"), "x")
        self.assertFalse(ok)
        self.assertIn("不存在", detail)

    def test_empty_list_is_fail(self):
        p = self._write([])
        ok, _ = R.check_published_list(p, "anything")
        self.assertFalse(ok)

    def test_fullwidth_punctuation_normalized(self):
        p = self._write(["Aera：在浏览器里托管长任务"])
        ok, _ = R.check_published_list(p, "Aera 在浏览器里托管长任务")
        self.assertTrue(ok, "全角冒号/空格应被归一化后命中")


class CheckPublicPage(unittest.TestCase):
    def _args(self, **over):
        class A(object):
            pass
        a = A()
        a.url = "https://example.com/p"
        a.expect = "关键词A,关键词B"
        a.title = "标题片段"
        a.chrome = "csdn"
        a.wait = 3.0
        for k, v in over.items():
            setattr(a, k, v)
        return a

    @mock.patch.object(R.subprocess, "run")
    def test_params_forwarded(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
        ok, _ = R.check_public_page(self._args())
        self.assertTrue(ok)
        cmd = run.call_args[0][0]
        self.assertIn("--url", cmd)
        self.assertIn("https://example.com/p", cmd)
        self.assertIn("--expect", cmd)
        self.assertIn("关键词A,关键词B", cmd)
        self.assertIn("--title", cmd)
        self.assertIn("--chrome", cmd)
        self.assertIn("csdn", cmd)
        self.assertIn("3.0", cmd)

    @mock.patch.object(R.subprocess, "run")
    def test_browser_unavailable_is_fail(self, run):
        run.return_value = mock.Mock(returncode=2, stdout="", stderr="")
        ok, detail = R.check_public_page(self._args())
        self.assertFalse(ok)
        self.assertIn("浏览器起不来", detail)

    @mock.patch.object(R.subprocess, "run")
    def test_page_miss_is_fail_with_output(self, run):
        run.return_value = mock.Mock(returncode=1, stdout="正文缺关键词", stderr="")
        ok, detail = R.check_public_page(self._args())
        self.assertFalse(ok)
        self.assertIn("正文缺关键词", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
