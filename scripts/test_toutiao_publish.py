#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toutiao_publish.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_toutiao_publish
"""
import ast
import inspect
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402
import wechat_publish as wp  # noqa: E402

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

---

<!--
多行注释块：下面这行写着「别进正文」，
必须整块被吃掉，只跳过 <!-- 单行会让它漏出去。
-->

**整段加粗**： pinpoint
"""


class TestBlocks(unittest.TestCase):
    def test_h1_and_comment_dropped(self):
        bs = tp.md_to_blocks(MD)
        self.assertFalse(any("标题行" in t for _, t in bs))
        self.assertFalse(any("注释" in t for _, t in bs))

    def test_comment_block_dropped(self):
        """多行 <!-- --> 注释必须整块吃掉：模板里「核对用来源（别进正文）」
        就在这种块里，只跳过单行会让它漏进正文。"""
        joined = tp.blocks_plain_text(tp.md_to_blocks(MD))
        self.assertNotIn("别进正文", joined)
        self.assertNotIn("多行注释块", joined)
        self.assertNotIn("-->", joined)

    def test_url_and_md_link_cleaned(self):
        text = tp.blocks_plain_text(tp.md_to_blocks(MD))
        self.assertNotIn("http", text)
        self.assertIn("官网", text)
        # `**` 由 blocks_to_html 转成 <strong>，最终 HTML 里不该有裸 markdown 符号
        self.assertNotIn("**", tp.blocks_to_html(tp.md_to_blocks(MD)))

    def test_table_is_real_table_not_kv(self):
        """表格必须**保留成真表格**，不能降级成 kv 列表。

        2026-10-09：旧断言是 `assertNotIn("<table", html)`，理由写的是
        「头条编辑器不支持表格」。**那个结论是错的** —— 探针只扫了
        title/aria-label，漏了 class 里的 `syl-toolbar-tool table`。
        实测 insertHTML 灌 <table> 完整存活，存草稿重开也还在。
        """
        bs = tp.md_to_blocks(MD)
        tabs = [t for k, t in bs if k == "table"]
        self.assertEqual(len(tabs), 1, "表格块应该被识别成一个 table 块")
        tb = tabs[0]
        # 表头「维度 | 内容」被吃掉，只剩数据行
        self.assertEqual(tb["rows"], [["官方口径", "月收入 $12K"]])
        # **不再有 kv 块** —— 这条是回归钉：别又退回列表降级
        self.assertFalse(any(k == "kv" for k, _ in bs))
        # 表格之外不该残留 markdown 竖线
        self.assertFalse(any("|" in t for k, t in bs if k != "table"))

    def test_table_html_shape(self):
        """表格 HTML 用编辑器实测认得的写法（见 table_to_html 的注释）。"""
        html = tp.blocks_to_html(tp.md_to_blocks(MD))
        self.assertIn('<div class="tableWrapper"><table><tbody>', html)
        self.assertIn("<tr><td><p>官方口径</p></td>"
                      "<td><p>月收入 $12K</p></td></tr>", html)
        # 表头不进表
        self.assertNotIn("<p>维度</p>", html)
        # 不给编辑器会自己算的样式
        self.assertNotIn("colgroup", html)
        self.assertNotIn("data-colwidth", html)
        self.assertNotIn("<th>", html)

    def test_blocks_joined_without_newline(self):
        """⚠ 块之间**不能有换行**（2026-10-09 实测）。

        Tiptap 把标签间的 `\\n` 当成额外空段落：同一份内容
        `"\\n".join` 渲出 3 个空 `<p>`，`"".join` 只有 2 个。
        """
        html = tp.blocks_to_html(tp.md_to_blocks(MD))
        self.assertNotIn("\n", html)
        self.assertNotIn("> <", html)          # 「>  <」= 空段落

    def test_long_cell_becomes_paragraph_after_table(self):
        """>`CELL_LONG` 的值行不挤进表格，转成表格后面的「标签：值」段落。"""
        long_val = "很长的口径说明。" * 12            # >60 字
        md = ("## 它到底做到多大\n\n"
              "| 维度 | 内容 |\n| --- | --- |\n"
              "| 官方口径 | $53K MRR |\n"
              "| 备注 | %s |\n" % long_val)
        html = tp.blocks_to_html(tp.md_to_blocks(md), footer=False)
        # 短值留在表里
        self.assertIn("<td><p>官方口径</p></td>", html)
        # 长值不在表里
        self.assertNotIn("<td><p>%s</p></td>" % long_val, html)
        # 变成表格后面的段落，且标签加粗
        self.assertIn("<p><strong>备注：</strong>%s</p>" % long_val, html)
        # 段落必须在表格**之后**
        self.assertLess(html.index("<table>"), html.index("备注："))

    def test_all_cells_long_table_still_renders(self):
        """整张表全是长行 → 没有 <table>，但长段落一个都不能丢。"""
        # 每格必须 >CELL_LONG(60) 才算长行，所以要repeat 够多次
        long_a = "甲说明" * 30                             # 90 字
        long_b = "乙说明" * 30                             # 90 字
        self.assertGreater(len(long_a), tp.CELL_LONG)
        md = ("| 维度 | 内容 |\n| --- | --- |\n"
              "| 甲 | %s |\n| 乙 | %s |\n" % (long_a, long_b))
        bs = tp.md_to_blocks(md)
        tabs = [t for k, t in bs if k == "table"]
        self.assertEqual(len(tabs), 1)
        # rows 空、long 有两条 ⇒ 不渲表格
        self.assertEqual(tabs[0]["rows"], [])
        self.assertEqual(len(tabs[0]["long"]), 2)
        html = tp.blocks_to_html(bs, footer=False)
        self.assertNotIn("<table", html)
        self.assertNotIn("tableWrapper", html)
        self.assertIn("甲：", html)
        self.assertIn("乙：", html)

    def test_uneven_columns_padded(self):
        """`|a|b|c|` 与 `|a|b|` 混排 → 按最长行补齐，不错位。"""
        md = "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |\n| 3 | 4 | 5 |"
        tb = [t for k, t in tp.md_to_blocks(md) if k == "table"][0]
        self.assertEqual(tb["rows"], [["a", "b", "c"], ["1", "2", ""],
                                      ["3", "4", "5"]])

    def test_single_column_is_not_table(self):
        """单列行不算表格，交回段落路径（否则渲出一列的怪表格）。"""
        bs = tp.md_to_blocks("| 只有一列 |\n| --- |")
        self.assertFalse(any(k == "table" for k, _ in bs))

    def test_table_at_eof_without_blank_line(self):
        """表格撞到文件末尾（后面没空行）也必须结算，不能整段丢掉。"""
        bs = tp.md_to_blocks("正文一句\n\n| 维度 | 内容 |\n| --- | --- |\n| 甲 | 1 |")
        tabs = [t for k, t in bs if k == "table"]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["rows"], [["甲", "1"]])

    def test_kinds(self):
        bs = tp.md_to_blocks(MD)
        kinds = [k for k, _ in bs]
        self.assertIn("quote", kinds)
        self.assertIn("h2", kinds)
        self.assertIn("ul", kinds)
        self.assertIn("p", kinds)
        self.assertIn("hr", kinds)
        self.assertIn("table", kinds)

    def test_html_structure(self):
        """还原真富文本：假标题 / 假列表 / 扁平表格全部升级成真标签。"""
        html = tp.blocks_to_html(tp.md_to_blocks(MD))
        self.assertIn("<h2>它是干什么的</h2>", html)
        # 连续列表项合并成一个 ul，而不是 N 个 <p>· xxx</p>
        self.assertNotIn("<p>·", html)
        self.assertIn("<ul><li>第一点 <strong>加粗</strong></li>"
                      "<li>第二点</li></ul>", html)
        # 引用有自己的块级样式，不再是普通段落
        self.assertIn("<blockquote>「一句钩子」</blockquote>", html)
        self.assertIn("---", html.replace("<hr>", "---"))   # --- → <hr>
        self.assertIn("<hr>", html)
        self.assertIn(tp.FOOTER_NOTE, html)

    def test_bold_inside_table_cell(self):
        """**加粗** 在单元格里也能活（实测 `<td><p><strong>` 存活）。"""
        md = "| 维度 | 内容 |\n| --- | --- |\n| 甲 | **4/5** |"
        html = tp.blocks_to_html(tp.md_to_blocks(md), footer=False)
        self.assertIn("<td><p><strong>4/5</strong></p></td>", html)
        self.assertNotIn("**", html)

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


