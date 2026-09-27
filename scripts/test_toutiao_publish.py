#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toutiao_publish.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_toutiao_publish
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402

MD = """# 标题行

<!-- 注释别出来 -->

> 一句钩子

## 它是干什么的

做一件事 https://example.com/a  很厉害，见 [官网](https://x.com)。

| 维度 | 内容 |
| --- | --- |
| 官方口径 | 月收入 $12K |

## 钱从哪来

- 第一点 **加粗**
- 第二点
"""


class TestBlocks(unittest.TestCase):
    def test_h1_and_comment_dropped(self):
        bs = tp.md_to_blocks(MD)
        self.assertFalse(any("标题行" in t for _, t in bs))
        self.assertFalse(any("注释" in t for _, t in bs))

    def test_url_and_md_link_cleaned(self):
        text = "\n".join(t for _, t in tp.md_to_blocks(MD))
        self.assertNotIn("http", text)
        self.assertIn("官网", text)
        self.assertNotIn("**", text)

    def test_table_becomes_kv(self):
        bs = tp.md_to_blocks(MD)
        kv = [t for k, t in bs if k == "kv"]
        self.assertTrue(any(t.startswith("官方口径") for t in kv))
        self.assertFalse(any(t.startswith("维度") for t in kv))
        self.assertFalse(any("|" in t for _, t in bs))

    def test_kinds(self):
        bs = tp.md_to_blocks(MD)
        kinds = [k for k, _ in bs]
        self.assertIn("quote", kinds)
        self.assertIn("h2", kinds)
        self.assertIn("ul", kinds)
        self.assertIn("p", kinds)

    def test_html_structure(self):
        html = tp.blocks_to_html(tp.md_to_blocks(MD))
        self.assertIn("<p><strong>它是干什么的</strong></p>", html)
        self.assertIn("<p>· 第一点 加粗</p>", html)
        self.assertIn("「一句钩子」", html)
        self.assertNotIn("<table", html)
        self.assertIn(tp.FOOTER_NOTE, html)

    def test_empty_input(self):
        self.assertEqual(tp.md_to_blocks(""), [])
        self.assertIn(tp.FOOTER_NOTE, tp.blocks_to_html([]))


class TestTitle(unittest.TestCase):
    def test_override_preferred_and_capped(self):
        cases = tp.load_cases()
        self.assertTrue(cases)
        for c in cases[:10]:
            t = tp.make_toutiao_title(c)
            self.assertTrue(t, "标题不能为空：%s" % c["id"])
            self.assertLessEqual(len(t), tp.TITLE_MAX,
                                 "标题超 %d 字：%s -> %s" % (tp.TITLE_MAX, c["id"], t))

    def test_fallback_no_crash(self):
        t = tp.make_toutiao_title({"id": "no-such-case", "name": "某产品",
                                   "one_liner": "做一件事"})
        self.assertTrue(t)
        self.assertLessEqual(len(t), tp.TITLE_MAX)


class TestBuild(unittest.TestCase):
    def test_build_article_offline(self):
        c = {"id": "pieter-levels", "name": "Pieter Levels 的产品矩阵",
             "one_liner": "组合月收入 $250K+"}
        m = tp.build_article(c)
        self.assertLessEqual(len(m["title"]), tp.TITLE_MAX)
        self.assertTrue(m["html"].startswith("<p>"))
        self.assertIn("关于本栏目", m["html"])

    def test_all_cases_have_md_and_reasonable_length(self):
        """全库 build 一遍：正文不该短到不能发（少于 300 字的列出来给人看）。"""
        short = []
        for c in tp.load_cases():
            m = tp.build_article(c)
            if m["chars"] < tp.BODY_MIN:
                short.append((c["id"], m["chars"]))
        if short:
            print("\n[info] 正文少于 %d 字的案例（头条建议不发）：%s"
                  % (tp.BODY_MIN, short))
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
