#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`ledger_health.py` 判据回归：只测「怎么下结论」，不碰浏览器、不碰真台账。

钉死的核心：**回填 ID 只认硬证据，绝不靠标题相似度猜** ——
猜错一次就会把 A 文章的 ID 记到 B 名下，之后每一轮巡检都跟着错。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L          # noqa: E402
import ledger_health as LH  # noqa: E402


class _NoIO(unittest.TestCase):
    """把 L.get 换成内存桩，避免测试读到真台账。"""

    def stub(self, rec):
        self._old = L.get
        L.get = lambda platform, cid: rec

    def tearDown(self):
        if hasattr(self, "_old"):
            L.get = self._old


class TestBackfill(_NoIO):
    REMOTE = {"pub": {"53385991": "Rezi：AI 简历生成器",
                      "53280245": "MORT：AI 求职代理"},
              "draft": {}, "draft_ok": True, "pub_ok": True}

    def test_cv_number_in_note(self):
        self.stub({"id": "rezi", "note": "P1对账: 标题直接命中(cv53385991) → Rezi"})
        gid, why = LH._backfill("bilibili", "rezi", self.REMOTE)
        self.assertEqual(gid, "53385991")
        self.assertIn("cv", why)

    def test_alias_in_note(self):
        self.stub({"id": "mort", "note": "对账: 『MORT：AI 求职代理』在册"})
        gid, _ = LH._backfill("bilibili", "mort", self.REMOTE)
        self.assertEqual(gid, "53280245")

    def test_no_guess_when_ambiguous(self):
        """标题不精确相等就不回填 —— 相似度匹配只用于诊断，不能写账。"""
        self.stub({"id": "x", "title": "Rezi：AI 简历生成器（旧口径）"})
        gid, why = LH._backfill("bilibili", "x", self.REMOTE)
        self.assertIsNone(gid)
        self.assertIn("无唯一命中", why)

    def test_empty_remote(self):
        self.stub({"id": "x", "note": "cv12345678"})
        gid, why = LH._backfill("bilibili", "x",
                                {"pub": {}, "pub_ok": False})
        self.assertIsNone(gid)
        self.assertIn("没读到", why)


class TestCheckOnline(unittest.TestCase):
    def _run(self, recs, remote):
        return {i["code"]: i for i in LH.check_online("x", recs, remote)}

    def test_stale(self):
        """本地记已发布、线上 ID 不在列表里 ⇒ STALE（可能被删，别自动重发）。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "999"}},
                        {"pub": {"111": "别的"}, "pub_ok": True})
        self.assertIn("STALE", got)

    def test_promote(self):
        """本地还是草稿、线上已发布 ⇒ PROMOTE。"""
        got = self._run({"a": {"id": "a", "state": L.DRAFT, "remote_id": "111"}},
                        {"pub": {"111": "标题"}, "pub_ok": True})
        self.assertIn("PROMOTE", got)

    def test_orphan(self):
        """线上有、本地任何记录都没指向它 ⇒ ORPHAN。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "111"}},
                        {"pub": {"111": "已登记", "222": "没登记"},
                         "pub_ok": True})
        self.assertIn("ORPHAN", got)
        self.assertEqual(got["ORPHAN"]["detail"].count("222"), 1)

    def test_note_alias_suppresses_second_article(self):
        """note 里写过的『旧篇标题』不算漏登（bustem/cometly 各两篇）。"""
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "111",
                               "note": "旧篇『B：老标题』也在线上"}},
                        {"pub": {"111": "A：新", "222": "B：老标题"},
                         "pub_ok": True})
        self.assertNotIn("ORPHAN", got)

    def test_no_conclusion_when_unreadable(self):
        got = self._run({"a": {"id": "a", "state": L.PUBLISHED, "remote_id": "999"}},
                        {"pub": {}, "pub_ok": False})
        self.assertNotIn("STALE", got, "读不到 ≠ 线上没有")


class TestOffline(_NoIO):
    def test_unverified_flagged(self):
        recs = {"a": {"id": "a", "state": L.UNVERIFIED, "remote_id": ""}}
        codes = [i["code"] for i in LH.check_offline("toutiao", recs)]
        self.assertIn("UNVERIFIED", codes)

    def test_no_id_flagged(self):
        recs = {"a": {"id": "a", "state": L.PUBLISHED, "remote_id": ""}}
        codes = [i["code"] for i in LH.check_offline("toutiao", recs)]
        self.assertIn("NO_ID", codes)


def _rec(state, rid="", did="", title="", note=""):
    r = {"id": "case1", "state": state, "remote_id": rid,
         "title": title, "note": note, "at": "2026-11-01 10:00:00"}
    if did:
        r["draft_remote_id"] = did
    return r