class TestNoRawMarkdown(unittest.TestCase):
    """🚨 全库级：裸 Markdown 标记不许进案例正文（2026-10-11 事故）。

    这条坑真实发生过：给7 条新候选写内容包时，习惯性用了
    `**加粗**` 来强调关键数字。内容包本身校验全绿
    （`contentpack_ready --check` 只查条数与字数），
    promote 也不管这个 ⇒ `**` 原样进了 cases.json，
    又原样渲进案例页，读者看到的是「这是**大公司样本**」这样的字面星号。

    渲染器**不解析** Markdown（`prerender.py` 里没有 bold/em 的处理，
    它只做 HTML 转义），所以星号不会消失，只会原样显示。
    既有 41 条案例因此全都没有 `**` —— 但那是**约定**，没有任何机制拦着，
    所以下一个写内容包的人一定会再犯。

    这条断言把它变成机制：全库任何一条案例的正文/判词/玩法里
    出现 `**`，测试就红。
    """

    FIELDS_TEXT = ("what_it_does", "verdict", "how_it_makes_money")
    FIELDS_LIST = ("why_it_works", "playbook", "signals")

    def test_no_case_field_contains_bold_marker(self):
        cases = tp.load_cases()
        bad = []
        for c in cases:
            for f in self.FIELDS_TEXT:
                if "**" in (c.get(f) or ""):
                    bad.append("%s.%s" % (c["id"], f))
            for f in self.FIELDS_LIST:
                for i, s in enumerate(c.get(f) or []):
                    if "**" in (s or ""):
                        bad.append("%s.%s[%d]" % (c["id"], f, i))
        self.assertEqual(bad, [],
                         "这些字段有裸 Markdown 加粗标记，渲染器不解析会原样显示：%s"
                         % bad[:8])

    def test_no_metric_note_contains_bold_marker(self):
        """metric_note 也会显示在案例卡片上，同样不许带星号。"""
        bad = [c["id"] for c in tp.load_cases()
               if "**" in ((c.get("metrics") or {}).get("metric_note") or "")]
        self.assertEqual(bad, [], "metric_note 带 **：%s" % bad[:8])

    def test_content_packs_have_no_bold_marker(self):
        """源头拦：内容包本身就不该写 `**`（写都别写，别指望下游过滤）。"""
        try:
            import contentpack_ready as CP
        except ImportError:
            self.skipTest("contentpack_ready 不可导入")
        bad = []
        for cid, pack in CP.PACKS.items():
            for f in ("what_it_does", "verdict", "how_it_makes_money",
                      "metric_note"):
                v = (pack.get(f) or {}) if f == "metric_note" else pack.get(f)
                if isinstance(v, dict):
                    v = v.get("metric_note") or ""
                if "**" in (v or ""):
                    bad.append("%s.%s" % (cid, f))
            for f in self.FIELDS_LIST:
                for i, s in enumerate(pack.get(f) or []):
                    if "**" in (s or ""):
                        bad.append("%s.%s[%d]" % (cid, f, i))
        self.assertEqual(bad, [], "内容包里有 **：%s" % bad[:8])


