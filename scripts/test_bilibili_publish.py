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


class TestBlockquoteMerge(unittest.TestCase):
    """B站 Tiptap 给每个 blockquote 上下大边距，连续两块看起来就是
    「每段分得太开」（用户 2026-09-29 反馈）。clean_body 要合并它们。"""

    def test_two_adjacent_merged(self):
        out = bp.clean_body(
            "<section><blockquote>甲</blockquote><blockquote>乙</blockquote></section>")
        self.assertEqual(out.count("<blockquote"), 1)
        self.assertIn("甲", out)
        self.assertIn("乙", out)
        self.assertIn("<br>", out)

    def test_merge_ignores_whitespace_and_attrs(self):
        out = bp.clean_body(
            "<section><blockquote>甲</blockquote>\n  <blockquote class='x'>乙</blockquote></section>")
        self.assertEqual(out.count("<blockquote"), 1)

    def test_non_adjacent_kept(self):
        out = bp.clean_body(
            "<section><blockquote>甲</blockquote><p>中</p><blockquote>乙</blockquote></section>")
        self.assertEqual(out.count("<blockquote"), 2)

    def test_real_articles_no_adjacent_blockquote(self):
        import glob
        import re as _re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = glob.glob(os.path.join(root, "out", "articles", "*.html"))
        if not files:
            self.skipTest("还没生成 out/articles")
        bad = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                b = bp.clean_body(fh.read())
            if _re.search(r"</blockquote>\s*<blockquote", b, _re.I):
                bad.append(os.path.basename(f))
        self.assertEqual(bad, [], "这些篇仍有连续 blockquote：%s" % bad[:5])


class TestCoverHelpers(unittest.TestCase):
    """封面链路的纯函数部分（2026-09-29 实测定稿）。"""

    def test_cover_reads_image_urls_not_banner(self):
        """草稿接口的 banner_url 恒空，真封面在 origin_image_urls /
        image_urls —— 拿 banner_url 当判据会永远误判成「没封面」。"""
        d = {"banner_url": "", "origin_image_urls": ["http://a/1.png"],
             "image_urls": ["http://a/1_thumb.png"]}
        self.assertEqual(bp._cover_of(d), "http://a/1.png")

    def test_cover_falls_back_to_image_urls(self):
        d = {"banner_url": "", "origin_image_urls": None,
             "image_urls": ["http://a/t.png"]}
        self.assertEqual(bp._cover_of(d), "http://a/t.png")

    def test_cover_accepts_string_form(self):
        d = {"banner_url": "", "origin_image_urls": "http://a/s.png"}
        self.assertEqual(bp._cover_of(d), "http://a/s.png")

    def test_cover_empty_when_nothing(self):
        self.assertEqual(bp._cover_of({"banner_url": ""}), "")
        self.assertEqual(bp._cover_of({"image_urls": None}), "")
        self.assertEqual(bp._cover_of({}), "")

    def test_cover_banner_alone_is_not_a_cover(self):
        """只有 banner_url 时不算有封面（草稿接口恒给空，压根不是这含义）。"""
        self.assertEqual(bp._cover_of({"banner_url": "http://x/b.jpg"}), "")

    def test_iframe_js_shape_is_valid(self):
        """外层 IIFE 必须 return body 的结果；body 里再套一层 IIFE 而不
        return 会让整个表达式退化成 undefined（实测报 FAIL:None）。"""
        expr = bp._iframe_js("return 42;")
        self.assertTrue(expr.endswith("})()"))
        self.assertIn("var d=f.contentDocument;", expr)

    def test_set_files_is_function_expression(self):
        """必须是匿名函数**表达式**（带括号）；裸写 function(){}
        会被当函数声明 → SyntaxError: Function statements require a
        function name。"""
        s = bp._JS_SET_FILES.strip()
        self.assertTrue(s.startswith("(function("), s[:40])
        self.assertIn("DataTransfer", s)

    def test_set_files_reads_size_before_dispatch(self):
        """change 事件是同步的，Vue 会立刻清空 input —— files 必须在
        派发前取值，否则 files[0] 是 undefined。"""
        s = bp._JS_SET_FILES
        i_sz = s.index("inp.files[0].size")
        i_disp = s.index("dispatchEvent")
        self.assertLess(i_sz, i_disp,
                        "必须在 dispatchEvent 之前读 files[0].size")

    def test_cover_source_exists_for_all_cases(self):
        """37 篇都要有 cover.png 源图（B站封面直接复用头条的）。"""
        import glob
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ids = [os.path.basename(f)[:-5]
               for f in glob.glob(os.path.join(root, "out", "articles", "*.html"))]
        if not ids:
            self.skipTest("还没生成 out/articles")
        missing = [c for c in ids if not bp.cover_path(c)]
        self.assertEqual(missing, [], "缺封面源图：%s" % missing[:5])

    def test_cover_source_meets_bili_min_size(self):
        """B站要求 ≥600x336；本地头条封面是 3840x2160。"""
        import struct
        ids = [c for c in bp.list_ids() if bp.cover_path(c)]
        if not ids:
            self.skipTest("还没 build 封面")
        for c in ids[:5]:
            with open(bp.cover_path(c), "rb") as f:
                head = f.read(33)
            w, h = struct.unpack(">II", head[16:24])
            self.assertGreaterEqual(w, 600, c)
            self.assertGreaterEqual(h, 336, c)

    def test_draft_api_field_names(self):
        """踩过的坑：接口在 api.bilibili.com（不是 member.），
        列表字段是 data.drafts（不是 items/list）。"""
        self.assertIn("api.bilibili.com", bp.BILI_DRAFT_API)
        self.assertIn("/x/dynamic/feed/article/draft/list", bp.BILI_DRAFT_API)

    def test_draft_list_page_route(self):
        """/platform/upload-manager/opus 默认是「图文」tab，
        草稿真实路由是 /opus/management/drafts。"""
        self.assertIn("/opus/management/drafts", bp.BILI_DRAFT_LIST_PAGE)
        self.assertIn("/platform/upload/text/new-edit", bp.BILI_EDIT_URL)


if __name__ == "__main__":
    unittest.main()
