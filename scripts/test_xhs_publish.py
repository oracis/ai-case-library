# -*- coding: utf-8 -*-
"""xhs_publish 纯函数测试（文案改写 / 标题预算 / 金额压缩 / HTML 转义）。"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as x


def _mkc(**kw):
    c = {"id": "t1", "name": "Test", "name_en": "Test",
         "one_liner": "一句话定位", "what_it_does": "做什么",
         "how_it_makes_money": "怎么赚钱",
         "why_it_works": ["因为A", "因为B", "因为C", "因为D"],
         "playbook": ["学点1", "学点2"], "category": "AI 工具",
         "metrics": {"headline": "收入 $1.6K / 售价 $60K / 倍数 3.1x"},
         "replicability": {"tech": 4, "distribution": 3, "capital": 3,
                           "timing": 2},
         "solo_fit": {"score": 55.2, "rank": 29},
         "china_fit": {"score": 41.4, "rank": 35, "blocker": "牌照"}}
    c.update(kw)
    return c


class TestAmount(unittest.TestCase):
    def test_compact(self):
        self.assertEqual(x.compact_amount("$128,000"), "$128K")
        self.assertEqual(x.compact_amount("$1,032,000"), "$1.03M")
        self.assertEqual(x.compact_amount("$1.6K"), "$1.6K")
        self.assertEqual(x.compact_amount("$842"), "$842")
        self.assertEqual(x.compact_amount("$60,000"), "$60K")
        self.assertEqual(x.compact_amount("$85,000"), "$85K")

    def test_first_amount_skips_mrr_word(self):
        # "MRR" 的 M 不能被 [KM]? 吃进去
        h = "$128,000 MRR（该数据集内最高当前 MRR）"
        self.assertEqual(x.first_amount(h), "$128K")

    def test_first_amount_missing(self):
        self.assertEqual(x.first_amount("无金额口径"), "")


class TestTitle(unittest.TestCase):
    def test_basic_within_budget(self):
        t = x.make_xhs_title(_mkc())
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)
        self.assertIn("$1.6K", t)
        self.assertNotIn("…", t)

    def test_cheng_jiao_caliber(self):
        c = _mkc(id="pm", name="Promptmonitor.io",
                 one_liner="监测品牌在各类 AI 模型回答中的曝光情况",
                 metrics={"headline": "以 $85,000 成交（TrustMRR 平台公开案例）"})
        t = x.make_xhs_title(c)
        self.assertTrue(len(t) <= x.TITLE_MAX)
        self.assertIn("$85K", t)
        self.assertIn("成交", t)

    def test_mrr_caliber(self):
        c = _mkc(id="prosp", one_liner="（待补充：产品定位未明）",
                 metrics={"headline": "$128,000 MRR（最高当前 MRR）"})
        t = x.make_xhs_title(c)
        self.assertTrue(len(t) <= x.TITLE_MAX)
        self.assertIn("$128K", t)

    def test_long_name_falls_back(self):
        c = _mkc(id="x", name="VeryLongProductName exceeding budget",
                 one_liner="一个特别特别长的定位描述" * 3)
        t = x.make_xhs_title(c)
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)
        self.assertNotIn("…", t)

    def test_no_amount(self):
        c = _mkc(id="y", metrics={"headline": ""})
        t = x.make_xhs_title(c)
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)

    def test_dedup_falls_to_next_candidate(self):
        c = _mkc(id="p", one_liner="（待补充：产品定位未明）")
        t1 = x.make_xhs_title(c)
        t2 = x.make_xhs_title(c, used={t1})
        self.assertNotEqual(t1, t2)
        self.assertTrue(len(t2) <= x.TITLE_MAX)


class TestBody(unittest.TestCase):
    def test_budget(self):
        c = _mkc(
            what_it_does="做" * 200,
            how_it_makes_money="赚" * 300,
            why_it_works=["因" * 60] * 4,
            playbook=["学" * 80] * 4,
            metrics={"headline": "收入 $2.4K / 售价 $28K" * 4})
        note = x.build_note(c)
        self.assertLessEqual(len(note["body"]), x.BODY_MAX)
        self.assertLessEqual(len(note["title"]), x.TITLE_MAX)

    def test_tags_format(self):
        tags = x.build_tags(_mkc())
        self.assertTrue(all(t.startswith("#") for t in tags))
        self.assertIn("#AI工具", tags)
        self.assertEqual(len(tags), len(set(tags)))

    def test_caliber_word_kept(self):
        c = _mkc(id="z", metrics={"headline": "MRR $2,869；累计收入 $62,352"})
        body = x.build_body(c)
        self.assertIn("MRR", body)   # 口径词不许被改写丢掉


class TestDesc(unittest.TestCase):
    def test_placeholder_falls_back(self):
        self.assertEqual(x.short_desc(_mkc(one_liner="（待补充：产品定位未明）")),
                         "AI 工具小生意")

    def test_paren_and_suffix_removed(self):
        self.assertEqual(
            x.short_desc(_mkc(one_liner="跨境云通信（VoIP + SMS）服务")),
            "跨境云通信")


class TestCoverHeadline(unittest.TestCase):
    def test_whole_segments_only(self):
        h = "收入 $6.2K / 售价 $15K / 倍数 0.2x"
        out = x.cover_headline(h)
        self.assertEqual(out, "收入 $6.2K / 售价 $15K")
        self.assertFalse(out.rstrip().endswith(("倍数", "/")))

    def test_paren_stripped(self):
        h = "MRR $2,869；累计收入 $62,352（RevenueCat API 验证）"
        out = x.cover_headline(h)
        self.assertNotIn("（", out)

    def test_budget(self):
        self.assertTrue(len(x.cover_headline("收入 $1.6K / 售价 $60K / 倍数 3.1x")) <= 24)


class TestHtml(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(x.esc('<a & "b"'), "&lt;a &amp; &quot;b&quot;")

    def test_card_html_wellformed(self):
        import xml.etree.ElementTree as ET
        c = _mkc(id="t2", name="T<2>")
        for p in range(1, 6):
            html = x.card_html(c, p, 5)
            self.assertIn('charset="utf-8"', html)
            # body 部分按 XML 校验（style 内无 < >，body 文本已转义）
            body = html.split("</style></head><body>")[1]
            self.assertEqual(
                ET.fromstring(body.replace("<body>", "").replace(
                    "</body>", "").replace("</html>", "").strip()).tag, "div")
            self.assertNotIn("archaic", html)   # 防再混入乱码


class TestDraft(unittest.TestCase):
    """草稿（离开编辑页自动暂存）相关的纯逻辑。"""

    def test_signal_js_returns_object(self):
        js = x._JS_DRAFT_SIGNAL
        self.assertIn("草稿箱中有未发布的作品", js)
        self.assertTrue(js.startswith("(function(){"))
        self.assertIn("return {hint:", js)

    def test_draft_signal_dict(self):
        class S:
            def eval(self, expr, refresh_context=False):
                return {"hint": True, "n": -1}
        self.assertEqual(x._draft_signal(S()), {"hint": True, "n": -1})

    def test_draft_signal_json_str(self):
        class S:
            def eval(self, expr, refresh_context=False):
                return '{"hint": false, "n": 3}'
        self.assertEqual(x._draft_signal(S()), {"hint": False, "n": 3})

    def test_draft_signal_none(self):
        class S:
            def eval(self, expr, refresh_context=False):
                raise RuntimeError("ctx gone")
        self.assertIsNone(x._draft_signal(S()))


if __name__ == "__main__":
    unittest.main()
