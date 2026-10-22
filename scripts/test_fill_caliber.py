#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_caliber 的判定测试（2026-10-21）。

`caliber` 是 verify_rules 的 must：空着 = 案例一直挂着「未选择收入口径」。
补它最大的风险不是漏填，是**填错** —— 口径写错一个，数字就差十倍。

这里钉的是四类坑，全部来自真实数据：
  · 否定句里的口径词（checkvibe：「不宜换算成 ARR」，真口径是累计）
  · note 说清是 run-rate 而 headline 写 ARR（viktor）
  · headline 两个口径词并存，取门面数字那个（shipfast：累计 $1.27M + 近30天 $3K）
  · 判不了就别写（bustem 的榜单快照；archive 里档案已 404、两种解释都成立）
  · 「2025 全年」判 annual —— 2026-10-21 前八个口径里没有它，只能留空
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fill_caliber as FC  # noqa: E402


def case(headline="", note="", **metrics):
    m = {"headline": headline, "metric_note": note}
    m.update(metrics)
    return {"id": metrics.pop("_id", "x"), "metrics": m, "sources": []}


class TestHeadlineToken(unittest.TestCase):
    def test_arr(self):
        for h in ("$200M ARR", "约 $200M ARR", "$33M ARR（2025）"):
            self.assertEqual(FC.decide(case(h))[0], "arr", h)

    def test_mrr(self):
        for h in ("$293K MRR", "$20,086 MRR", "$3,000+ MRR（仅自报）"):
            self.assertEqual(FC.decide(case(h))[0], "mrr", h)

    def test_last_30d(self):
        self.assertEqual(FC.decide(case("近 30 天约 $40K"))[0], "last_30d")

    def test_两个口径词并存取门面数字(self):
        # shipfast 的真实 headline：主数字是累计，后半句是对比
        c = case("累计 $1.27M，但近 30 天只剩约 $3K", all_time=1270252)
        self.assertEqual(FC.decide(c)[0], "lifetime")


class TestNoteBeatsHeadline(unittest.TestCase):
    def test_run_rate(self):
        c = case("$15M ARR（10 周达成）", "15M 是 ARR run-rate（年化运行率），不是已收到的现金",
                 arr=15000000)
        self.assertEqual(FC.decide(c)[0], "run_rate")

    def test_否定句里的口径词不算(self):
        # checkvibe：两个 ARR 都在否定句里，真口径是「累计」
        c = case("6 周 $3,400 收入",
                 "创始自报的 6 周累计毛收入，非月度经常性收入。不宜换算成 ARR。")
        self.assertEqual(FC.decide(c)[0], "lifetime")

    def test_自己承认没定的note不采信(self):
        # bustem：note 明写「两种口径都说得通，需复核」
        c = case("$397,729（TrustMRR 榜单值）",
                 "数字取自 TrustMRR 公开榜单快照。⚠️ 该榜单列名为「Leaderboard MRR / All time」，"
                 "两种口径解释都说得通，进库前需到自己页面复核。")
        key, why = FC.decide(c)
        self.assertIsNone(key)
        self.assertIn("榜单", why)


class TestNumberMatch(unittest.TestCase):
    def test_headline数字与metrics一致(self):
        c = case("组合月收入 $250K+", mrr=250000)
        self.assertEqual(FC.decide(c)[0], "mrr")

    def test_数字对不上就不猜(self):
        c = case("$9,999 神秘数字", mrr=250000)
        self.assertIsNone(FC.decide(c)[0])


class TestAnnual(unittest.TestCase):
    """annual 是 2026-10-21 新增的第八个口径。

    它存在的唯一理由是「已发生的自然年合计」这三个字：ARR 是前瞻、lifetime 是
    跨年累计、run_rate 是乘 12 的估计，三个都装不下「2025 全年 $1,032,000」。
    这几条钉住边界 —— 既要认得出年度收入，也不能把「2025 年 6 月收入」这种
    单月数字误判成年度。
    """

    def test_全年(self):
        c = case("2025 全年 $1,032,000")
        self.assertEqual(FC.decide(c)[0], "annual")

    def test_年度收入(self):
        for h in ("2024 年收入 $50K", "2025 年度营收 $1.2M"):
            self.assertEqual(FC.decide(case(h))[0], "annual", h)

    def test_单月收入不算年度(self):
        # 「2025 年 6 月收入」被 `[^0-9]{0,4}` 挡住：年份后面跨不过数字
        self.assertIsNone(FC.decide(case("2025 年 6 月收入 $30K"))[0])

    def test_累计在前仍取累计(self):
        # 门面数字是累计时不能因为后半句有「全年」就翻成年报口径
        c = case("开业至今累计 $3.2M；其中 2025 全年 $1.03M", all_time=3200000)
        self.assertEqual(FC.decide(c)[0], "lifetime")

    def test_否定句里的年度不算(self):
        c = case("$9,999", "这是 6 周流水，不是年度收入，也不能乘 12 当年化。")
        self.assertIsNone(FC.decide(c)[0])


class TestRefuse(unittest.TestCase):
    def test_榜单快照判不了(self):
        c = case("$397,729（TrustMRR 榜单值）")
        key, why = FC.decide(c)
        self.assertIsNone(key)
        self.assertIn("榜单", why)

    def test_空headline(self):
        self.assertIsNone(FC.decide(case(""))[0])


class TestHelpers(unittest.TestCase):
    def test_headline_nums_单位展开(self):
        self.assertEqual(FC.headline_nums("累计 $1.27M"), [1270000.0])
        self.assertEqual(FC.headline_nums("$202K MRR"), [202000.0])
        self.assertEqual(FC.headline_nums("$20,086 MRR"), [20086.0])
        self.assertEqual(FC.headline_nums("$250K–$300K"), [250000.0, 300000.0])

    def test_num_match_容差2pct(self):
        self.assertTrue(FC.num_match(20086, 20000))
        self.assertFalse(FC.num_match(20086, 25000))

    def test_否定检测(self):
        pat = FC.TOKENS[1][0]          # ARR
        self.assertTrue(FC._affirmative(pat, "公司披露 $10M ARR"))
        self.assertFalse(FC._affirmative(pat, "不宜换算成 ARR"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
