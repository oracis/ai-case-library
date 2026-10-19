#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agnes_cover.py 的纯函数单测（**不连网、不调 API**）。

跑法（在项目根目录下）：
    python -m unittest scripts.test_agnes_cover

只测三件真正会出错的事：
  1. prompt 里不能出现中文（图像模型画不出中文，硬塞出乱码图）
  2. 构图方向必须跟目标尺寸一致（曾把 square 写成 horizontal）
  3. category 必须 100% 命中风格表（miss 就全落 DEFAULT_STYLE，风格不统一）

外加落盘像素回读逻辑（png_size）与 key 读取优先级。
"""
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import agnes_cover as A  # noqa: E402


def has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


class TestPromptIsAscii(unittest.TestCase):
    """prompt 必须纯 ASCII —— 这是 2026-09-30 实际踩到的坑。"""

    def _cases(self):
        with open(A.CASES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else d.get("cases", d)

    def test_no_cjk_in_any_prompt(self):
        bad = []
        for c in self._cases():
            for t in sorted(A.TARGETS):
                p = A.build_prompt(c, t)
                if has_cjk(p):
                    bad.append((c.get("id"), t))
        self.assertEqual(bad, [], "prompt 含中文: %s" % bad[:5])

    def test_no_punctuation_fragments(self):
        """中文句子里的 ASCII 碎片（`+`、`/`）不该进 prompt。

        实测踩到：`SaaS 买卖 + 数据服务` → `subject: a + software product`。
        """
        for c in self._cases():
            for t in sorted(A.TARGETS):
                p = A.build_prompt(c, t)
                self.assertNotIn("subject: a  ", p, "空 subject: %s / %s" % (c.get("id"), t))
                self.assertNotIn("subject: a +", p, "符号碎片进了 subject")
                self.assertNotIn("subject: a /", p, "符号碎片进了 subject")

    def test_ascii_only_filters_cjk_but_keeps_words(self):
        self.assertEqual(A._ascii_only("帮电商品牌全网打假"), "")
        self.assertEqual(A._ascii_only("SaaS 买卖 + 数据服务"), "SaaS")
        self.assertEqual(A._ascii_only("Chatbase AI"), "Chatbase AI")
        # 去重保序
        self.assertEqual(A._ascii_only("Voklit Voklit PROSP"), "Voklit PROSP")

    def test_subject_falls_back_to_name(self):
        """one_liner 是中文时，subject 要回退到英文产品名，不能空。"""
        c = {"id": "x", "name": "Bustem", "one_liner": "帮品牌打假",
             "category": "营销工具"}
        p = A.build_prompt(c, "square")
        self.assertIn("Bustem", p)
        self.assertFalse(has_cjk(p))


class TestCompositionMatchesTarget(unittest.TestCase):
    """构图方向必须与目标尺寸一致 —— 曾把 square 也写成 horizontal。"""

    MARKERS = {
        "square": "square composition",
        "wide": "wide horizontal composition",
        "tall": "vertical composition",
    }

    def test_marker_per_target(self):
        c = {"id": "x", "name": "Demo", "category": "AI 工具"}
        for t, marker in self.MARKERS.items():
            p = A.build_prompt(c, t)
            self.assertIn(marker, p, "target=%s 缺 %s" % (t, marker))
            # 且**不含**其它方向的 marker
            for other, om in self.MARKERS.items():
                if other != t:
                    self.assertNotIn(om, p,
                                     "target=%s 混进了 %s 的构图" % (t, om))

    def test_no_text_guard_always_present(self):
        """no text 类约束必须每条都在，否则模型把产品名当标语画出来。"""
        c = {"id": "x", "name": "Chatbase", "category": "SaaS"}
        for t in sorted(A.TARGETS):
            p = A.build_prompt(c, t)
            self.assertIn("no text", p)
            self.assertIn("no letters", p)
            self.assertIn("no watermark", p)


class TestCategoryCoverage(unittest.TestCase):
    """category 必须 100% 命中，否则风格不统一。"""

    def test_all_real_categories_covered(self):
        with open(A.CASES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        cases = d if isinstance(d, list) else d.get("cases", d)
        real = {(c.get("category") or "").strip() for c in cases}
        miss = sorted(x for x in real - set(A.CATEGORY_STYLE) if x)
        self.assertEqual(miss, [], "未覆盖的 category: %s" % miss)
        self.assertEqual(miss, [], "未覆盖的 theme: %s" % (
            sorted(x for x in real - set(A.CATEGORY_THEME) if x),))

    def test_unknown_category_falls_back(self):
        p = A.build_prompt({"id": "x", "name": "X",
                            "category": "不存在的分类"}, "square")
        self.assertIn(A.DEFAULT_STYLE, p)


class TestTargetsAndPixels(unittest.TestCase):
    """size 取值与像素回读 —— 服务端**不严格执行** size，必须回读。"""

    def test_size_values_are_legal(self):
        """Agnes 只认 1K/2K/3K/4K/WIDTHxHEIGHT，`16:9` 会 400。"""
        for t, spec in A.TARGETS.items():
            s = spec["size"]
            self.assertTrue(
                s in ("1K", "2K", "3K", "4K") or "x" in s,
                "target=%s 的 size=%r 会被服务端拒" % (t, s))

    def test_wide_does_not_use_4k(self):
        """`4K` 实测会随机返回 3845x2157 或 4096x4096，不可控。"""
        self.assertNotEqual(A.TARGETS["wide"]["size"], "4K")
        self.assertIn("x", A.TARGETS["wide"]["size"])

    def test_png_size_reads_header(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
                        + struct.pack(">II", 1234, 567))
            self.assertEqual(A.png_size(p), (1234, 567))
            # 非 PNG 必须是 None 而不是抛异常
            bad = os.path.join(td, "b.png")
            with open(bad, "wb") as f:
                f.write(b"not a png at all")
            self.assertIsNone(A.png_size(bad))

    def test_min_w_matches_want(self):
        for t, spec in A.TARGETS.items():
            self.assertLessEqual(spec["min_w"], spec["want"][0],
                                 "target=%s 的 min_w 高于目标宽度，永远 FAIL" % t)


class TestKeyLoading(unittest.TestCase):
    """key 读取优先级：env → data/secrets.json → ~/.workbuddy/keys/。"""

    def test_env_wins(self):
        old = os.environ.get("AGNES_API_KEY")
        os.environ["AGNES_API_KEY"] = "sk-from-env"
        try:
            k, src = A.load_key()
            self.assertEqual(k, "sk-from-env")
            self.assertIn("env", src)
        finally:
            if old is None:
                os.environ.pop("AGNES_API_KEY", None)
            else:
                os.environ["AGNES_API_KEY"] = old

    def test_falls_back_to_file(self):
        old = os.environ.pop("AGNES_API_KEY", None)
        try:
            k, src = A.load_key()
            if k:
                self.assertTrue(
                    src.endswith("agnes-api-key.txt") or "secrets" in src,
                    "key 来源标注不对: %s" % src)
        finally:
            if old is not None:
                os.environ["AGNES_API_KEY"] = old


class TestSpecGap(unittest.TestCase):
    def test_gap_is_serial(self):
        """间隔必须 > 0（并发会撞限流，且本项目 CDP 侧也刻意串行）。"""
        for t in A.TARGETS:
            self.assertGreater(A.spec_gap(t), 0)


if __name__ == "__main__":
    unittest.main()
