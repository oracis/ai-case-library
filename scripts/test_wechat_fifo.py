#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`wechat_fifo.py` 的离线单测（不连浏览器、不写真实台账）。

钉住 2026-11-07 的两个真坑：
① 只按「最久」挑会挑到**已发表过的同名旧稿**（发布模式发完草稿箱列表滞后）；
② 远端草稿在本地两本台账都没有记录 ⇒ `send --appmsgid` 直接报「找不到」，
   FIFO 等于开不了，必须先把远端真实存在的草稿补登进来。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wechat_fifo as WF  # noqa: E402


def _read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def _drafts():
    return {
        "100001": {"title": "最久未发", "update_time": 1700000000},
        "100002": {"title": "已发过的同名", "update_time": 1600000000},
        "100003": {"title": "次久未发", "update_time": 1700003600},
        "100004": {"title": "无时间戳", "update_time": 0},
        "100005": {"title": "", "update_time": 1500000000},
    }


class PickOldest(unittest.TestCase):

    def test_剔除已发表同名再按时间挑最久(self):
        (aid, item), rows, st = WF.pick_oldest(
            _drafts(), [{"title": "已发过的同名"}])
        self.assertEqual(aid, "100001")
        self.assertEqual(item["title"], "最久未发")
        self.assertEqual([r[1] for r in rows],
                         ["100001", "100003", "100004"])
        self.assertEqual(st["dup"], 1)
        self.assertEqual(st["candidates"], 3)

    def test_标题归一去空白再剔重(self):
        drafts = {"1": {"title": " 游戏厅： Quake ", "update_time": 10}}
        (aid, _item), _rows, st = WF.pick_oldest(
            drafts, [{"title": "游戏厅：Quake"}])
        self.assertIsNone(aid)
        self.assertEqual(st["dup"], 1)

    def test_无时间戳排最后不当最久(self):
        drafts = {
            "old": {"title": "有时间", "update_time": 1700000000},
            "unknown": {"title": "没时间", "update_time": 0},
        }
        (aid, _item), rows, st = WF.pick_oldest(drafts, [])
        self.assertEqual(aid, "old")
        self.assertEqual(rows[-1][1], "unknown")
        self.assertEqual(st["no_time"], 1)

    def test_空标题不计入候选(self):
        _aid, _item, st = WF.pick_oldest(_drafts(), [])
        self.assertEqual(st["no_title"], 1)

    def test_全已发表或空箱时返回None(self):
        (aid, item), rows, _st = WF.pick_oldest(
            {"1": {"title": "A", "update_time": 1}}, [{"title": "A"}])
        self.assertIsNone(aid)
        self.assertIsNone(item)
        self.assertEqual(rows, [])
        (aid2, _i), _r, st2 = WF.pick_oldest({}, [])
        self.assertIsNone(aid2)
        self.assertEqual(st2["candidates"], 0)

    def test_已发表侧字段名是title不是appmsg_id(self):
        # wechat_sync.fetch_published_remote 展开后带 title；拿错字段会剔不掉
        self.assertTrue(WF.norm_title("  A B  ") == "ab")


class PickOldestByCase(unittest.TestCase):
    """PRD #90：公众号 FIFO 原来只按标题剔同名 ⇒ 标题改过的旧稿会重发。

    Freedoom 那次是「两套 id + 同名」；换成 11-02 改过标题的 xhs 那种
    形状，光按标题就认不出来了。必须补按 (case_id, wechat) 剔重。
    """

    DRAFTS = {
        "100001": {"title": "最久未发", "update_time": 1600000000},
        "100002": {"title": "改过标题的旧稿", "update_time": 1500000000},
        "100003": {"title": "认不出的孤儿稿", "update_time": 1800000000},
    }

    def test_已发案例的改标题旧稿被剔掉(self):
        def case_of(aid):
            return {"100002": "divine-widgets"}.get(aid, "")

        (aid, item), rows, st = WF.pick_oldest(
            self.DRAFTS, [], published_cases={"divine-widgets"},
            case_of=case_of)
        self.assertEqual(aid, "100001")
        self.assertEqual(st["dup_case"], 1)
        self.assertEqual(st["candidates"], 2)
        self.assertNotIn("100002", [r[1] for r in rows])

    def test_两套id归一后算同一个案例(self):
        """`ext-ep-003-freedoom` 与 `ep-003-freedoom` 是同一期游戏稿。"""
        (aid, _), _, st = WF.pick_oldest(
            {"100009": {"title": "游戏厅：Freedoom", "update_time": 1600000000}},
            [], published_cases={"ep-003-freedoom"},
            case_of=lambda _aid: "ext-ep-003-freedoom")
        self.assertIsNone(aid)
        self.assertEqual(st["dup_case"], 1)

    def test_认不出case的孤儿稿保留(self):
        (aid, _), _, st = WF.pick_oldest(
            {"wx-1": {"title": "认不出的孤儿稿",
                      "update_time": 1500000000}},
            [], published_cases={"divine-widgets"},
            case_of=lambda _aid: "")
        self.assertEqual(aid, "wx-1")
        self.assertEqual(st["dup_case"], 0)

    def test_不传集合时保持纯标题口径向后兼容(self):
        (aid, _), _, st = WF.pick_oldest(
            self.DRAFTS, [{"title": "最久未发"}])
        self.assertEqual(aid, "100002")
        self.assertEqual(st["dup"], 1)

    def test_main必须把case级剔重接进真发路径(self):
        """只改纯函数、main 忘传 ⇒ 真发仍然没有这道闸（最容易回退的地方）。"""
        with mock.patch.object(WF, "read_remote", return_value=({}, [])), \
                mock.patch.object(WF.FD, "published_case_ids",
                                  return_value={"divine-widgets"}) as pub, \
                mock.patch.object(WF, "pick_oldest",
                                  return_value=((None, None), [], {
                                      "draft": 0, "published": 0, "dup": 0,
                                      "dup_case": 0, "no_title": 0,
                                      "no_time": 0, "candidates": 0})) as pick:
            self.assertEqual(WF.main(["--list"]), 2)
        self.assertTrue(pub.called)
        kw = pick.call_args.kwargs
        self.assertEqual(kw.get("published_cases"), {"divine-widgets"})
        self.assertTrue(callable(kw.get("case_of")))