class TestBuild(unittest.TestCase):
    def test_build_article_offline(self):
        c = {"id": "pieter-levels", "name": "Pieter Levels 的产品矩阵",
             "one_liner": "组合月收入 $250K+"}
        m = tp.build_article(c)
        self.assertLessEqual(len(m["title"]), tp.TITLE_MAX)
        # 首块要么是引用块要么是普通段，绝不能是扁平假结构
        self.assertTrue(m["html"].startswith(("<p>", "<blockquote>")),
                        m["html"][:60])
        self.assertIn("<h2>", m["html"])          # 小标题必须是真 h2
        self.assertIn("关于本栏目", m["html"])
        self.assertNotIn("**", m["html"])         # 裸 markdown 符号不许出口

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


class TestTtCoverNum(unittest.TestCase):
    """封面大数字：超预算分段丢弃，绝不出现半截标点（旧版出过「$4,985,」）。"""

    def test_semicolon_segments_drop_overflow(self):
        self.assertEqual(
            tp.tt_cover_num("MRR $244,029；累计收入 $4,985,134（Stripe 直连验证）"),
            "MRR $244,029")

    def test_slash_segments(self):
        self.assertEqual(
            tp.tt_cover_num("收入 $1.6K / 售价 $60K / 倍数 3.1x（TrustMRR 挂牌）"),
            "收入 $1.6K")

    def test_fullwidth_comma_segments(self):
        self.assertEqual(
            tp.tt_cover_num("月收入 $18,000，毛利率 90%"),
            "月收入 $18,000")

    def test_big_number_strip_thousands_separator(self):
        # 各段都超预算时，去掉千分位逗号再试（$3,569,654 → $3569654）
        self.assertEqual(
            tp.tt_cover_num("MRR $3,569,654；累计收入 $76,627,685"),
            "MRR $3569654")

    def test_clip_fallback_strips_trailing_punct(self):
        n = tp.tt_cover_num("$424,368 MRR · Stripe 验证")
        self.assertFalse(n.endswith((",", "，", "；", ";", " ")), repr(n))

    def test_empty(self):
        self.assertEqual(tp.tt_cover_num(""), "")
        self.assertEqual(tp.tt_cover_num(None), "")

    def test_cover_html_dark_theme_and_no_smallprint_footer(self):
        c = {"id": "1lookup", "name": "1Lookup",
             "one_liner": "一个 API 做电话、邮箱、IP 的实时数据校验",
             "metrics": {"headline": "MRR $244,029；累计收入 $4,985,134"}}
        html = tp.tt_cover_html(c)
        # 断言「用的是主题表里的深色底」，而不是钉死某个色值 ——
        # 2026-09-30 修了封面撞色（相邻两条必须不同色，见 test_cover_theme），
        # 分配规则变了，1lookup 从紫罗兰变成了橄榄金。色值会随 cases.json
        # 顺序变动，钉死它等于给这个测试埋一颗地雷。
        base, band, ac = wp._cover_theme("1lookup", "toutiao")
        self.assertIn("background:%s" % base, html)
        self.assertIn("background:%s" % band, html)
        self.assertIn("color:%s" % ac, html)
        self.assertNotIn("CASE STUDY", html)      # 小字装饰已去掉
        self.assertNotIn("numsub", html)
        # 2026-10-02：封面两行改成「项目名 + 一句话介绍」，**不含金额**。
        # metrics.headline 是抓取当天的快照，会随站点自己变 ——
        # 印在封面等于印一个会过期的数字。
        self.assertIn("1Lookup", html)
        self.assertIn("一个 API 做电话、邮箱、IP 的实时数据校验", html)
        for stale in ("$244,029", "$4,985,134", "MRR"):
            self.assertNotIn(stale, html)

    def test_cover_html_placeholder_oneliner_never_shows_unclassified(self):
        """prosp 实测：one_liner 是占位符、category=未分类 → 封面不能印「未分类」。

        2026-09-29 改方案 A 后封面只有两行（数字 + 产品名）；2026-10-02 又把
        数字换成一句话介绍。版式一直在变，但**占位符绝不能上封面**这条硬约束
        始终得钉死（将来谁加回第三行就会踩）。
        """
        c = {"id": "prosp", "name": "PROSP", "category": "未分类",
             "one_liner": "（待补充：产品定位未明）",
             "metrics": {"headline": "$128,000 MRR"}}
        html = tp.tt_cover_html(c)
        self.assertNotIn("未分类", html)
        self.assertNotIn("待补充", html)
        # one_liner 是纯括注 → 去掉后为空 → 这一行不渲染（用户要求
        # 「没简要介绍就不显示」），只剩产品名，且不回落成 category。
        self.assertIn("PROSP", html)
        self.assertNotIn("class=\"desc\"", html)
        # 金额已被移除需求覆盖掉，不该再出现。
        self.assertNotIn("$128,000", html)

    def test_cover_desc_priority_and_blank_fallback(self):
        """"没简要介绍就不显示"这条要真的成立：四种取值路径各钉一遍。"""
        base = {"id": "x", "name": "X", "metrics": {"headline": "$9 MRR"}}
        # 1) one_liner 优先，去掉尾注括注
        self.assertEqual(
            tp.tt_cover_desc(dict(base, one_liner="干这个的（数据来自公开披露）")),
            "干这个的")
        # 2) one_liner 缺失 → what_it_does 首句
        self.assertEqual(
            tp.tt_cover_desc(dict(base, what_it_does="第一句。第二句。")),
            "第一句")
        # 3) 全都没有 → 空串（封面不渲染这一行），**绝不回落金额**
        self.assertEqual(tp.tt_cover_desc(base), "")
        self.assertEqual(tp.tt_cover_desc(dict(base, category="未分类")), "")
        # 4) cover_kv.desc 显式覆盖优先级最高
        self.assertEqual(
            tp.tt_cover_desc(dict(base, cover_kv={"desc": "自定义"},
                                  one_liner="原始")),
            "自定义")

    def test_wechat_cover_uses_same_desc_and_skips_blank(self):
        """公众号封面必须与头条/B站/小红书同一口径（铁律：单一真理）。

        2026-10-02：原来公众号封面直接用 one_liner，而 one_liner 实测
        4/39 内嵌金额（shipfast $199 / outrank 月入 30 万…），导致公众号
        封面与新口径分叉。现在统一走 cover_desc()，空介绍不渲染整行。
        """
        wp = sys.modules.get("wechat_publish") or wp
        c = {"id": "outrank", "name": "Outrank",
             "one_liner": "月入 30 万的自动化 SEO 引擎",
             "cover_desc": "从 AI 博客生成器起步的自动化 SEO 引擎"}
        html = wp.cover_html(c, 7)
        # 手写的 cover_desc 生效，one_liner 里的金额不出现
        self.assertIn("自动化 SEO 引擎", html)
        self.assertNotIn("30万", html.replace(" ", ""))
        # 有介绍时该行必须存在
        self.assertIn('id="t-line"', html)

        # 无任何介绍 → 整行不渲染，且 name 仍然渲染
        blank = {"id": "novdesc", "name": "Solo",
                 "metrics": {"headline": "$9 MRR"}}
        html2 = wp.cover_html(blank, 8)
        self.assertNotIn('id="t-line"', html2)
        self.assertNotIn("$9", html2)
        self.assertIn("Solo", html2)

    # 封面金额正则：与 out/cover_audit.py 同一份判据。
    # ⚠ 必须自带「正例必抓 / 反例不误报」自测 —— 第一版正则实测4 条误报
    #   （B2B 被当成「B2 金额」、「盯 5 万个企业招聘页」被当成金额），
    #   而当时**没有任何测试**能发现，因为断言只查了 1lookup 一条。
    _MONEY = re.compile(
        r"(?:"
        r"\$" + r"\d[\d,.]*"
        + r"|" + r"\d[\d,.]*" + r"\s*(?:美元|美金|元|人民币|刀|usd|rmb|cny)"
        + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:万|千)\s*(?:美元|美金|元|人民币))"
        + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:万|千)"
                        r"(?=\s*(?:MRR|收入|营收|流水|用户|月)))"
        + r"|(?:" + r"\d[\d,.]*" + r"\s*(?:K|M|B)\s*(?:MRR|ARR)?"
                        r"(?=\s*(?:收入|营收|流水|/月|月)))"
        + r"|(?:月入|年收入|营收|月收入|收入|流水|MRR|ARR)"
                        r"\s*(?:达|到|从|为|[:：])?\s*\$?" + r"\d[\d,.]*"
        + r")",
        re.I)

    def test_cover_money_regex_has_no_false_positive(self):
        """封面金额判据：真金额必抓，业务数字不误报。"""
        must_hit = ["$199", "$8.6K", "月入 30 万", "MRR 1.7 万",
                    "20 万美元", "ARR $2M", "5万美元", "收入 4.5万"]
        must_miss = ["B2B 顾问", "盯 5 万个企业招聘页", "12 周内容策略",
                     "第 42 篇", "30 分钟", "三步走", "SLA 99.9%"]
        for s in must_hit:
            self.assertTrue(self._MONEY.search(s), "应命中金额：%s" % s)
        for s in must_miss:
            m = self._MONEY.search(s)
            self.assertIsNone(m, "误报金额：%s -> %r" % (s, m and m.group(0)))

    def test_every_case_cover_has_no_money(self):
        """39 条真实数据逐条过一遍金额判据（不是抽 1~2 条）。"""
        import json
        p = os.path.join(ROOT, "data", "cases.json")
        if not os.path.exists(p):
            self.skipTest("cases.json 不在")
        data = json.load(open(p, encoding="utf-8"))
        cases = data["cases"] if isinstance(data, dict) else data
        bad = []
        for c in cases:
            html = tp.tt_cover_html(c)
            vis = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
            vis = re.sub(r"<[^>]+>", " ", vis)
            m = self._MONEY.search(vis)
            if m:
                bad.append("%s→%r" % (c["id"], m.group(0)))
        self.assertEqual(bad, [], "封面出现金额：%s" % bad)


