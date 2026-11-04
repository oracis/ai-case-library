#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/scrub_jargon.py` 的回归锁（2026-11-05）。

锁的是三件容易复发的事：
1. **黑话回流**：`data/cases.json` 里任何「会印进正文」的字段都不许再出现
   `候选 / 入库时 / last_30d_revenue / price_point …`（168 处是一次性清完的，
   再长回来就是写稿那步又用内部词汇了）。
2. **数字被改**：清洗只换说法，金额/百分比/日期一个都不许动。
3. **改错地方**：`metrics.caliber="last_30d"` 是机器取值不是文案，碰了会毁判据；
   引号里抄来的页面原文也不许动（改了就是伪造引文）。

跑法：`cd scripts && python -X utf8 -m unittest test_jargon -v`
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import scrub_jargon as S          # noqa: E402


class TestRules(unittest.TestCase):

    def test_field_names_become_reader_words(self):
        cases = [
            ("price_point $50,000 是产品价格", "定价 $50,000 是产品价格"),
            ("定价（price_point）为 $10,000", "定价为 $10,000"),
            ("MRR（候选档案 headline）", "MRR（据 TrustMRR 档案）"),
            ("候选资料：last_30d_revenue $3,171", "该站档案：近 30 天收入 $3,171"),
        ]
        for before, want in cases:
            self.assertEqual(S.scrub_text(before), want)

    def test_quote_content_is_untouched(self):
        """引号里是页面原文（'MRR $694'），替换它等于伪造引文。"""
        s = "档案头条数字写的是 'MRR $694'，不是 $698"
        self.assertIn("'MRR $694'", S.scrub_text(s))

    def test_field_name_in_quotes_is_fixed(self):
        """但引号里装的是 json key（'last_30d_revenue':）时该改 —— 那不是引文。"""
        s = "但候选资料中 'last_30d_revenue': 1424.0 与原文表格不一致"
        out = S.scrub_text(s)
        self.assertNotIn("last_30d_revenue", out)
        self.assertIn("1424.0", out)

    def test_space_between_chinese_and_english_is_kept(self):
        """`$99/月 和 Custom 版` 的空格是排版需要，不许压成「月和」。"""
        s = "定价分为 Pro 版 $99/月 和 Custom 定制版"
        self.assertEqual(S.scrub_text(s), s)

    def test_idempotent(self):
        for s in ("候选资料 headline 称 'MRR $1,627'",
                  "早期入库时收入未公开",
                  "price_point 为 $10,000"):
            once = S.scrub_text(s)
            self.assertEqual(S.scrub_text(once), once, "二次清洗又变了：%s" % once)

    def test_legit_terms_survive(self):
        """正当术语不许被洗：`候选人` 是招聘场景的词（jobright 稿实际用到），
        `候选集` 是检索/排序的通用词（腾讯云 mort 稿），`候选表` 是反向样本
        方法论里定义过的概念。裸 `候选→档案` 会造出「档案人」「档案集」这种错句。"""
        for s in ("「候选人质量」能直接对应招聘成本",
                  "先粗筛到一个较小的候选集，再对候选集跑模型",
                  "候选表加了一列必填的 caliber"):
            self.assertEqual(S.scrub_artifact(s)[0], s, "正当术语被改了：%s" % s)

    def test_quoted_text_not_counted_as_leftover(self):
        """引号内的页面原文既不改，也不该被算成「残留黑话」——
        判据与改动范围必须一致，否则 verify 永远红。"""
        self.assertEqual(S._detect_hits("他写的是「headline 里那个数」"), [])
        self.assertTrue(S._detect_hits("候选资料里写着 headline"))


class TestNumbers(unittest.TestCase):

    def test_money_percent_date_survive(self):
        s = ("候选数字：近 30 天 $359、All time $4,726、增长 +71.5%"
             "（Revenue 同步于 2026-11-03T06:46:40Z）")
        out = S.scrub_text(s)
        for token in ("$359", "$4,726", "+71.5%", "2026-11-03"):
            self.assertIn(token, out)

    def test_field_name_digits_are_not_treated_as_data(self):
        """`last_30d_revenue` 里的 30 不是数据 —— 第一版把它算进数字校验，
        apply 因此被整批中止。"""
        before = "近 30 天收入 $957（候选资料 headline 与 last_30d_revenue 字段）"
        after = S.scrub_text(before)
        self.assertEqual(S._digits(before), S._digits(after))
        self.assertIn("$957", after)


class TestRepoData(unittest.TestCase):
    """存量锁：全库可见字段不许再有黑话。"""

    def test_no_jargon_in_visible_fields(self):
        cases = S.load()
        left = S.residual(cases)
        self.assertEqual(
            left, [],
            "又出现内部黑话了（%d 处），跑 scrub_jargon.py --check 看清单：%s"
            % (len(left), [(x[0], x[1], x[2]) for x in left[:5]]))

    def test_metrics_enum_not_scrubbed(self):
        """枚举值不许被清洗（`caliber="last_30d"` 是判据输入，不是文案）。"""
        case = {"id": "x", "metrics": {"caliber": "last_30d",
                                       "last_30d_revenue": 1.0}}
        for loc, holder, key in S.iter_visible(case):
            msg = "枚举/数值字段不该进清洗范围：%s" % loc
            self.assertFalse(loc.endswith("caliber"), msg)
            self.assertFalse(loc.endswith("last_30d_revenue"), msg)

    def test_cases_file_still_loads(self):
        cases = S.load()
        self.assertGreater(len(cases), 400)
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)), "改数据把 id 弄重了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
