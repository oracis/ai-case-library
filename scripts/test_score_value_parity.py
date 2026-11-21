#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分「数值」一致性判据的回归测试（PRD #185）。

为什么需要这道闸
----------------
#184 把**文案**统一到「库是唯一真值」，但**数值**方向相反：
SCORES / DELIVERY 是人工判断的源头，库里的 china_fit / solo_fit 是派生结果。
方向不同，不能照搬删表；可此前全仓没有任何脚本核对「表改了没重跑」或
「库被手改了」，只能靠人肉对账 —— 这就是 #184 收尾时留下的隐患。

这里钉住四件事：
  1. 真库逐键一致（465 条表判断 + 124 条中位数兜底，0 漂移、0 幽灵；
     2026-11-21 库 564→589 后按真值更新计数，判据结构不变）
  2. 判据真能抓到漂移（幽灵条目 / 数值被改 / provisional 矛盾 / 兜底值漂移）
  3. 11-06 口径不许绕过：缺表案例必须走中位数兜底并标 provisional
  4. 兜底文案的样本数必须诚实，库外 id 不得混进中位数统计

只读，不写盘。cases 一律从 data/cases.json 读入后传参，不碰 SQLite。
"""

import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import score_china_fit as CF       # noqa: E402
import score_solo_fit as SF        # noqa: E402
import score_value_parity as SVP   # noqa: E402


def load_cases():
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        d = json.load(f)
    return d["cases"] if isinstance(d, dict) else d


def kinds(result, kind):
    return [e for e in result["errors"] if e["kind"] == kind]


class TestRealLibrary(unittest.TestCase):
    """真库回归：判据必须对当前 589 条全绿。

    2026-11-21：库从 564 → 589（新增 25 条案例，先跑 score_china_fit 再跑
    score_solo_fit 补齐评分）。这里只更新**事实性计数** 564→589 / 99→124，
    判据结构一条不放宽：表↔库逐键一致、兜底等于当前中位数、两轴 provisional
    集合一致全都照旧。新增的 25 条都是缺表中位数兜底（provisional），
    不是人工判断 —— `table_scored` 仍是 465。
    """

    def test_real_library_is_clean(self):
        r = SVP.audit(load_cases())
        self.assertTrue(r["ok"], "真库报了数值不同步：%s" % r["errors"][:5])
        self.assertEqual(r["errors"], [])

    def test_counts_match_11_06_policy(self):
        """465 条人工表判断 + 124 条中位数兜底，两轴 provisional 集合一致。"""
        r = SVP.audit(load_cases())
        s = r["stats"]
        self.assertEqual(s["cases"], 589)
        self.assertEqual(s["table_scored"], 465)
        self.assertEqual(s["fallback_provisional"], 124)
        self.assertEqual(s["provisional_china"], 124)
        self.assertEqual(s["provisional_solo"], 124)
        self.assertEqual(s["preserved"], 0)

    def test_tables_have_no_ghost_and_same_ids(self):
        """#185：删掉 private-venture-1 后两表必须同集合、且都在库内。"""
        K = {c["id"] for c in load_cases()}
        S, D = set(CF.SCORES), set(SF.DELIVERY)
        self.assertEqual(S ^ D, set(), "两张判断表 id 集合不一致")
        self.assertEqual(S - K, set(), "SCORES 里有库外幽灵条目")
        self.assertEqual(D - K, set(), "DELIVERY 里有库外幽灵条目")
        self.assertNotIn("private-venture-1", D)

    def test_fallback_sample_count_is_honest(self):
        """兜底文案写的样本数必须等于生成器实际统计的表条目数。"""
        r = SVP.audit(load_cases())
        self.assertEqual(kinds(r, "fallback_sample_count_china"), [])
        self.assertEqual(kinds(r, "fallback_sample_count_solo"), [])
        self.assertIn("全库 %d 条" % len(CF.SCORES),
                      CF.fallback_note(CF.fallback_dims()))
        self.assertIn("全库 %d 条" % len(SF.DELIVERY),
                      SF.fallback_delivery()[1])

    def test_ghost_does_not_change_median(self):
        """删幽灵只改样本数、不改中位数分值 ⇒ 兜底分值零影响。"""
        self.assertEqual(SF.fallback_delivery()[0], 4)
        self.assertEqual(CF.to_score(CF.weighted_total(CF.fallback_dims())), 60.6)


