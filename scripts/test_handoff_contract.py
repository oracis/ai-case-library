#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""handoff_contract.precheck 的准入契约回归锁（不联网、不碰 data）。

锁住「候选 → 案例」(②→③) 三道闸的顺序与判据，以及纯函数契约：
  · 闸 1：候选存在 + 未进案例库（幂等）；
  · 闸 2：有核实草稿 + VR.evaluate() publishable；
  · 闸 3：VR.content_gate() 正文四硬字段齐；
  · 拒绝时用 gates_failed / missing / blocked 的 label 拼原因；
  · 不改入参、不写盘（用 mock 替掉两个 VR 依赖）。
"""
import copy
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import handoff_contract as H  # noqa: E402
import verify_rules as VR     # noqa: E402


def _pass_result():
    return {"publishable": True, "tier": "B", "gates_failed": [], "missing": []}


def _cand(cid="c1"):
    return {"id": cid, "name": "Demo", "metrics": {"mrr": 100}}


class TestPrecheckGates(unittest.TestCase):
    def test_gate1_missing_candidate(self):
        self.assertEqual(
            H.precheck("gone", [_cand()], {"gone": {}}, set()),
            (None, None, "候选不存在（可能已晋升）"))

    def test_gate1_already_in_cases(self):
        # 幂等：已在案例库，即便其它条件都满足也不许再晋升
        self.assertEqual(
            H.precheck("c1", [_cand()], {"c1": {}}, {"c1"}),
            (None, None, "已在 cases.json"))

    def test_gate2_no_verification_draft(self):
        self.assertEqual(
            H.precheck("c1", [_cand()], {}, set()),
            (None, None, "没有核实草稿（先跑 verify_cookie.py）"))

    def test_gate2_evaluate_failed_with_gates(self):
        result = {"publishable": False, "gates_failed": [{"label": "官网确认"}],
                  "missing": [{"label": "不该显示"}]}
        with mock.patch.object(VR, "evaluate", return_value=result):
            got = H.precheck("c1", [_cand()], {"c1": {"d": 1}}, set())
        self.assertIsNone(got[0])
        self.assertEqual(got[2], "规则引擎不过：官网确认")
        self.assertNotIn("不该显示", got[2])  # gates_failed 优先于 missing

    def test_gate2_evaluate_failed_with_missing(self):
        result = {"publishable": False, "gates_failed": [],
                  "missing": [{"label": "截图"}, {"label": "定价页"}]}
        with mock.patch.object(VR, "evaluate", return_value=result):
            got = H.precheck("c1", [_cand()], {"c1": {"d": 1}}, set())
        self.assertEqual(got[2], "规则引擎不过：截图；定价页")

    def test_gate3_content_gate_failed(self):
        with mock.patch.object(VR, "evaluate", return_value=_pass_result()), \
             mock.patch.object(VR, "content_gate",
                               return_value={"ok": False, "blocked": [
                                   {"label": "what_it_does"}, {"label": "verdict"}]}):
            got = H.precheck("c1", [_cand()], {"c1": {"d": 1}}, set())
        self.assertEqual(got[2], "正文闸门不过：what_it_does；verdict")

    def test_all_gates_pass_returns_candidate_and_result(self):
        cand, draft = _cand(), {"d": 1}
        result = _pass_result()
        with mock.patch.object(VR, "evaluate", return_value=result) as ev, \
             mock.patch.object(VR, "content_gate", return_value={"ok": True}) as cg:
            got = H.precheck("c1", [cand], {"c1": draft}, set())
        self.assertIs(got[0], cand)      # 返回的是原对象，不是副本
        self.assertIs(got[1], result)
        self.assertIsNone(got[2])
        ev.assert_called_once_with(draft)
        cg.assert_called_once_with(cand)

    def test_gate3_not_called_when_gate2_fails(self):
        result = {"publishable": False, "gates_failed": [{"label": "x"}], "missing": []}
        with mock.patch.object(VR, "evaluate", return_value=result), \
             mock.patch.object(VR, "content_gate") as cg:
            H.precheck("c1", [_cand()], {"c1": {}}, set())
        cg.assert_not_called()  # 闸门顺序：前置不过就不往下走

    def test_precheck_does_not_mutate_inputs(self):
        cands, vers, case_ids = [_cand()], {"c1": {"d": 1}}, set()
        before = (copy.deepcopy(cands), copy.deepcopy(vers), set(case_ids))
        with mock.patch.object(VR, "evaluate", return_value=_pass_result()), \
             mock.patch.object(VR, "content_gate", return_value={"ok": True}):
            H.precheck("c1", cands, vers, case_ids)
        self.assertEqual((cands, vers, case_ids), before)


class TestContractWiring(unittest.TestCase):
    def test_promote_ready_reexports_same_function(self):
        import promote_ready as P
        self.assertIs(P.precheck, H.precheck)
        self.assertEqual(H.precheck.__module__, "handoff_contract")

    def test_contract_docstring_states_no_side_effects(self):
        # 契约文档必须写明「改判据要同步 server」——防两处判定分叉
        self.assertIn("server.py", H.__doc__)


if __name__ == "__main__":
    unittest.main()