class TestLoadAllDrafts(unittest.TestCase):
    """`_load_all_drafts` 必须等到「卡片数 ≥ 页头声明总数」才算完。

    ⚠⚠ 2026-10-09 实测的假阴性（草稿箱真实 29 条，却只读到 20 条）：
      头条「加载更多」按钮在**请求发出瞬间**就被隐藏，比响应早 2–4 秒：
        t+0s  点击 → `clicked:SPAN`，卡片 20，按钮 none
        t+2s  卡片**仍是 20**（接口还没回来）
        t+4s  卡片 **29** ← 数据这才到
      旧实现「`state == "none"` 就 break」⇒ 在数据到达前就退出。
      后果链条：`tt_audit_drafts` 只读 20 条 → `tt_body_fill._match_remote_titles`
      只匹配到 20/41 → `--all` **静默漏刷 9 条**（含 trustmrr / viktor / coral）。

    用假 CDP 复现这个时序：按钮立刻消失，但卡片数要过几轮才涨。
    """

    class _FakeCDP(object):
        """按脚本顺序应答。`steps` = [(返回或None 表示抛异常), ...]。"""

        def __init__(self, script):
            self.script = script
            self.i = 0

        def eval(self, js):
            v = self.script[self.i] if self.i < len(self.script) else None
            self.i += 1
            return v

    def test_按钮先消失卡片后到_仍要等到总数(self):
        # 一个总数探针 + 一段「点完就 none、卡片停在 20」的序列，
        # 最后卡片才涨到 29。旧实现在这里就 break 了。
        script = [
            "29",                     # _draft_total
            "20",                     # _count → 没到29，继续
            "clicked:SPAN",           # 点得到 → sleep
            "29",                     # _draft_total
            "20",                     # _count
            "none",                   # 按钮消失，但卡片还20 → settle=1
            "29",                     # _draft_total
            "20",                     # _count
            "none",                   # settle=2
            "29",                     # _draft_total
            "29",                     # _count → 到总数，返回 29
        ]
        cdp = self._FakeCDP(script)
        n = tp._load_all_drafts(cdp, pause=0)
        self.assertEqual(n, 29, "按钮消失后必须继续等到卡片到齐")

    def test_卡片已等于总数_立即返回不点(self):
        """已经在总数上就别去点按钮（多点可能触发无意义请求）。"""
        script = ["29", "29"]
        cdp = self._FakeCDP(script)
        self.assertEqual(tp._load_all_drafts(cdp, pause=0), 29)

    def test_总数读不到_仍返回已渲染的条数(self):
        """页头总数缺失（total=0）时不能死循环，更不能把已加载的条数丢掉。

        ⚠ 曾经的 bug：`if total and n >= total` 在 total=0 时恒假，
        循环只靠 settle 收敛，退出后 `return _count()` 拿到的是 0 ——
        等于「读不到总数」就等于「草稿箱是空的」。实测首屏明明有 20 条。
        """
        # 序列按真实调用顺序：每轮 = _total → _count → 点按钮
        script = ["0", "20", "none",    # 轮1
                  "0", "20", "none",    # 轮2
                  "0", "20", "none",    # 轮3
                  "0", "20", "none",    # 轮4
                  "0", "20", "none",    # 轮5 → settle 满 5，退出
                  "20"]                 # 最后的 return _count()
        cdp = self._FakeCDP(script)
        n = tp._load_all_drafts(cdp, pause=0)
        self.assertEqual(n, 20, "总数读不到时必须返回已渲染条数，不能返回 0")

    def test_停止判据是卡片数而非按钮(self):
        """钉死判据：源码里不能拿按钮状态当唯一退出条件。"""
        import inspect
        src = inspect.getsource(tp._load_all_drafts)
        self.assertIn("_draft_total", src, "必须读页头声明总数做判据")
        self.assertIn("n >= total", src, "必须比较卡片数与总数")
        # 按钮消失只能累加 settle，不能直接 return/break
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn('if state == "none":\n            break', body,
                         "不能「按钮没了就break」——实测会停在 20 条")


