#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_article.money_scale 的**口径**测试（2026-10-21 修）。

修的洞：`money_scale` 此前是 `max(metrics 里所有数字)`，等于把三种口径
混在一个刻度上。实测 255 条里 215 条的「最大数字」来自 `all_time`（累计），
另有 `visitors`（访客计数）与 `growth_30d`（百分比）也被当成金额。
后果是 `--top N` 的排期由「谁累计攒得多 / 谁访客多 / 谁增速百分比大」决定，
而不是「谁现在挣得多」。

判据：只认 mrr / last_30d_revenue / arr 三个**当前口径**字段，统一年化成
USD/年；累计、计数、百分比一律不计入。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import make_article as MA  # noqa: E402


def case(metrics=None):
    return {"id": "x", "metrics": metrics or {}}


def bonus(c):
    """只留金额那一项的分（去掉纠错与象限的贡献）。"""
    return MA.virality_base(c)


class TestMoneyCaliber(unittest.TestCase):

    def test_累计口径不计入(self):
        """all_time 是累计，不是收入现值 —— DATA_SCHEMA:211 明写「不是 ARR」。"""
        c = case({"mrr": 0, "last_30d_revenue": 0, "all_time": 572000})
        self.assertEqual(MA.money_scale(c), 0)
        self.assertEqual(bonus(c), 0.0, "两年攒下 57 万、如今归零的死项目不该有钱的分")

    def test_死项目不许盖过活着的(self):
        dead = case({"mrr": 0, "last_30d_revenue": 0, "all_time": 572000})
        alive = case({"mrr": 12000, "last_30d_revenue": 11000, "all_time": 30000})
        self.assertGreater(MA.money_scale(alive), MA.money_scale(dead))
        self.assertGreater(bonus(alive), bonus(dead))

    def test_计数不计入(self):
        """visitors / subscriptions / customers 是计数，不是钱。"""
        c = case({"visitors": 378000, "subscriptions": 9000, "customers": 4200})
        self.assertEqual(MA.money_scale(c), 0)

    def test_百分比不计入(self):
        """growth_30d 一类是百分比 —— 实测出现过 73638.6 被当 $7.3 万。"""
        c = case({"mrr": 1000, "growth_30d": 73638.6,
                  "revenue_growth_30d": 155157.1, "mrr_growth_30d": 12.5})
        self.assertEqual(MA.money_scale(c), 12000)

    def test_mrr与近30天取大者且年化(self):
        self.assertEqual(MA.money_scale(case({"mrr": 1000})), 12000)
        self.assertEqual(MA.money_scale(case({"last_30d_revenue": 2000})), 24000)
        self.assertEqual(MA.money_scale(case({"mrr": 1000,
                                              "last_30d_revenue": 2000})), 24000)

    def test_arr不重复年化(self):
        """arr 本来就是年化 —— 再乘 12 会把 $2 亿吹成 $24 亿。"""
        self.assertEqual(MA.money_scale(case({"arr": 200000000})), 200000000)

    def test_非订阅制只给近30天时也有量级(self):
        """Current MRR 对非订阅制恒 0（红线），这类条目只有 last_30d 可用。"""
        c = case({"mrr": 0, "last_30d_revenue": 9000})
        self.assertEqual(MA.money_scale(c), 108000)
        self.assertEqual(bonus(c), 0.5)

    def test_脏数据不许崩(self):
        for bad in ({"mrr": "244,029"}, {"mrr": None}, {"mrr": -5},
                    {"mrr": float("nan")}, {"mrr": float("inf")},
                    {"mrr": True}):
            self.assertEqual(MA.money_scale(case(bad)), 0, bad)
        self.assertEqual(MA.money_scale({"metrics": None}), 0)
        self.assertEqual(MA.money_scale({}), 0)

    def test_档位按年化收入(self):
        """$10 万/年、$100 万/年、$1000 万/年、$1 亿/年 各一档。"""
        tiers = [(8334, 0.5), (83334, 1.0), (833334, 2.0), (8333334, 3.0)]
        for monthly, want in tiers:
            c = case({"mrr": monthly})
            self.assertEqual(bonus(c), want, "mrr=%d 年化后应落 %.1f 档"
                             % (monthly, want))


if __name__ == "__main__":
    unittest.main(verbosity=2)
