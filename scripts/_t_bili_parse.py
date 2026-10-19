#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`Bilibili.parse_batch()` 的回归测试。

⚠ 起因（2026-10-05）：真跑批量时两条草稿**确实存进了 B站草稿箱**
（`aid=343012/343013`），但解析器只认 `✓ 标题` 前缀，而脚本实际输出
`✓ API 新建草稿 …` ⇒ 两条全被判 failed、台账写 failed、队列反复重发
⇒ 远端堆重复草稿。**判据绑死前缀 = 事故配方。**

这里把三种历史输出格式都钉死，防止再绑死成另一种。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from multiplatform.adapters import get          # noqa: E402

AD = get("bilibili")


class TestParseBatch(unittest.TestCase):
    def test_api_new_draft_prefix(self):
        """API 路径（2026-05 后的实际格式）：`✓ API 新建草稿 aid=…`"""
        out = ("[1/2] easymix\n"
               "  ✓ API 新建草稿 aid=343012《标题》正文 1246 字 / 22 段\n"
               "[2/2] harperai\n"
               "  ✓ API 新建草稿 aid=343013《标题》正文 1258 字 / 23 段\n"
               "完成 2/2")
        done, failed = AD.parse_batch(out)
        self.assertEqual(done, {"easymix", "harperai"})
        self.assertEqual(failed, set())

    def test_legacy_title_prefix(self):
        """旧格式 `✓ 标题《…》` 仍要认（不能为了新格式把老回归掉）。"""
        out = ("[1/1] mort\n"
               "  ✓ 标题《MORT》正文 900 字\n")
        done, failed = AD.parse_batch(out)
        self.assertEqual(done, {"mort"})
        self.assertEqual(failed, set())

    def test_saved_draft_prefix(self):
        out = ("[1/1] zed\n  ✓ 已存草稿 aid=1《x》\n")
        done, _ = AD.parse_batch(out)
        self.assertEqual(done, {"zed"})

    def test_failure_line_overrides(self):
        """脚本自己打的失败清单最准，要能覆盖前面的 ✓。"""
        out = ("[1/2] a\n  ✓ API 新建草稿 aid=1《x》\n"
               "[2/2] b\n  ✗ 失败了\n"
               "失败 1 条：b\n")
        done, failed = AD.parse_batch(out)
        self.assertEqual(done, {"a"})
        self.assertEqual(failed, {"b"})

    def test_partial_one_ok_one_fail(self):
        out = ("[1/2] a\n  ✓ API 新建草稿 aid=1《x》\n"
               "[2/2] b\n  ✗ 草稿保存失败：超时\n"
               "完成 2/2\n")
        done, failed = AD.parse_batch(out)
        self.assertEqual(done, {"a"})
        self.assertEqual(failed, {"b"})

    def test_empty(self):
        done, failed = AD.parse_batch("")
        self.assertEqual(done, set())
        self.assertEqual(failed, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)