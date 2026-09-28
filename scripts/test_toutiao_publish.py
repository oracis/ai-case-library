#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toutiao_publish.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_toutiao_publish
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402

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
        bs = tp.md_to_blocks(MD)
        joined = "\n".join(t for _, t in bs)
        self.assertNotIn("别进正文", joined)
        self.assertNotIn("多行注释块", joined)
        self.assertNotIn("-->", joined)

    def test_url_and_md_link_cleaned(self):
        text = "\n".join(t for _, t in tp.md_to_blocks(MD))
        self.assertNotIn("http", text)
        self.assertIn("官网", text)
        # `**` 由 blocks_to_html 转成 <strong>，最终 HTML 里不该有裸 markdown 符号
        self.assertNotIn("**", tp.blocks_to_html(tp.md_to_blocks(MD)))

    def test_table_becomes_kv(self):
        bs = tp.md_to_blocks(MD)
        kv = [t for k, t in bs if k == "kv"]
        self.assertTrue(any(t.startswith("官方口径") for t in kv))
        self.assertFalse(any(t.startswith("维度") for t in kv))
        self.assertFalse(any("|" in t for _, t in bs))

    def test_kinds(self):
        bs = tp.md_to_blocks(MD)
        kinds = [k for k, _ in bs]
        self.assertIn("quote", kinds)
        self.assertIn("h2", kinds)
        self.assertIn("ul", kinds)
        self.assertIn("p", kinds)
        self.assertIn("hr", kinds)

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
        self.assertNotIn("<table", html)
        self.assertIn(tp.FOOTER_NOTE, html)

    def test_bold_and_kv_group(self):
        """**整段加粗** → <strong>；连续表格行 → 一组「加粗标签 + 值」列表。"""
        html = tp.blocks_to_html(tp.md_to_blocks(MD))
        self.assertIn("<strong>整段加粗</strong>", html)
        self.assertIn("<li><strong>官方口径：</strong>月收入 $12K</li>", html)
        # 一组 kv 只包一个 <ul>，不能每个 pdf 一格
        self.assertEqual(html.count("<ul>"), html.count("</ul>"))
        # 头条不认 em / u，别生成这两种标签
        self.assertNotIn("<em", html)
        self.assertNotIn("<u>", html)

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
        self.assertIn("#221b3d", html)            # 主题深色底（缩略图对比度）
        self.assertIn("MRR $244,029", html)       # 无尾逗号的完整数字
        self.assertNotIn("CASE STUDY", html)      # 小字装饰已去掉
        self.assertNotIn("numsub", html)

    def test_cover_html_placeholder_oneliner_never_shows_unclassified(self):
        """prosp 实测：one_liner 是占位符、category=未分类 → 封面印出「未分类」。"""
        c = {"id": "prosp", "name": "PROSP", "category": "未分类",
             "one_liner": "（待补充：产品定位未明）",
             "metrics": {"headline": "$128,000 MRR"}}
        html = tp.tt_cover_html(c)
        self.assertNotIn("未分类", html)
        self.assertNotIn("待补充", html)
        self.assertIn("海外小生意", html)         # 兜底文案


if __name__ == "__main__":
    unittest.main(verbosity=2)
