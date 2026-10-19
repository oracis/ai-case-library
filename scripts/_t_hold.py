"""人工挂起通道（triage.hold / ai_verify.pick_candidates）的回归测试。

要守住的不变量：
1. **人写下的决定必须压过机器判据**。2026-10-05 实测的洞：Draftly 被 AI 判过
   publishable 之后，作者已明确「按一手数据降级、不晋升」，triage 却仍天天报它
   ready（动作文案是「去后台点发布，别再花 AI」）。机器每轮都在替人撤销决定。
2. 挂起只压 ready，**不压 deep/backfill** —— 挂起是「别自动推进」，
   不是「这条没价值」。材料真齐了，人自己改标记或删掉，不该由机器代劳。
3. 挂起必须同时堵住 triage 分级**和** pick_candidates 选取。只改前者的话，
   条目从 ready 掉到 later 就会漏进 AI 核验队列 —— 换个姿势被自动推进。
4. is_held 的真值判据不能写成 `if rec.get("hold")` —— 空串/字符串 "off" 都是
   真值陷阱（与项目里 configure() 判据踩过的坑同类）。

⚠ 固件自带数据，绝不读真实 data/（方法论 B）：测试不能随线上数据状态漂移。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import triage as T# noqa: E402
import ai_verify as A                                             # noqa: E402
import verify_rules as R                                          # noqa: E402


def cand(cid="fx", hold=None, **kw):
    c = {"id": cid, "name": "Fixture " + cid,
         "one_liner": "固件候选",
         "metrics": {"headline": "月入 $2K"},
         "sources": [{"url": "https://%s.dev/" % cid, "kind": "stripe"}]}
    if hold is not None:
        c["hold"] = hold
    c.update(kw)
    return c


def publishable_draft():
    """一条能被规则引擎判成 publishable 的草稿（材料已齐）。"""
    return {"gates": [g["key"] for g in R.GATES],
            "musts": [m["key"] for m in R.MUSTS],
            "verification": "official",
            "source_kinds": ["official"],
            "caliber": "arr"}


class IsHeldTest(unittest.TestCase):
    def test_真值判据_不吃空串与off(self):
        for rec, exp in (({}, False),
                         ({"hold": True}, True),
                         ({"hold": False}, False),
                         ({"hold": 1}, True),
                         ({"hold": 0}, False),
                         ({"hold": "true"}, True),
                         ({"hold": "yes"}, True),
                         ({"hold": "on"}, True),
                         ({"hold": "off"}, False),
                         ({"hold": ""}, False),
                         ({"hold_reason": "人工降级"}, True),
                         ({"hold_reason": ""}, False),
                         ({"hold_reason": "   "}, False)):
            self.assertEqual(T.is_held(rec), exp, rec)

    def test_非字典入参不炸(self):
        self.assertFalse(T.is_held(None))
        self.assertFalse(T.is_held("hold"))
        self.assertFalse(T.is_held([]))


class GradeHoldTest(unittest.TestCase):
    def test_挂起压过ready(self):
        """核心回归：Draftly 那条路。草稿 publishable + 人工挂起 ⇒ 不是 ready。"""
        idx = T.Index(drafts={"fx": publishable_draft()})
        got = T.score_record(cand(hold=True), "candidate", idx)
        self.assertTrue(got["draft_ready"],
                        "草稿的原始判定要留痕，不能因为挂起就改写成False")
        self.assertFalse(got["ready"],
                         "对外的 ready 必须是有效就绪；只改 grade 会留下 "
                         "grade=later 却 ready=True 的矛盾状态，调用方只读 ready 就误动")
        self.assertEqual(got["grade"], "later")
        self.assertTrue(got["held"])
        self.assertNotIn("去后台点发布", got["action"],
                         "挂起条目绝不能出现「去点发布」的动作文案")

    def test_未挂起时ready照旧(self):
        """闸门不能反向失效：不挂就要照常报 ready。"""
        idx = T.Index(drafts={"fx": publishable_draft()})
        got = T.score_record(cand(), "candidate", idx)
        self.assertTrue(got["ready"])
        self.assertEqual(got["grade"], "ready")
        self.assertFalse(got["held"])

    def test_hold_reason也能挂起(self):
        idx = T.Index(drafts={"fx": publishable_draft()})
        got = T.score_record(cand(hold_reason="作者按一手数据决定降级"), "candidate", idx)
        self.assertEqual(got["grade"], "later")
        self.assertTrue(got["held"])
        self.assertIn("hold", {f["key"] for f in got["flags"]})

    def test_挂起不压deep(self):
        """挂起只压 ready：材料齐到能深核的条目，分级仍按机器判据走。"""
        rich = cand(hold=True, category="AI 工具",
                    metrics={"headline": "MRR $12,000", "mrr": 12000},
                    verification="stripe",
                    sources=[{"url": "https://fx.dev/", "kind": "first_hand"},
                             {"url": "https://trustmrr.com/startup/fx",
                              "kind": "third_party"}])
        got = T.score_record(rich, "candidate")
        self.assertTrue(got["held"])
        self.assertIn(got["grade"], ("deep", "later"),
                      "挂起条目不该被抬成 ready，但深核资格不该被误伤")

    def test_重复仍压过挂起(self):
        """硬标记优先级最高：已发布重复的条目即便挂起也是 drop。"""
        idx = T.Index(cases=[{"id": "fx", "name": "Fixture fx"}])
        got = T.score_record(cand(hold=True), "candidate", idx)
        self.assertEqual(got["grade"], "drop")


class PickCandidatesHoldTest(unittest.TestCase):
    def test_挂起条目不进批量核验队列(self):
        """只改 triage 不够：ready→later 会漏进这里，必须一起堵。"""
        got = A.pick_candidates([cand("a"), cand("b", hold=True)])
        self.assertEqual([c["id"] for c in got], ["a"])

    def test_显式include_held可放回(self):
        got = A.pick_candidates([cand("a"), cand("b", hold=True)], include_held=True)
        self.assertEqual(sorted(c["id"] for c in got), ["a", "b"])

    def test_显式点名不受挂起限制(self):
        """人点名本身就是最新决定，必须能覆盖挂起标记。"""
        got = A.pick_candidates([cand("a"), cand("b", hold=True)], ids=["b"])
        self.assertEqual([c["id"] for c in got], ["b"])


if __name__ == "__main__":
    unittest.main()