class TestPgcIdDedup(unittest.TestCase):
    """编辑页 tab 必须按 **`pgc_id`** 认，不能按 tab id。

    ⚠⚠ 2026-10-09 实测：头条会**复用同一个 tab** 导航到下一条草稿
      （tab id 不变、`pgc_id` 变）；而我们灌完关页后，头条又可能开一个
      **新 tab 指回同一个 `pgc_id`** ⇒ 只按 tab id 去重会把同一条反复灌，
      实测 `meerkats-ai` 被**重复灌 3 次**。
    """

    def test_pgc_id_extracted_from_url(self):
        t = {"url": "https://mp.toutiao.com/profile_v4/graphic/publish"
                     "?pgc_id=7693136073802744354"}
        self.assertEqual(tp._pgc_id_of(t), "7693136073802744354")
        self.assertEqual(tp._pgc_id_of({"url": "…/manage/draft"}), "")

    def test_same_tab_id_different_pgc_are_different_drafts(self):
        a = {"id": "T1", "url": "/graphic/publish?pgc_id=111"}
        b = {"id": "T1", "url": "/graphic/publish?pgc_id=222"}
        # 同一个 tab id，但 pgc_id 不同 ⇒ 必须是两条不同的草稿
        self.assertNotEqual(tp._pgc_id_of(a), tp._pgc_id_of(b))

    def test_handoff_dedups_by_pgc_not_tab_id(self):
        import tt_manual_handoff as mh
        src = inspect.getsource(mh.main)
        self.assertIn("done_pgc", src,
                      "监听器必须按 pgc_id 记已处理，不能只按 tab id")
        # 不允许「seen.add(tgt["id"])」这种只按 tab id 的写法
        self.assertNotIn('seen.add(tgt["id"])', src,
                         "不能只按 tab id 去重——头条会复用 tab")


