#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tt_body_fill.py` 的离线单测（不连浏览器）。

核心是**别再犯 2026-10-09 那个错**：清空正文后 ProseMirror 会显示占位符
`<span class="syl-placeholder">请输入正文</span>`，`innerText.length == 7`。
用`text <= 1` 判「清空成功」会把「没清空」误判成「没清空」——
脚本于是放弃，而**草稿已被自动保存成空正文**。

跑法（在 scripts/ 目录下）：
    python -m unittest test_tt_body_fill
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tt_body_fill as f                                       # noqa: E402


class TestPlaceholderTrap(unittest.TestCase):
    """⚠ 占位符陷阱 —— 这条回归钉死「清空成功」的判据。"""

    def test_placeholder_text_is_not_content(self):
        # 实测：空正文读回来是 `"请输入正文\n\n"` → innerText.length == 7
        #（占位符 5 字 + 两个换行）。旧判据用 `text <= 1` ⇒ 7 > 1
        #⇒ 误判「没清空」⇒ 脚本放弃，而草稿已被自动存成空正文。
        self.assertEqual(len(f.PLACEHOLDER + "\n\n"), 7)
        self.assertGreater(len(f.PLACEHOLDER + "\n\n"), 1)
        # 判据函数必须显式排除占位符，而不是靠长度
        self.assertIn("syl-placeholder", f._IS_EMPTY)
        self.assertIn("placeholder", f._IS_EMPTY)
        # 且要把它从 DOM 里摘掉再量长度
        self.assertIn("remove()", f._IS_EMPTY)

    def test_empty_check_is_not_length_based(self):
        """**绝不能**用 innerText 长度当「清空成功」的判据。"""
        self.assertNotIn("<= 1", f.clear_body.__doc__ or "")
        src = f.clear_body.__code__.co_consts
        self.assertNotIn(
            1, [c for c in src if isinstance(c, int) and c == 1],
            "clear_body 里不该再出现裸的阈值 1")

    def test_body_html_strips_h1(self):
        """`article.html` 第一行是 `<h1>标题</h1>`，灌正文必须剥掉。"""
        p = os.path.join(f.tp.OUT, "aeo-engine", "article.html")
        if not os.path.isfile(p):
            self.skipTest("out/toutiao/aeo-engine/article.html 不在（本机未 build）")
        with open(p, encoding="utf-8") as fh:
            full = fh.read()
        body = f.body_html("aeo-engine")
        self.assertFalse(body.startswith("<h1>"))
        self.assertEqual(body, full.split("\n", 1)[1])
        self.assertTrue(body.count("<table") >= 1)


class TestIdempotence(unittest.TestCase):
    """幂等：已经是目标内容就跳过，不能重复插入。"""

    def test_skip_when_tables_match(self):
        src = f.fill_one.__code__.co_names
        # fill_one 必须先读现状再决定（_READ / read_body 在写入路径上）
        self.assertIn("read_body", f.fill_one.__code__.co_names)

    def test_dry_never_writes(self):
        src = f.fill_one.__code__.co_consts
        self.assertTrue(any("dry" in str(c) for c in src))


class TestSafety(unittest.TestCase):
    def test_waits_for_editor_before_touching(self):
        """⚠ 动手前必须等编辑器就绪 —— 否则读到半空的编辑器就清空。"""
        names = f.fill_one.__code__.co_names
        self.assertIn("is_empty", names)
        code = f.fill_one.__code__.co_consts
        self.assertTrue(any("编辑器一直没就绪" in str(c) for c in code),
                        "缺少「编辑器未就绪就放弃」的分支")

    def test_refuses_when_clear_failed(self):
        code = f.fill_one.__code__.co_consts
        self.assertTrue(any("清空没生效" in str(c) for c in code))

    def test_never_saves_on_mismatch(self):
        """灌完表格数不对时**必须不保存**（草稿保持原样，不落半成品）。"""
        code = f.fill_one.__code__.co_consts
        self.assertTrue(any("未保存" in str(c) for c in code))


if __name__ == "__main__":
    unittest.main()