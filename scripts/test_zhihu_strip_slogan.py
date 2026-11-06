#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zhihu_strip_slogan 单测：不联网。守住「只删一段 / 双回读 / 失败回滚」。"""
import copy
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import zhihu_strip_slogan as R                                      # noqa: E402

AID = "2102032057182446679"
AID2 = "2101282414362472778"
HEAD = "<blockquote><p>Freedoom 是一个自由开源的项目…</p></blockquote>\n"
TAIL = "\n<h2>先说清楚</h2>\n<p>正文第二段，一个字都不能动。</p>"
CONTENT = HEAD + R.PARA + TAIL
EXPECTED = HEAD + TAIL[1:]          # 删段时吃掉一个换行，段落间距仍是一个
SUMMARY_OLD = ("Freedoom 是一个自由开源的项目… " + R.SLOGAN +
               " 先说清楚 正文第二段…")
SUMMARY_NEW = "Freedoom 是一个自由开源的项目… 先说清楚 正文第二段…"


class FakeApi(object):
    def __init__(self, recompute_summary=True, summary_readonly=False,
                 wash_title=False, lose_draft=False, content_mismatch=False):
        self.d = {
            AID: {"id": AID, "title": "Freedoom，免费合法的毁灭战士",
                  "state": "draft", "content": CONTENT, "summary": SUMMARY_OLD},
            AID2: {"id": AID2, "title": "2048，一个网页就能玩的数字合并",
                   "state": "draft", "content": CONTENT.replace("Freedoom", "2048"),
                   "summary": SUMMARY_OLD.replace("Freedoom", "2048")},
        }
        self.listed = {AID: "Freedoom，免费合法的毁灭战士",
                       AID2: "2048，一个网页就能玩的数字合并"}
        self.patches = []
        self.recompute_summary = recompute_summary
        self.summary_readonly = summary_readonly
        self.wash_title = wash_title
        self.lose_draft = lose_draft
        self.content_mismatch = content_mismatch
        self.mismatch_done = False

    def drafts(self):
        return {} if self.lose_draft else dict(self.listed)

    def detail(self, aid):
        return copy.deepcopy(self.d[aid])

    def patch(self, aid, payload):
        self.patches.append((aid, dict(payload)))
        d = self.d[aid]
        if "content" in payload:
            if self.content_mismatch and not self.mismatch_done:
                d["content"] = "被洗坏的正文"
                self.mismatch_done = True      # 回滚那次 PATCH 必须能写回原文
            else:
                d["content"] = payload["content"]
            if self.recompute_summary and "summary" not in payload:
                d["summary"] = d["content"].replace(HEAD, "").replace(TAIL, "")
                if R.KEYWORD in d["summary"]:
                    d["summary"] = SUMMARY_NEW
        if "summary" in payload and not self.summary_readonly:
            d["summary"] = payload["summary"]
        if self.wash_title and "content" in payload:
            d["title"] = ""
        return {"id": aid}


class TestStripContent(unittest.TestCase):
    def test_只删那一段_其余逐字不变(self):
        new, why = R.strip_content(CONTENT)
        self.assertIsNotNone(new)
        self.assertEqual(new, EXPECTED)
        self.assertNotIn("游戏厅", new)
        self.assertIn("删 1 段", why)
        self.assertEqual(new.count("<h2>"), CONTENT.count("<h2>"))

    def test_已清的幂等跳过(self):
        new, why = R.strip_content(HEAD + TAIL)
        self.assertIsNone(new)
        self.assertIn("没有完整标语段", why)

    def test_标语出现两次就不猜(self):
        new, why = R.strip_content(HEAD + R.PARA + "\n" + R.PARA + TAIL)
        self.assertIsNone(new)
        self.assertIn(">1 不猜", why)

    def test_还有别的栏目字样就跳过(self):
        c = CONTENT.replace("<h2>先说清楚</h2>", "<h2>游戏厅补遗</h2>")
        new, why = R.strip_content(c)
        self.assertIsNone(new)
        self.assertIn("只报告不动", why)

    def test_变体不是整句不许删(self):
        variant = "<p>「游戏厅」栏目，每期只讲一款游戏：本篇特别版。</p>"
        new, why = R.strip_content(HEAD + variant + TAIL)
        self.assertIsNone(new)
        self.assertIn("没有完整标语段", why)

    def test_不成行也能定点删(self):
        c = HEAD.replace("\n", "") + R.PARA + TAIL
        new, why = R.strip_content(c)
        self.assertIsNotNone(new)
        self.assertNotIn("游戏厅", new)


class TestDoOne(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch.object(R, "_backup", return_value="tmp/fake.json")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_summary自动重算_成功且双回读(self):
        api = FakeApi(recompute_summary=True)
        ok, why = R.do_one(AID, "x", api)
        self.assertTrue(ok, why)
        self.assertEqual(api.d[AID]["content"], EXPECTED)
        self.assertNotIn("游戏厅", api.d[AID]["summary"])
        self.assertEqual(api.d[AID]["title"], "Freedoom，免费合法的毁灭战士")
        self.assertEqual(len(api.patches), 1, "summary 自动重算时不该再 PATCH")

    def test_summary没重算就显式补写(self):
        api = FakeApi(recompute_summary=False)
        ok, why = R.do_one(AID, "x", api)
        self.assertTrue(ok, why)
        self.assertIn("显式补写", why)
        self.assertEqual(api.patches[-1][1], {"summary": SUMMARY_NEW})

    def test_summary补写无效则判失败并回滚(self):
        api = FakeApi(recompute_summary=False, summary_readonly=True)
        ok, why = R.do_one(AID, "x", api)
        self.assertFalse(ok)
        self.assertIn("已尝试回滚", why)
        self.assertEqual(api.d[AID]["content"], CONTENT, "正文必须原样回滚")
        self.assertEqual(api.d[AID]["summary"], SUMMARY_OLD)

    def test_回读content不一致则回滚(self):
        api = FakeApi(content_mismatch=True)
        ok, why = R.do_one(AID, "x", api)
        self.assertFalse(ok)
        self.assertEqual(api.d[AID]["content"], CONTENT)

    def test_title被洗掉则回滚(self):
        api = FakeApi(wash_title=True)
        ok, why = R.do_one(AID, "x", api)
        self.assertFalse(ok)
        self.assertIn("title/state", why)
        self.assertEqual(api.d[AID]["content"], CONTENT)

    def test_草稿箱列表找不到该稿则回滚(self):
        api = FakeApi(lose_draft=True)
        ok, why = R.do_one(AID, "x", api)
        self.assertFalse(ok)
        self.assertIn("草稿箱列表", why)
        self.assertEqual(api.d[AID]["content"], CONTENT)


class TestRun(unittest.TestCase):
    def test_干跑不发PATCH(self):
        api = FakeApi()
        rc = R.run(go=False, out=io.StringIO(), api=api)
        self.assertEqual(rc, 0)
        self.assertEqual(api.patches, [])

    def test_check在有残留时返回1(self):
        self.assertEqual(R.run(check=True, out=io.StringIO(), api=FakeApi()), 1)
        api = FakeApi()
        for d in api.d.values():
            d["content"] = EXPECTED
        self.assertEqual(R.run(check=True, out=io.StringIO(), api=api), 0)

    def test_go两篇全成且二次check归零(self):
        api = FakeApi()
        self.assertEqual(R.run(go=True, out=io.StringIO(), api=api), 0)
        self.assertEqual(R.run(check=True, out=io.StringIO(), api=api), 0)
        for d in api.d.values():
            self.assertNotIn("游戏厅", d["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
