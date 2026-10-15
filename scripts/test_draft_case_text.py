#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""draft_case_text 的机器守卫回归测试。

这些守卫是「AI 起草 → 入库」这条链路唯一的安全网，而它们**每一个都是
被实测打脸之后才加上/改对的**，所以每条断言都对应一次真实事故：

    中文守卫          3 条里 2 条吐英文
    客单价不准        12454÷407 模型给 29.04（正确 30.60）
    派生放行集合      组合爆炸，「MRR 增长 88%」照样通过
    增速断言          .md 原文里什么数字都有，通用数字守卫拦不住编的增速
    毛利率误杀        守卫把合法的「毛利率 80%」当增速拦了
    空 dict 跳过      `if summary:` 把空素材条目的守卫整个绕过
    slug 对不上       fireply 的 slug 对不上 fireply-ai → 拿到空素材
    MRR 贴错标签      素材 MRR=1279 / 30天=1155，模型写「MRR $1,155」
    货币写错「元」     28 条把 USD 写成「元」，trustmrr 全是美元应写 $
    裸 .0 浮点        37 条整数被写成 31677.0 这类浮点尾

改守卫前先跑这个：python scripts/test_draft_case_text.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import draft_case_text as D  # noqa: E402


def _draft(**kw):
    base = {
        "one_liner": "", "what_it_does": "", "how_it_makes_money": "",
        "verdict": "", "why_it_works": [], "playbook": [],
    }
    base.update(kw)
    return base