class TestDetectsDrift(unittest.TestCase):
    """判据必须真能抓到问题 —— 不能只会说「没事」。"""

    def setUp(self):
        self.cases = load_cases()

    def _first_table_case(self):
        for c in self.cases:
            if c["id"] in CF.SCORES and c["id"] in SF.DELIVERY:
                return c
        self.fail("库里没有带人工判断的案例")

    def _first_fallback_case(self):
        for c in self.cases:
            if (c.get("china_fit") or {}).get("provisional"):
                return c
        self.fail("库里没有走中位数兜底的案例")

    def test_ghost_entry_is_caught(self):
        D = dict(SF.DELIVERY)
        D["__ghost_probe__"] = (5, "")
        r = SVP.audit(self.cases, delivery=D)
        self.assertFalse(r["ok"])
        self.assertEqual([e["case_id"] for e in kinds(r, "ghost_in_delivery")],
                         ["__ghost_probe__"])
        # 幽灵同时让「只在 DELIVERY 里」的覆盖错位现形（该 id 不在库内，
        # ① 抓幽灵、② 只查库内交集，所以这里不该额外报覆盖错位）
        self.assertEqual(kinds(r, "table_coverage_mismatch"), [])

    def test_china_value_drift_is_caught(self):
        cases = copy.deepcopy(self.cases)
        c = next(x for x in cases if x["id"] == self._first_table_case()["id"])
        c["china_fit"]["dims"]["demand"] += 1
        r = SVP.audit(cases)
        self.assertFalse(r["ok"])
        self.assertTrue(kinds(r, "china_value_drift"),
                        "改了一维 dims 却没报 china_value_drift")

    def test_solo_score_drift_is_caught(self):
        cases = copy.deepcopy(self.cases)
        c = next(x for x in cases if x["id"] == self._first_table_case()["id"])
        c["solo_fit"]["score"] = 12.3
        r = SVP.audit(cases)
        self.assertFalse(r["ok"])
        self.assertTrue(kinds(r, "solo_value_drift"))

    def test_provisional_with_table_is_caught(self):
        """表里有人工判断却标 provisional —— 两轴口径撕裂，必须报错。"""
        cases = copy.deepcopy(self.cases)
        c = next(x for x in cases if x["id"] == self._first_table_case()["id"])
        c["china_fit"]["provisional"] = True
        c["solo_fit"]["provisional"] = True
        r = SVP.audit(cases)
        self.assertTrue(kinds(r, "provisional_with_table_china"))
        self.assertTrue(kinds(r, "provisional_with_table_solo"))

    def test_fallback_drift_is_caught(self):
        """缺表案例的兜底值必须等于当前中位数，被手改了要报。"""
        cases = copy.deepcopy(self.cases)
        c = next(x for x in cases if x["id"] == self._first_fallback_case()["id"])
        c["china_fit"]["score"] = 99.9
        c["solo_fit"]["score"] = 1.1
        r = SVP.audit(cases)
        self.assertTrue(kinds(r, "fallback_drift_china"))
        self.assertTrue(kinds(r, "fallback_drift_solo"))

    def test_provisional_set_mismatch_is_caught(self):
        cases = copy.deepcopy(self.cases)
        c = next(x for x in cases if x["id"] == self._first_fallback_case()["id"])
        c["china_fit"].pop("provisional", None)
        r = SVP.audit(cases)
        self.assertTrue(kinds(r, "provisional_set_mismatch"))

    def test_table_coverage_mismatch_is_caught(self):
        S = dict(CF.SCORES)
        cid = self._first_table_case()["id"]
        S.pop(cid)
        r = SVP.audit(self.cases, scores=S)
        self.assertTrue(kinds(r, "table_coverage_mismatch"))

    def test_strict_fallback_can_be_relaxed_but_ghost_still_caught(self):
        """关掉兜底严查只是不查分值，幽灵/覆盖错位这类结构性错误仍要报。"""
        D = dict(SF.DELIVERY)
        D["__ghost_probe__"] = (5, "")
        r = SVP.audit(self.cases, delivery=D, strict_fallback=False)
        self.assertTrue(kinds(r, "ghost_in_delivery"))