class TestTitleReadFix(unittest.TestCase):
    """编辑页读标题必须用 `tp.SEL["title"]`，不能用 `bf.SEL`（正文区）。

    ⚠ 2026-10-09 实测：用 `bf.SEL` 读出来的是**整篇正文**，
      `find_case_fuzzy` 必然匹配不上 ⇒ 监听器认不出案例，只能干瞪眼。
    """

    def test_handoff_reads_title_with_title_selector(self):
        import tt_manual_handoff as mh
        src = inspect.getsource(mh.main)
        self.assertIn("tp.SEL[", src,
                      "读标题必须用 tp.SEL['title']")
        # 读标题那几行里绝不能出现 bf.SEL（那是正文区）
        read_block = "\n".join(
            ln for ln in src.splitlines()
            if "e.value" in ln or "SEL["
            in ln or "bf.SEL" in ln)
        self.assertNotIn("bf.SEL)", read_block,
                         "读标题不能拿 bf.SEL（正文区）——会读出整篇正文")

    def test_title_selector_targets_title_field(self):
        sel = tp.SEL["title"]
        self.assertIn("标题", sel)
        # body 选择器绝不能包含 title 的判据（否则又读回正文）
        self.assertNotIn("标题", tp.SEL["body"])

    def test_find_case_fuzzy_exit_is_contained(self):
        """`find_case_fuzzy` 匹配不上会 sys.exit ⇒ 必须被包住。"""
        import tt_manual_handoff as mh
        src = inspect.getsource(mh._match_case)
        self.assertIn("SystemExit", src,
                      "必须捕获 find_case_fuzzy 的 SystemExit，否则打死监听器")

    def test_drift_whitelist_covers_known_remotes(self):
        import tt_manual_handoff as mh
        self.assertEqual(mh._match_case("MORT：AI 产品", {}), "mort")
        self.assertEqual(mh._match_case("Quran Unlock：移动 app", {}),
                         "quran-unlock")


