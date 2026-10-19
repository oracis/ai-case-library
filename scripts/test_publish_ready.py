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


LONG_A = "这是一条刻意写得很长很长以便通过二十字下限校验的理由文案"

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
        """每个内容包都要**对得上一个真实案例或候选**，不能有写错的 id。

        ⚠️ 原断言硬编码 `set(PACKS) == {5条}`，方向是反的：
        每新增一批内容包它就红一次，而红的原因与代码质量无关 ——
        于是人会习惯性忽略它，等它真报警时也不信了。

        ⚠️ 第二版用「候选池里不存在的 id」当判据，也踩了同一个坑：
        promote 会把候选**移出**候选池，于是内容包一被用掉、
        断言就红。内容包的寿命横跨「候选期」与「已发布期」两个状态，
        判据必须横跨两者都成立。
        """
        with open(CP.CAND_PATH, encoding="utf-8") as f:
            pool = {c.get("id") for c in json.load(f)}
        with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
            published = {c.get("id") for c in json.load(f)}

        # 内容包可以对应「还在候选池」或「已晋升成案例」——两者都算对得上。
        dangling = set(CP.PACKS) - pool - published
        self.assertEqual(dangling, set(),
                         "这些内容包既没有对应候选也没有对应案例（id 写错了？）")

        # 本轮 7 条 Stripe 深核通过的候选，内容包必须都在
        expect = {"cometly", "vid-ai", "podawaa", "publbee",
                  "vectosolve", "augora-ai",
                  "wpconvert-ai-convert-ai-sites-to-wordpress"}
        self.assertEqual(expect - set(CP.PACKS), set(),
                         "本轮 Stripe 深核通过的候选缺内容包")

    def test_new_batch_declares_caliber_is_mrr(self):
        """🚨 7 条新内容包统一声明 MRR 口径（红线 1：收入口径不许混用）。

        候选池里`caliber` 一律是 "mrr"，而内容包的 metric_note 是读者看到的
        唯一口径说明 —— 两者若不一致，案例页上会出现「标 MRR、讲累计」的矛盾。
        """
        for cid in ("cometly", "vid-ai", "podawaa", "publbee",
                    "vectosolve", "augora-ai",
                    "wpconvert-ai-convert-ai-sites-to-wordpress"):
            with self.subTest(cid=cid):
                note = CP.PACKS[cid]["metrics"]["metric_note"]
                self.assertIn("口径统一用", note)
                self.assertIn("MRR", note)

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

    def test_apply_preserves_caliber_keys(self):
        """🚨 钉住「整体替换 metrics 不许抹掉口径字段」（2026-10-11 修的实质缺陷）。

        `cmd_apply` 原本 `c["metrics"] = dict(pack["metrics"])` 整个换掉，
        而内容包只带展示字段 ⇒ 深核流程产出的 `caliber` / `mrr_growth_30d` /
        `revenue_growth_30d` / `customers` 全被抹掉。

        为什么这不是洁癖：
          · `caliber` 是红线「收入口径不许混用」的落点，promote 读它写进案例 ——
            抹掉后案例只剩一个裸数字，没有任何口径声明；
          · cometly 的 MRR -4.9% vs 收入 +35.4% 那个背离，全靠这两个增速字段
            才看得见 —— 抹掉之后案例页会印出一个自相矛盾的增长故事。
        """
        cands = json.loads(self.orig)
        probe = {
            "id": "_test_probe2", "name": "Probe2",
            "metrics": {
                "headline": "MRR $1,234（采集时的旧快照）", "mrr": 1234,
                "caliber": "mrr", "growth_30d": -4.9,
                "mrr_growth_30d": -4.9, "revenue_growth_30d": 35.4,
                "customers": 288,
            },
        }
        cands.insert(0, probe)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cands, f, ensure_ascii=False, indent=1)
            f.write("\n")

        CP.PACKS["_test_probe2"] = {
            "metrics": {"headline": "MRR $999（核实后）", "mrr": 999,
                        "metric_note": "口径统一用 MRR，累计 $0 不可年化。"},
            "what_it_does": "做一件事", "verdict": "一句话判断",
            "how_it_makes_money": "按月订阅",
            "why_it_works": [LONG_A, LONG_A, LONG_A],
            "playbook": [LONG_A, LONG_A, LONG_A],
            "signals": ["信号一", "信号二"],
        }
        try:
            self.assertEqual(CP.cmd_apply(["_test_probe2"]), 0)
            with open(self.path, encoding="utf-8") as f:
                after = [c for c in json.load(f) if c["id"] == "_test_probe2"][0]
        finally:
            CP.PACKS.pop("_test_probe2", None)

        m = after["metrics"]
        # 展示字段：内容包的值必须赢（采集时的旧快照要被替换掉）
        self.assertEqual(m["headline"], "MRR $999（核实后）")
        self.assertEqual(m["mrr"], 999)
        # 口径字段：内容包没提到 ⇒ 必须原样活下来
        self.assertEqual(m["caliber"], "mrr")
        self.assertEqual(m["mrr_growth_30d"], -4.9)
        self.assertEqual(m["revenue_growth_30d"], 35.4)
        self.assertEqual(m["growth_30d"], -4.9)
        self.assertEqual(m["customers"], 288)

    def test_pack_can_override_reserved_if_explicit(self):
        """内容包**显式**写了口径字段时，允许覆盖 —— 保留不是封锁。"""
        cands = json.loads(self.orig)
        probe = {"id": "_test_probe3", "name": "P3",
                 "metrics": {"headline": "旧", "mrr": 1, "caliber": "mrr"}}
        cands.insert(0, probe)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cands, f, ensure_ascii=False, indent=1)
            f.write("\n")

        CP.PACKS["_test_probe3"] = {
            "metrics": {"headline": "新", "mrr": 2, "caliber": "lifetime",
                        "metric_note": "口径改成累计收入，不可年化。"},
            "what_it_does": "做一件事", "verdict": "一句话判断",
            "how_it_makes_money": "按月订阅",
            "why_it_works": [LONG_A, LONG_A, LONG_A],
            "playbook": [LONG_A, LONG_A, LONG_A],
            "signals": ["信号一", "信号二"],
        }
        try:
            self.assertEqual(CP.cmd_apply(["_test_probe3"]), 0)
            with open(self.path, encoding="utf-8") as f:
                after = [c for c in json.load(f) if c["id"] == "_test_probe3"][0]
        finally:
            CP.PACKS.pop("_test_probe3", None)
        self.assertEqual(after["metrics"]["caliber"], "lifetime")