class TestPolicy11_06(unittest.TestCase):
    """11-06 已裁定口径：缺表走中位数兜底，不许补成「人工判断」。"""

    def test_fallback_cases_are_provisional_and_not_in_tables(self):
        cases = load_cases()
        prov = [c for c in cases if (c.get("china_fit") or {}).get("provisional")]
        # 2026-11-21：库 564→589，新增 25 条同为缺表兜底 ⇒ 99→124。
        self.assertEqual(len(prov), 124)
        for c in prov:
            cid = c["id"]
            self.assertNotIn(cid, CF.SCORES, "%s 不该有人工 china 判断" % cid)
            self.assertNotIn(cid, SF.DELIVERY, "%s 不该有人工 delivery 判断" % cid)
            self.assertTrue((c.get("solo_fit") or {}).get("provisional"))
            self.assertTrue(c.get("composite"), "%s 缺 composite" % cid)

    def test_fallback_values_equal_current_median(self):
        """兜底是统计量，不是人工结论；值必须跟着当前中位数。"""
        cases = load_cases()
        exp_c = SVP.expected_fallback_china()
        fd = SF.fallback_delivery()
        prov = [c for c in cases if (c.get("china_fit") or {}).get("provisional")]
        for c in prov:
            cf, sf = c["china_fit"], c["solo_fit"]
            for k, v in exp_c.items():
                self.assertEqual(cf.get(k), v, "%s china %s 不符" % (c["id"], k))
            exp_s = SVP.expected_fallback_solo(c, fd)
            self.assertIsNotNone(exp_s)
            for k, v in exp_s.items():
                self.assertEqual(sf.get(k), v, "%s solo %s 不符" % (c["id"], k))

    def test_deprecating_fallback_without_table_is_error(self):
        """把 provisional 去掉但分数仍是兜底值 ⇒ 判据要当作漂移/错位抓住。"""
        cases = copy.deepcopy(load_cases())
        c = next(x for x in cases if (x.get("china_fit") or {}).get("provisional"))
        c["solo_fit"].pop("provisional", None)
        r = SVP.audit(cases)
        self.assertTrue(kinds(r, "provisional_set_mismatch"))


class TestCliAndWiring(unittest.TestCase):
    """判据要能被常驻入口调到，不是只活在单测里。"""

    def test_cli_check_rc_zero(self):
        import subprocess
        # run_all_tests 会给本进程注入 LEDGER_DB=tmp/test_ledger.db；子进程默认
        # 继承它就会去读播种库而不是真值库，容易假绿。显式剥掉覆盖变量，
        # 让 CLI 读默认 data/ledger.db（--check 全程只读，不写盘）。
        env = {k: v for k, v in os.environ.items()
               if k not in ("LEDGER_DB", "CASE_MIRROR")}
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "score_value_parity.py"),
             "--check"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("[OK]", p.stdout)

    def test_fit_ready_exposes_parity_check(self):
        """fit_ready --check 是发布前体检口，必须带上数值一致性读数。"""
        with open(os.path.join(ROOT, "scripts", "fit_ready.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("score_value_parity", src,
                      "fit_ready --check 没接数值对账判据")

    def test_no_write_calls_in_parity_module(self):
        """判据只读：不许出现 save_cases / save_json 等写盘调用。"""
        with open(os.path.join(ROOT, "scripts", "score_value_parity.py"),
                  encoding="utf-8") as f:
            src = f.read()
        for banned in ("save_cases(", "save_json(", "open(", "os.replace("):
            self.assertNotIn(banned, src, "判据里出现写盘调用 %s" % banned)


if __name__ == "__main__":
    unittest.main()
