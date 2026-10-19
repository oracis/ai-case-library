#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`toutiao_reconcile.py` 的纯函数回归（不碰浏览器）。

钉死三件事：
  1. note 里的『…』必须被当别名 —— 否则老口径标题全判成幽灵；
  2. 一个 case 发过两篇（新旧口径）时两篇都要认；
  3. 三态判定（draft / published / ghost）不能靠顺序蒙。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import toutiao_reconcile as rc  # noqa: E402


class TestCollectAlias(unittest.TestCase):
    def test_note_alias_extracted(self):
        rows = [{"id": "voklit",
                 "note": "P1对账: 『月收$1.6K：给跨境团队搭云通信』(金额1600)"}]
        self.assertEqual(rc.collect_alias(rows)["voklit"],
                         ["月收$1.6K：给跨境团队搭云通信"])

    def test_multiple_aliases_per_case(self):
        """bustem 这种「新旧两篇都发了」的，note 里有两个『』。"""
        rows = [{"id": "bustem",
                 "note": "新口径『Bustem：帮电商品牌全网打假』+旧口径"
                         "『月收$398K：帮品牌删仿品和窜货链接』两篇"}]
        self.assertEqual(len(rc.collect_alias(rows)["bustem"]), 2)

    def test_no_note_no_alias(self):
        self.assertEqual(rc.collect_alias([{"id": "x"}]), {})


class TestMatchTitles(unittest.TestCase):
    def test_alias_hits_before_name(self):
        """老口径标题只有别名能认（产品名不在标题里）。"""
        case = {"name": "Vid.AI"}
        got = rc.match_titles("vid-ai", case,
                              ["两年做到累计 $162 万，然后峰值留存掉到 46%"],
                              ["两年做到累计 $162 万，然后峰值留存掉到 46%"])
        self.assertEqual(got, ["两年做到累计 $162 万，然后峰值留存掉到 46%"])

    def test_both_articles_of_one_case(self):
        """新旧两篇都要进结果 —— 只取第一条会把第二篇变成假孤儿。"""
        case = {"name": "Bustem"}
        titles = ["Bustem：帮电商品牌全网打假",
                  "月收$398K：帮品牌删仿品和窜货链接"]
        got = rc.match_titles("bustem", case,
                              ["Bustem：帮电商品牌全网打假",
                               "月收$398K：帮品牌删仿品和窜货链接"], titles)
        self.assertEqual(len(got), 2)

    def test_no_hit_returns_empty(self):
        self.assertEqual(rc.match_titles("zzz", {"name": "ZZZ"}, [], ["别的标题"]), [])


class TestBuildRows(unittest.TestCase):
    def test_three_states(self):
        led_d = [{"id": "still"}, {"id": "gone"}]
        led_p = [{"id": "sent"}]
        cases = {"still": {"name": "Still"},
                 "gone": {"name": "Gone"},
                 "sent": {"name": "Sent"}}
        rows = rc.build_rows(led_d, led_p, cases,
                             ["Still：还在草稿箱"], ["Sent：已经发了"])
        self.assertEqual(rows["still"]["state"], "draft")
        self.assertEqual(rows["sent"]["state"], "published")
        self.assertEqual(rows["gone"]["state"], "ghost")

    def test_published_wins_over_draft(self):
        """两侧都命中时算已发布（草稿升格后草稿没删的情况真实存在）。"""
        cases = {"c": {"name": "Case"}}
        rows = rc.build_rows([{"id": "c"}], [{"id": "c"}], cases,
                             ["Case：草稿"], ["Case：已发"])
        self.assertEqual(rows["c"]["state"], "published")

    def test_states_come_from_titles_not_ledger(self):
        """台账写着已发、线上却没有 ⇒ 必须是 ghost，不能被台账自证清白。"""
        cases = {"c": {"name": "Case"}}
        rows = rc.build_rows([], [{"id": "c"}], cases, [], [])
        self.assertEqual(rows["c"]["state"], "ghost")


if __name__ == "__main__":
    unittest.main()