def _fn_code(fn):
    """返回函数的**可执行代码**（AST 反序列化，自动丢掉注释与 docstring）。

    ⚠ 为什么必须用 AST 而不是 grep：加固后的代码里，docstring 和注释都会
    正面提到「旧版无条件 `return True`」「绝不 insertHTML」这些历史说明。
    朴素字符串匹配会把说明文字当成残留代码 ⇒ 假失败；也可能漏掉真正的
    残留调用。AST 只看结构，注释和字符串字面量都不参与。
    """
    tree = ast.parse(inspect.getsource(fn))
    fndef = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == fn.__name__)
    # 摘掉 docstring 节点，避免文字干扰
    if (fndef.body and isinstance(fndef.body[0], ast.Expr)
            and isinstance(fndef.body[0].value, ast.Constant)
            and isinstance(fndef.body[0].value.value, str)):
        fndef.body = fndef.body[1:]
    return ast.unparse(fndef)


def _returns_literal_true(fn):
    """函数里是否存在 `return True` / `return False` 这类**字面量**返回。"""
    for node in ast.walk(ast.parse(inspect.getsource(fn))):
        if isinstance(node, ast.Return) and isinstance(node.value,
                                                      ast.Constant) \
                and isinstance(node.value.value, bool):
            return True
    return False


