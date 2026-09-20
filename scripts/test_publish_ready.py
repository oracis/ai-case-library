#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_ready / contentpack_ready 的回归测试。

钉住的核心事实只有一条：**「材料已齐」不等于「可以发布」**。

2026-09-17 那次翻车就是没分清这两件事 —— 6 条候选被 AI 核完、必填全齐、
判定「可发布」，于是被自动提升成案例；但候选上 why_it_works / playbook /
verdict 全是空的，promote 只搬运现有字段，于是发出去 6 张只有数字没有内容的
卡片，当天全部退回候选池。

所以：
  · publish_ready.content_gaps 是那道闸门，它的行为要逐个字段钉住
  · contentpack_ready 的内容包必须自带数字（metrics + metric_note），
    否则核实过的正确数字会被采集时的错误 headline 顶掉

不依赖 server，不联网。
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import contentpack_ready as CP      # noqa: E402
import publish_ready as PR          # noqa: E402


FULL = {
    "id": "demo",
    "what_it_does": "做一件事",
    "verdict": "一句话判断",
    "how_it_makes_money": "按月订阅",
    "why_it_works": ["理由一", "理由二", "理由三"],
    "playbook": ["动作一", "动作二", "动作三"],
}


class TestContentGaps(unittest.TestCase):
    """闸门本身：缺什么就该报什么，一条都不能漏。"""

    def test_full_pack_passes(self):
        self.assertEqual(PR.content_gaps(FULL), [])

    def test_each_scalar_field_blocks(self):
        for field in ("what_it_does", "verdict", "how_it_makes_money"):
            with self.subTest(field=field):
                broken = dict(FULL, **{field: ""})
                gaps = PR.content_gaps(broken)
                self.assertIn(field, " ".join(gaps),
                              "%s 为空时必须被拦下" % field)

    def test_empty_why_and_play_block(self):
        for field in ("why_it_works", "playbook"):
            with self.subTest(field=field):
                self.assertTrue(PR.content_gaps(dict(FULL, **{field: []})))

    def test_threshold_is_three(self):
        """2 条不够、3 条才放行 —— 这是和 verify_rules 的 solo_playbook 对齐的下限。"""
        for n in (0, 1, 2):
            with self.subTest(n=n):
                self.assertTrue(PR.content_gaps(dict(FULL, why_it_works=["x"] * n)))
                self.assertTrue(PR.content_gaps(dict(FULL, playbook=["x"] * n)))
        self.assertEqual(PR.content_gaps(dict(FULL, why_it_works=["x"] * 3)), [])
        self.assertEqual(PR.content_gaps(dict(FULL, playbook=["x"] * 3)), [])

    def test_none_values_do_not_crash(self):
        """字段可能是 None 而不是缺失 —— 采集器给的就是 None。"""
        broken = {"id": "x", "why_it_works": None, "playbook": None,
                  "what_it_does": None, "verdict": None, "how_it_makes_money": None}
        gaps = PR.content_gaps(broken)
        self.assertEqual(len(gaps), 5)


class TestSelect(unittest.TestCase):
    """select 是发布前的最后一道：不合格的必须挑出去，不能静默放过。"""

    def test_splits_good_and_bad(self):
        good = dict(FULL, id="good")
        bad = {"id": "bad"}
        chosen, skipped = PR.select([good, bad], None)
        self.assertEqual([c["id"] for c in chosen], ["good"])
        self.assertEqual([s[0] for s in skipped], ["bad"])

    def test_id_filter_cannot_bypass_gate(self):
        """--id 指定到不合格条目时也不许破例 —— 否则闸门形同虚设。"""
        bad = {"id": "bad"}
        chosen, skipped = PR.select([bad], ["bad"])
        self.assertEqual(chosen, [])
        self.assertEqual([s[0] for s in skipped], ["bad"])

    def test_id_filter_only_picks_named(self):
        a = dict(FULL, id="a")
        b = dict(FULL, id="b")
        chosen, _ = PR.select([a, b], ["b"])
        self.assertEqual([c["id"] for c in chosen], ["b"])


