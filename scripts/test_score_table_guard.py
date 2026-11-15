#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRD #186：历史评分表写入脚本的封存回归 + 「两表无文案副本」常驻判据。

守住三件事：

  1. 判据本身能抓到 ``note=`` / 非空理由，也能放行干净表（两头都测，防假绿）；
  2. 真表当前确实干净（与 ``test_tables_have_no_note_copies`` 双保险）；
  3. 两个历史脚本**重跑真的被拒**，且拒绝过程不写任何盘 —— 直接证明
     「重跑重新插入 note 副本」这条根因被堵死，而不是把报红当误报忽略。

只读，不写盘。
"""
import hashlib
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import score_china_fit as CF          # noqa: E402
import score_solo_fit as SF           # noqa: E402
import score_table_guard as guard     # noqa: E402

ONEOFF = os.path.join(ROOT, "scripts", "_oneoff")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestNoteCopyDetector(unittest.TestCase):
    """判据必须能抓污染，也要能放行干净表 —— 两头都测，防假绿。"""

    def test_detects_scores_note(self):
        scores = {"a": {"demand": 3, "note": "副本"}}
        delivery = {"a": (3, "")}
        bad_s, bad_d = guard.note_copy_ids(scores, delivery)
        self.assertEqual(bad_s, ["a"])
        self.assertEqual(bad_d, [])
        self.assertFalse(guard.table_is_note_free(scores, delivery))

    def test_detects_delivery_reason(self):
        scores = {"a": {"demand": 3}}
        delivery = {"a": (3, "非空理由副本")}
        bad_s, bad_d = guard.note_copy_ids(scores, delivery)
        self.assertEqual(bad_s, [])
        self.assertEqual(bad_d, ["a"])
        self.assertFalse(guard.table_is_note_free(scores, delivery))

    def test_detects_both_and_reports_ids(self):
        scores = {"a": {"note": "x"}, "b": {"demand": 4}, "c": {"note": "z"}}
        delivery = {"a": (2, ""), "b": (3, "理由"), "c": (4, "理由2")}
        self.assertEqual(guard.note_copy_ids(scores, delivery),
                         (["a", "c"], ["b", "c"]))

    def test_clean_tables_pass(self):
        scores = {"a": {"demand": 3, "blocker": None},
                  "b": {"demand": 5, "blocker": "x"}}
        delivery = {"a": (3, ""), "b": (5, SF.NOTE_IN_LIBRARY)}
        self.assertEqual(guard.note_copy_ids(scores, delivery), ([], []))
        self.assertTrue(guard.table_is_note_free(scores, delivery))

    def test_real_tables_are_note_free(self):
        """真表 465/465 条都得干净；若这里红了，是表真被污染，不是误报。"""
        self.assertGreater(len(CF.SCORES), 400, "样本太少，测试会假绿")
        self.assertGreater(len(SF.DELIVERY), 400, "样本太少，测试会假绿")
        bad_s, bad_d = guard.note_copy_ids(CF.SCORES, SF.DELIVERY)
        self.assertEqual((bad_s, bad_d), ([], []),
                         "静态表出现文案副本：SCORES=%s DELIVERY=%s"
                         % (bad_s[:8], bad_d[:8]))
        self.assertEqual(SF.NOTE_IN_LIBRARY, "")


class TestLegacyWritersSealed(unittest.TestCase):
    """两个历史脚本：重跑必须被拒，且拒绝过程不碰任何文件。"""

    def test_seal_list_matches_files(self):
        for name in guard.LEGACY_WRITERS:
            with self.subTest(script=name):
                self.assertTrue(os.path.isfile(os.path.join(ONEOFF, name)),
                                "封存清单里的脚本不存在：%s" % name)

    def test_both_scripts_call_seal(self):
        """源码级钉子：不许有人把封存口删掉或只接一半。"""
        for name in guard.LEGACY_WRITERS:
            with self.subTest(script=name):
                with open(os.path.join(ONEOFF, name), encoding="utf-8") as f:
                    src = f.read()
                self.assertIn("import score_table_guard", src,
                              "%s 没接封存口" % name)
                self.assertIn("guard.refuse_legacy_writer", src,
                              "%s 的 main() 没调封存口" % name)

    def test_refuse_exits_with_reason(self):
        with self.assertRaises(SystemExit) as ctx:
            guard.refuse_legacy_writer("probe.py")
        msg = str(ctx.exception)
        for token in ("SEALED", "PRD #186", "note=", "case_library"):
            self.assertIn(token, msg)
        self.assertNotIn("--force", msg, "封存口不许留覆盖开关")

    def test_rerun_is_refused_and_readonly(self):
        """行为级：真去调 main()，必须拒绝，且两张表与库镜像字节不变。

        这是「重跑重新插入 note 副本」的直接回归：不用靠幂等锚点（id /
        banner 注释）碰巧还在，封存口在任何文件读写之前就退出。
        """
        watched = [os.path.join(ROOT, "scripts", "score_china_fit.py"),
                   os.path.join(ROOT, "scripts", "score_solo_fit.py"),
                   os.path.join(ROOT, "data", "cases.json")]
        before = {p: _sha(p) for p in watched}
        for i, name in enumerate(guard.LEGACY_WRITERS):
            with self.subTest(script=name):
                mod = _load("_seal_probe_%d" % i, os.path.join(ONEOFF, name))
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()
                self.assertIn("SEALED", str(ctx.exception))
        after = {p: _sha(p) for p in watched}
        self.assertEqual(after, before, "封存拒绝过程动了静态表或库镜像")


if __name__ == "__main__":
    unittest.main(verbosity=2)
