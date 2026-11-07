#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aliyun_set_abstract 回归测试（2026-11-06 新建）。

钉死当天踩到的坑：**提交成功 ≠ 生效**。
改已发布文章的摘要，点「发布文章」会跳回 `/article/<aid>`、编辑页回读也正常，
但文章同时重新进审核，公开页 `div.article-desc` 与 `meta description` 还是旧值。
所以判据只能是公开页回读，`parse_public_desc` 就是那道回读的解析层。

检查方式：纯字符串解析 + 源码结构断言，不起浏览器、不联网。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import aliyun_set_abstract as A                                # noqa: E402

PAGE = """<html><head>
<meta name="last-modified" content="2026-11-06 15:25:51" />
<meta name="description" content="旧口号" />
</head><body>
<div class="article-desc">
  <b>简介：</b>
  <span>2048 是一个用原生 JavaScript 写的 4×4 数字合并小游戏，仓库约 13453 star。</span>
</div>
<div class="article-inner markdown-body"><p>正文首段</p></div>
</body></html>"""


class TestParsePublicDesc(unittest.TestCase):
    def test_extracts_desc_and_lastmod(self):
        desc, lm = A.parse_public_desc(PAGE)
        self.assertIn("13453 star", desc)
        self.assertEqual(lm, "2026-11-06 15:25:51")

    def test_collapses_whitespace(self):
        desc, _ = A.parse_public_desc(PAGE)
        self.assertNotIn("\n", desc)
        self.assertEqual(desc.strip(), desc)
        self.assertNotIn("  ", desc)

    def test_ignores_body_first_paragraph(self):
        """desc 只认 article-desc，别把正文首段当摘要（曾怀疑是首段）。"""
        desc, _ = A.parse_public_desc(
            '<div class="article-inner markdown-body"><p>正文首段</p></div>')
        self.assertEqual(desc, "")

    def test_missing_fields(self):
        desc, lm = A.parse_public_desc("<html></html>")
        self.assertEqual((desc, lm), ("", ""))
        self.assertEqual(A.parse_public_desc("")[0], "")


class TestSourceContract(unittest.TestCase):
    """源码结构断言：判据不能被悄悄改回「只看跳转」。"""

    def _src(self):
        with open(os.path.join(HERE, "aliyun_set_abstract.py"),
                  encoding="utf-8") as f:
            return f.read()

    def test_main_verifies_public_page(self):
        s = self._src()
        self.assertIn("verify_public(", s)
        self.assertIn("--no-verify", s)
        # 返回码约定：0 已生效 / 2 已提交未生效
        self.assertIn("return 2", s)

    def test_no_hardcoded_abs_path(self):
        s = self._src()
        self.assertNotIn('sys.path.insert(0, r"D:', s)
        self.assertIn("os.path.dirname(os.path.abspath(__file__))", s)


if __name__ == "__main__":
    unittest.main()
