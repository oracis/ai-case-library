#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`publish_slot.decide()` 的日配额 / 两类交替口径测试（离线，不读写台账）。

钉住一条 2026-11-06 用户指出的既有瑕疵：**腾讯云日配额 3，全库却只有两类稿**。
两类都发过之后，旧逻辑仍然固定 `want="game"` 且 reason 写「补另一类」——
理由说反（实际是又发游戏），序列也不讲交替（上一篇是游戏就成了连发两篇）。

新口径：两类都发过 ⇒ 没有「没发过的类」可补，按「上一篇的对面」接着交替
（ai→game→ai），reason 如实写「两类都发过」，不再出现「补另一类」。

跑法：cd scripts && python -X utf8 -m unittest test_publish_slot -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import publish_slot as S     # noqa: E402

DAY = "2026-11-06"


class QuotaAlternation(unittest.TestCase):
    """配额 > 类数（3 > 2）时怎么排第三篇。"""

    def setUp(self):
        self._records = S.records
        self._ai_pool = S.ai_pool
        self._game_pool = S.game_pool
        self._missing = S.game_missing_pack
        self._quota = dict(S.DAILY_QUOTA)
        # 两类都有稿：只测「该发哪类」，别让空池把结论拐去 need_draft / blocked
        S.ai_pool = lambda plat, ready_only=True: ["chessigma"]
        S.game_pool = lambda plat: ["ext-ep-001-x"]
        S.game_missing_pack = lambda plat: []

    def tearDown(self):
        S.records = self._records
        S.ai_pool = self._ai_pool
        S.game_pool = self._game_pool
        S.game_missing_pack = self._missing
        S.DAILY_QUOTA.clear()
        S.DAILY_QUOTA.update(self._quota)

    def _decide(self, rows, platform="tencentcloud"):
        S.records = lambda plat: sorted(rows)
        return S.decide(platform, DAY)

    # -------------------------------------------------- 两类都发过（第三篇）

    def test_third_post_follows_alternation_after_game(self):
        """今日 ai → game，第三篇该是 ai（不是再发游戏）。"""
        d = self._decide([("2026-11-06 09:00", "chessigma"),
                          ("2026-11-06 14:00", "ext-ep-001-x")])
        self.assertEqual(d["quota"], 3)
        self.assertEqual(d["kind"], "ai", "上一篇是游戏 ⇒ 第三篇应交替回 AI case")
        self.assertIn("两类都发过", d["reason"])
        self.assertNotIn("补另一类", d["reason"], "两类都发过时没有「另一类」可补")

    def test_third_post_follows_alternation_after_ai(self):
        """今日 game → ai，第三篇该是 game。"""
        d = self._decide([("2026-11-06 09:00", "ext-ep-001-x"),
                          ("2026-11-06 14:00", "chessigma")])
        self.assertEqual(d["kind"], "game")
        self.assertIn("两类都发过", d["reason"])
        self.assertNotIn("补另一类", d["reason"])

    def test_third_post_never_repeats_previous_kind(self):
        """顺序反过来也一样：第三篇永远不等于「今天最后一篇」的类。"""
        seq = [("2026-11-06 09:00", "chessigma"),
               ("2026-11-06 14:00", "ext-ep-001-x")]
        prev = S.kind_of(seq[-1][1])
        self.assertNotEqual(self._decide(seq)["kind"], prev)

    # -------------------------------------------------- 只发过一类（仍应说补）

    def test_single_kind_still_says_other(self):
        """只发过 AI ⇒ 确实还有「另一类」没发，reason 保持「补另一类」。"""
        d = self._decide([("2026-11-06 09:00", "chessigma")])
        self.assertEqual(d["kind"], "game")
        self.assertIn("补另一类", d["reason"])
        self.assertNotIn("两类都发过", d["reason"])

    def test_single_kind_game(self):
        d = self._decide([("2026-11-06 09:00", "ext-ep-001-x")])
        self.assertEqual(d["kind"], "ai")
        self.assertIn("补另一类", d["reason"])

    # -------------------------------------------------- 上限 / 配额 2 平台

    def test_quota_reached_stops(self):
        d = self._decide([("2026-11-06 09:00", "chessigma"),
                          ("2026-11-06 12:00", "ext-ep-001-x"),
                          ("2026-11-06 15:00", "cod-dex")])
        self.assertIsNone(d["kind"])
        self.assertIn("达到上限", d["reason"])

    def test_quota_two_platform_has_no_third_post(self):
        """博客园配额 2：两类发完就停，不能走到「两类都发过还继续」。"""
        d = self._decide([("2026-11-06 09:00", "chessigma"),
                          ("2026-11-06 10:00", "ext-ep-001-x")],
                         platform="cnblogs")
        self.assertIsNone(d["kind"])
        self.assertIn("达到上限", d["reason"])


if __name__ == "__main__":
    unittest.main()
