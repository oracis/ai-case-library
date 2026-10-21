#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promote_to_candidates 的**选稿口径**测试（2026-10-21 修）。

修的洞：自动挑待核条目时直接按 `inbox.json` 的**文件顺序**取 —— 那是采集
追加顺序，和「哪条更值得核」毫无关系。实测 135 条自动待办里分数 55/65
交错（clawrapid 65 → copykitten 55 → appmockupgenerator 65…），于是 AI
深核预算先花在谁身上，取决于谁先被采到。

同一处还有第二个洞：`T.score_record(i, "inbox")` 不传 index ⇒ 默认空索引
⇒ `duplicate_of` 的三条判据（已在候选池 / 与候选池同名 / 官网与已发布案例
相同）全部拿空集合比，是**死代码**。当前数据上那 5 条重复恰好因为 0 分被
别的规则拦住了，换个数据就会漏进晋升名单。
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import promote_to_candidates as P  # noqa: E402
import triage as T                 # noqa: E402


def rec(rid, score=60, grade="deep", slug=None, source="trustmrr"):
    r = {"id": rid, "name": rid, "harvest_source": source,
         "trustmrr_slug": slug if slug is not None else rid}
    r["_grade"], r["_score"] = grade, score
    return r


def fake_score(rec_, scope="candidate", index=None):
    return {"grade": rec_.get("_grade", "deep"),
            "score": rec_.get("_score", 60)}


class TestPickTargets(unittest.TestCase):

    def pick(self, inbox, have=(), index=None):
        with mock.patch.object(P.T, "score_record", fake_score):
            return P.pick_targets(inbox, set(have), index)

    def test_按分数降序(self):
        rows = self.pick([rec("a", 55), rec("b", 65), rec("c", 70)])
        self.assertEqual([r for r, _ in rows], ["c", "b", "a"])

    def test_同分按id兜底不依赖文件顺序(self):
        # 倒着喂，输出仍按 id 升序 —— 文件顺序不许当隐藏的第二排序键。
        rows = self.pick([rec("zz", 60), rec("mm", 60), rec("aa", 60)])
        self.assertEqual([r for r, _ in rows], ["aa", "mm", "zz"])

    def test_非deep排除(self):
        rows = self.pick([rec("a", 90, grade="later"), rec("b", 10, grade="drop")])
        self.assertEqual(rows, [])

    def test_已在池排除(self):
        rows = self.pick([rec("a"), rec("b")], have=["a"])
        self.assertEqual([r for r, _ in rows], ["b"])

    def test_非trustmrr来源排除(self):
        rows = self.pick([rec("a", source="hn"), rec("b")])
        self.assertEqual([r for r, _ in rows], ["b"])

    def test_无slug排除(self):
        """没有 trustmrr_slug 就实拉不到 —— 进了名单也只会在探针里变成 unknown。"""
        r = rec("a")
        r["trustmrr_slug"] = ""
        rows = self.pick([r, rec("b")])
        self.assertEqual([x for x, _ in rows], ["b"])

    def test_空队列返回空(self):
        self.assertEqual(self.pick([]), [])

    def test_索引透传给score_record(self):
        """自动选稿必须带真实索引 —— 传 None 等于重复检测失效。"""
        idx = T.Index()
        seen = {}

        def spy(rec_, scope="candidate", index=None):
            seen["index"] = index
            return {"grade": "deep", "score": 60}

        with mock.patch.object(P.T, "score_record", spy):
            P.pick_targets([rec("a")], set(), idx)
        self.assertIs(seen["index"], idx)


class TestIndexFor(unittest.TestCase):
    """index_for 建出来的索引必须真能判重复（空索引连「同官网」都看不出来）。"""

    CASES = [{"id": "existing", "name": "Existing",
              "sources": [{"url": "https://dup.example.com"}]}]
    DUP = {"id": "dup", "name": "Dup",
           "source_url": "https://dup.example.com/pricing"}

    def test_真实索引能判出同官网(self):
        with mock.patch.object(P.T, "load_all",
                               lambda: {"cases": self.CASES, "candidates": [],
                                        "inbox": [], "verifications": {}}):
            idx = P.index_for([], [])
        self.assertEqual(idx.duplicate_of(self.DUP, "inbox"),
                         "官网与已发布案例相同")

    def test_空索引判不出来(self):
        self.assertIsNone(T.Index().duplicate_of(self.DUP, "inbox"),
                          "这条就是旧代码的行为：判据全拿空集合比")

    def test_候选池同名也能判出(self):
        with mock.patch.object(P.T, "load_all",
                               lambda: {"cases": [], "candidates":
                                        [{"id": "other", "name": "Dup"}],
                                        "inbox": [], "verifications": {}}):
            idx = P.index_for([], [{"id": "other", "name": "Dup"}])
        self.assertEqual(idx.duplicate_of(self.DUP, "inbox"), "与候选池条目同名")


if __name__ == "__main__":
    unittest.main(verbosity=2)
