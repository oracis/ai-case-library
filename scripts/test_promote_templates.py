#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晋升模板文案回归测试（2026-11-05）。

锁住三处修过的坑，防止改回旧模板：
  1. `build_metric_note` 尾句在「队列缓存值 == 本次核对值」时不再硬说
     「与上述实测不同」（当天采集当天晋升的条目 4/4 误报）；
  2. `metrics.headline` 不再写「实时核对」（官方 .md 是 Cached snapshot），
     但必须保留「TrustMRR … 核对」锚点，让下游四个清理判据继续工作；
  3. `note` 不再冒名 `audit_delisted.py`，并写明缓存快照属性。

纯函数测试：不读不写真台账，`run_all_tests.py` 的指纹校验安全。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clean_title_caliber as C  # noqa: E402
import make_article as A  # noqa: E402
import promote_to_candidates as P  # noqa: E402

SYNCED = "2026-11-05T04:00:12.787Z"


def make_metrics(mrr=3929.0, last30=3865.0):
    return {
        "mrr": mrr,
        "last_30d_revenue": last30,
        "all_time": 29861.0,
        "subscriptions": 7,
        "mrr_growth_30d": 61.4,
        "revenue_growth_30d": 14.5,
    }


def make_rec(mrr=3929.0, last30=3865.0, added=True):
    return {
        "id": "sample-case",
        "name": "Sample Case",
        "trustmrr_slug": "sample-case",
        "source_url": "https://trustmrr.com/startup/sample-case",
        "website": "https://example.com/",
        "harvest_source": "trustmrr",
        "source_kind": "verified",
        "metrics": make_metrics(mrr, last30),
        "added_at": "2026-11-05" if added else None,
    }


def make_live():
    return {
        "state": "live",
        "synced_at": SYNCED,
        "verified_via": "Stripe (API key)",
        "monthly": [],
        "metrics": make_metrics(),
    }


class PromoteTemplateTests(unittest.TestCase):
    """类名刻意带 Templates，避免与既有测试类同名被静默遮蔽。"""

    def test_same_value_says_consistent_not_different(self):
        note = P.build_metric_note(make_rec(), make_live(), make_metrics(), 0.614)
        self.assertNotIn("与上述实测不同", note)
        self.assertIn("一致", note)
        self.assertIn("$3,929", note)
        self.assertIn("缓存快照", note)
        self.assertIn("不是实时值", note)
        self.assertIn("2026-11-05", note)

    def test_different_value_still_flags_both_numbers(self):
        note = P.build_metric_note(
            make_rec(mrr=4598.0), make_live(), make_metrics(mrr=4478.0), -0.059)
        self.assertIn("两者不同", note)
        self.assertIn("$4,598", note)
        self.assertIn("$4,478", note)
        self.assertNotIn("与上述实测不同", note)

    def test_no_old_value_keeps_first_fill_wording(self):
        rec = make_rec()
        rec["metrics"]["mrr"] = None
        rec["metrics"]["last_30d_revenue"] = None
        note = P.build_metric_note(rec, make_live(), make_metrics(), None)
        self.assertIn("首次补齐", note)
        self.assertNotIn("与上述实测不同", note)

    def test_non_subscription_compares_last30_not_mrr(self):
        rec = make_rec(mrr=0, last30=2148.0)
        m = make_metrics(mrr=0, last30=2148.0)
        note = P.build_metric_note(rec, make_live(), m, None)
        self.assertIn("一致", note)
        self.assertIn("$2,148", note)

    def test_headline_drops_realtime_but_keeps_cleanable_anchor(self):
        out = P.to_candidate(make_rec(), make_live(), make_metrics(), 0.614)
        hl = out["metrics"]["headline"]
        self.assertNotIn("实时", hl)
        self.assertIn("TrustMRR 核对", hl)
        # 下游四个判据里 (实时)? 都是可选组 —— 新串仍须被清成纯金额
        self.assertEqual(A._clean_title_hook(hl), "MRR $3,929")
        self.assertTrue(C.CALIBER.search(hl))

    def test_non_subscription_headline_still_cleanable(self):
        m = make_metrics(mrr=0, last30=2148.0)
        out = P.to_candidate(make_rec(mrr=0, last30=2148.0),
                             make_live(), m, None)
        hl = out["metrics"]["headline"]
        self.assertNotIn("实时", hl)
        self.assertEqual(A._clean_title_hook(hl), "近 30 天收入 $2,148")
        self.assertTrue(C.CALIBER.search(hl))

    def test_note_no_false_provenance_and_states_snapshot(self):
        out = P.to_candidate(make_rec(), make_live(), make_metrics(), 0.614)
        self.assertNotIn("audit_delisted", out["note"])
        self.assertNotIn("/startup/<slug>.md", out["note"])
        self.assertIn("支付服务商 API 直读", out["note"])
        self.assertIn("缓存快照", out["note"])
        self.assertIn("引用须带时点与口径", out["note"])

    def test_metric_note_of_same_value_snapshot_is_in_candidate(self):
        out = P.to_candidate(make_rec(), make_live(), make_metrics(), 0.614)
        self.assertIn("一致", out["metrics"]["metric_note"])
        self.assertNotIn("与上述实测不同", out["metrics"]["metric_note"])

    def test_candidate_verification_is_provenance_not_evidence_grade(self):
        """候选池 verification 是「支付侧直读」标记，不从来源记录继承证据等级。

        2026-11-05 用户确认：candidates.json 的 stripe 与 verifications.json 的
        partial 是两套语义、两个写入者，不互相覆盖；晋升时才由草稿等级落到
        cases.json。这里钉住 to_candidate() 不搬 rec 里的 verification。
        """
        rec = make_rec()
        rec["verification"] = "partial"
        out = P.to_candidate(rec, make_live(), make_metrics(), 0.614)
        self.assertEqual(out["verification"], "stripe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
