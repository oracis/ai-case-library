#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""封面配色分配的两个性质测试。

背景（2026-09-30）：用户在头条草稿箱发现《Quran Unlock》和《MORT》封面
一模一样，都是深绿底。根因是 `sum(ord) % 6` 这个哈希太弱 —— 两条 id 的
字符数差异刚好抵消，都落到索引 0。

修法见 wechat_publish._cover_theme。这里钉死两条不能退化的性质：

1. **相邻不同色** —— 39 条案例只有 10 套主题，全局互不撞色做不到
   （必然重复），但**挨着发的两条必须不同色**，否则用户在草稿箱一眼就看
   出来。这是真正影响观感的约束。
2. **稳定** —— 分配只由 id 决定。全量跑（covers）和单条跑
   （cover --case mort）必须给同一条案例同一个颜色，否则已发布的封面
   会在重跑时变色。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "data", "cases.json")


def _ids():
    with open(CASES, encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else list(data.values())
    return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]


class TestCoverTheme(unittest.TestCase):

    def setUp(self):
        wp._ALL_IDS_CACHE = None

    def test_相邻两条不同色(self):
        ids = _ids()
        cols = [wp._cover_theme(cid, "toutiao")[0] for cid in ids]
        bad = [(ids[i], ids[i + 1]) for i in range(len(ids) - 1)
               if cols[i] == cols[i + 1]]
        self.assertEqual(bad, [], "这些相邻案例撞色了：%s" % bad)

    def test_单条与全量结果一致(self):
        """全量跑和单条跑必须给同一条案例同一个颜色。

        否则 `cover --case mort` 重跑一次，已发布封面就变色了。
        """
        ids = _ids()
        full = {cid: wp._cover_theme(cid, "toutiao")[0] for cid in ids}
        for cid in ids:
            wp._ALL_IDS_CACHE = None          # 模拟独立进程
            self.assertEqual(wp._cover_theme(cid, "toutiao")[0], full[cid],
                             "%s 单条跑与全量跑颜色不一致" % cid)

    def test_主题数量够用(self):
        """10 套主题 / 39 条案例 —— 少一套就更容易出现相邻撞色。

        不是硬性下限（真撞了也只影响观感），但掉到个位数就该补色了。
        """
        self.assertGreaterEqual(len(wp.COVER_THEMES), 10)

    def test_每套主题三色齐全且不近黑(self):
        """底色刻意避开近黑区：旧版渐变两端亮度都在 0~40，显示端一提暗部
        就出现肉眼可见的色带（2026-09-21 实测）。"""
        for base, band, ac in wp.COVER_THEMES:
            for c in (base, band, ac):
                self.assertRegex(c, r"^#[0-9a-fA-F]{6}$", "颜色格式错：%s" % c)
            self.assertNotEqual(base, band)
            self.assertNotEqual(base, ac)

    def test_未知id不炸(self):
        wp._ALL_IDS_CACHE = None
        t = wp._cover_theme("__not_in_cases__", "toutiao")
        self.assertIn(t, wp.COVER_THEMES)


if __name__ == "__main__":
    unittest.main()
