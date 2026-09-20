#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fit_ready 的回归测试。

2026-09-20 发布 5 条新案例后，test_make_article 有 2 项失败：
每篇公众号草稿都该有的「能不能搬回国内」「一个人能不能做」两段是空的 ——
因为新案例缺 china_fit / solo_fit，而那两套判断表没跟着发布一起填。

这个缺口不会自己暴露成报错，只会让文章静默掉两段、让首页的适配度视图少人。
所以在这里钉住三件事：

  1. 已发布案例不许缺 replicability —— 它没有任何脚本生成，最容易漏
  2. DELIVERY / SCORES 表里给新案例补的判断，必须覆盖它们
  3. 评分脚本本身要能对全部案例算到底（缺判断时它会实打实报出来）

只读，不写盘。
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fit_ready as FR            # noqa: E402
import score_china_fit as CF      # noqa: E402
import score_solo_fit as SF       # noqa: E402

# 2026-09-20 发布的那一批
BATCH = ("1lookup", "stan", "magicslides-app", "autoreels-ai", "insect-bite-id")


def load_cases():
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        return json.load(f)


class TestReplicability(unittest.TestCase):

    def test_every_case_has_replicability(self):
        """replicability 是 solo_fit 的输入，缺了它 solo_fit 直接算不出来。"""
        missing = FR.missing_replicability(load_cases())
        self.assertEqual(missing, [], "这些案例缺 replicability：%s" % missing)

    def test_four_dims_in_range(self):
        for c in load_cases():
            rep = c.get("replicability") or {}
            for dim in ("tech", "distribution", "capital", "timing"):
                with self.subTest(case=c.get("id"), dim=dim):
                    v = rep.get(dim)
                    self.assertIsInstance(v, int)
                    self.assertTrue(1 <= v <= 5, "%s.%s=%s 越界" % (c["id"], dim, v))

    def test_batch_is_registered(self):
        for cid in BATCH:
            with self.subTest(cid=cid):
                self.assertIn(cid, FR.REPLICABILITY)


class TestDeliveryAndChinaFit(unittest.TestCase):

    def test_batch_has_delivery_judgement(self):
        for cid in BATCH:
            with self.subTest(cid=cid):
                self.assertIn(cid, SF.DELIVERY,
                              "%s 没有 delivery 判断，solo_fit 会缺一条" % cid)
                score, why = SF.DELIVERY[cid]
                self.assertTrue(1 <= score <= 5)
                self.assertTrue(str(why).strip(), "%s 的 delivery 没写理由" % cid)

    def test_batch_has_china_fit_judgement(self):
        for cid in BATCH:
            with self.subTest(cid=cid):
                self.assertIn(cid, CF.SCORES,
                              "%s 没有 china_fit 判断" % cid)
                s = CF.SCORES[cid]
                for key, _label, _desc in CF.DIMS:
                    self.assertIn(key, s, "%s 缺维度 %s" % (cid, key))
                    self.assertTrue(1 <= s[key] <= 5)
                self.assertTrue(str(s.get("note") or "").strip(),
                                "%s 的 china_fit 没写依据" % cid)

    def test_no_case_missing_judgements(self):
        cases = load_cases()
        self.assertEqual(FR.missing_solo(cases), [])
        self.assertEqual(FR.missing_china(cases), [])


class TestScoredOutput(unittest.TestCase):
    """算完的分数要落在案例上 —— 否则 make_article 那两段还是空的。"""

    def test_batch_has_all_derived_scores(self):
        by_id = {c["id"]: c for c in load_cases()}
        for cid in BATCH:
            c = by_id[cid]
            with self.subTest(cid=cid):
                self.assertTrue(c.get("solo_fit"), "%s 缺 solo_fit" % cid)
                self.assertTrue(c.get("china_fit"), "%s 缺 china_fit" % cid)
                self.assertTrue(c.get("composite"), "%s 缺 composite" % cid)

    def test_scores_are_normalised(self):
        for c in load_cases():
            for key in ("solo_fit", "china_fit", "composite"):
                blk = c.get(key) or {}
                if not blk:
                    continue
                with self.subTest(case=c.get("id"), key=key):
                    self.assertTrue(0 <= blk.get("score", -1) <= 100,
                                    "%s.%s 不在 0-100" % (c["id"], key))

    def test_solo_fit_reverses_replicability(self):
        """build/reach/capital/window 由 replicability 反推，两者不能打架。

        replicability 是 1=最容易，solo_fit 是 5=最好做，方向相反 ——
        这条断言就是在防「有人只改了一边」。
        """
        for c in load_cases():
            rep = c.get("replicability") or {}
            solo = (c.get("solo_fit") or {}).get("dims") or {}
            if not rep or not solo:
                continue
            pairs = [("build", "tech"), ("reach", "distribution"),
                     ("capital", "capital"), ("window", "timing")]
            for s_key, r_key in pairs:
                with self.subTest(case=c.get("id"), dim=s_key):
                    self.assertEqual(solo.get(s_key), 6 - rep.get(r_key),
                                     "%s: solo.%s 与 replicability.%s 方向不一致"
                                     % (c["id"], s_key, r_key))


class TestCheckReportsGaps(unittest.TestCase):
    """--check 得真能报出缺口，不能永远说「没事」。"""

    def test_missing_replicability_detects(self):
        probe = [{"id": "x"}, {"id": "y", "replicability": {"tech": 1}}]
        self.assertEqual(FR.missing_replicability(probe), ["x"])

    def test_missing_detects_only_when_no_stored_score(self):
        """已有 solo_fit 的案例不该再被点名 —— 判断表可以后补。"""
        probe = [{"id": "x", "solo_fit": {"score": 50}}]
        self.assertEqual(FR.missing_solo(probe), [])
        probe2 = [{"id": "x"}]
        self.assertEqual(FR.missing_solo(probe2), ["x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
