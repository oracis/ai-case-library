#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`bili_draft_sync.py` 回归锁：草稿归属只认硬证据，不认相似度。

三条钉死的：
  1. 标题/正文必须**逐字相等**才算认领，宁可不认领也不猜（2026-10-19 头条
     那次纯标题匹配误判 13 条幽灵，就是这条松了的后果）；
  2. 同步是**幂等**的：已经记过的 article_id 不会重复写、不会刷旧时间；
  3. 多个 case 撞同一判据 ⇒ 报出来让人判，绝不挑一个写进账。
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import bili_draft_sync as S  # noqa: E402

# 正文样本：去掉标点后仍然唯一
BODY = ("挂牌倍数为未披露我去核了一遍TrustMRR页面明确列出挂牌价30000"
        "Revenuemultiple16x它是干什么的给Shopify商家做的服务端Meta广告追踪"
        "与自动化把广告点击与实际转化对应起来serversidetracking并自动跑关键流程"
        "目标不是让广告花得更多而是让商家看到的数字是真的钱从哪来订阅制卖给在"
        "Meta上投放广告的Shopify商家客户投放规模越大越愿意付费收入和客户的广度"
        "直接决定它收多少")


def _draft(aid, title, summary="", mtime=1792345004):
    return {"article_id": aid, "title": title, "summary": summary,
            "mtime": mtime}


class TestNorm(unittest.TestCase):
    def test_strips_decorations(self):
        """线上 summary 是 clean_body 加工后的版本（段落前加 ▶、加粗变了形）。

        只去标点是不够的 —— 2026-10-19 那 5 条（vid-ai / vectosolve / publbee /
        podawaa / augora-ai）就是因为多了几个 `▶` 被判成"没归属"。
        """
        a = "它是干什么的\n\n▶ <strong>给 Shopify</strong> 商家做 **追踪**"
        b = "它是干什么的 给Shopify商家做追踪"
        self.assertEqual(S._norm(a), S._norm(b))

    def test_drops_h1(self):
        """标题是单独提交的字段，正文从 h1 之后算起 —— 不剥掉两边永远差一段。"""
        html = "<p>前导</p><h1>标题行</h1><p>真正的正文</p>"
        self.assertNotIn("标题行", S._drop_h1(html))
        self.assertIn("真正的正文", S._drop_h1(html))


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.idx = {
            "vectosolve": {"title": "VectoSolve：位图转可编辑 SVG",
                           "sig": BODY[:S.SIG_LEN]},
            "other": {"title": "别的：完全不相干", "sig": "另外一段正文"},
        }

    def test_title_exact_hit(self):
        p = S.plan([_draft("343501", "VectoSolve：位图转可编辑 SVG")],
                   self.idx, recs={})
        self.assertEqual(p["claimed"][0]["cid"], "vectosolve")
        self.assertEqual(p["claimed"][0]["how"], "title")
        self.assertEqual(p["unclaimed"], [])

    def test_body_hit_after_decorations(self):
        """正文被加工过（前半段一致、后半段有出入）也要能认出来：
        窗口从 200 收到 120 逐字匹配，仍然是精确的。"""
        # 模拟线上版本：第 120 字之后和本地不一致
        summary = BODY[:150] + "这里线上多写了一句本地没有的话"
        p = S.plan([_draft("343501", "标题被改过了", summary)],
                   self.idx, recs={})
        self.assertEqual(p["unclaimed"], [])
        self.assertEqual(p["claimed"][0]["cid"], "vectosolve")
        self.assertEqual(p["claimed"][0]["how"], "body")

    def test_ambiguous_is_not_guessed(self):
        idx = dict(self.idx)
        idx["clone"] = {"title": "VectoSolve：位图转可编辑 SVG", "sig": ""}
        p = S.plan([_draft("343501", "VectoSolve：位图转可编辑 SVG")],
                   idx, recs={})
        self.assertEqual(p["claimed"], [], "撞两个 case 不许挑一个写账")
        self.assertEqual(len(p["unclaimed"]), 1)
        self.assertIn("多个", p["unclaimed"][0]["why"])

    def test_unknown_stays_unclaimed(self):
        p = S.plan([_draft("999999", "从没见过的东西", "一段没见过的正文")],
                   self.idx, recs={})
        self.assertEqual(p["claimed"], [])
        self.assertEqual(len(p["unclaimed"]), 1)

    def test_already_owned_is_idempotent(self):
        recs = {"vectosolve": {"id": "vectosolve", "state": "draft",
                               "at": "2026-10-16 14:10", "remote_id": "",
                               "draft_remote_id": "343501"}}
        p = S.plan([_draft("343501", "VectoSolve：位图转可编辑 SVG")],
                   self.idx, recs=recs)
        self.assertEqual(p["claimed"][0]["how"], "already")
        self.assertEqual(p["unclaimed"], [])


class TestApplyPlan(unittest.TestCase):
    """落账必须走 set_draft_id：只补草稿 ID，不许动状态。"""

    def setUp(self):
        self._old = L.DATA
        self.dir = tempfile.mkdtemp(prefix="bdsync_")
        L.DATA = self.dir
        for name in ("bili_published.json", "bili_drafts.json"):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write("[]")

    def tearDown(self):
        L.DATA = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_does_not_downgrade_published(self):
        L.promote("bilibili", "trustmrr", remote_id="53460777", title="T")
        p = {"claimed": [{"cid": "trustmrr", "article_id": "342372",
                          "title": "T", "at": "2026-10-19 01:36",
                          "how": "title"}]}
        S.apply_plan(p)
        rec = L.load("bilibili")["trustmrr"]
        self.assertEqual(rec["state"], L.PUBLISHED, "存草稿不许推翻已发布状态")
        self.assertEqual(rec["draft_remote_id"], "342372")


if __name__ == "__main__":
    unittest.main()
