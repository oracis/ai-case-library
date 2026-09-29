#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili_publish.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_bilibili_publish
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bilibili_publish as bp  # noqa: E402

ART = """<section>
<h1>1Lookup：MRR $244,029；累计收入 $4,985,134（Stripe 直连验证）</h1>
<blockquote>导语：一门接口生意。</blockquote>
<h2>它是干什么的</h2>
<p>第一段，讲产品本身。<strong>加粗内容</strong>。</p>
<p>第二段提到微信小程序的事实性表述。</p>
<p style="color:gray">带 style 的段落。</p>
<p>结尾段：欢迎来我的案例库看完整核实记录。</p>
</section>"""


class TestMakeTitle(unittest.TestCase):
    def test_prefers_toutiao_meta_title(self):
        """有头条精修标题时优先复用（prosp 等已 build 过 meta.json）。"""
        t = bp.make_title("prosp", ART)
        # prosp 的头条标题是「月收$128K：销售挖客户线索的工具」
        self.assertTrue(t.startswith("月收") or t, t)

    def test_fallback_h1_strips_paren(self):
        """无头条 meta 时退回 h1，剥括号数据说明并截 30 字。"""
        t = bp.make_title("no-such-case", ART)
        self.assertNotIn("（", t)
        self.assertLessEqual(len(t), 30)


class TestCleanBody(unittest.TestCase):
    def setUp(self):
        self.body = bp.clean_body(ART)
        import re as _re
        self.plain = _re.sub(r"<[^>]+>", "", self.body)

    def test_h1_removed(self):
        self.assertNotIn("MRR $244,029", self.body)

    def test_full_body_kept(self):
        """回归：导流段正则曾跨段吞正文（1808 字→241 字）。
        关键词只允许删关键词所在的那个 <p>。"""
        self.assertIn("第一段，讲产品本身", self.plain)
        self.assertIn("第二段提到微信小程序", self.plain)
        self.assertIn("带 style 的段落", self.plain)

    def test_leadout_paragraph_dropped(self):
        self.assertNotIn("案例库", self.plain)
        self.assertNotIn("完整核实记录", self.plain)

    def test_style_stripped(self):
        self.assertNotIn("style=", self.body)

    def test_markdown_bold_converted(self):
        self.assertIn("<strong>加粗内容</strong>", self.body)

    def test_blockquote_kept(self):
        self.assertIn("导语", self.body)


class TestRiskCheck(unittest.TestCase):
    def test_cta_blocked(self):
        for t in ("加我微信看更多", "扫码进群", "关注公众号：万物解释者"):
            self.assertTrue(bp.risk_check(t), t)

    def test_factual_mention_allowed(self):
        """事实性平台提及不是导流（2026-09-29 核过 12 处全是这类）。"""
        for t in ("预约流程必须接微信小程序", "公众号的发布接口不开放",
                  "AI 生成个性化私信内容", "企业微信触达"):
            self.assertEqual(bp.risk_check(t), [], t)


class TestStripSpan(unittest.TestCase):
    """裸 <span> 剥离（2026-09-29）。

    公众号模板用 `<span>标签</span>` 做灰色小标签，style 被剥掉后 span
    就是无意义包裹 —— 37 篇共 1162 个。但**带属性的必须保留**。
    """

    def test_bare_span_stripped(self):
        out = bp.clean_body("<section><p><span>官方口径</span>：abc</p></section>")
        self.assertNotIn("<span>", out)
        self.assertIn("官方口径：abc", out)

    def test_styled_span_kept(self):
        out = bp.clean_body(
            '<section><p><span class="x" style="color:red">A</span></p></section>')
        self.assertIn("<span", out)
        self.assertIn("A", out)

    def test_nested_bare_span(self):
        out = bp.clean_body("<section><p><span>外<span>内</span></span></p></section>")
        self.assertIn("外", out)
        self.assertIn("内", out)

    def test_real_body_has_no_bare_span(self):
        """样本文章里不该再有裸 span（回归钉死）。"""
        import re as _re
        body = bp.clean_body(ART)
        self.assertIsNone(
            _re.search(r"<span(?![^>]*\b(?:class|style|id)\s*=)", body),
            "样本正文里仍有裸 span")

    def test_real_articles_all_span_free(self):
        """out/bili 下的真实产物逐篇过一遍（没建产物时跳过）。"""
        import glob
        import re as _re
        files = glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "out", "bili", "*", "article.html"))
        if not files:
            self.skipTest("还没 build 过 out/bili")
        bad = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                b = bp.clean_body(fh.read())
            if _re.search(r"<span(?![^>]*\b(?:class|style|id)\s*=)", b):
                bad.append(os.path.basename(os.path.dirname(f)))
        self.assertEqual(bad, [], "这些篇仍有裸 span：%s" % bad[:5])


if __name__ == "__main__":
    unittest.main()
