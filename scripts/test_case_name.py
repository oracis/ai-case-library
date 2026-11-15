#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示名金额闸回归测试（PRD #183）。只读，不写盘、不写真库。

钉住两件事：
  1. HN 帖子标题里的成本/售价不会再通过 `name` 混进标题与头条封面；
  2. 入库链路的每一道口子（线索池 / 事实卡 / 候选池 / 写库）都真的接了闸，
     不是只有一次性脚本修过那两条旧数据。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_name as CN              # noqa: E402
import case_store as CS             # noqa: E402
import hn_evidence as HNE           # noqa: E402
import hn_leads as HL               # noqa: E402
import prerender as P               # noqa: E402
import promote_to_candidates as PTC  # noqa: E402

# PRD #182 里被 `_oneoff/fix_hn_name_money.py` 手工修掉的两条真实 HN 标题
TITLE_20K = "A decent text-to-image model trained for ~$20K (Apache 2.0 Weights)"
CLEAN_20K = "A decent text-to-image model (Apache 2.0 Weights)"
TITLE_300 = "My $300 embroidery machine needed a subscription, so I built my own app"
CLEAN_300 = "My embroidery machine needed a subscription, so I built my own app"


class TestClean(unittest.TestCase):
    def test_two_real_hn_titles(self):
        self.assertEqual(CN.clean_display_name(TITLE_20K), CLEAN_20K)
        self.assertEqual(CN.clean_display_name(TITLE_300), CLEAN_300)
        self.assertEqual(CN.money_hits(CLEAN_20K), [])
        self.assertEqual(CN.money_hits(CLEAN_300), [])

    def test_idempotent(self):
        for s in (TITLE_20K, TITLE_300, CLEAN_20K, CLEAN_300):
            once = CN.clean_display_name(s)
            self.assertEqual(CN.clean_display_name(once), once)

    def test_clean_names_untouched(self):
        """没金额的名称一个字节都不改，不能误伤 0 A.D. / 7-Zip / GPT-4 之类。"""
        for s in ("0 A.D.: 古文明 RTS", "7-Zip / GPT-4 / Windows 11",
                  "A decent text-to-image model (Apache 2.0 Weights)",
                  "韦诺之战：六边形战棋的地形与昼夜规则", "Plausible Analytics"):
            self.assertEqual(CN.clean_display_name(s), s)
            self.assertEqual(CN.money_hits(s), [])

    def test_money_variants(self):
        """货币符号 / 中文金额 / MRR 后缀都要剥干净。"""
        for s in ("Show HN: Foo — $5,371.18 MRR", "一个 20 万美元 的开源工具",
                  "月入 5 万 的独立产品", "MRR $3K analytics", "Foo ¥1,000 版",
                  "I put an AI agent on a $7/month VPS with IRC as its transport layer"):
            cleaned = CN.clean_display_name(s)
            self.assertEqual(CN.money_hits(cleaned), [],
                             "%r → %r 仍命中金额" % (s, cleaned))
        # 线索池里真实存在的第 3 条：`mo|month` 交替顺序错了会把 month 削成 nth
        self.assertEqual(
            CN.clean_display_name(
                "I put an AI agent on a $7/month VPS with IRC as its transport layer"),
            "I put an AI agent on a VPS with IRC as its transport layer")

    def test_money_hits_detects_judgement_patterns(self):
        self.assertTrue(CN.money_hits("$300 Foo"))
        self.assertTrue(CN.money_hits("Foo MRR bar"))
        self.assertTrue(CN.money_hits("收入管理助手"))   # 判据里的中文收入词

    def test_assert_clean_name_and_clean_or_raise(self):
        self.assertEqual(CN.assert_clean_name(CLEAN_300), CLEAN_300)
        with self.assertRaises(ValueError) as cm:
            CN.assert_clean_name(TITLE_300, "probe.name")
        self.assertIn("probe.name", str(cm.exception))
        self.assertEqual(CN.clean_or_raise(TITLE_300), CLEAN_300)
        # 剥不干净（名称本身就是收入词）时打回人工，不许代码编一句产品说明
        with self.assertRaises(ValueError):
            CN.clean_or_raise("收入管理助手")


class TestSingleSource(unittest.TestCase):
    def test_prerender_reuses_same_patterns(self):
        """渲染判据与入库判据必须是同一份对象，不许各抄一份正则。"""
        self.assertIs(P.MONEY_PAT, CN.MONEY_PAT)
        self.assertIs(P.MONEY_WORD_PAT, CN.MONEY_WORD_PAT)

    def test_all_ingest_sites_wired(self):
        """源码级回归：四个入库口 + 渲染口都真的引用了闸。"""
        expect = {
            "case_store.py": "_guard_case_name",
            "hn_leads.py": "CN.clean_display_name",
            "hn_evidence.py": "CN.clean_or_raise",
            "promote_to_candidates.py": "CN.clean_or_raise",
            "prerender.py": "_CN.MONEY_PAT",
        }
        for name, token in expect.items():
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn(token, src, "%s 没接展示名金额闸" % name)


class TestIngestChain(unittest.TestCase):
    @staticmethod
    def _hit():
        return {"objectID": "99887", "title": "Show HN: " + TITLE_300,
                "story_text": "", "url": "https://example.com/foo",
                "created_at": "2026-11-15T10:00:00Z",
                "points": 88, "num_comments": 12}

    @staticmethod
    def _ev():
        return {"source_kind": "homepage", "verification": "founder",
                "evidence_url": "https://example.com", "checked_at": "2026-11-15",
                "note": ""}

    def test_to_lead_strips_money_but_keeps_original_title(self):
        lead = HL.to_lead(self._hit())
        self.assertEqual(lead["name"], CLEAN_300)
        self.assertEqual(lead["name_en"], CLEAN_300)
        self.assertEqual(lead["hn_title"], self._hit()["title"])  # 证据不丢
        self.assertEqual(CN.money_hits(lead["name"]), [])

    def test_build_inbox_entry_strips_money(self):
        lead = {"id": "hn-99887", "name": TITLE_300, "name_en": TITLE_300,
                "hn_title": "Show HN: " + TITLE_300, "hn_points": 88,
                "hn_comments": 12, "hn_created_at": "2026-11-15",
                "source_url": "https://example.com", "website": "https://example.com"}
        entry = HNE.build_inbox_entry(lead, self._ev())
        self.assertEqual(entry["name"], CLEAN_300)
        self.assertEqual(entry["name_en"], CLEAN_300)
        self.assertEqual(entry["hn_title"], lead["hn_title"])

    def test_to_candidate_hn_strips_money(self):
        rec = {"id": "hn-99887", "name": TITLE_300, "name_en": TITLE_300,
               "hn_title": "Show HN: " + TITLE_300, "hn_evidence": self._ev(),
               "metrics": {"headline": "收入未披露"}}
        out = PTC.to_candidate_hn(rec)
        self.assertEqual(out["name"], CLEAN_300)
        self.assertEqual(out["hn_title"], rec["hn_title"])

    def test_write_gate_blocks_any_money_name(self):
        """最后一道硬闸：任何调用方带金额写真库都当场报错。"""
        CS._guard_case_name({"id": "ok", "name": CLEAN_300, "name_en": CLEAN_300})
        with self.assertRaises(ValueError):
            CS._guard_case_name({"id": "bad", "name": TITLE_300})
        with self.assertRaises(ValueError):
            CS._guard_case_name({"id": "bad", "name": "Foo", "name_en": TITLE_20K})


if __name__ == "__main__":
    unittest.main(verbosity=2)
