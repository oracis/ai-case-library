#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili_publish.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_bilibili_publish
"""
import os
import re
import subprocess
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
        """无人工覆盖时才看头条 meta，且**必须不含金额**才允许复用。"""
        t = bp.make_title("prosp", ART)
        # prosp 已有人工覆盖（无金额口径），断言的应是覆盖生效
        self.assertFalse(bp.TITLE_MONEY_RE.search(t), t)

    def test_fallback_h1_strips_paren(self):
        """无头条 meta 时退回 h1，剥括号数据说明并截 30 字。"""
        t = bp.make_title("no-such-case", ART)
        self.assertNotIn("（", t)
        self.assertLessEqual(len(t), 30)

    def test_标题一律不带金额(self):
        """2026-10-02 用户要求：封面去了金额，标题也不许带。

        金额是抓取当天的快照，站点自己会变。逐条断言而不是抽样 ——
        抽样漏掉一条就等于线上多一条带金额的草稿。
        """
        ov = bp._bili_title_overrides()
        self.assertGreaterEqual(len(ov), 39, "标题覆盖表应覆盖全部案例")
        bad = []
        for cid in ov:
            t = bp.make_title(cid, ART)
            if bp.TITLE_MONEY_RE.search(t):
                bad.append((cid, t))
        self.assertEqual(bad, [], "标题仍带金额：%r" % (bad,))

    def test_标题不超30字且无省略号(self):
        bad = []
        for cid in bp._bili_title_overrides():
            t = bp.make_title(cid, ART)
            if len(t) > 30:
                bad.append((cid, "超长 %d" % len(t), t))
            if "…" in t:
                bad.append((cid, "省略号", t))
        self.assertEqual(bad, [])

    def test_金额正则不误报(self):
        """「B2B 的 B2」「5 万个企业招聘页」都不是金额。

        这条踩过两次：金额正则写太宽会把 B2B 当成 $2，把普通数量
        当成营收，审计就会一直报假警，最后没人看审计结果。
        """
        for s in ("GojiberryAI：B2B 团队的 AI GTM 平台",
                  "MORT：AI 求职代理，盯 5 万个企业招聘页",
                  "MORT：AI 求职代理，扫 5 万条简历"):
            self.assertIsNone(bp.TITLE_MONEY_RE.search(s), s)
        for s in ("月收$1.6K：给跨境团队搭云通信",
                  "年收$200M：AI 客服解决才收钱",
                  "$85K成交：盯住 AI 怎么提你品牌",
                  "月入 30 万的自动化 SEO 引擎"):
            self.assertIsNotNone(bp.TITLE_MONEY_RE.search(s), s)


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

    def test_封面源图覆盖所有已排队的草稿(self):
        """要发的每一篇都必须有 cover.png 源图（B站封面直接复用头条的）。

        ⚠ 2026-10-29 改口径：原来钉的是「out/articles 下 37 篇全有封面」。
        文章库后来涨到 270 篇，其中 195 篇只是入库、从没走过头条那条排版线，
        压根不会发 B站，自然没有 cover.png——继续按「全部文章」钉，等于要求
        给不发 B站的稿子统统做封面（而且真去生成就是 195 张白做的图）。

        真正会发布的是 `data/bili_drafts.json` 里排队的那批（B站一天只让发
        1 篇，队列就是全部待发面）。钉它才抓得住真事故：没封面 = 发到那一刻
        必失败（`send-draft` 会报「没有封面源图」）。

        out/ 是 gitignore 的构建产物，CI 里没有 ⇒ 明确跳过而不是假装通过。
        """
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(root, "out", "toutiao")):
            self.skipTest("没有 out/toutiao（构建产物未生成，CI 常态）")
        p = os.path.join(root, "data", "bili_drafts.json")
        if not os.path.isfile(p):
            self.skipTest("没有 data/bili_drafts.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        ids = list(d.keys()) if isinstance(d, dict) else [x.get("id") for x in d]
        ids = [c for c in ids if c]
        if not ids:
            self.skipTest("B站草稿账是空的")
        missing = [c for c in ids if not bp.cover_path(c)]
        self.assertEqual(missing, [],
                         "已排队的草稿缺封面源图：%s" % missing[:5])

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


class TestTagGapNewline(unittest.TestCase):
    """标签间换行是「每段分得太开」的真凶（2026-09-30 实测）。

    B站 Tiptap 的 insertHTML 把 `>` 与下一个 `<` 之间的空白文本节点当成
    额外空段落：`<p>A</p>\\n<p>B</p>` → A、空段、B。coral 实测灌入后全文
    5785px、夹 20+ 个 58px 空段（正常段 29px）；压掉换行后只剩 2514px。
    """

    def test_no_newline_between_tags(self):
        html = "<section><p>A</p>\n<p>B</p>\n<blockquote>C</blockquote>\n</section>"
        out = bp.clean_body(html)
        self.assertNotIn("\n", out)
        self.assertIn("<p>A</p><p>B</p><blockquote>C</blockquote>", out)

    def test_space_inside_paragraph_kept(self):
        """只压标签**之间**的空白，段内文字间的空格是正文内容，必须留。"""
        html = "<section><p>付费意愿 3/5 与 支付 3/5</p></section>"
        out = bp.clean_body(html)
        self.assertIn("付费意愿 3/5 与 支付 3/5", out)

    def test_all_37_articles_newline_free(self):
        ids = bp.list_ids()
        self.assertGreaterEqual(len(ids), 30)
        for cid in ids:
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertNotIn("\n", out, "%s 的clean_body 仍含换行" % cid)


class TestHeadingToBold(unittest.TestCase):
    """B站编辑器没有小标题功能，h2/h3 必须降级成加粗段落。

    B站 CSS 把 h1-h6 默认样式全清掉（`:where(p,h1..h6){margin:0;font-size:inherit}`），
    唯一给 h2 的规则是 `font-weight:500;margin-top:36px;font-size:18px` ——
    不加粗 + 上方 36px + 下方 0，就是用户说的「小标题没有样式、和下面内容
    隔了太多行」。内联 style 灌进去也会被 Tiptap 剥掉（实测存草稿重开后
    style 消失），所以只能走 `<p><strong>`。
    """

    def test_h2_becomes_strong_para(self):
        out = bp.clean_body("<section><h2>钱从哪来</h2><p>月订阅。</p></section>")
        self.assertIn("<p><strong>▶ 钱从哪来</strong></p>", out)
        self.assertNotIn("<h2", out)

    def test_h3_becomes_strong_para(self):
        out = bp.clean_body("<section><h3>细节</h3><p>x</p></section>")
        self.assertIn("<p><strong>▶ 细节</strong></p>", out)
        self.assertNotIn("<h3", out)

    def test_numbered_heading_keeps_prefix(self):
        """`## 一、xxx` 的序号本来就该有，不加▶。"""
        out = bp.clean_body("<section><h2>一、它是什么</h2><p>x</p></section>")
        self.assertIn("<p><strong>一、它是什么</strong></p>", out)
        self.assertNotIn("▶", out)

    def test_no_heading_left_in_37(self):
        import re as _re
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertEqual(_re.findall(r"<h[1-6][ >]", out), [],
                             "%s 仍残留 h 标签" % cid)


FLEX_P = ('<p style="margin:0;background:#f6f8fa;display:flex;'
          'justify-content:space-between;">'
          '<span style="color:#57606a;">%s</span>'
          '<span style="font-weight:bold;">%s</span></p>')


class TestFlexTableToKvBlock(unittest.TestCase):
    """公众号的「数据表格」是 flex 伪装的，必须转成 B站能看的键值块。

    用户反馈「表格形式的数据样式都丢失了」。实测 37/37 全有这种表，
    每篇 11–16 行，共 500 行。源 HTML 里**没有** `<table>`，是
    `<p style="display:flex;justify-content:space-between">` + 两个
    `<span>`（标签/值）两端对齐伪装出来的。剥 style + 剥裸 span 会把
    两者粘成 `官方口径$3,150 MRR`。
    """

    def test_真表格不会漏进输出(self):
        """量**输出**，不量源码（2026-10-29 改判据）。

        原判据是「源 HTML 里没有 `<table>`」——那是 2026-09-30 的事实：公众号
        模板用 flex 伪装表格。但手写稿（`out/articles/mcdpoints.html`）会直接
        写 `<table>`，此后这个前提就不成立了，继续钉源码等于禁止手写稿用表格。

        真正要保证的是「B站编辑器里看不到表格」：B站没有表格能力，灌进去会被
        整体降级成一个 `<p>`、单元格粘成一串。所以断言落在 `clean_body` 的产物上，
        由 `_tables_to_kv_block` 负责把真表格转成键值块。
        """
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertNotIn("<table", out.lower(),
                             "%s 输出里漏进了真表格" % cid)

    def test_flex_row_becomes_kv(self):
        html = "<section>" + FLEX_P % ("官方口径", "$3,150 MRR") + \
               FLEX_P % ("客户", "44 个活跃订阅") + "</section>"
        out = bp.clean_body(html)
        self.assertIn("官方口径", out)
        self.assertIn("$3,150 MRR", out)
        # 关键：标签和值之间必须有分隔，不能粘连
        self.assertNotIn("官方口径$3,150", out)

    def test_group_wrapped_in_one_blockquote(self):
        """整组包**一个** blockquote（不是每行一个）。

        实测：整组一个 135px + 22px 缩进；每行一个 159px 且更碎 ——
        连续多个 blockquote 各带 24px 边距，正是「每段分得太开」的成因。
        """
        html = "<section>" + FLEX_P % ("团队", "未披露") + \
               FLEX_P % ("融资", "未披露") + "</section>"
        out = bp.clean_body(html)
        self.assertEqual(out.count("<blockquote"), 1)
        self.assertIn("<blockquote>", out)

    def test_label_column_padded_to_same_width(self):
        """标签列补半角空格到统一槽位，**分隔符落在同一列**。

        ⚠ 2026-10-09 改口径：旧断言钉的是全角空格（`　+`），实现已换成
        「标签 + 半角空格 + ｜ 」。全角在 B站比例字体下宽度不定，
        正是用户截图里「值列对不齐」的成因，全角钉法已作废。
        """
        html = "<section>" + FLEX_P % ("够得着客户", "3/5") + \
               FLEX_P % ("启动轻", "5/5") + "</section>"
        out = bp.clean_body(html)
        # ⚠ 两个取捕获的坑：
        #   ① 必须贪婪 `[^<]*`：惰性会被后面的 `\s*` 抢走补空格。
        #   ② **绝不能 rstrip** —— 补的半角空格正是要测的东西，rstrip 等于
        #      把被测对象删掉。捕获组回溯后正好是「标签 + 补空格」，
        #      ｜ 归 `\s*`，不含分隔符本身。
        # 断言用 `_display_width`（CJK 记 2），不是 len()：补的是半角空格，
        # 标签是中文，字符数天然不等，显示宽度才相等。
        rows = re.findall(r"<p>([^<]*)｜\s*<strong>", out)
        self.assertEqual(len(rows), 2, out)
        widths = {bp._display_width(r) for r in rows}
        self.assertEqual(len(widths), 1, "标签列没对齐：%r" % rows)

    def test_用表意空格U3000补齐而非半角(self):
        """回归钉：标签列的填充物必须是 U+3000（恒 1em），不是半角空格。

        ⚠⚠ **2026-10-10 修正**：这条断言原来写的是「不许出现 U+3000」，
        那是在禁止**全角空格对齐**（旧方案，已废弃）。但排查发现真正的问题
        恰恰是**半角空格**：B站正文是比例字体，半角宽度约0.25–0.5em 且随
        字体浮动 ⇒ 数值算准了渲染仍对不齐（用户截图即此现象）。
        U+3000 在中文字体里恒为 1em、与中文字等宽，是唯一可靠解法，
        所以断言方向必须反过来：**必须用 U+3000，且不许用半角补齐**。
        """
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            rows = re.findall(r"<p>([^<]*?)｜\s*<strong>", out)
            if not rows:
                continue
            self.assertTrue(
                any("\u3000" in r for r in rows)
                or len({bp._display_width(r) for r in rows}) == 1,
                "%s 的标签列既没补 U+3000 也没对齐" % cid)

    def test_标签列不用半角空格补齐(self):
        """标签与分隔符之间不许出现半角空格（比例字体下宽度不定）。

        只允许「标签 + U+3000* ｜ + 半角空格 + 值」的形态。
        """
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            rows = re.findall(r"<p>([^<]*?)｜\s*<strong>", bp.clean_body(h))
            for r in rows:
                if "\u3000" in r:
                    # U+3000 之后不该再跟 ASCII 空格（分隔符前只留 1 个）
                    after = r.split("\u3000")[-1]
                    self.assertNotIn(
                        "  ", after,
                        "%s 分隔符前有多个半角空格：%r" % (cid, r))
                else:
                    # 无补齐 ⇒ 只能是「纯中文标签 + 单个半角空格」
                    self.assertNotIn(
                        "  ", r,
                        "%s 疑似用半角空格补齐：%r" % (cid, r))

    def test_分隔符把两列切开(self):
        """标签和值必须被 ｜ 隔开，不能粘连（用户看到的「像乱码」）。"""
        html = "<section>" + FLEX_P % ("付费意愿", "3/5") + \
               FLEX_P % ("支付可达", "3/5") + "</section>"
        out = bp.clean_body(html)
        self.assertEqual(out.count("｜"), 2, out)
        self.assertNotIn("付费意愿3/5", out)
        self.assertNotIn("付费意愿<strong>", out)

    def test_输出不含多余右花括号(self):
        """回归钉：2026-10-09 真的 bug —— return 里字面量多写一个 `}`。

        实测 trustmrr 3 处 `</blockquote>}`，正文凭空冒出花括号。
        根因就是 return 语句本身，不是正则；曾误诊为「正则跨标签 / 捕获组
        错位」并据此「加固」了两个本来正确的正则。钉死输出侧。

        ⚠ 2026-10-29 收窄到「**标签紧跟着的**裸花括号」，即 `>}` 这个形态：
        那正是当初那个 bug 的样子（模板里多写的字面量，粘在 `</blockquote>`
        后面）。正文里的 `}` 是内容——mcdpoints / mcdpoints-wx 两篇讲 TOON
        格式，原样引用 `[160]{字段列表}:`，整段禁用会逼着人删掉真实引用才算过。
        收窄后判据仍然钉在缺陷形态上，不是「放宽到不管」。
        """
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertNotIn(">}", out, "%s 有标签后紧跟的裸花括号" % cid)
            self.assertEqual(out.count("<blockquote>"),
                             out.count("</blockquote>"),
                             "%s blockquote 开闭不配对" % cid)

    def test_槽位下限为0(self):
        """补空格数下限必须是 0 —— 下限 1 会让最长标签那行凭空多推一格，
        ｜ 比别的行右移，正是要消灭的那种参差。"""
        html = "<section>" + FLEX_P % ("团队", "1 人") + \
               FLEX_P % ("窗口还开着", "4/5") + "</section>"
        out = bp.clean_body(html)
        # 捕获组是「标签 + 补空格 + ｜ 前那个固定间隔空格」，
        # 所以对齐目标是 slot+1 而不是 slot。最长标签那行补空格=0，
        # 若下限写成 1 这里会变成 slot+2，测试即挂。
        rows = re.findall(r"<p>([^<]*)｜\s*<strong>", out)
        self.assertEqual(len(rows), 2, out)
        self.assertEqual({bp._display_width(r) for r in rows},
                         {bp._display_width("窗口还开着") + 1}, rows)

    def test_相邻非flex段落不被吞掉(self):
        """转换只吃连续的 flex 行，前后普通段落必须原样保留。"""
        html = ("<section><p>前导段落。</p>"
                + FLEX_P % ("团队", "1 人")
                + FLEX_P % ("融资", "$0，无 VC") +
                "<p>后继段落。</p></section>")
        out = bp.clean_body(html)
        self.assertIn("前导段落。", out)
        self.assertIn("后继段落。", out)
        self.assertIn("团队", out)
        self.assertIn("无 VC", out)
        self.assertEqual(out.count("<p>"), out.count("</p>"))

    def test_value_is_bold(self):
        html = "<section>" + FLEX_P % ("客户", "44 个") + \
               FLEX_P % ("团队", "未披露") + "</section>"
        self.assertIn("<strong>44 个</strong>", bp.clean_body(html))

    def test_single_row_not_wrapped(self):
        """只有一行时别包 blockquote（一个 blockquote 包单行没意义）。"""
        html = "<section>" + FLEX_P % ("年化收入", "$1.2M") + "</section>"
        out = bp.clean_body(html)
        self.assertNotIn("<blockquote", out)
        self.assertIn("$1.2M", out)

    def test_all_37_have_kv_blocks(self):
        """37 篇每篇都得转出键值块，且不能残留粘连。"""
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertIn("<blockquote>", out, "%s 没转出键值块" % cid)
            # flex 特征必须已被消费掉
            self.assertNotIn("space-between", out, "%s 残留 flex" % cid)

    def test_order_matters_flex_before_strip_span(self):
        """顺序回归：转换必须在剥裸 span 之前，否则 span 分列信息没了。

        钉法：clean_body 源码里 `_flex_rows_to_kv_block` 必须出现在
        「剥裸 span」那条 re.sub 之前。
        """
        import inspect
        code = [ln for ln in inspect.getsource(bp.clean_body).splitlines()
                if not ln.strip().startswith("#")]
        body = "\n".join(code)
        i_flex = body.index("_flex_rows_to_kv_block")
        i_span = body.index(r"<span(?![^>]*\b(?:class|style|id)")
        i_style = body.index(r'\s+style=\"[^\"]*\"')
        self.assertLess(i_flex, i_span, "必须在剥裸 span 之前")
        self.assertLess(i_flex, i_style, "必须在去 style 之前")

    def test_display_width_counts_cjk_as_two(self):
        self.assertEqual(bp._display_width("团队"), 4)
        self.assertEqual(bp._display_width("够得着客户"), 10)
        self.assertEqual(bp._display_width("　"), 2)   # 全角空格
        self.assertEqual(bp._display_width("3/5"), 3)


class TestNoRealTableInserted(unittest.TestCase):
    """B站不支持真表格，别往里灌 <table>。

    实测：工具栏零命中；insertHTML 灌 `<table><tr><td>A</td><td>B</td></tr>`
    会被整体降级成**一个 <p>**，所有单元格粘成一串，连换行都没有 ——
    比不处理还糟。
    """

    def test_clean_body_never_emits_table(self):
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            out = bp.clean_body(h)
            self.assertNotIn("<table", out.lower(), "%s 输出了 table" % cid)


class TestKeyClearPath(unittest.TestCase):
    """replace 清空必须走真实键盘事件，不能用 execCommand。

    execCommand('selectAll')+delete 在 DOM 上看着删干净，ProseMirror 内部
    state 却没同步 —— 下一步读children 旧内容自己回来，insertHTML 静默
    无效，整篇正文被塞进遗留的 <blockquote class="eva3-blockquote"> 里，
    渲染成一整块引用（就是「样式丢失、段落分得太开」的成因）。
    """

    def test_key_helper_sends_both_events(self):
        sent = []

        class FakeCDP:
            def send(self, method, params=None):
                sent.append((method, dict(params or {})))

        bp._key(FakeCDP(), "a", "KeyA", 65, mods=2)
        self.assertEqual([m for m, _ in sent],
                         ["Input.dispatchKeyEvent", "Input.dispatchKeyEvent"])
        self.assertEqual(sent[0][1]["type"], "keyDown")
        self.assertEqual(sent[1][1]["type"], "keyUp")
        self.assertEqual(sent[0][1]["modifiers"], 2)
        self.assertEqual(sent[0][1]["windowsVirtualKeyCode"], 65)

    def test_publish_source_has_no_execcommand_selectall(self):
        """守住回归：别再把 execCommand selectAll 换回来。

        只查**代码行**（剔除 # 注释），否则注释里为了说明这个坑而写的
        "execCommand('selectAll')" 字样会把自己判失败。
        """
        import inspect
        code = [ln for ln in inspect.getsource(bp.publish_one).splitlines()
                if not ln.strip().startswith("#")]
        body = "\n".join(code)
        self.assertNotIn("execCommand('selectAll')", body)
        self.assertNotIn("execCommand('delete')", body)
        self.assertIn("_key(cdp", body)

    def test_insert_verifies_top_level_blocks(self):
        """灌完必须校验顶层块数与字数，否则静默无效会被当成成功。"""
        import inspect
        src = inspect.getsource(bp.publish_one)
        self.assertIn("正文疑似没灌进去", src)


class TestVerifySaved(unittest.TestCase):
    """保存后必须远端回读 —— 「点了保存」不等于「存上了」。

    2026-09-30 实测踩到：旧版点完按钮就return True，远端正文一字未变，
    整轮 37 条全"成功"但远端是旧版。这类假成功最难查。
    """

    def test_verify_saved_exists_and_checks_mtime(self):
        import inspect
        src = inspect.getsource(bp._verify_saved)
        self.assertIn("mtime", src)
        self.assertIn("summary", src)
        self.assertIn('return "ok"', src)

    def test_publish_calls_verify_saved_on_replace(self):
        import inspect
        src = inspect.getsource(bp.publish_one)
        self.assertIn("_verify_saved(", src)

    def test_verify_saved_signature(self):
        import inspect
        sig = inspect.signature(bp._verify_saved)
        self.assertEqual(list(sig.parameters),
                         ["cdp", "title", "expect_head", "old_mtime"])


class TestNoLeakedTabs(unittest.TestCase):
    """replace 模式不许开多余的「新建入口」tab。

    2026-09-30 实测踩到：publish_one 开头无条件 _open_editor_tab()，
    而 replace 模式直接按 article_id 打开已有草稿，那个 tab 完全用不上。
    批量 37 条时废 tab 累积，第 2 条就 TimeoutError: timed out
    （栈在 _open_editor_tab 的 Page.navigate），整轮中断。
    """

    def test_replace_skips_open_editor_tab(self):
        import inspect
        code = [ln for ln in inspect.getsource(bp.publish_one).splitlines()
                if not ln.strip().startswith("#")]
        body = "\n".join(code)
        self.assertIn("(None, None) if replace else _open_editor_tab(cdp)", body)

    def test_publish_one_cleans_tabs_in_finally(self):
        import inspect
        src = inspect.getsource(bp.publish_one)
        self.assertIn("keep_tabs", src)
        self.assertIn("_close_stale_bili_tabs(cdp)", src.split("finally")[-1])

    def test_publish_all_has_retries(self):
        import inspect
        sig = inspect.signature(bp.publish_all)
        self.assertIn("retries", sig.parameters)
        src = inspect.getsource(bp.publish_all)
        self.assertIn("重试", src)
        # 单条异常不能中断整轮
        self.assertIn("except Exception", src)

    def test_cli_exposes_retries_and_keep_tabs(self):
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, bp.__file__, "publish", "--help"],
            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("--retries", out)
        self.assertIn("--keep-tabs", out)


class TestDraftDedup(unittest.TestCase):
    """草稿箱去重（2026-09-30 加）。

    重复是怎么来的：`publish --case X` 重试、或 publish_all 中途被杀，
    都会在草稿箱里**再建一份** —— B站发布是「新建草稿」不是「更新」。

    两个坑都踩过，钉在这里：
      1. 接口路径是 draft/**delete**，不是 draft/remove（猜 remove 会返回
         空 {}，连 {code,msg} 都没有，一度以为没权限）。
      2. 写操作必须带 `bili_jct`，否则 -111「CSRF 校验失败」。它在 cookie
         里但不会随 credentials 自动进 body，得显式拼进 csrf 字段。
    """

    def test_删除接口路径是delete不是remove(self):
        self.assertTrue(bp.BILI_DRAFT_REMOVE_API.endswith("/draft/delete"))
        self.assertNotIn("draft/remove", bp.BILI_DRAFT_REMOVE_API)

    def test_删除请求带csrf(self):
        import inspect
        src = inspect.getsource(bp._draft_remove)
        self.assertIn("bili_jct", src, "必须从 cookie 取 bili_jct")
        self.assertIn("csrf", src)

    def test_保留有封面的那条(self):
        """按 aid 最小保留会留下空白草稿、删掉带封面的那条（实测踩过）。

        同标题重复时，优先留 `_cover_of` 非空的。
        """
        import inspect
        src = inspect.getsource(bp.dedup)
        self.assertIn("_cover_of", src)

    def test_默认真删_用户已授权(self):
        """2026-09-30 用户授权：删重复不用再问，dedup 默认就删。

        `--delete` 留作兼容旧调用，真正控制行为的是 `--dry`。
        """
        import inspect
        sig = inspect.signature(bp.dedup)
        self.assertFalse(sig.parameters["dry"].default,
                         "dedup 默认必须真删（用户已授权），要预览用 --dry")
        out = subprocess.run(
            [sys.executable, bp.__file__, "dedup", "--help"],
            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("--dry", out, "必须有 --dry 才能只列清单不删")
        self.assertIn("--delete", out, "--delete 保留为兼容旧调用的冗余开关")


class TestCoverForce(unittest.TestCase):
    """`cover --force` 必须真重设，不能只影响「跳不跳过」（2026-09-30 修）。

    旧实现里 force 只传给 cover_all 决定跳不跳过，cover_one 内部永远
    「封面已存在 → 跳过上传」，所以 `cover --case X --force` 打印
    「封面已存在，跳过上传」什么都没换 —— 用户以为改了配色却没换图。
    """

    def test_cover_one接受force(self):
        import inspect
        sig = inspect.signature(bp.cover_one)
        self.assertIn("force", sig.parameters)
        self.assertFalse(sig.parameters["force"].default,
                         "force 必须默认 False，否则普通 cover 会重传全部")

    def test_批量透传force到cover_one(self):
        import inspect
        src = inspect.getsource(bp.cover_all)
        self.assertIn("force=force", src,
                      "cover_all 必须把 force 传给 cover_one")

    def test_单条CLI透传force(self):
        import inspect
        src = inspect.getsource(bp.main)
        self.assertIn("cover_one(args.case, dry=args.dry, force=args.force)",
                      src)
        self.assertIn("cover_all(only_missing=not args.force, force=args.force)",
                      src)

    def test_force时会先删旧封面(self):
        """不删旧封面，裁剪框预填的是旧图，新图容易叠上去。"""
        import inspect
        src = inspect.getsource(bp.cover_one)
        self.assertIn("_drop_existing_cover", src)
        self.assertTrue(hasattr(bp, "_drop_existing_cover"))
        dsrc = inspect.getsource(bp._drop_existing_cover)
        self.assertIn("删除", dsrc, "要点掉 .selected-action 里的「删除」")

    def test_删旧封面删不掉不阻断(self):
        """删不掉不算硬失败 —— _upload_cover_file 有「重新上传」兜底。

        判据：调用 `_drop_existing_cover` 的返回值不能被当条件用
        （不能写 `if not _drop_existing_cover(...): return False`）。
        """
        import inspect
        src = inspect.getsource(bp.cover_one)
        call = [ln for ln in src.splitlines()
                if "_drop_existing_cover(" in ln and "def " not in ln]
        self.assertTrue(call, "应有无条件调用")
        self.assertFalse(call[0].strip().startswith("if "),
                         "删旧封面的结果不能当阻断条件：%s" % call[0].strip())
        self.assertTrue(call[0].strip().endswith(")"),
                        "应是无条件调用（返回值丢弃）")


class TestPublishVerdict(unittest.TestCase):
    """`publish_verdict`：发布按钮能不能点，以及**为什么**不能点。

    判据的顺序本身是重点 —— 2026-10-20 之前就是因为先看 disabled 后看提示，
    把「当日投稿额度用完」误诊成了「headless 封面校验」，白绕了一整天。
    """

    READY = {"found": True, "disabled": False, "cls": "vui_button vui_button--blue",
             "x": 1064, "y": 801, "w": 88, "h": 36}

    def _st(self, **kw):
        s = dict(self.READY)
        s.update(kw)
        return s

    def test_可点时返回True(self):
        ok, why = bp.publish_verdict(self.READY)
        self.assertTrue(ok, why)
        self.assertIn("可点", why)

    def test_额度用完优先于disabled(self):
        """灰按钮 + 额度提示 ⇒ 必须报额度，不许报「封面/正文没过校验」。"""
        ok, why = bp.publish_verdict(self._st(
            disabled=True, cls="vui_button vui_button--blue vui_button--disabled",
            quota=True, hint="已达到当日投稿上限"))
        self.assertFalse(ok)
        self.assertIn("投稿上限", why)
        self.assertNotIn("封面", why, "别再把额度问题栽赃给封面：%s" % why)

    def test_灰按钮但不是额度_报未知原因(self):
        ok, why = bp.publish_verdict(self._st(disabled=True))
        self.assertFalse(ok)
        self.assertIn("灰", why)
        # 「B站限 N 篇/天」是额度分支独有的话术；没走那条分支就不许出现
        self.assertNotIn("B站限", why, "别误导成额度问题：%s" % why)

    def test_找不到按钮(self):
        ok, why = bp.publish_verdict({"found": False, "quota": False})
        self.assertFalse(ok)
        self.assertIn("没找到", why)

    def test_空状态不是可点(self):
        """读不到状态 ≠ 可以点。静默放行是假绿的另一种写法。"""
        for st in ({}, None):
            ok, why = bp.publish_verdict(st)
            self.assertFalse(ok, repr(st))
            self.assertTrue(why)

    def test_额度用完按钮都没渲染(self):
        ok, why = bp.publish_verdict({"found": False, "quota": True,
                                      "hint": "已达到当日投稿上限"})
        self.assertFalse(ok)
        self.assertIn("投稿上限", why)

    def test_额度报错用页面原话_不自己编数字(self):
        """额度几篇/天跟账号等级有关（LV1–3 是 1 篇、LV4–6 5 篇），本账号
        今天发 1 篇就到顶了 → 脚本不猜数字，直接把编辑器顶上的原话搬过来。
        """
        ok, why = bp.publish_verdict(self._st(
            disabled=True, quota=True, hint="已达到当日投稿上限"))
        self.assertFalse(ok)
        self.assertIn("已达到当日投稿上限", why)
        self.assertNotIn("5 篇", why, "别替平台编额度：%s" % why)


class TestPickTargetDraft(unittest.TestCase):
    """`send-draft --case X` 必须发 X 自己的草稿。

    早期版本：--case 只用来反查 cid，**实际发的是最久那条**，还把发布结果
    记到 X 头上 —— 指定 A、发出 B、账写 A。这条用例就是钉死这个 bug 的。
    """

    POOL = [
        {"article_id": 111, "title": "Aaa：最早的", "mtime": 100},
        {"article_id": 222, "title": "Bbb：中间的", "mtime": 200},
        {"article_id": 333, "title": "Ccc：最晚的", "mtime": 300},
    ]

    def test_不给cid就挑最久的(self):
        d, why = bp._pick_target_draft(self.POOL)
        self.assertEqual(d["article_id"], 111, why)
        self.assertIn("最久", why)

    def test_给了cid就挑它的_不是最久的(self):
        def _html(cid):
            return "<h1>x</h1>"

        def _title(cid, html):
            return "Ccc：最晚的"

        old, oldt = bp.load_article_html, bp.make_title
        try:
            bp.load_article_html, bp.make_title = _html, _title
            d, why = bp._pick_target_draft(self.POOL, "ccc")
        finally:
            bp.load_article_html, bp.make_title = old, oldt
        self.assertEqual(d["article_id"], 333, why)
        self.assertNotEqual(d["article_id"], 111, "不许发最久那条凑数")

    def test_标题被改过就退到含case名(self):
        d, why = bp._match_case_draft(
            self.POOL, "bbb", "Bbb：线上改过标题（不等于预期）")
        self.assertEqual(d["article_id"], 222, why)
        self.assertIn("case 名", why)

    def test_匹配不上要报不知道(self):
        d, why = bp._match_case_draft(self.POOL, "zzz", "无关标题")
        self.assertIsNone(d)
        self.assertIn("zzz", why)

    def test_send_draft用_pick而不是直接取第一条(self):
        import inspect
        src = inspect.getsource(bp.send_draft)
        self.assertIn("_pick_target_draft", src)
        self.assertNotIn("oldest = pool[0]", src)


class TestPublishStateJs(unittest.TestCase):
    """挖状态/点击的三段 JS 必须真的查 iframe、真的判 disabled。

    不做 CSS 选择器层面的「橡皮图章」断言，只验它们含关键语义；形变=no-op
    的坑已经在 `TestCoverHelpers` 里交过学费了。
    """

    def test_状态JS先找按钮再看文案(self):
        self.assertIn("read-editor", bp.JS_PUBLISH_STATE)
        self.assertIn("contentDocument", bp.JS_PUBLISH_STATE)
        self.assertIn("已达到当日投稿上限", bp.JS_PUBLISH_STATE)
        self.assertIn("disabled", bp.JS_PUBLISH_STATE)

    def test_点击JS灰按钮不许点(self):
        """disabled 时返回 'disabled' 而不是照样 click 一下假装点过。"""
        self.assertIn("if(b.disabled) return 'disabled';", bp.JS_CLICK_PUBLISH)

    def test_确认弹窗只点发布相关的(self):
        """别对着任意弹窗点「确定」—— 旁边就是不可逆的删除类按钮。"""
        self.assertIn("发布|投稿|专栏", bp.JS_CONFIRM_DIALOG)
        self.assertIn("not-publish-dialog", bp.JS_CONFIRM_DIALOG)

    def test_成功提示认toast文案(self):
        self.assertIn("提交成功", bp.JS_SUCCESS_HINT)

    def test_send_draft走UI发布(self):
        """send-draft 必须是真发布：不许再拿 submit=1 糊弄。

        ⚠ 只看**可执行代码**，不看 docstring —— 文档里引用历史结论
        （「submit=1 不发布」）是应当的，拿它当断言素材会把「写清楚了」
        判成「还在干」（`Ut f(col)` 那条老规矩：量执行载体，不量字面叙述）。
        """
        import ast
        import inspect
        src = inspect.getsource(bp.send_draft)
        tree = ast.parse(src.lstrip())
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]            # 剥掉 docstring
        code = ast.unparse(fn)
        self.assertIn("ui_publish_draft", code)
        self.assertNotIn("submit", code,
                         "submit=1 只存草稿不发布，不能当发布出口：\n%s" % code)
        self.assertNotIn("save_via_api", code)


class TestPublishAllIdempotent(unittest.TestCase):
    """直跑 `publish --all` 必须过本地幂等（2026-10-21 修）。

    修的洞：适配器 has_state_file=False，清单是唯一幂等源，而脚本
    入口不查它 —— 第二次 `publish --all` = 全量重复草稿（适配器注释
    里「远端 38→101 条副本」事故的配方）。runner 路径一向安全
    （batch 恒传 --only），危险的是人手边这条直跑命令。
    """

    def _run(self, ids, done, replace=False, only=None):
        """用假 list_ids/done 跑 publish_all，任何 CDP 触碰都会炸。"""
        old_ids, old_done = bp.list_ids, bp._local_done_ids
        bp.list_ids = lambda: list(ids)
        bp._local_done_ids = lambda: set(done)
        try:
            return bp.publish_all(replace=replace, only=only,
                                  use_api=False)
        finally:
            bp.list_ids, bp._local_done_ids = old_ids, old_done

    def test_全量已存时直接返回不建重复(self):
        ok, failed = self._run(["a", "b"], done=["a", "b"])
        self.assertEqual((ok, failed), (0, []),
                         "全部已存还往下跑就是重复草稿")

    def test_只发没存过的(self):
        # done 里只有 a ⇒ publish_all 应继续处理 b/c。这里不断言它真发
        # （要连 CDP），只断言过滤后的待办集：用一个会在 use_api=False
        # 前打印的探针拿不到，改为直接验证 _local_done_ids 被用于过滤。
        ids, done = ["a", "b", "c"], ["a"]
        old_ids, old_done = bp.list_ids, bp._local_done_ids
        bp.list_ids = lambda: list(ids)
        bp._local_done_ids = lambda: set(done)
        seen = {}
        old_publish_one = bp.publish_one

        def fake_one(cid, **kw):
            seen[cid] = True
            return True
        try:
            bp.publish_one = fake_one
            ok, failed = bp.publish_all(replace=False, only=None,
                                        use_api=False)
        finally:
            bp.publish_one = old_publish_one
            bp.list_ids, bp._local_done_ids = old_ids, old_done
        self.assertEqual(sorted(seen), ["b", "c"],
                         "已存的 a 必须被跳过，没存过的照发")
        self.assertEqual(failed, [])

    def test_only显式指定不过滤(self):
        # 断点续传语义：--only 点名就要跑，哪怕本地记过
        seen = []
        old_ids, old_done = bp.list_ids, bp._local_done_ids
        bp.list_ids = lambda: ["a", "b"]
        bp._local_done_ids = lambda: {"a"}
        old_one = bp.publish_one

        def fake_one(cid, **kw):
            seen.append(cid)
            return True
        try:
            bp.publish_one = fake_one
            bp.publish_all(replace=False, only=["a"], use_api=False)
        finally:
            bp.publish_one = old_one
            bp.list_ids, bp._local_done_ids = old_ids, old_done
        self.assertEqual(seen, ["a"], "--only 是逃生门，不该被幂等挡")

    def test_replace不过滤(self):
        # 更新已有草稿的语义：--replace 全量跑是合理的
        seen = []
        old_ids, old_done = bp.list_ids, bp._local_done_ids
        bp.list_ids = lambda: ["a"]
        bp._local_done_ids = lambda: {"a"}
        old_one = bp.publish_one

        def fake_one(cid, **kw):
            seen.append(cid)
            return True
        try:
            bp.publish_one = fake_one
            bp.publish_all(replace=True, only=None, use_api=False)
        finally:
            bp.publish_one = old_one
            bp.list_ids, bp._local_done_ids = old_ids, old_done
        self.assertEqual(seen, ["a"], "--replace 要覆盖更新，不该被跳过")

    def test_local_done_ids读真实两个账(self):
        """_local_done_ids 对真实数据应返回非空（防读取路径写坏）。"""
        done = bp._local_done_ids()
        self.assertIn("bustem", done)


if __name__ == "__main__":
    unittest.main()
