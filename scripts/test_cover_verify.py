#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/cover_verify.py` 的回归锁（2026-11-05）。

锁的是四件最容易复发的事：

1. **把「没查过」写成「通过」** —— 这是本仓库最贵的一类假绿。
   `unknown` 必须是 `unknown`，不许因为「没报错」就变成 `ok`。
2. **无证据的 ok** —— 调用方声称读到了封面，但说不出读了哪个字段、
   读到什么值 ⇒ `record()` 必须把它降级成 `unknown`。
3. **过期记录替今天作证** —— 三个月前的 `ok` 不算数，超 TTL 降级。
4. **拦错层级** —— 只有 `empty` 能 block；`unknown/local/none` 只许 warn。
   若把这三种也 block，闸会天天误拦，最后被人用 `--force` 关掉。

跑法：`cd scripts && python -X utf8 -m unittest test_cover_verify -v`
"""
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cover_verify as CV          # noqa: E402


class _Tmp(unittest.TestCase):
    """每条用例一个临时台账，绝不碰 `data/cover_readback.json`。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cv_")
        self.led = os.path.join(self.dir, "cover_readback.json")

    def rec(self, case, plat, state, ev="", source="test"):
        return CV.record(case, plat, state, ev, source=source, path=self.led)


class TestStates(_Tmp):

    def test_unknown_is_not_ok(self):
        """没有记录 ⇒ unknown。绝不能因为「没报错」就算通过。"""
        v = CV.verify("csdn", "nope", path=self.led)
        self.assertEqual(v["state"], "unknown")
        self.assertNotEqual(v["state"], "ok")

    def test_ok_needs_evidence(self):
        """声称 ok 但没给证据 ⇒ 降级 unknown（「打印了但没人读」那类假绿）。"""
        r = self.rec("a", "csdn", "ok", "")
        self.assertEqual(r["state"], "unknown")
        self.assertIn("降级", r["evidence"])

    def test_ok_with_evidence_survives(self):
        self.rec("a", "csdn", "ok", "coverImage=https://img/x.png")
        self.assertEqual(CV.verify("csdn", "a", path=self.led)["state"], "ok")

    def test_empty_stays_empty(self):
        self.rec("a", "csdn", "empty", "coverImage 为空")
        v = CV.verify("csdn", "a", path=self.led)
        self.assertEqual(v["state"], "empty")
        self.assertIn("空的", v["detail"])

    def test_illegal_state_rejected(self):
        """`record()` 不许收下没定义的状态 —— 拼错的状态名会让闸静默放行。"""
        with self.assertRaises(ValueError):
            self.rec("a", "csdn", "OK", "x")

    def test_stale_record_downgrades(self):
        """30 天前的 ok 不能替今天作证。"""
        self.rec("a", "csdn", "ok", "coverImage=https://img/x.png")
        d = CV.load_ledger(self.led)
        d["a"]["csdn"]["at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 40 * 86400))
        CV._save(d, self.led)
        v = CV.verify("csdn", "a", path=self.led)
        self.assertEqual(v["state"], "unknown")
        self.assertIn("40 天", v["detail"])

    def test_ttl_zero_disables_expiry(self):
        """`--max-age 0` = 不设上限（给人工复核用），别把开关做成没作用。"""
        self.rec("a", "csdn", "ok", "coverImage=https://img/x.png")
        d = CV.load_ledger(self.led)
        d["a"]["csdn"]["at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 400 * 86400))
        CV._save(d, self.led)
        self.assertEqual(CV.verify("csdn", "a", path=self.led, max_age=0)["state"],
                         "ok")


class TestCapability(unittest.TestCase):

    def test_matrix_covers_all_ten(self):
        """九平台 + B站二号（同一平台两个账号，各占一个发布位）。"""
        self.assertEqual(len(CV.CAP), 10)
        for p in ("wechat", "toutiao", "xhs", "bilibili", "bilibili2", "cnblogs",
                  "zhihu", "csdn", "tencentcloud", "aliyun"):
            self.assertIn(p, CV.CAP)

    def test_none_and_local_are_not_ok(self):
        """脚本不上封面 / 无可读字段 ⇒ 既不是 ok，也不是 empty（不能拦）。"""
        for p in ("wechat", "cnblogs", "zhihu"):
            self.assertEqual(CV.verify(p, "x")["state"], "none")
        for p in ("toutiao", "xhs"):
            self.assertEqual(CV.verify(p, "x")["state"], "local")

    def test_unknown_platform_is_unknown(self):
        v = CV.verify("douyin", "x")
        self.assertEqual(v["state"], "unknown")
        self.assertIn("没有平台", v["detail"])

    def test_bilibili_field_is_not_banner_url(self):
        """草稿接口的 banner_url 恒为 ""，拿它判会永远误判成没封面。"""
        self.assertIn("origin_image_urls", CV.CAP["bilibili"]["field"])
        self.assertIn("banner_url", CV.CAP["bilibili"]["why"])

    def test_live_platforms_only_five(self):
        """能真读的平台只有这五个；其余只能 `none` / `local`。

        二号（`bilibili2`）与一号同字段，但必须连二号自己的浏览器。
        """
        live = [p for p in CV.CAP if CV.CAP[p]["mode"] == "live"]
        self.assertEqual(sorted(live),
                         ["aliyun", "bilibili", "bilibili2", "csdn", "tencentcloud"])


class TestFinding(_Tmp):

    def test_only_empty_blocks(self):
        self.rec("a", "csdn", "empty", "coverImage 为空")
        f = CV.finding("csdn", "a", path=self.led)
        self.assertEqual(f["level"], "block")

    def test_ok_produces_no_finding(self):
        self.rec("a", "csdn", "ok", "coverImage=https://img/x.png")
        self.assertIsNone(CV.finding("csdn", "a", path=self.led))

    def test_unknown_only_warns(self):
        for p in ("csdn", "wechat", "toutiao"):
            f = CV.finding(p, "nope", path=self.led)
            self.assertEqual(f["level"], "warn", p)
            self.assertIn("未回读" if p == "csdn" else "封面", f["rule"])

    def test_rule_names_are_distinct(self):
        """三种「非 ok 非 empty」要能被区分开，否则读日志的人不知道该干什么。"""
        rules = {CV.finding(p, "nope", path=self.led)["rule"]
                 for p in ("csdn", "toutiao", "wechat")}
        self.assertEqual(len(rules), 3)


class TestProbeInjection(_Tmp):

    def test_probe_result_is_used_and_phrased(self):
        v = CV.verify("csdn", "x", probe=("empty", "coverImage 为空"))
        self.assertEqual(v["state"], "empty")
        self.assertEqual(v["source"], "probe")

    def test_illegal_probe_state_falls_back(self):
        v = CV.verify("csdn", "x", probe=("MAYBE", "?"))
        self.assertEqual(v["state"], "unknown")

    def test_publish_time_platforms_have_no_live_probe(self):
        """腾讯云/阿里云是「发表时脚本自己判」，本模块不另造一套浏览器代码。"""
        self.assertNotIn("tencentcloud", CV._PROBE)
        self.assertNotIn("aliyun", CV._PROBE)
        self.assertEqual(CV.CAP["tencentcloud"]["probe"], "publish-time")


class TestCaseKey(_Tmp):
    """`-b2` 是二号**产物 id** 的后缀，台账键必须是同一篇。

    2026-11-06 之前两边各写各的：发布链路拿 `ext-ep-004-quake-b2`（产物 id）
    写、`publish_one` 拿 `ext-ep-004-quake`（档期 case id）读 ⇒ 永远对不上，
    `cover_remote` 恒 unknown。
    """

    def test_带b2后缀与不带是同一格(self):
        self.rec("ext-ep-004-quake-b2", "bilibili2", "ok", "aid=376917 image_urls=https://x/1.jpg")
        v = CV.verify("bilibili2", "ext-ep-004-quake", path=self.led)
        self.assertEqual(v["state"], "ok")
        self.assertIn("376917", v["evidence"])
        self.assertEqual(sorted(CV.load_ledger(self.led)),
                         ["ext-ep-004-quake"], "台账里不许出现 -b2 的重复格")

    def test_键归一不碰普通case_id(self):
        for c in ("chessigma", "ext-ep-001-super-mario-bros", "upvoty"):
            self.assertEqual(CV._key(c), c)

    def test_clear也按归一后的键删(self):
        self.rec("ext-ep-004-quake-b2", "bilibili2", "ok", "aid=1")
        self.assertTrue(CV.clear("ext-ep-004-quake", "bilibili2", path=self.led))
        self.assertEqual(CV.verify("bilibili2", "ext-ep-004-quake-b2",
                                   path=self.led)["state"], "unknown")


class TestRecordCli(_Tmp):
    """`--record`：发布链路在草稿还在时写一条（发表后就读不到 image_urls）。"""

    def test_写ok要带证据_没有就降级且返回1(self):
        import io
        from contextlib import redirect_stdout
        old = CV.LEDGER
        try:
            CV.LEDGER = self.led
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = CV.main(["--record", "--case", "ext-ep-005-supertux-b2",
                              "--platform", "bilibili2", "--state", "ok"])
            self.assertEqual(rc, 1, "声称 ok 没证据 ⇒ 非零，别让调用方以为写成了")
            self.assertIn("降级", buf.getvalue())
            self.assertEqual(CV.verify("bilibili2", "ext-ep-005-supertux",
                                       path=self.led)["state"], "unknown")
        finally:
            CV.LEDGER = old

    def test_写ok带证据就落库(self):
        import io
        from contextlib import redirect_stdout
        old = CV.LEDGER
        try:
            CV.LEDGER = self.led
            with redirect_stdout(io.StringIO()):
                rc = CV.main(["--record", "--case", "ext-ep-005-supertux-b2",
                              "--platform", "bilibili2", "--state", "ok",
                              "--evidence", "aid=376919 image_urls=https://x/1.jpg"])
            self.assertEqual(rc, 0)
            v = CV.verify("bilibili2", "ext-ep-005-supertux", path=self.led)
            self.assertEqual(v["state"], "ok")
            self.assertEqual(v["source"], "publish-time")
        finally:
            CV.LEDGER = old


class TestLedgerHygiene(unittest.TestCase):

    def test_repo_ledger_states_are_legal(self):
        """真台账里若出现拼错的状态名，闸会把它当 unknown —— 静默丢一次判定。"""
        d = CV.load_ledger()
        bad = [(c, p, r.get("state"))
               for c, slots in d.items() for p, r in slots.items()
               if r.get("state") not in CV.STATES]
        self.assertEqual(bad, [], "台账里有非法状态：%s" % bad[:5])

    def test_repo_ledger_has_no_evidence_free_ok(self):
        """存量里的 ok 必须都带证据 —— 空证据的 ok 就是「我说它有」。"""
        d = CV.load_ledger()
        bad = [(c, p) for c, slots in d.items() for p, r in slots.items()
               if r.get("state") == "ok" and not str(r.get("evidence") or "").strip()]
        self.assertEqual(bad, [], "无证据的 ok：%s" % bad[:5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
