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

import contextlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fit_ready as FR            # noqa: E402
import score_china_fit as CF      # noqa: E402
import score_note_policy as SNP   # noqa: E402
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
        by_id = {c["id"]: c for c in load_cases()}
        for cid in BATCH:
            with self.subTest(cid=cid):
                self.assertIn(cid, SF.DELIVERY,
                              "%s 没有 delivery 判断，solo_fit 会缺一条" % cid)
                score, why = SF.DELIVERY[cid]
                self.assertTrue(1 <= score <= 5)
                # PRD #184：表里第二位恒为空串，理由真值在库里（判据不放松，换到真值处）。
                self.assertEqual(why, SF.NOTE_IN_LIBRARY)
                self.assertTrue(str((by_id[cid].get("solo_fit") or {})
                                    .get("delivery_note") or "").strip(),
                                "%s 的库里 delivery_note 为空" % cid)

    def test_batch_has_china_fit_judgement(self):
        by_id = {c["id"]: c for c in load_cases()}
        for cid in BATCH:
            with self.subTest(cid=cid):
                self.assertIn(cid, CF.SCORES,
                              "%s 没有 china_fit 判断" % cid)
                s = CF.SCORES[cid]
                for key, _label, _desc in CF.DIMS:
                    self.assertIn(key, s, "%s 缺维度 %s" % (cid, key))
                    self.assertTrue(1 <= s[key] <= 5)
                self.assertNotIn("note", s, "%s 的 SCORES 又写回了文案副本" % cid)
                self.assertTrue(str((by_id[cid].get("china_fit") or {})
                                    .get("note") or "").strip(),
                                "%s 的库里 china_fit.note 为空" % cid)

    def test_no_case_missing_judgements(self):
        cases = load_cases()
        self.assertEqual(FR.missing_solo(cases), [])
        self.assertEqual(FR.missing_china(cases), [])


class TestSingleSourceOfTruth(unittest.TestCase):
    """PRD #184：评分文案只允许存在于 case_library，静态表不许再存副本。"""

    def test_tables_have_no_note_copies(self):
        self.assertEqual(sum("note" in s for s in CF.SCORES.values()), 0,
                         "SCORES 里又出现 note 副本")
        self.assertEqual(sum(bool(v[1]) for v in SF.DELIVERY.values()), 0,
                         "DELIVERY 里又出现理由副本")
        self.assertEqual(SF.NOTE_IN_LIBRARY, "")
        # PRD #186：判据已上移成常驻单一源，这里再走一遍确认同源（三条原判据
        # 一条不放宽）。报红就是真污染，别当误报忽略。
        import score_table_guard as guard
        self.assertTrue(guard.table_is_note_free(CF.SCORES, SF.DELIVERY),
                        "常驻判据与本地断言结论不一致，别把报红当误报")

    def test_no_refresh_backdoor(self):
        import inspect
        self.assertNotIn("refresh", inspect.signature(SNP.resolve_note).parameters,
                         "裁定口又开了覆盖后门")
        for name in ("score_china_fit.py", "score_solo_fit.py"):
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("--refresh-notes", src, "%s 又有覆盖开关" % name)
            self.assertNotIn("refresh=args", src, "%s 又传了 refresh" % name)

    def test_library_notes_are_nonempty(self):
        """真值在库里：465 条人工判断的文案都得有内容（不许靠表兜底）。"""
        cases = load_cases()
        human = [c for c in cases
                 if (c.get("china_fit") or {}).get("score") is not None
                 and not (c.get("china_fit") or {}).get("provisional")]
        self.assertGreater(len(human), 400, "样本太少，测试会假绿")
        for c in human:
            with self.subTest(case=c["id"]):
                self.assertTrue((c["china_fit"].get("note") or "").strip())
                self.assertTrue(((c.get("solo_fit") or {}).get("delivery_note") or "").strip())


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
        self.assertEqual(FR.missing_replicability(probe), ["x", "y"])

    def test_旧维度键名不算齐(self):
        """校验必须与 score_solo_fit 的 INVERT 一致，不能只判非空。

        2026-10-05 实测：harperai / easymix 晋升时写的是旧键名
        {tech, data, sales, domain}，非空 → --check 报「缺 0 条」，
        而 score_solo_fit 要 {tech, capital, distribution, timing} →
        算不出 solo_fit、composite 少 2 条、测试挂 4 项。
        校验工具报「齐」而下游说「缺」，比直接报错更坏：它让人以为不用管。
        """
        old_keys = [{"id": "x", "replicability": {"tech": 3, "data": 2,
                                                  "sales": 3, "domain": 3}}]
        self.assertEqual(FR.missing_replicability(old_keys), ["x"],
                         "旧维度键名的条目必须被判为缺失")
        full = [{"id": "y", "replicability": {"tech": 3, "capital": 2,
                                             "distribution": 3, "timing": 2}}]
        self.assertEqual(FR.missing_replicability(full), [])

    def test_维度值不是int也算缺(self):
        probe = [{"id": "x", "replicability": {"tech": "3", "capital": 2,
                                                "distribution": 3, "timing": 2}}]
        self.assertEqual(FR.missing_replicability(probe), ["x"],
                         "字符串数字会让 score_solo_fit 静默跳过，必须提前报出来")

    def test_missing_detects_only_when_no_stored_score(self):
        """已有 solo_fit 的案例不该再被点名 —— 判断表可以后补。"""
        probe = [{"id": "x", "solo_fit": {"score": 50}}]
        self.assertEqual(FR.missing_solo(probe), [])
        probe2 = [{"id": "x"}]
        self.assertEqual(FR.missing_solo(probe2), ["x"])


