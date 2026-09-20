"""verify_rules.py 的单元测试。

这一层要守住的核心不变量：
1. 门槛是「一道成立就进库」：只要有一道被确认成立，AI 的反证就不再一票否决，
   降级为「待人工复核」提醒。只有「一道都没成立 + 有明确反证」才不进库。
2. 缺硬性必填 → 永远不可发布，跟质量分多高无关
3. 门槛「拿不准」（unknown）≠ 被否决：它只是一次没核到，交人工确认，不判死
4. 没有一手来源不再一票否决（用户要求放开），降级为「待人工复核」提醒

历史教训：只有中文二手转述是库里两个数字错误的共同成因。它现在是提醒而非拦路，
但仍然要能被眼尖看到 —— 所以第 4 条只放宽「能不能发」，没放宽「要不要提醒」。

同样地，第 1 条放宽的是「谁说了算」：AI 一次检索的否定不替人判死候选，
但它找到的反证必须原样摆到界面上（denied_gates / warnings），由人来推翻。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules as R                                        # noqa: E402

ALL_GATES = [g["key"] for g in R.GATES]
ALL_MUSTS = [m["key"] for m in R.MUSTS]
ALL_BONUS = [b["key"] for b in R.BONUS]


def full(**over):
    """一份默认全通过的配置，测试里只覆盖关心的那项。"""
    v = {
        "gates": list(ALL_GATES),
        "musts": list(ALL_MUSTS),
        "bonus": [],
        "caliber": "arr",
        "verification": "official",
        "source_kinds": ["official"],
    }
    v.update(over)
    return v


class Blank(unittest.TestCase):
    def test_blank_not_publishable(self):
        self.assertFalse(R.evaluate(R.blank())["publishable"])

    def test_blank_gates_are_unverified_not_denied(self):
        # 空白草稿是「还没开始核」，不是「被证伪」—— 不该被判死
        r = R.evaluate(R.blank())
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertEqual(len(r["unverified_gates"]), len(R.GATES))

    def test_denied_gate_kills_tier(self):
        # 明确否决仍然没有档次可言 —— 它是「不进库」
        r = R.evaluate(full(gates=[], gates_denied=list(ALL_GATES)))
        self.assertIsNone(r["tier"])
        self.assertFalse(r["publishable"])


class Gates(unittest.TestCase):
    def test_一道成立就进库_有反证也不拦(self):
        # 本轮的核心改动：一道 yes 兜住整条，另一道的反证只降级成提醒
        v = full(gates=[ALL_GATES[0]], gates_denied=[ALL_GATES[-1]])
        r = R.evaluate(v)
        self.assertTrue(r["publishable"])
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertIn("denied_" + ALL_GATES[-1], [w["key"] for w in r["warnings"]])
        self.assertIn("含 1 项 AI 反证", r["verdict"])

    def test_一道都没成立_遇反证才拦(self):
        # 没有一道被确认成立，AI 又拿到了明确反证 —— 这种进库就是污染
        r = R.evaluate(full(gates=[], gates_denied=[ALL_GATES[-1]]))
        self.assertFalse(r["publishable"])
        self.assertEqual([g["key"] for g in r["gates_failed"]], [ALL_GATES[-1]])
        self.assertIsNone(r["tier"])

    def test_一道都没成立且全被否_也拦(self):
        v = full(gates=[], gates_denied=list(ALL_GATES))
        r = R.evaluate(v)
        self.assertFalse(r["publishable"])
        self.assertEqual(len(r["gates_failed"]), len(R.GATES))

    def test_gate_denial_kills_tier_even_with_full_score(self):
        # 关键不变量：质量分满分也救不回「一道都没成立」的门槛否决
        r = R.evaluate(full(gates=[], gates_denied=list(ALL_GATES),
                            bonus=list(ALL_BONUS)))
        self.assertFalse(r["publishable"])
        self.assertIsNone(r["tier"])
        self.assertGreater(r["bonus_score"], R.TIER_THRESHOLD)

    def test_gate_denial_message_says_rejected(self):
        r = R.evaluate(full(gates=[], gates_denied=list(ALL_GATES)))
        self.assertIn("门槛", r["verdict"])
        self.assertIn("不进库", r["verdict"])

    def test_unverified_gates_pass_to_draft(self):
        # 核心放宽：AI 拿不准 ≠ 被否 —— 进草稿交人工确认，不判死
        r = R.evaluate(full(gates=[]))
        self.assertTrue(r["publishable"])
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertEqual(len(r["unverified_gates"]), len(R.GATES))
        self.assertIn("待人工复核", r["verdict"])

    def test_denied_gates_列出所有反证_不论拦没拦(self):
        # 界面要靠这个字段把反证画出来 —— 放行的那些更不能丢
        r = R.evaluate(full(gates=[ALL_GATES[0]], gates_denied=ALL_GATES[1:]))
        self.assertEqual([g["key"] for g in r["denied_gates"]], ALL_GATES[1:])
        self.assertEqual(len(r["gates_failed"]), 0)

    def test_unverified_and_denied_are_exclusive(self):
        # 一道门槛不能既是「被否」又是「拿不准」
        r = R.evaluate(full(gates=[], gates_denied=[ALL_GATES[0]]))
        keys = [g["key"] for g in r["unverified_gates"]]
        self.assertNotIn(ALL_GATES[0], keys)
        self.assertEqual(len(r["unverified_gates"]) + len(r["denied_gates"]),
                         len(R.GATES))


class Musts(unittest.TestCase):
    def test_missing_one_blocking_must_blocks_publish(self):
        # 缺一个**拦发布**的必填项就必须拦下 —— 只看拦的那种，别把
        # 非阻塞的标记算进来，否则这条测试会随标记项数量变化而失去意义。
        blocking = [m["key"] for m in R.MUSTS if m.get("blocking", True)]
        v = full(musts=blocking[:-1])
        r = R.evaluate(v)
        self.assertFalse(r["publishable"])
        self.assertEqual(r["missing_count"], 1)

    def test_只缺人工核读不拦发布(self):
        # 2026-09-17 改：human_read 从「拦发布」降级成「标记 + 提醒」。
        # 它不勾照样能进库，但必须留下一句提醒和 human_read=False，
        # 让「这条没人核过」在案例上看得见。
        v = full(musts=[m["key"] for m in R.MUSTS if m["key"] != "human_read"])
        r = R.evaluate(v)
        self.assertTrue(r["publishable"])
        self.assertEqual(r["missing_count"], 0)
        self.assertFalse(r["human_read"])
        self.assertIn("pending_human_read", [w["key"] for w in r["warnings"]])

    def test_勾上人工核读后提醒消失(self):
        r = R.evaluate(full())
        self.assertTrue(r["human_read"])
        self.assertNotIn("pending_human_read", [w["key"] for w in r["warnings"]])

    def test_只缺口径一致不拦发布(self):
        # 2026-09-17 用户定：caliber_consistent 也降级成标记。
        # 它拦发布时是死胡同 —— AI 结构性地核不动（要支付侧页面，常抓不到），
        # 一律答 False，于是每条候选都永久停在这一项。降级后不拦，但挂提醒，
        # 且 AI 的判断与理由会存进草稿给人看（caliber_reason）。
        v = full(musts=[m["key"] for m in R.MUSTS if m["key"] != "caliber_consistent"])
        r = R.evaluate(v)
        self.assertTrue(r["publishable"])
        self.assertIn("pending_caliber_consistent",
                      [w["key"] for w in r["warnings"]])

    def test_只剩口径已判定一项拦发布(self):
        # 守住这件事本身。再往 MUSTS 里加非阻塞项时这条会提醒你：
        # 要同时补 PENDING_WARNINGS 文案（否则界面出现「尚未满足：xxx」这种兜底）。
        self.assertEqual(
            [m["key"] for m in R.MUSTS if not m.get("blocking", True)],
            ["caliber_consistent", "human_read"])
        self.assertEqual(
            [m["key"] for m in R.MUSTS if m.get("blocking", True)],
            ["caliber_decided"])

    def test_每个非阻塞项都有提醒文案(self):
        # 缺文案就会在界面上出现「尚未满足：我亲自看过原文」这种机器味兜底
        for m in R.MUSTS:
            if m.get("blocking", True):
                continue
            self.assertIn(m["key"], R.PENDING_WARNINGS, m["key"])

    def test_人工核读时间戳原样带出(self):
        r = R.evaluate(full(human_read_at="2026-09-17 14:00"))
        self.assertEqual(r["human_read_at"], "2026-09-17 14:00")
        # 没有时是空串不是 None —— 界面直接渲染，别显示出 "None"
        self.assertEqual(R.evaluate(full())["human_read_at"], "")

    def test_no_caliber_blocks(self):
        self.assertFalse(R.evaluate(full(caliber=""))["publishable"])

    def test_bad_caliber_blocks(self):
        self.assertFalse(R.evaluate(full(caliber="随便写的"))["publishable"])

    def test_every_caliber_in_enum_passes(self):
        for k in R.CALIBER_KEYS:
            self.assertTrue(R.evaluate(full(caliber=k))["publishable"], k)

    def test_no_verification_blocks(self):
        self.assertFalse(R.evaluate(full(verification=""))["publishable"])

    def test_every_verification_in_enum_passes(self):
        for k in R.VERIFICATION_KEYS:
            self.assertTrue(R.evaluate(full(verification=k))["publishable"], k)

    def test_no_source_blocks(self):
        self.assertFalse(R.evaluate(full(source_kinds=[]))["publishable"])


class SourceTiers(unittest.TestCase):
    """二手转述必须被看见 —— 这是库里真实错误共同的成因。

    2026-09-16 起它从「一票否决」改成提醒：能不能发由人决定，
    但必须标出来，否则数字会被当成已核实。
    """

    def _warn_keys(self, source_kinds):
        return [w["key"] for w in R.evaluate(full(source_kinds=source_kinds))["warnings"]]

    def test_secondary_only_warns_not_blocks(self):
        r = R.evaluate(full(source_kinds=["secondary"]))
        self.assertTrue(r["publishable"])        # 不再一票否决
        keys = [w["key"] for w in r["warnings"]]
        self.assertIn("secondary_only", keys)
        self.assertIn("primary_source_kind", keys)

    def test_secondary_plus_press_still_warns(self):
        # 媒体 + 二手，没有一手来源 → 可进草稿，但必须在提醒里
        r = R.evaluate(full(source_kinds=["secondary", "press"]))
        self.assertTrue(r["publishable"])
        self.assertIn("primary_source_kind", self._warn_keys(["secondary", "press"]))

    def test_press_alone_warns(self):
        # 可信媒体是 B 级，不算一手 —— 提醒但放行
        r = R.evaluate(full(source_kinds=["press"]))
        self.assertTrue(r["publishable"])
        self.assertIn("primary_source_kind", self._warn_keys(["press"]))

    def test_secondary_plus_official_is_clean(self):
        # 二手转述可以保留，只要同时有一手来源
        r = R.evaluate(full(source_kinds=["secondary", "official"]))
        self.assertTrue(r["publishable"])
        self.assertEqual(r["warnings"], [])

    def test_stripe_is_primary(self):
        r = R.evaluate(full(source_kinds=["stripe"]))
        self.assertTrue(r["publishable"])
        self.assertEqual(r["warnings"], [])

    def test_verdict_counts_pending_review(self):
        r = R.evaluate(full(source_kinds=["press"]))
        self.assertIn("待人工复核", r["verdict"])


class Tiers(unittest.TestCase):
    """档位政策（2026-09-20 放宽为三档之后）。

    顺序是「先看来源，再看质量分」：一手来源进精品，只有第三方来源且口径待核
    进实核，其余进备选。质量分只在没有来源线索可用时兜底 —— 所以下面每个
    用例都要同时说清来源构成和核实等级，单看 bonus 已经测不出档位了。
    """

    def test_no_bonus_with_first_hand_goes_premium(self):
        # full() 默认带 official（一手来源）→ 精品，与质量分无关
        r = R.evaluate(full())
        self.assertTrue(r["publishable"])
        self.assertEqual(r["tier"], R.TIER_PREMIUM)

    def test_third_party_only_goes_standard(self):
        # 只有第三方来源、口径待核 → 实核池（上一版会被打成备选，这正是放宽点）
        r = R.evaluate(full(source_kinds=["press"], verification="partial"))
        self.assertTrue(r["publishable"])
        self.assertEqual(r["tier"], R.TIER_STANDARD)

    def test_self_reported_goes_backup(self):
        # 只有创始人自报 → 备选池（这才是真正意义上的「待核」）
        r = R.evaluate(full(source_kinds=["founder"], verification="founder"))
        self.assertTrue(r["publishable"])
        self.assertEqual(r["tier"], R.TIER_BACKUP)

    def test_full_bonus_goes_premium(self):
        r = R.evaluate(full(bonus=list(ALL_BONUS)))
        self.assertEqual(r["tier"], R.TIER_PREMIUM)
        self.assertEqual(r["bonus_score"], r["bonus_max"])

    def test_threshold_boundary(self):
        # 加分项是 20/20/15×4，够线的最低组合是 20+20+15+15 = 70。
        # 三道全成立时还有 +10 自动加成，所以这里是 80。
        seventy = ["founder_disclosure", "pricing_confirmed",
                   "corrections_found", "replicable_low"]
        r = R.evaluate(full(bonus=seventy))
        self.assertEqual(r["bonus_score"], 70 + R.GATE_ALL_BONUS["points"])
        self.assertEqual(r["tier"], R.TIER_PREMIUM)

    def test_below_threshold_is_backup(self):
        # 没有一手来源时，质量分是档位的兜底：55 < 60 → 备选。
        # 20+20+15 = 55；把门槛加成排除掉（gates=[]）才测得准。
        fifty_five = ["founder_disclosure", "pricing_confirmed", "replicable_low"]
        r = R.evaluate(full(source_kinds=["founder"], verification="founder",
                            gates=[], bonus=fifty_five))
        self.assertEqual(r["bonus_score"], 55)
        self.assertLess(r["bonus_score"], R.TIER_THRESHOLD)
        self.assertEqual(r["tier"], R.TIER_BACKUP)

    def test_unknown_bonus_key_ignored(self):
        r = R.evaluate(full(gates=[], bonus=["不存在的加分项"]))
        self.assertEqual(r["bonus_score"], 0)

    def test_verdict_contains_tier_hint(self):
        # 三档各自的判定文案都要出现对应的档位名
        self.assertIn("精品", R.evaluate(full(bonus=list(ALL_BONUS)))["verdict"])
        self.assertIn("实核", R.evaluate(
            full(source_kinds=["press"], verification="partial"))["verdict"])
        self.assertIn("备选", R.evaluate(
            full(source_kinds=["founder"], verification="founder"))["verdict"])


class GateBonus(unittest.TestCase):
    """三道门槛全成立的自动加成。

    它不是第七个勾选框（那样界面会出现一个勾不动的项），而是从 gates 推导出来的。
    测它的重点是「差一道就不给」 —— 加成给的时机比给多少重要。
    """

    def test_三道全成立_自动加分(self):
        r = R.evaluate(full(gates=list(ALL_GATES), bonus=[]))
        self.assertEqual(r["gate_bonus"], R.GATE_ALL_BONUS["points"])
        self.assertEqual(r["bonus_score"], R.GATE_ALL_BONUS["points"])
        self.assertIn(R.GATE_ALL_BONUS["label"], r["bonus_hits"])

    def test_少一道就一分不加(self):
        r = R.evaluate(full(gates=ALL_GATES[:-1], bonus=[]))
        self.assertEqual(r["gate_bonus"], 0)
        self.assertEqual(r["bonus_score"], 0)

    def test_有反证时不给加成(self):
        # 一道既在 gates 又在 gates_denied 的脏数据，不能骗到加成
        r = R.evaluate(full(gates=list(ALL_GATES), gates_denied=[ALL_GATES[0]]))
        self.assertEqual(r["gate_bonus"], 0)

    def test_加成能把差十分的顶进精品(self):
        # 用「只有创始人自报」的输入：档位此时完全由质量分决定，
        # 才测得出 +10 加成是不是真把 50 顶过了 60 线。
        # （若用默认的一手来源输入，档位早被来源定死，加成有没有生效就看不出。）
        fifty = ["founder_disclosure", "corrections_found", "replicable_low"]
        base = dict(source_kinds=["founder"], verification="founder")
        without = R.evaluate(full(gates=[], bonus=fifty, **base))
        withgates = R.evaluate(full(gates=list(ALL_GATES), bonus=fifty, **base))
        self.assertEqual(without["bonus_score"], 50)
        self.assertEqual(without["tier"], R.TIER_BACKUP)
        self.assertEqual(withgates["bonus_score"], 60)
        self.assertEqual(withgates["tier"], R.TIER_PREMIUM)

    def test_加成计入满分上限(self):
        # 界面显示 X / bonus_max，加成必须算进上限，否则会出现 110/100
        r = R.evaluate(full())
        self.assertEqual(r["bonus_max"],
                         sum(b["points"] for b in R.BONUS) + R.GATE_ALL_BONUS["points"])
        self.assertEqual(R.evaluate(full(bonus=list(ALL_BONUS)))["bonus_score"],
                         r["bonus_max"])

    def test_加成不是可勾项(self):
        # 手动勾 gates_all_yes 不该重复计分
        self.assertNotIn(R.GATE_ALL_BONUS["key"], [b["key"] for b in R.BONUS])
        self.assertEqual(R.score({"bonus": [R.GATE_ALL_BONUS["key"]]})[0], 0)


class Robustness(unittest.TestCase):
    def test_none_input(self):
        self.assertFalse(R.evaluate(None)["publishable"])

    def test_dunder_ignored(self):
        # bonus 里混进非字符串不该炸（gates=[] 排除自动加成，才测得准）
        self.assertEqual(R.evaluate(full(gates=[], bonus=[1, 2]))["bonus_score"], 0)

    def test_result_shape_stable(self):
        keys = {"ok", "publishable", "gates_failed", "denied_gates",
                "unverified_gates",
                "missing", "missing_count", "warnings", "warning_count",
                "bonus_score", "bonus_max", "bonus_hits", "threshold",
                "tier", "tier_label", "verdict", "gate_bonus", "gate_bonus_label"}
        self.assertTrue(keys.issubset(R.evaluate(R.blank()).keys()))


class Schema(unittest.TestCase):
    def test_schema_exposes_every_rule(self):
        s = R.schema()
        self.assertEqual(len(s["gates"]), len(R.GATES))
        self.assertEqual(len(s["musts"]), len(R.MUSTS))
        self.assertEqual(len(s["bonus"]), len(R.BONUS))
        self.assertEqual(len(s["calibers"]), len(R.CALIBERS))
        self.assertEqual(len(s["verifications"]), len(R.VERIFICATIONS))

    def test_schema_is_json_serializable(self):
        import json
        json.dumps(R.schema(), ensure_ascii=False)

    def test_every_rule_key_unique(self):
        for group in ("gates", "musts", "bonus"):
            keys = [x["key"] for x in R.schema()[group]]
            self.assertEqual(len(keys), len(set(keys)), group)


class CaseTierPolicy(unittest.TestCase):
    """案例的默认定档政策（2026-09-20 放宽为三档）。

    背景：上一版只有两档，政策是「有一手来源 → 精品，否则 → 备选」。跑下来
    17 条备选里**全部都有第三方来源**（press / review），只是拿不到一手证据 ——
    它们和「只有创始人自报」的案例被塞进同一个备选，抹掉了「有出处」和
    「没人核过」的区别。

    政策（顺序）：一手来源 → 精品；verification 为 partial（有第三方来源、
    口径待核）→ 实核；质量分够线 → 精品；其余 → 备选。
    """

    def test_有一手来源_即使零分也进精品(self):
        t, why = R.default_case_tier({
            "sources": [{"url": "https://trustmrr.com/startup/x", "kind": "stripe"}],
            "quality_score": 0,
        })
        self.assertEqual(t, R.TIER_PREMIUM)
        self.assertIn("stripe", why)

    def test_official_也算一手(self):
        t, _ = R.default_case_tier({
            "sources": [{"url": "https://x.com/", "kind": "official"}],
            "quality_score": 0,
        })
        self.assertEqual(t, R.TIER_PREMIUM)

    def test_只有第三方来源_口径待核进实核(self):
        """放宽点就在这一条：有独立第三方来源、标了 partial 的，进实核而非备选。"""
        t, why = R.default_case_tier({
            "sources": [{"url": "https://saasxtra.com/?p=1", "kind": "review"}],
            "verification": "partial",
            "quality_score": 0,
        })
        self.assertEqual(t, R.TIER_STANDARD)
        self.assertIn("第三方来源", why)

    def test_press不算一手_但够得着实核(self):
        """媒体报道是转述，正是这个库要防的那一层 —— 但它仍算「有第三方来源」，
        标 partial 时归实核；没标 partial 时够不到实核，落回备选。"""
        t, _ = R.default_case_tier({
            "sources": [{"url": "https://techcrunch.com/x", "kind": "press"}],
            "verification": "partial",
            "quality_score": 0,
        })
        self.assertEqual(t, R.TIER_STANDARD)
        t2, _ = R.default_case_tier({
            "sources": [{"url": "https://techcrunch.com/x", "kind": "press"}],
            "quality_score": 0,
        })
        self.assertEqual(t2, R.TIER_BACKUP)

    def test_质量分够_没有一手来源也进精品(self):
        """政策是「或」不是「只」：核得够实诚照样能进精品。"""
        t, why = R.default_case_tier({
            "sources": [{"url": "https://x.com/", "kind": "review"}],
            "quality_score": R.TIER_THRESHOLD,
        })
        self.assertEqual(t, R.TIER_PREMIUM)
        self.assertIn("质量分", why)

    def test_草稿的source_kinds也能认(self):
        """草稿有扁平 source_kinds，案例只有 sources —— 两条路都要通。"""
        t, _ = R.default_case_tier({"source_kinds": ["stripe", "press"],
                                    "quality_score": 0})
        self.assertEqual(t, R.TIER_PREMIUM)
        t2, _ = R.default_case_tier({"source_kinds": ["press", "secondary"],
                                     "quality_score": 0})
        self.assertEqual(t2, R.TIER_BACKUP)

    def test_first_hand_kinds_去重且只认两种(self):
        rec = {"source_kinds": ["stripe", "stripe", "press"],
               "sources": [{"kind": "official"}, {"kind": "review"},
                           {"kind": "stripe"}]}
        self.assertEqual(R.first_hand_kinds(rec), {"stripe", "official"})

    def test_空记录不炸(self):
        for rec in ({}, {"sources": None, "source_kinds": None},
                    {"sources": ["不是 dict"]}):
            t, _ = R.default_case_tier(rec)
            self.assertEqual(t, R.TIER_BACKUP)
        self.assertEqual(R.first_hand_kinds({}), set())

    def test_evaluate与案例政策口径一致(self):
        """边界（2026-09-20 改）：后台预览的档位必须和实际发布的一致。

        上一版两者分家 —— evaluate 按 60 分线、案例按来源 —— 结果是后台提示
        「发布到备选池」、真发布却进了精品池。现在两条路共用 default_case_tier，
        这条测试守住「不许再分叉」。
        """
        r = R.evaluate(full(source_kinds=["stripe"], bonus=[]))
        self.assertTrue(r["publishable"])
        t, _ = R.default_case_tier({"source_kinds": ["stripe"], "quality_score": 0})
        self.assertEqual(r["tier"], t)              # 预览 = 实际
        self.assertEqual(r["tier"], R.TIER_PREMIUM)

    def test_真实案例数据每条都有档位和理由(self):
        import json as _json
        import os as _os
        p = _os.path.join(ROOT, "data", "cases.json")
        with open(p, encoding="utf-8") as f:
            cases = _json.load(f)
        missing = [c.get("id") for c in cases if not c.get("tier")]
        self.assertEqual(missing, [], "案例缺档位：%s" % missing)
        no_why = [c.get("id") for c in cases if not c.get("tier_reason")]
        self.assertEqual(no_why, [], "案例缺定档理由：%s" % no_why)
        for c in cases:
            self.assertIn(c["tier"],
                          (R.TIER_PREMIUM, R.TIER_STANDARD, R.TIER_BACKUP),
                          c.get("id"))
            # 有一手来源的必须都在精品池 —— 这正是这条政策要保证的事
            if R.first_hand_kinds(c):
                self.assertEqual(c["tier"], R.TIER_PREMIUM, c.get("id"))
            # 没有一手来源、只标了 partial 的必须都在实核池 —— 放宽点的落点
            elif c.get("verification") == "partial":
                self.assertEqual(c["tier"], R.TIER_STANDARD, c.get("id"))


class EvidenceLevel(unittest.TestCase):
    """声称的核实等级必须有来源撑得住。

    这批断言是 2026-09-17 那次审计留下的：当时 11 条案例标着 stripe / official，
    登记的来源却只有第三方拆解站。等级不是装饰，是给读者的信任凭证 ——
    虚标一次，整个库的「核过」两个字都跟着贬值。所以它要有回归测试。
    """

    def test_只有stripe来源_才配得上stripe(self):
        self.assertEqual(R.best_supported_level(["stripe"]), "stripe")
        self.assertEqual(R.best_supported_level(["stripe", "press"]), "stripe")

    def test_只有官方来源_退到official(self):
        self.assertEqual(R.best_supported_level(["official"]), "official")

    def test_第三方报道_只撑得住partial(self):
        self.assertEqual(R.best_supported_level(["press"]), "partial")
        # review 是数据里一直在用、但原先没登记进 SOURCE_TIERS 的那种
        self.assertEqual(R.best_supported_level(["review"]), "partial")

    def test_创始人自述_只撑得住founder(self):
        self.assertEqual(R.best_supported_level(["founder"]), "founder")

    def test_只有中文二手_任何等级都不成立(self):
        self.assertIsNone(R.best_supported_level(["secondary"]))
        self.assertIsNone(R.best_supported_level([]))
        self.assertIsNone(R.best_supported_level(None))

    def test_review_已登记为B档来源(self):
        """review 这个 kind 早就在用，不能再是个漏登记的黑户。"""
        self.assertIn("review", R.SOURCE_TIER_KEYS)
        tier = dict((k, t) for k, _l, t, _p in R.SOURCE_TIERS)["review"]
        self.assertEqual(tier, "B")
        # B 档不算一手（first_hand_kinds 返回集合，空集合即 False）
        self.assertFalse(R.first_hand_kinds({"source_kinds": ["review"]}))

    def test_撑得住时不建议改(self):
        for v, kinds in (("stripe", ["stripe"]),
                         ("official", ["official"]),
                         ("partial", ["review"]),
                         ("founder", ["founder"]),
                         ("partial", ["stripe"])):      # 声称的更弱，没问题
            to, why = R.evidence_gap(v, kinds)
            self.assertEqual((to, why), (None, ""), "%s / %s" % (v, kinds))

    def test_虚标时降到证据撑得住的那档(self):
        self.assertEqual(R.evidence_gap("stripe", ["review"])[0], "partial")
        self.assertEqual(R.evidence_gap("official", ["press"])[0], "partial")
        self.assertEqual(R.evidence_gap("stripe", ["founder"])[0], "founder")
        # 理由要能直接给人看，不能只吐等级代号
        _to, why = R.evidence_gap("stripe", ["review"])
        self.assertIn("支付网关验证", why)
        self.assertIn("口径待核", why)

    def test_连最弱等级都撑不住时不自动降(self):
        """只有中文二手：交给人工重核，不许换个标签蒙混过关。"""
        to, why = R.evidence_gap("stripe", ["secondary"])
        self.assertIsNone(to)
        self.assertTrue(why)

    def test_真实数据里没有虚标(self):
        import json as _json
        import os as _os
        p = _os.path.join(ROOT, "data", "cases.json")
        with open(p, encoding="utf-8") as f:
            cases = _json.load(f)
        bad = []
        for c in cases:
            ks = [s.get("kind") for s in (c.get("sources") or [])
                  if isinstance(s, dict) and s.get("kind")]
            to, _why = R.evidence_gap(c.get("verification"), ks)
            if to:
                bad.append("%s(%s>%s)" % (c.get("id"), c.get("verification"), to))
        self.assertEqual(bad, [], "等级高于来源证据：%s" % "、".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