class Numbers(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(D._norm("1,155"), 1155.0)
        self.assertEqual(D._norm("12454.0"), 12454.0)

    def test_extract_numbers(self):
        self.assertEqual(D.extract_numbers("MRR $1,155 与 30 天 $12,454"),
                         {1155.0, 12454.0, 30.0})

    def test_ghost_number_flagged(self):
        """素材里没有的数字必须被拦下。"""
        p, ghosts = D.check_numbers(_draft(one_liner="MRR $99,999"),
                                    {12454.0, 407.0})
        self.assertTrue(p)
        self.assertIn(99999.0, ghosts)

    def test_source_number_allowed(self):
        p, _g = D.check_numbers(_draft(one_liner="MRR $12,454"), {12454.0})
        self.assertEqual(p, [])

    def test_no_derived_allowance(self):
        """派生放行集合已砍掉：407-30=377 不该因为「能算出来」就放行。

        原实现把日期、粉丝数也拿去做两两四则运算，组合爆炸后几乎任何数
        都能凑出来 —— 守卫形同虚设。
        """
        p, _g = D.check_numbers(_draft(one_liner="增长到 377 个"), {407.0, 30.0})
        self.assertTrue(p)


class GrowthClaims(unittest.TestCase):
    def test_fabricated_growth_flagged(self):
        """「MRR 增长 88%」：素材没给增速，必须拦。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["近 30 天 MRR 增长了 88%"]),
            {"growth_30d": 62.0})
        self.assertTrue(p)

    def test_source_growth_allowed(self):
        p = D.check_growth_claims(
            _draft(why_it_works=["近 30 天增速 12.1%"]),
            {"growth_30d": 12.1})
        self.assertEqual(p, [])

    def test_profit_margin_not_mistreated(self):
        """毛利率是素材真字段，不能被当成编造的增速误杀。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["80% 的毛利率说明盈利能力强"]),
            {"profit_margin": 80.0})
        self.assertEqual(p, [])

    def test_empty_summary(self):
        """素材为空时任何增速断言都不该放行。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["增长 30%"]), {})
        self.assertTrue(p)


class Caliber(unittest.TestCase):
    def test_non_subscription_cannot_say_mrr(self):
        """MRR 恒 0 的非订阅制，写「MRR $X」= 口径混用（红线 1）。"""
        p = D.check_caliber(_draft(one_liner="MRR $1,155"),
                            {"cur_mrr": 0, "rev_30d": 1155.0})
        self.assertTrue(p)
        self.assertIn("非订阅制", p[0])

    def test_mrr_label_must_match_source(self):
        """把近 30 天收入贴上 MRR 标签：数字有出处但标签错，最隐蔽。"""
        p = D.check_caliber(_draft(one_liner="MRR $1,155"),
                            {"cur_mrr": 1279.0, "rev_30d": 1155.0})
        self.assertTrue(p)
        self.assertIn("贴错标签", p[0])

    def test_correct_mrr_allowed(self):
        p = D.check_caliber(_draft(one_liner="MRR $1,279"),
                            {"cur_mrr": 1279.0, "rev_30d": 1155.0})
        self.assertEqual(p, [])

    def test_zero_mrr_claim_allowed(self):
        """「MRR 恒 0」本身是正确说法，不该被拦。"""
        p = D.check_caliber(_draft(one_liner="MRR 0，非订阅制"),
                            {"cur_mrr": 0, "rev_30d": 1155.0})
        self.assertEqual(p, [])


class CurrencyFormat(unittest.TestCase):
    """货币单位（元）与裸 .0 浮点守卫：实测 28 条写成「元」、37 条带 .0。"""

    def test_cny_yuan_flagged(self):
        p = D.check_currency_format(_draft(
            one_liner="近30天已验证收入31677.0元"))
        self.assertTrue(p)
        self.assertIn("元", p[0])

    def test_eur_yuan_allowed(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="每月 1990 欧元，按欧元计价"))
        self.assertEqual(p, [])

    def test_element_yuan_allowed(self):
        p = D.check_currency_format(_draft(
            playbook=["利用游戏化元素提升留存"]))
        self.assertEqual(p, [])

    def test_bare_float_flagged(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="毛利率为 85.0%"))
        self.assertTrue(p)
        self.assertIn(".0", p[0])

    def test_normal_decimal_allowed(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="客单价为 $7.76"))
        self.assertEqual(p, [])

    def test_dollar_ok(self):
        p = D.check_currency_format(_draft(
            one_liner="近30天已验证收入 $31,677"))
        self.assertEqual(p, [])


class Validate(unittest.TestCase):
    def test_empty_summary_still_runs_guards(self):
        """`if summary:` 会因空 dict 为 falsy 而跳过守卫（踩过）。

        slug 对不上 summary 时正是素材最空、模型最容易编的时候，
        守卫反而被关掉了 —— 必须写 `is not None`。
        """
        problems = D.validate(
            _draft(one_liner="MRR $1,155", why_it_works=["增长 88%"]),
            {1155.0}, {})
        self.assertTrue(any("非订阅制" in p or "贴错标签" in p for p in problems))
        self.assertTrue(any("增速" in p for p in problems))

    def test_non_chinese_flagged(self):
        problems = D.validate(_draft(one_liner="MRR $12,454 for autoclip"),
                              {12454.0}, {"cur_mrr": 12454.0})
        self.assertTrue(any("不是中文" in p for p in problems))

    def test_banned_phrase_flagged(self):
        problems = D.validate(
            _draft(one_liner="MRR $1,234 的工具",
                   why_it_works=["前景广阔，值得关注"]),
            {1234.0}, {"cur_mrr": 1234.0})
        self.assertTrue(any("空话" in p for p in problems))

    def test_replicability_keys(self):
        problems = D.validate(
            _draft(one_liner="MRR $1,234 的工具",
                   replicability={"tech": 2, "data": 2,
                                  "sales": 3, "domain": 4}),
            {1234.0}, {"cur_mrr": 1234.0})
        self.assertTrue(any("键名不对" in p for p in problems))


class Lookup(unittest.TestCase):
    def test_slug_fallback_to_id(self):
        """fireply 的 slug 对不上 fireply-ai，必须按 id 兜底。"""
        summ = {"fireply-ai": {"cur_mrr": 1279.0}}
        self.assertIs(D.lookup_summary(summ, "fireply", "fireply-ai"),
                      summ["fireply-ai"])

    def test_slug_direct_hit(self):
        summ = {"autoclip": {"cur_mrr": 1.0}}
        self.assertIs(D.lookup_summary(summ, "autoclip", "autoclip"),
                      summ["autoclip"])

    def test_missing_returns_none(self):
        self.assertIsNone(D.lookup_summary({}, "x", "y"))


class FactsCard(unittest.TestCase):
    def test_arpu_precomputed(self):
        """客单价必须由脚本算好 —— 模型做除法会算错（踩过两次）。"""
        card = D.facts_card("x", {"cur_mrr": 12454.0, "active_subs": 407}, "")
        self.assertIn("$30.6", card)

    def test_arpu_absent_for_non_subscription(self):
        card = D.facts_card("x", {"cur_mrr": 0, "rev_30d": 1155.0}, "")
        self.assertIn("无法推算", card)

    def test_missing_fields_marked(self):
        card = D.facts_card("x", {}, "")
        self.assertIn("创始人：未公开", card)


if __name__ == "__main__":
    unittest.main(verbosity=2)