class TestStateAndPack(unittest.TestCase):
    """2026-11-20 之后补的假警防线：deleted 合法、缺产物只是 INFO。"""

    def setUp(self):
        # 必须与 ROOT 同盘：check_offline 用 os.path.relpath 生成 INFO 文案，
        # Windows 上 C: 的临时目录对 D: 的 ROOT 会直接 ValueError。
        self.tmp = tempfile.mkdtemp(
            prefix="lh_", dir=os.path.join(os.path.dirname(HERE), "tmp"))
        self._old_pack = dict(LH.PACK_DIR)
        LH.PACK_DIR["toutiao"] = os.path.join(self.tmp, "toutiao")
        os.makedirs(LH.PACK_DIR["toutiao"])

    def tearDown(self):
        LH.PACK_DIR.clear()
        LH.PACK_DIR.update(self._old_pack)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _offline(self, recs, dups=()):
        with patch.object(L, "duplicates", return_value=list(dups)):
            return [i["code"] for i in LH.check_offline("toutiao", recs)]

    def test_deleted_is_legal_terminal_state(self):
        os.makedirs(os.path.join(self.tmp, "toutiao", "case1"))
        self.assertEqual(self._offline({"case1": _rec(L.DELETED, rid="1")}), [],
                         "回收站稿不能再报 BAD_STATE（11-20 假警）")

    def test_duplicate_reported_once(self):
        os.makedirs(os.path.join(self.tmp, "toutiao", "case1"))
        self.assertEqual(self._offline({"case1": _rec(L.PUBLISHED, rid="1")},
                                       dups=["case1"]), ["DUP"])

    def test_missing_content_pack_is_info_not_error(self):
        self.assertEqual(self._offline({"case1": _rec(L.PUBLISHED, rid="1")}),
                         ["NO_PACK"])
        issues = LH.check_offline("toutiao", {"case1": _rec(L.PUBLISHED, rid="1")})
        self.assertEqual(issues[0]["level"], "INFO")

        os.makedirs(os.path.join(self.tmp, "toutiao", "case1"))
        self.assertEqual(self._offline({"case1": _rec(L.PUBLISHED, rid="1")}), [])

    def test_wechat_auditing_and_blocked_are_legal_states(self):
        """ledger._WECHAT_STATES 放行 auditing/blocked，体检不能报 BAD_STATE。"""
        LH.PACK_DIR["wechat"] = os.path.join(self.tmp, "articles")
        os.makedirs(LH.PACK_DIR["wechat"])
        recs = {"a": _rec("auditing", rid="1"), "b": _rec("blocked", rid="2")}
        with patch.object(L, "duplicates", return_value=[]):
            codes = [i["code"] for i in LH.check_offline("wechat", recs)]
        self.assertNotIn("BAD_STATE", codes)

    def test_wechat_pack_is_single_html_file(self):
        LH.PACK_DIR["wechat"] = os.path.join(self.tmp, "articles")
        os.makedirs(LH.PACK_DIR["wechat"])
        recs = {"case1": _rec(L.PUBLISHED, rid="1")}
        with patch.object(L, "duplicates", return_value=[]):
            codes = [i["code"] for i in LH.check_offline("wechat", recs)]
        self.assertEqual(codes, ["NO_PACK"])
        with open(os.path.join(self.tmp, "articles", "case1.html"), "w",
                  encoding="utf-8") as f:
            f.write("x")
        with patch.object(L, "duplicates", return_value=[]):
            codes = [i["code"] for i in LH.check_offline("wechat", recs)]
        self.assertEqual(codes, [])


class TestOnlineEdge(unittest.TestCase):
    def test_stale_draft_id_is_reported_not_autocleared(self):
        recs = {"case1": _rec(L.DRAFT, did="444", title="草稿")}
        issues = LH.check_online("toutiao", recs,
                                 {"draft": {}, "draft_ok": True,
                                  "pub": {}, "pub_ok": True})
        self.assertEqual([i["code"] for i in issues], ["STALE_DRAFT"])
        self.assertIn("人工确认", issues[0]["fix"])

    def test_draft_id_cannot_claim_a_published_article(self):
        """草稿 id 与正文 id 是两回事，混进 claimed 会让 ORPHAN 漏报。"""
        recs = {"a": _rec(L.PUBLISHED, rid="111", title="A"),
                "b": _rec(L.DRAFT, did="222", rid="222", title="B")}
        issues = LH.check_online("bilibili", recs,
                                 {"pub": {"111": "A", "222": "B"},
                                  "pub_ok": True, "draft_ok": True,
                                  "draft": {"222": "B"}})
        codes = [i["code"] for i in issues]
        self.assertIn("ORPHAN", codes, "线上 B 被草稿 id 认领掉了")

    def test_only_published_state_claims_remote_id(self):
        recs = {"a": _rec(L.DRAFT, rid="111", title="A")}
        issues = LH.check_online("toutiao", recs,
                                 {"pub": {"111": "A"}, "pub_ok": True})
        self.assertIn("ORPHAN", [i["code"] for i in issues])


