# -*- coding: utf-8 -*-
"""title_match.py 回归锁：B站/头条/公众号共用的一套认亲判据。

2026-11-20 之前两边各写一份容差（一个 12 字、一个 20 字），同一篇稿在
两个平台会得到不同结论。这里钉死唯一口径：归一化后完全相等，或一方是
另一方前缀且差 ≤ MAX_GAP；空标题永不认亲；候选唯一由调用方保证。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import title_match as T  # noqa: E402


class TestNorm(unittest.TestCase):
    def test_nfkc_fullwidth_to_halfwidth(self):
        self.assertEqual(T.norm("ＡＩ　工具"), "ai工具")

    def test_punctuation_and_quotes_are_stripped(self):
        raw = "NetHack，能玩死你的地牢模拟器-腾讯云开发者社区-腾讯云"
        self.assertEqual(T.norm(raw), "nethack能玩死你的地牢模拟器腾讯云开发者社区腾讯云")

    def test_all_punctuation_is_empty(self):
        self.assertEqual(T.norm("—～《》「」（）"), "")

    def test_none_and_empty(self):
        self.assertEqual(T.norm(None), "")
        self.assertEqual(T.norm(""), "")


class TestTitleMatch(unittest.TestCase):
    def test_exact_after_normalization(self):
        self.assertTrue(T.title_match("ＡＩ　工具－拆解", "ai工具拆解"))

    def test_platform_suffix_is_within_gap(self):
        # 平台在标题后面挂的尾巴必须能被认出来，否则已发侧全是 ORPHAN
        self.assertTrue(T.title_match("古代文明 RTS：读 0 A.D. 的资源与时代",
                                      "古代文明 RTS：读 0 A.D. 的资源与时代-博客园"))

    def test_gap_larger_than_limit_is_rejected(self):
        short = "AI 工具"
        long = short + "很长很长的尾巴" * 3
        self.assertGreater(len(T.norm(long)) - len(T.norm(short)), T.MAX_GAP)
        self.assertFalse(T.title_match(short, long))

    def test_common_prefix_but_not_prefix_relation(self):
        self.assertFalse(T.title_match("AI 工具测评", "AI 工具推荐"))

    def test_empty_side_never_matches(self):
        for a, b in (("", "任何标题"), ("「」", "任何标题"),
                     ("任何标题", ""), ("", "")):
            self.assertFalse(T.title_match(a, b), (a, b))

    def test_ascii_case_insensitive(self):
        self.assertTrue(T.title_match("Quake 1999", "quake 1999"))

    def test_match_is_symmetric(self):
        a, b = "标题", "标题-补一点尾巴"
        self.assertEqual(T.title_match(a, b), T.title_match(b, a))

    def test_gap_threshold_is_exactly_twelve(self):
        base = "标题"
        self.assertTrue(T.title_match(base, base + "x" * T.MAX_GAP))
        self.assertFalse(T.title_match(base, base + "x" * (T.MAX_GAP + 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