class TestSegmentCarry(unittest.TestCase):
    """钉住「赛道标记必须跟着晋升一起搬」（2026-10-11）。

    背景：cometly 的 `solo_possible` 被官网证伪（9 人团队、公开扩编、
    Enterprise 配专属 solutions engineer）⇒ 被标成 `segment="赛道样本"` +
    `exclude_from_solo_cases=true`，口径是「写赛道体量，不写一个人怎么做」。

    🚨 而 promote 里的 `new_case` 是**白名单**构造：候选上没列的字段一律不带。
    这个标记全库只有 promote 一处读 —— 不显式透传，它就在晋升那一刻蒸发，
    cometly 得以「一个人怎么做」的身份进 cases.json。
    这类静默丢字段最难发现：晋升返回 201、案例页正常渲染，只是口径反了。

    另外钉住反向：白名单**不能**开成「全字段透传」。promote 上游是 HTTP
    请求体，无条件透传等于让调用方往对外案例页注入任意字段。
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, ROOT)
        import server                                                   # noqa: E402
        cls.server = server

    def test_carries_all_three_segment_fields(self):
        out = self.server._carry_segment({
            "id": "cometly",
            "segment": "赛道样本",
            "segment_reason": "体量够证明赛道有 $20 万/月的生意，但 solo_possible 被官网证伪",
            "exclude_from_solo_cases": True,
        })
        self.assertEqual(out, {
            "segment": "赛道样本",
            "segment_reason": "体量够证明赛道有 $20 万/月的生意，但 solo_possible 被官网证伪",
            "exclude_from_solo_cases": True,
        })

    def test_carries_partial(self):
        """只有 segment 没有 reason 也要搬 —— 半截标记仍是有意义的信号。"""
        out = self.server._carry_segment({"segment": "赛道样本"})
        self.assertEqual(out, {"segment": "赛道样本"})

    def test_ordinary_candidate_carries_nothing(self):
        """普通个人案例不该被塞进任何赛道标记。"""
        out = self.server._carry_segment({
            "id": "publbee", "name": "Publbee", "mrr": 3010,
            "segment": None, "exclude_from_solo_cases": False,
        })
        self.assertEqual(out, {})

    def test_does_not_leak_unrelated_fields(self):
        """🚨 反向断言：不能变成「所有字段都搬」。"""
        out = self.server._carry_segment({
            "segment": "赛道样本",
            "evil_injected": "x",
            "_internal": "y",
            "note": "内部备注",
        })
        self.assertEqual(out, {"segment": "赛道样本"})

    def test_real_cometly_is_marked_after_promote(self):
        """cometly 的赛道标记必须**最终落在 cases.json 里**。

        ⚠️ 判据不能写「在 candidates.json 里」—— cometly 已于 2026-10-11
        正式晋升，不再留在候选池。这条断言最初就钉在候选池上，
        结果晋升当天它立刻变红，而红的原因跟代码质量无关。
        标记的**最终归宿是案例**（promote 之后），所以断言就钉在那里 ——
        这才是「标记有没有被丢掉」唯一有意义的地方。
        """
        with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
            raw = f.read()
        cases = json.loads(raw)
        cometly = [c for c in cases if c.get("id") == "cometly"]
        self.assertEqual(len(cometly), 1, "cometly 应已晋升为案例")
        out = self.server._carry_segment(cometly[0])
        self.assertEqual(out.get("segment"), "赛道样本")
        self.assertIs(out.get("exclude_from_solo_cases"), True)

    def test_no_case_lost_its_segment_mark(self):
        """反向断言：标了赛道的案例，标记必须还在。

        防止将来有人「为了简化 new_case」把 _carry_segment 删掉 ——
        那种改动不会让任何单条测试变红，只会安静地让 cometly
        以「一个人怎么做」的口径对外展示。
        """
        with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
            cases = json.load(f)
        marked = [c["id"] for c in cases if c.get("segment") == "赛道样本"]
        self.assertTrue(marked, "没有任何案例带赛道标记 —— 标记可能被丢了")
        for cid in marked:
            c = [x for x in cases if x["id"] == cid][0]
            self.assertIs(c.get("exclude_from_solo_cases"), True,
                          "%s 标了赛道样本却没标 exclude_from_solo_cases" % cid)


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