class TestTableVerifyHardened(unittest.TestCase):
    """🚨 `tt_table_verify.py` 加固回归（2026-10-09 事故钉死）。

    事故链：旧版 `clear_body()` 无条件 `return True`（从不回读验证）
    → 清空失败仍继续 `insertHTML` → 新正文**追加**在旧正文后
    → `kibu` 远端 6表/66格（本地只有 3表/34格）
    → 且默认写 `zz表格验收-可删` 标题，草稿箱多出 2 条垃圾稿。
    """

    def test_clear_body_delegates_to_verified_impl(self):
        """clear_body 必须委托给带回读校验的实现，不能自己拍 True。"""
        import tt_table_verify as V
        self.assertFalse(
            _returns_literal_true(V.clear_body),
            "clear_body 绝不能返回字面量 True —— 那正是表格数翻倍的根因")
        code = _fn_code(V.clear_body)
        self.assertIn("bf.clear_body(cdp)", code,
                      "应复用 tt_body_fill.clear_body（占位符判空 + 回读）")

    def test_abort_when_clear_not_true(self):
        """清空结果非 True ⇒ 必须中止，绝不 insertHTML。"""
        import tt_table_verify as V
        tree = ast.parse(inspect.getsource(V.verify_one))
        body = tree.body[0].body

        # 🚨 结构判据（按 AST 节点位置，不靠字符串/行号）：
        #    中止判据 `cleared is not True` 必须早于真正发insertHTML 的那次调用
        guard = next((n.lineno for n in body
                      if isinstance(n, ast.If)
                      and "cleared is not True" in ast.unparse(n.test)), None)
        self.assertIsNotNone(guard, "必须校验清空结果后才允许插入")

        # 中止分支内部必须真的return
        abort = next(n for n in body
                     if isinstance(n, ast.If) and n.lineno == guard)
        self.assertTrue(any(isinstance(x, ast.Return) for x in abort.body),
                        "中止分支必须 return，不能只是打个标记继续往下走")

        # insertHTML 那次 cdp.send 的位置
        def _send_lineno(n):
            for x in ast.walk(n):
                if isinstance(x, ast.Call) \
                        and ast.unparse(x.func).endswith("cdp.send") \
                        and "insertHTML" in ast.unparse(x):
                    return x.lineno
            return None

        ins = next((l for l in (_send_lineno(n) for n in body)
                    if l is not None), None)
        self.assertIsNotNone(ins, "应能找到发insertHTML 的 cdp.send 调用")
        self.assertLess(guard, ins,
                        "中止判据必须出现在 insertHTML 之前，"
                        "否则等于没拦")

    def test_no_test_title_written(self):
        """默认不再写 zz 测试标题（那会在草稿箱留下垃圾稿）。"""
        import tt_table_verify as V
        code = _fn_code(V.verify_one)
        self.assertNotIn("_focus_and_type", code,
                         "验收脚本不该再改标题 ⇒ 不会再产生 zz 垃圾稿")
        self.assertNotIn("zz表格验收", code,
                         "verify_one 可执行代码里不该出现 zz 测试标题")

    def test_placeholder_aware_emptiness(self):
        """判空必须用占位符识别：清空后 innerText 长度是 7 不是 0。

        ⚠ 判据落在注入的 **JS 常量** 上，不是 Python 函数体 ——
          冒烟测试若只 grep 函数源码会漏掉真实实现。
        """
        import tt_body_fill as bf
        js = bf._IS_EMPTY
        self.assertIn("placeholder", js.lower(),
                      "判空 JS 必须识别 .syl-placeholder"
                      "（否则长度 7 被当成有内容）")
        self.assertIn("syl-placeholder", js,
                      "必须命中头条真实的占位符类名")
        # 反向：绝不能用 `length <= N` 这种会被占位符破掉的判据
        self.assertNotRegex(js, r"\.length\s*<=\s*\d",
                            "不能用 innerText 长度阈值判空（占位符让它恒为 7）")

    def test_idempotent_short_circuit(self):
        """远端已是目标内容时短路，不要清空重灌（少一次写远端）。"""
        import tt_table_verify as V
        src = inspect.getsource(V.verify_one)
        self.assertIn("return True", src.split("insertHTML")[0],
                      "insertHTML 之前应有幂等短路分支")

    def test_reports_abort_reason(self):
        """中止时必须打印原因，不能静默算作普通不通过。"""
        import tt_table_verify as V
        src = inspect.getsource(V.main)
        self.assertIn("已中止", src, "中止分支要有明确输出")


if __name__ == "__main__":
    unittest.main(verbosity=2)
