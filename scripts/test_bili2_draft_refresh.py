#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`bili2_draft_refresh.py` 的纯函数单测（不连浏览器）。

2026-11-06 这里出过一次假绿：幂等判据写成「标题相等 + 正文含 MARK 那句」，
于是正文多一整句栏目标语也照判 SKIP（ep-004 归一后 775 vs 810 字）。
现在改成整篇逐字比 —— 这组用例就是钉那个判据的。

跑法：`python -X utf8 -m unittest scripts.test_bili2_draft_refresh`
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bili2_draft_refresh as R      # noqa: E402

BODY_HTML = ("<p>导语一段。</p><h2>先说清楚：这里只有源码，没有游戏文件</h2>"
             "<p>正文里有个 <strong>加粗</strong>，还有 C++ 和 Java。</p>")
BODY_PLAIN = ("导语一段。\n\n先说清楚：这里只有源码，没有游戏文件\n\n"
              "正文里有个 加粗，还有 C++ 和 Java。")


class TestBodySame(unittest.TestCase):

    def test_排版不同也算一致(self):
        """B站存纯文本 + 空行，重建是 HTML：按内容比，不按排版比。"""
        self.assertTrue(R.body_same(BODY_PLAIN, BODY_HTML))

    def test_多一句就是不一致(self):
        """锚点句（MARK）在 ≠ 正文对得上 —— 这就是那次假绿的形状。"""
        dirty = BODY_PLAIN.replace(
            "导语一段。",
            "导语一段。\n\n「游戏厅」栏目，每期只讲一款游戏：它是什么、怎么玩。")
        self.assertIn(R.MARK, dirty)
        self.assertFalse(R.body_same(dirty, BODY_HTML),
                         "含 MARK 也必须报不一致，否则又退回「有锚点即 SKIP」")

    def test_HTML实体不算差异(self):
        """B站把 `'` 存成 `&#39;`（ep-008 实测），不解实体会每次都白改一遍。"""
        self.assertTrue(R.body_same("它&#39;s 一个模板", "<p>它's 一个模板</p>"))

    def test_少一段也要报(self):
        self.assertFalse(R.body_same(BODY_PLAIN.replace("导语一段。", ""), BODY_HTML))

    def test_空正文与有正文不同(self):
        self.assertFalse(R.body_same("", BODY_HTML))


class TestWiring(unittest.TestCase):

    def test_判据用的是body_same(self):
        """形变防护：别再改回只看标题 + 锚点。"""
        import inspect
        src = inspect.getsource(R.refresh_one)
        self.assertIn("body_same(", src)
        self.assertNotIn("MARK in old_body", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