class TestRelaxedJudgement(unittest.TestCase):
    """2026-11-06 放宽两张人工表（用户：「这两张表放宽」）。

    放宽的前提是**不丢信息**，所以这里钉三条：
      1. 缺表也要算得分（以前 score_china_fit 缺表 `continue` + 末尾 pop，
         一跑就把未填案例的旧分清空；score_solo_fit 缺表直接跳过、不进榜）
      2. 兜底分必须带 `provisional` 与「未人工评分」字样，不许冒充人工判断
      3. 库里已有真实判断（表里没写）必须原样保留，兜底不能盖掉它
    """

    def test_fallback_dims_are_sane(self):
        fb = CF.fallback_dims()
        for key, _label, _desc in CF.DIMS:
            with self.subTest(dim=key):
                self.assertIsInstance(fb[key], int)
                self.assertTrue(1 <= fb[key] <= 5, "%s=%s 越界" % (key, fb[key]))

    def test_fallback_delivery_is_sane(self):
        score, why = SF.fallback_delivery()
        self.assertIsInstance(score, int)
        self.assertTrue(1 <= score <= 5, score)
        self.assertIn("未人工评分", why)

    def _run(self, mod, cases, argv=None):
        """用假案例跑一遍 main()，拿回写出的 cases。"""
        saved = {}
        old = (mod.load_json, mod.save_json, list(sys.argv))
        mod.load_json = lambda name, default=None: cases if name == "cases" else default
        mod.save_json = lambda name, payload: saved.__setitem__(name, payload)
        sys.argv = argv or ["score"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mod.main()
        finally:
            mod.load_json, mod.save_json = old[0], old[1]
            sys.argv = old[2]
        self.assertEqual(rc, 0)
        return saved["cases"]

    def test_missing_table_still_scores(self):
        probe = [{"id": "__probe_relax__", "name": "Probe",
                  "replicability": {"tech": 3, "distribution": 3,
                                    "capital": 3, "timing": 3}}]
        cases = self._run(CF, probe)      # china 先写，solo 才能算出 composite
        cases = self._run(SF, cases)
        c = cases[0]

        self.assertTrue(c.get("china_fit"), "缺 SCORES 也应该算出 china_fit")
        self.assertTrue(c["china_fit"].get("provisional"))
        self.assertIn("未人工评分", c["china_fit"]["note"])

        self.assertTrue(c.get("solo_fit"), "缺 DELIVERY 也应该算出 solo_fit")
        self.assertTrue(c["solo_fit"].get("provisional"))
        self.assertIn("未人工评分", c["solo_fit"]["delivery_note"])
        self.assertTrue(c.get("composite"), "双轴分也该算出来")

    def test_stored_judgement_not_overwritten(self):
        """表里没有、库里已有人工判断 ⇒ 保留原值，不许被兜底盖掉。"""
        probe = [{"id": "__probe_keep__", "name": "ProbeKeep",
                  "replicability": {"tech": 3, "distribution": 3,
                                    "capital": 3, "timing": 3},
                  "china_fit": {"score": 88.8, "raw": 30.6, "note": "人工判断",
                                "dims": {k: 5 for k, _l, _d in CF.DIMS},
                                "scored_at": "2026-01-01"},
                  "solo_fit": {"score": 77.7, "raw": 22.5, "delivery_note": "人工判断",
                               "dims": {"delivery": 5}, "scored_at": "2026-01-01"}}]
        cases = self._run(CF, probe)
        c = cases[0]
        self.assertEqual(c["china_fit"]["score"], 88.8)
        self.assertEqual(c["china_fit"]["note"], "人工判断")
        self.assertNotIn("provisional", c["china_fit"])

        cases = self._run(SF, cases)
        c = cases[0]
        self.assertEqual(c["solo_fit"]["score"], 77.7)
        self.assertEqual(c["solo_fit"]["delivery_note"], "人工判断")
        self.assertNotIn("provisional", c["solo_fit"])

    # ---------------------------------------------------------- PRD #183
    # 2026-11-15：上一轮补 18 条新案例时跑分，SCORES / DELIVERY 表里的静态
    # 旧文案整段盖掉了库里 11-14 刷新过的 note（52 处数字回退）。上面那个
    # `test_stored_judgement_not_overwritten` 只覆盖「表里没有」的分支，
    # 下面这组钉住「表里也有」的分支。

    @staticmethod
    def _table_id():
        return next(iter(sorted(set(CF.SCORES) & set(SF.DELIVERY))))

    def _note_probe(self, cid, note, delivery_note):
        return [{"id": cid, "name": "ProbeNote",
                 "replicability": {"tech": 3, "distribution": 3,
                                   "capital": 3, "timing": 3},
                 "china_fit": {"score": 88.8, "raw": 30.6, "note": note,
                               "dims": {k: 5 for k, _l, _d in CF.DIMS},
                               "scored_at": "2026-01-01"},
                 "solo_fit": {"score": 77.7, "raw": 22.5,
                              "delivery_note": delivery_note,
                              "dims": {"delivery": 5},
                              "scored_at": "2026-01-01"}}]

    def test_initial_note_never_overwrites_stored_note(self):
        """库里刷新过的人工文案，不许被中位数兜底初值盖掉。"""
        cid = self._table_id()
        cases = self._run(CF, self._note_probe(cid, "库里刷新过的人工判断",
                                               "库里刷新过的人工理由"))
        self.assertEqual(cases[0]["china_fit"]["note"], "库里刷新过的人工判断")
        self.assertNotIn("未人工评分", cases[0]["china_fit"]["note"])
        cases = self._run(SF, cases)
        self.assertEqual(cases[0]["solo_fit"]["delivery_note"],
                         "库里刷新过的人工理由")
        self.assertNotIn("未人工评分", cases[0]["solo_fit"]["delivery_note"])

    def test_no_flag_can_overwrite_stored_note(self):
        """#184 后连开关都没了：误传 --refresh-notes 只会被 argparse 拒绝。"""
        cid = self._table_id()
        argv = ["score", "--refresh-notes"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self._run(CF, self._note_probe(cid, "库里刷新过的人工判断",
                                           "库里刷新过的人工理由"), argv=argv)

    def test_new_case_uses_provisional_initial_note(self):
        """库里没判断的新案例，拿中位数兜底说明当初值（表里已无副本可抄）。"""
        cid = self._table_id()
        probe = [{"id": cid, "name": "ProbeNew",
                  "replicability": {"tech": 3, "distribution": 3,
                                    "capital": 3, "timing": 3}}]
        cases = self._run(CF, probe)
        self.assertEqual(cases[0]["china_fit"]["note"],
                         CF.fallback_note(CF.fallback_dims()))
        self.assertIn("未人工评分", cases[0]["china_fit"]["note"])
        cases = self._run(SF, cases)
        self.assertEqual(cases[0]["solo_fit"]["delivery_note"],
                         SF.fallback_delivery()[1])
        self.assertIn("未人工评分", cases[0]["solo_fit"]["delivery_note"])

    def test_note_policy_pure_function(self):
        """裁定口本身：人工 / 兜底 / 空文案 / 未知段四种边界。"""
        human = {"score": 80, "note": "库里"}
        self.assertEqual(SNP.resolve_note({"china_fit": human}, "china_fit", "初值"),
                         ("库里", True))
        prov = {"score": 80, "provisional": True, "note": "中位数兜底"}
        self.assertEqual(SNP.resolve_note({"china_fit": prov}, "china_fit", "初值"),
                         ("初值", False))
        self.assertEqual(SNP.resolve_note({}, "china_fit", "初值"), ("初值", False))
        self.assertEqual(SNP.resolve_note(
            {"solo_fit": {"score": 70, "delivery_note": "库里理由"}},
            "solo_fit", "初值理由"), ("库里理由", True))
        self.assertEqual(SNP.resolve_note(
            {"china_fit": {"score": 80, "note": "a**b"}}, "china_fit", "初值"),
            ("ab", True))
        self.assertEqual(SNP.resolve_note(
            {"china_fit": {"score": 80}}, "china_fit", "初值"), ("初值", False))
        with self.assertRaises(KeyError):
            SNP.resolve_note({}, "not_a_section", "x")

    def test_score_scripts_delegate_to_note_policy(self):
        """源码级回归：两个跑分脚本都走裁定口，且都不再读表里的文案。"""
        for name in ("score_china_fit.py", "score_solo_fit.py"):
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("SNP.resolve_note", src, "%s 绕开了文案裁定口" % name)
            self.assertNotIn("refresh=args", src, "%s 又在传覆盖开关" % name)
            self.assertNotIn('s.get("note"', src, "%s 又在读表里的 note" % name)

    def test_real_ledger_notes_survive_a_full_rescore(self):
        """拿全库真数据在内存里重跑一遍评分，465 条人工 note 一字不许变。

        这是 PRD #182 那次「跑分洗掉 52 处文案」的直接回归：上面几个 probe
        只验证了规则，这里验证它覆盖到每一条真实数据。全程 mock 掉写盘。
        """
        import copy
        raw = load_cases()
        before_c, before_s = {}, {}
        for c in raw:
            cf = c.get("china_fit") or {}
            sf = c.get("solo_fit") or {}
            if cf.get("score") is not None and not cf.get("provisional") and cf.get("note"):
                before_c[c["id"]] = cf["note"]
            if (sf.get("score") is not None and not sf.get("provisional")
                    and sf.get("delivery_note")):
                before_s[c["id"]] = sf["delivery_note"]
        self.assertGreater(len(before_c), 400, "样本太少，测试会假绿")
        self.assertGreater(len(before_s), 400, "样本太少，测试会假绿")

        cases = self._run(CF, copy.deepcopy(raw))
        for c in cases:
            if c["id"] in before_c:
                self.assertEqual((c.get("china_fit") or {}).get("note"),
                                 before_c[c["id"]], c["id"])
        cases = self._run(SF, cases)
        for c in cases:
            if c["id"] in before_s:
                self.assertEqual((c.get("solo_fit") or {}).get("delivery_note"),
                                 before_s[c["id"]], c["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