class TestContentPacks(unittest.TestCase):
    """写好的 5 份内容包本身要合格 —— 别让手写的错别字溜进案例。"""

    def test_all_packs_pass_validation(self):
        for cid, pack in CP.PACKS.items():
            with self.subTest(cid=cid):
                self.assertEqual(CP.check_pack(cid, pack), [],
                                 "%s 的内容包不合格" % cid)

    def test_every_pack_has_metrics_with_note(self):
        """数字必须自带口径说明 —— 没有它读者分不清 MRR / 累计 / 流水。"""
        for cid, pack in CP.PACKS.items():
            with self.subTest(cid=cid):
                m = pack.get("metrics") or {}
                self.assertTrue(str(m.get("headline") or "").strip(),
                                "%s 缺 metrics.headline" % cid)
                note = str(m.get("metric_note") or "").strip()
                self.assertTrue(note, "%s 缺 metric_note" % cid)
                # 口径说明要真说清是什么口径，不能是一句废话
                self.assertTrue(
                    any(k in note for k in ("MRR", "累计", "经常性", "不可年化")),
                    "%s 的 metric_note 没说清口径：%s" % (cid, note[:40]))

    def test_packs_cover_the_ready_set(self):
        """今天这 5 条是 triage 判定的 ready 集合；内容包不能漏人。"""
        expect = {"1lookup", "stan", "magicslides-app", "autoreels-ai", "insect-bite-id"}
        self.assertEqual(set(CP.PACKS), expect)

    def test_no_stale_marketplace_price_in_headlines(self):
        """核实过的错数不能留在 headline 里。

        magicslides / autoreels / insect-bite 的「售价 $XXX / N.x 倍数」来自
        FOR SALE 列表里的**别的项目**，原文里根本不存在；stan 的「第 1 名」
        实为 #3。这些是最容易被抄回正文的坑，逐个钉住。
        """
        banned = {
            "magicslides-app": ["$500K", "4.6x", "$9.1K"],
            "autoreels-ai": ["$50K", "3.5x", "$1.2K"],
            "insect-bite-id": ["$75K", "1.6x", "$3.9K"],
            "stan": ["第 1 名"],
            "1lookup": ["$236,401"],
        }
        for cid, needles in banned.items():
            head = CP.PACKS[cid]["metrics"]["headline"]
            for n in needles:
                with self.subTest(cid=cid, needle=n):
                    self.assertNotIn(n, head,
                                     "%s 的 headline 还留着未证实的数字 %s" % (cid, n))


class TestApplyRoundTrip(unittest.TestCase):
    """--apply 真写盘那一步：字段写进去、metrics 整个替换（不 merge）。"""

    def setUp(self):
        self.path = CP.CAND_PATH
        with open(self.path, encoding="utf-8") as f:
            self.orig = f.read()

    def tearDown(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.orig)

    def test_apply_writes_content_and_replaces_metrics(self):
        # 先造一条「已发布、内容包被清空、数字还是采集时的错数」的候选。
        # 不能拿 1lookup 来试 —— 它发布完就不在候选池里了，apply 自然匹配不到。
        cands = json.loads(self.orig)
        probe = {"id": "_test_probe", "name": "Probe",
                 "metrics": {"headline": "$236,401；月环比 +5%", "mrr": 245026.11}}
        cands.insert(0, probe)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cands, f, ensure_ascii=False, indent=1)
            f.write("\n")

        # 借用 1lookup 的内容包，但挂在探针 id 上
        CP.PACKS["_test_probe"] = CP.PACKS["1lookup"]
        try:
            rc = CP.cmd_apply(["_test_probe"])
            self.assertEqual(rc, 0)

            with open(self.path, encoding="utf-8") as f:
                after = [c for c in json.load(f) if c["id"] == "_test_probe"][0]
        finally:
            CP.PACKS.pop("_test_probe", None)

        self.assertEqual(PR.content_gaps(after), [], "写完应当能过发布闸门")
        self.assertIn("$244,029", after["metrics"]["headline"])
        # 旧的 245026.11 必须被整个替换掉，不能 merge 留下来
        self.assertNotEqual(after["metrics"].get("mrr"), 245026.11)
        self.assertEqual(after["metrics"]["mrr"], 244029)

    def test_apply_refuses_unknown_id(self):
        self.assertEqual(CP.cmd_apply(["does-not-exist"]), 1)


class TestPublishScriptWiring(unittest.TestCase):
    """确认脚本本身没有被改歪：它必须真的调 promote，而不是自己写 cases.json。"""

    def test_uses_promote_endpoint_only(self):
        with open(os.path.join(ROOT, "scripts", "publish_ready.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("admin.promote(", src)
        self.assertNotIn('save_json("cases"', src)
        self.assertNotIn("cases.json\", \"w\"", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
