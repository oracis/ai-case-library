#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`gen_case_drafts.py` 的离线单测：生成的稿必须能过 `publish_guard` 内容闸。

2026-11-07 之前，`--gen` 产出的稿固定带 3 条 block（管线水印 / 罐头来源句 /
机械栏目骨架）+ 1 条 warn（数据缺时点）⇒ 补稿链路等于断的。这里把
「生成器产物 → scan_content 零 block」钉成回归，且 LLM 不可用时也要成立。
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_case_drafts as G  # noqa: E402
import publish_guard as PG  # noqa: E402

CASE = {
    "id": "demo-case",
    "name": "DemoCase",
    "one_liner": "一个把公开事件整理成信号的小工具",
    "what_it_does": "它把公开招聘、融资这类公开事件整理成可消费的信号，给销售团队用。",
    "how_it_makes_money": "按月订阅，按席位数计费，团队变大自然加钱。",
    "why_it_works": ["先用人工服务验证有人愿意付钱", "把信号当成带衰减的假设，不当事实"],
    "playbook": ["采集侧就做幂等键", "副作用配速率预算和人工闸门"],
    "verdict": "先把服务卖出去，再把被验证的流程写成软件。",
    "metrics": {"headline": "$12,000 MRR · Stripe 验证"},
    "verified_at": "2026-09-16",
    "caliber": "mrr",
    "sources": [{"label": "TrustMRR 验证页", "kind": "stripe"}],
}


class Header(unittest.TestCase):

    def test_不带管线水印(self):
        h = G.header("cnblogs", CASE, "某标题")
        self.assertEqual(h, "# 某标题\n")
        self.assertNotRegex(h, PG.WATERMARK)


class Timepoint(unittest.TestCase):

    def test_用真实快照日与口径(self):
        row = G._timepoint_row(CASE)
        self.assertIn("数据时点", row)
        self.assertIn("2026-09-16", row)
        self.assertIn("MRR", row)

    def test_没有快照日就不编(self):
        self.assertEqual(G._timepoint_row({"caliber": "mrr"}), "")


class FallbackSections(unittest.TestCase):

    def test_各平台数量与变体(self):
        for p, n in G.SECTION_SLOTS.items():
            rows = G.fallback_sections(CASE, p)
            self.assertEqual(len(rows), n)
            self.assertTrue(all("DemoCase" in r for r in rows[:4]))
            for r in rows:
                self.assertNotRegex("## " + r, PG.MECHANICAL_SECTIONS[0])
                self.assertNotRegex("## " + r, PG.MECHANICAL_SECTIONS[1])

    def test_换案例栏目就不同(self):
        other = dict(CASE, name="另一个产品")
        self.assertNotEqual(G.fallback_sections(CASE, "cnblogs"),
                            G.fallback_sections(other, "cnblogs"))


class ScanGate(unittest.TestCase):
    """生成稿必须零 block —— 这是补稿能不能发出去的唯一判据。"""

    def _assert_clean(self, platform, sections=None):
        text = G.header(platform, CASE, "一个产品说明式标题") + "\n" + \
            G.render(platform, CASE, "一个产品说明式标题", sections=sections)
        rows = PG.scan_content(platform, text)
        blocks = [r for r in rows if r.get("level") == "block"]
        self.assertEqual(blocks, [], "%s 仍有 block：%s" % (platform, blocks))
        return text

    def test_四个平台LLM不可用也能过闸(self):
        for p in G.SECTION_SLOTS:
            with self.subTest(platform=p):
                self._assert_clean(p, sections=None)

    def test_四个平台用LLM栏目也过闸(self):
        for p in ("cnblogs", "zhihu"):
            with self.subTest(platform=p):
                sec = ["从信号到可消费事实", "订阅怎么随团队变重",
                       "采集侧的幂等与预算", "先卖服务再写软件",
                       "做同类产品前的判断"][:G.SECTION_SLOTS[p]]
                self._assert_clean(p, sections=sec)

    def test_不再有罐头短语和时点warn(self):
        text = self._assert_clean("cnblogs")
        self.assertNotIn("数字带口径带修正", text)
        self.assertNotIn("数据来源：", text)
        rows = PG.scan_content("cnblogs", text)
        self.assertFalse([r for r in rows if r.get("rule") == "数据缺时点"])


class LlmSections(unittest.TestCase):

    def _fake_response(self, lines):
        d = {"choices": [{"message": {"content": "\n".join(lines)}}]}
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = json.dumps(d).encode("utf-8")
        return r

    def test_读不到配置就回退(self):
        with mock.patch.object(G, "_load", side_effect=OSError("no secrets")):
            self.assertEqual(G.llm_sections(CASE, "cnblogs"),
                             G.fallback_sections(CASE, "cnblogs"))

    def test_合格栏目被采用且剥掉序号(self):
        lines = ["1. 从信号到可消费事实", "二、订阅怎么随团队变重",
                 "- 采集侧的幂等与预算", "先卖服务再写软件"]
        with mock.patch.object(G, "_load", return_value={
                "ai_key": "k", "ai_base": "http://x/v1", "ai_model": "m"}), \
                mock.patch.object(G.urllib.request, "urlopen",
                                  return_value=self._fake_response(lines)):
            rows = G.llm_sections(CASE, "cnblogs")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], "从信号到可消费事实")
        self.assertFalse(any(x.startswith(("1.", "二、", "-")) for x in rows))

    def test_模型吐回通用栏目就回退(self):
        lines = ["这个产品是什么", "商业模型怎么搭", "工程上值得抄的点",
                 "为什么能跑起来"]
        with mock.patch.object(G, "_load", return_value={
                "ai_key": "k", "ai_base": "http://x/v1", "ai_model": "m"}), \
                mock.patch.object(G.urllib.request, "urlopen",
                                  return_value=self._fake_response(lines)):
            rows = G.llm_sections(CASE, "cnblogs")
        self.assertEqual(rows, G.fallback_sections(CASE, "cnblogs"))

    def test_行数不够就回退(self):
        with mock.patch.object(G, "_load", return_value={
                "ai_key": "k", "ai_base": "http://x/v1", "ai_model": "m"}), \
                mock.patch.object(G.urllib.request, "urlopen",
                                  return_value=self._fake_response(["只有一行栏目"])):
            rows = G.llm_sections(CASE, "zhihu")
        self.assertEqual(rows, G.fallback_sections(CASE, "zhihu"))


if __name__ == "__main__":
    unittest.main()