class TestApplyFixes(unittest.TestCase):
    def test_only_whitelisted_codes_are_applied(self):
        issues = [LH.issue("WARN", "toutiao", "a", "STALE", "x"),
                  LH.issue("WARN", "toutiao", "b", "ORPHAN", "x")]
        with patch.object(L, "mark") as mark, patch.object(L, "promote") as prom, \
                patch.object(L, "load") as load, patch.object(L, "save") as save:
            done = LH.apply_fixes("toutiao", issues)
        self.assertEqual(done, [])
        mark.assert_not_called()
        prom.assert_not_called()
        load.assert_not_called()
        save.assert_not_called()

    def test_promote_uses_fresh_remote_id(self):
        issue = LH.issue("WARN", "toutiao", "a", "PROMOTE", "x")
        with patch.object(L, "get", return_value={"remote_id": "888", "title": "T"}), \
                patch.object(L, "promote") as prom:
            done = LH.apply_fixes("toutiao", [issue])
        self.assertEqual(done, [("PROMOTE", "a")])
        prom.assert_called_once_with("toutiao", "a", remote_id="888",
                                     title="T", evidence="health_promote")

    def test_backfill_never_passes_verified_at(self):
        """verified_at 是快照日，自愈不许用 now 冲掉（红线）。"""
        issue = LH.issue("WARN", "toutiao", "a", "BACKFILL", "x")
        with patch.object(LH, "_backfill", return_value=("999", "标题唯一命中")), \
                patch.object(L, "mark") as mark:
            done = LH.apply_fixes("toutiao", [issue], {"pub": {"999": "T"}})
        self.assertEqual(done[0][0], "BACKFILL")
        self.assertNotIn("verified_at", mark.call_args.kwargs)

    def test_dup_merge_keeps_sidecar_time(self):
        issue = LH.issue("WARN", "toutiao", "a", "DUP", "x")
        recs = {"a": {"id": "a", "note": "既有注记"}}
        with patch.object(L, "load", return_value=recs), \
                patch.object(L, "save") as save, \
                patch.object(LH, "_dup_sidecar", return_value={"at": "2026-10-09"}):
            done = LH.apply_fixes("toutiao", [issue])
        self.assertEqual(done, [("DUP", "a")])
        self.assertIn("2026-10-09", recs["a"]["note"])
        save.assert_called_once_with("toutiao", recs)

    def test_exception_is_recorded_not_raised(self):
        issue = LH.issue("WARN", "toutiao", "a", "PROMOTE", "x")
        with patch.object(L, "get", return_value={"remote_id": "1"}), \
                patch.object(L, "promote", side_effect=RuntimeError("disk full")):
            done = LH.apply_fixes("toutiao", [issue])
        self.assertEqual(done[0][0], "FAIL:PROMOTE")


class TestDelegate(unittest.TestCase):
    @staticmethod
    def _fake_pub_reconcile():
        import types
        m = types.ModuleType("pub_reconcile")
        m.CLOUD_PLATFORMS = {"aliyun", "tencentcloud"}
        m.calls = []
        m.run_cloud_platform = lambda p, ns, rep: (
            m.calls.append(("cloud", p, ns)) or 0)
        m.run_list_platform = lambda p, ns, rep: (
            m.calls.append(("list", p, ns)) or 0)
        return m

    @staticmethod
    def _args(apply=False, apply_cloud=False):
        import types
        return types.SimpleNamespace(apply=apply, apply_cloud=apply_cloud)

    def test_list_platform_keeps_parameters(self):
        fake = self._fake_pub_reconcile()
        with patch.dict(sys.modules, {"pub_reconcile": fake}):
            rc = LH._delegate_other("cnblogs", self._args(apply=True))
        self.assertEqual(rc, 0)
        kind, plat, ns = fake.calls[0]
        self.assertEqual((kind, plat), ("list", "cnblogs"))
        self.assertTrue(ns.apply)
        self.assertEqual((ns.pages, ns.timeout, ns.delay), (4, 20.0, 1.5))

    def test_cloud_platform_gets_apply_cloud_flag(self):
        fake = self._fake_pub_reconcile()
        with patch.dict(sys.modules, {"pub_reconcile": fake}):
            rc = LH._delegate_other("aliyun", self._args(apply_cloud=True))
        self.assertEqual(rc, 0)
        kind, plat, ns = fake.calls[0]
        self.assertEqual((kind, plat), ("cloud", "aliyun"))
        self.assertTrue(ns.apply_cloud)


if __name__ == "__main__":
    unittest.main()