class EnsureLocalRecord(unittest.TestCase):

    def _paths(self, tmp, published=None, standalone=None):
        p1 = os.path.join(tmp, "wechat_published.json")
        p2 = os.path.join(tmp, "wechat_standalone.json")
        with open(p1, "w", encoding="utf-8") as f:
            json.dump(published or {}, f, ensure_ascii=False, indent=2)
        with open(p2, "w", encoding="utf-8") as f:
            json.dump(standalone or {}, f, ensure_ascii=False, indent=2)
        return p1, p2

    def test_远端有本地无则补登且下次能认亲(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = self._paths(tmp)
            with mock.patch.object(WF, "PUBLISHED_PATH", p1), \
                    mock.patch.object(WF, "STANDALONE", p2):
                cid, created = WF.ensure_local_record(
                    "100001", {"title": "最久未发", "digest": "摘要"})
                self.assertTrue(created)
                self.assertEqual(WF.local_cid_of("100001")[0], cid)
                with open(p2, encoding="utf-8") as f:
                    rec = json.load(f)[cid]
                self.assertEqual(rec["appmsgid"], "100001")
                self.assertEqual(rec["status"], "draft")
                self.assertEqual(rec["title"], "最久未发")
                # 补登是幂等的：再跑一次不新增、不改写
                raw = _read_bytes(p2)
                cid2, created2 = WF.ensure_local_record(
                    "100001", {"title": "最久未发"})
                self.assertFalse(created2)
                self.assertEqual(cid2, cid)
                self.assertEqual(_read_bytes(p2), raw)

    def test_两本账都认得到时不写盘(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = self._paths(
                tmp, standalone={"ep-001": {"appmsgid": "100001",
                                            "status": "draft"}})
            with mock.patch.object(WF, "PUBLISHED_PATH", p1), \
                    mock.patch.object(WF, "STANDALONE", p2):
                raw = _read_bytes(p2)
                cid, created = WF.ensure_local_record(
                    "100001", {"title": "已有"})
                self.assertFalse(created)
                self.assertEqual(cid, "ep-001")
                self.assertEqual(_read_bytes(p2), raw)

    def test_主台账里的案例稿也认得到(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = self._paths(
                tmp, published={"prosp": {"appmsgid": "100000251",
                                          "status": "draft"}})
            with mock.patch.object(WF, "PUBLISHED_PATH", p1), \
                    mock.patch.object(WF, "STANDALONE", p2):
                cid, created = WF.ensure_local_record(
                    "100000251", {"title": "PROSP"})
                self.assertEqual((cid, created), ("prosp", False))


class ReadRemoteGuard(unittest.TestCase):

    def test_两侧没读全不许选稿(self):
        import inspect
        src = inspect.getsource(WF.read_remote)
        self.assertIn("dok and pok", src)
        self.assertIn("fetch_draft_remote", src)
        self.assertIn("fetch_published_remote", src)

    def test_真发才起有头窗口并且默认不群发(self):
        import inspect
        src = inspect.getsource(WF.main)
        self.assertIn("CHROME_HEADED", src)
        self.assertIn("a.notify", src)


if __name__ == "__main__":
    unittest.main()
