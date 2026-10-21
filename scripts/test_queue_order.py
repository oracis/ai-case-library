#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待发队列的排序口径（2026-10-21 修）。

此前头条与小红书的 `pending_cases()` 都写 `todo[::-1]`，注释和 `queue` 打印
都说「库里最新的排最前」。可 `load_cases()` 返回的是 `cases.json` 的**文件
顺序**，而这个文件本来就是新的在前（实测头几条 2026-10-15、末几条 2026-09-11）
—— 取反之后实际发的是**最旧的那批**，谁也没发现。

现在两个入口统一走 `multiplatform.queue.newest_first`（显式按 `updated_at`
倒序）。这里同时钉住「口径本身」和「两个入口真的用了它」—— 后者防有人把
`[::-1]` 改回去。
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from multiplatform import queue as Q   # noqa: E402
import toutiao_publish as tp           # noqa: E402
import xhs_publish as xp               # noqa: E402


def case(cid, upd=None, ver=None):
    c = {"id": cid, "name": cid}
    if upd:
        c["updated_at"] = upd
    if ver:
        c["verified_at"] = ver
    return c


class TestNewestFirst(unittest.TestCase):
    def test_按日期倒序(self):
        cases = [case("old", "2026-09-11"), case("new", "2026-10-15"),
                 case("mid", "2026-10-01")]
        self.assertEqual([c["id"] for c in Q.newest_first(cases)],
                         ["new", "mid", "old"])

    def test_同日期保持原顺序(self):
        cases = [case("a", "2026-10-15"), case("b", "2026-10-15"),
                 case("c", "2026-10-15")]
        self.assertEqual([c["id"] for c in Q.newest_first(cases)],
                         ["a", "b", "c"])

    def test_没有日期的沉底(self):
        cases = [case("none"), case("old", "2026-09-11"), case("new", "2026-10-15")]
        self.assertEqual([c["id"] for c in Q.newest_first(cases)],
                         ["new", "old", "none"])

    def test_updated_at缺失退verified_at(self):
        self.assertEqual(Q.recency(case("x", ver="2026-10-02")), "2026-10-02")
        self.assertEqual(Q.recency(case("y")), "")

    def test_不修改入参(self):
        cases = [case("a", "2026-09-11"), case("b", "2026-10-15")]
        Q.newest_first(cases)
        self.assertEqual([c["id"] for c in cases], ["a", "b"])


class TestPlatformQueuesUseIt(unittest.TestCase):
    """两个平台的 `pending_cases` 必须真的最新在前（防改回 `[::-1]`）。"""

    CASES = [case("old", "2026-09-11"), case("new", "2026-10-15"),
             case("mid", "2026-10-01")]

    def test_头条队列最新在前(self):
        old_lc, old_ids = tp.load_cases, tp._ids
        tp.load_cases = lambda: list(self.CASES)
        tp._ids = lambda path: []                  # 台账全空 ⇒ 三条都待发
        try:
            todo = tp.pending_cases(need_built=False)
        finally:
            tp.load_cases, tp._ids = old_lc, old_ids
        self.assertEqual([c["id"] for c in todo], ["new", "mid", "old"])

    def test_小红书队列最新在前(self):
        old_lc = xp.load_cases
        old_pub, old_drf = xp._published_ids, xp._drafted_ids
        xp.load_cases = lambda: list(self.CASES)
        xp._published_ids = lambda: []
        xp._drafted_ids = lambda: []
        try:
            todo = xp.pending_cases(need_cards=False)
        finally:
            (xp.load_cases, xp._published_ids,
             xp._drafted_ids) = old_lc, old_pub, old_drf
        self.assertEqual([c["id"] for c in todo], ["new", "mid", "old"])

    def test_台账里的案例仍被跳过(self):
        old_lc, old_ids = tp.load_cases, tp._ids
        tp.load_cases = lambda: list(self.CASES)
        tp._ids = lambda path: ["new"]             # 头条已存/已发过 new
        try:
            todo = tp.pending_cases(need_built=False)
        finally:
            tp.load_cases, tp._ids = old_lc, old_ids
        self.assertEqual([c["id"] for c in todo], ["mid", "old"])


if __name__ == "__main__":
    unittest.main()
