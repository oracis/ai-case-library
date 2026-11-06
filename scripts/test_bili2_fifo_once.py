# -*- coding: utf-8 -*-
"""`bili2_fifo_once.readback()` 的判据测试（不联网、不起浏览器）。

为什么单独测它：这是「二号 FIFO 首跑到底算不算发成功」的唯一结论口。
它必须做到两件事，缺一件就会给出假绿：
  ① 只认**本次新增**的记录（发布前已存在的别算成本次成果）；
  ② 游戏仓台账必须变成 `published`（#78 的补写没落地 ⇒ 别报成功）。
另外 case 归一必须走 `games_state.norm()`：并集读口给出的可能是游戏仓写法
`ext-ep-003-freedoom`（无 `-b2`），手写剥前缀/后缀会漏。

用法：python scripts/test_bili2_fifo_once.py
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bili2_fifo_once as F  # noqa: E402
import games_state as GS     # noqa: E402
import publish_slot as SLOT  # noqa: E402


class TestReadback(unittest.TestCase):
    TODAY = "2026-11-07"

    def _patch(self, records, entries):
        return (
            mock.patch.object(SLOT, "today", lambda: self.TODAY),
            mock.patch.object(SLOT, "records", lambda plat: records),
            mock.patch.object(GS, "entries", lambda plat: entries),
        )

    def test_没有新增记录不算成功(self):
        recs = [(self.TODAY + " 08:00", "ext-ep-003-freedoom")]
        ents = [{"case": "ep-003-freedoom", "state": "published", "remote_id": "1"}]
        p1, p2, p3 = self._patch(recs, ents)
        with p1, p2, p3:
            self.assertFalse(F.readback({"ext-ep-003-freedoom"}),
                             "今天已发的那篇不能被当成本次成果")

    def test_新增且游戏仓published才算成功(self):
        recs = [(self.TODAY + " 09:00", "ext-ep-004-quake")]
        ents = [{"case": "ep-004-quake", "state": "published", "remote_id": "5379"}]
        p1, p2, p3 = self._patch(recs, ents)
        with p1, p2, p3:
            self.assertTrue(F.readback(set()))

    def test_新增但游戏仓没写published不算成功(self):
        recs = [(self.TODAY + " 09:00", "ext-ep-004-quake")]
        ents = [{"case": "ep-004-quake", "state": "drafted", "remote_id": "376917"}]
        p1, p2, p3 = self._patch(recs, ents)
        with p1, p2, p3:
            self.assertFalse(F.readback(set()),
                             "补写没落地时必须报失败，不能只看 db 有新行")

    def test_case归一用games_state_norm(self):
        """db 记 `ext-ep-004-quake-b2`、游戏仓记 `ep-004-quake` 也要对上。"""
        recs = [(self.TODAY + " 09:00", "ext-ep-004-quake-b2")]
        ents = [{"case": "ep-004-quake", "state": "published", "remote_id": "9"}]
        p1, p2, p3 = self._patch(recs, ents)
        with p1, p2, p3:
            self.assertTrue(F.readback(set()))


class TestSourceGuards(unittest.TestCase):
    """两条纪律用源码守护：不许绕过配额、不许在没回读时就报成功。"""

    def test_干跑先过配额闸(self):
        src = open(os.path.join(HERE, "bili2_fifo_once.py"), encoding="utf-8").read()
        self.assertIn('SLOT.decide("bilibili2")', src,
                      "先问档期/配额，别在箱里瞎发")
        self.assertIn("readback(before)", src, "回读必须真的被调用")

    def test_不用后台hold起浏览器(self):
        src = open(os.path.join(HERE, "bili2_fifo_once.py"), encoding="utf-8").read()
        body = src.split('"""', 2)[2]          # 去掉文件头 docstring（那里在讲这个坑）
        self.assertNotIn("--hold", body,
                         "Chrome 要在本进程内起（--hold 撑不住，见脚本 docstring）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
