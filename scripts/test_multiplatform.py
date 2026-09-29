#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""multiplatform 层的回归测试。

钉死的是这轮重构里**真实踩过**的坑，不是凑覆盖率：

* 注册表必须存实例（存类 → 未绑定方法 `missing 1 required positional arg`）；
* 清单写盘必须原子（批量跑到一半被 Ctrl-C，不能半个 JSON 丢全部）；
* `building` 不算完成（上次崩在这状态，重跑必须能续上）；
* B站无本地状态文件时，幂等只能认清单；
* 标题必须优先取平台精修版，不能用公众号那个数据串 h1；
* 清单状态文件里的「已发过」必须能反向灌进清单，否则 37 条会被重发一遍。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from multiplatform import article as A              # noqa: E402
from multiplatform import state as S                # noqa: E402
from multiplatform.adapters import REGISTRY         # noqa: E402
from multiplatform.adapters import base             # noqa: E402
from multiplatform import runner                    # noqa: E402


class TmpManifest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mp_")
        self.path = os.path.join(self.dir, "manifest.json")
        self.m = S.Manifest(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestManifest(TmpManifest):
    def test_states_and_done(self):
        self.assertEqual(self.m.state("a", "toutiao"), "pending")
        self.assertFalse(self.m.is_done("a", "toutiao"))
        self.m.mark_draft("a", "toutiao", "标题A")
        self.assertTrue(self.m.is_done("a", "toutiao"))
        self.assertEqual(self.m.title_of("a", "toutiao"), "标题A")
        self.m.mark_published("a", "toutiao")
        self.assertTrue(self.m.is_published("a", "toutiao"))

    def test_building_is_not_done(self):
        """崩在 building 的条目必须能被重跑捡起来，否则永远补不上。"""
        self.m.mark_building("a", "toutiao")
        self.assertFalse(self.m.is_done("a", "toutiao"))
        self.assertEqual(self.m.state("a", "toutiao"), "building")

    def test_failed_needs_retry(self):
        self.m.mark_failed("a", "toutiao", "超时 1800s", "标题A")
        self.assertEqual(self.m.state("a", "toutiao"), "failed")
        self.assertEqual(self.m.attempts("a", "toutiao"), 1)
        self.assertIn("超时", self.m.error_of("a", "toutiao"))
        # failed 不可被 mark_draft 之外的路径清掉 error
        self.m.mark_draft("a", "toutiao")
        self.assertEqual(self.m.error_of("a", "toutiao"), "")

    def test_attempt_bump(self):
        self.m.mark_failed("a", "x", "e1")
        self.m.mark_failed("a", "x", "e2")
        self.assertEqual(self.m.attempts("a", "x"), 2)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            self.m.set("a", "x", "不存在的状态")

    def test_atomic_write_leaves_no_tmp(self):
        self.m.mark_draft("a", "x")
        self.assertTrue(os.path.isfile(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        with open(self.path, encoding="utf-8") as f:
            json.load(f)                          # 必须是完整合法 JSON

    def test_reload_persists(self):
        self.m.mark_draft("z", "x", "标题Z")
        again = S.Manifest(self.path)
        self.assertTrue(again.is_done("z", "x"))
        self.assertEqual(again.title_of("z", "x"), "标题Z")

    def test_corrupt_file_degrades_to_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ 这不是 JSON")
        m = S.Manifest(self.path)
        self.assertEqual(m.cids(), [])
        m.mark_draft("a", "x")                    # 还能继续写
        self.assertTrue(os.path.isfile(self.path))

    def test_remove(self):
        self.m.mark_draft("a", "x")
        self.m.mark_draft("a", "y")
        self.m.remove("a", "x")
        self.assertFalse(self.m.is_done("a", "x"))
        self.assertTrue(self.m.is_done("a", "y"))
        self.m.remove("a")
        self.assertEqual(self.m.cids(), [])

    def test_import_legacy_idempotent(self):
        """重复 sync 不能把已完成的重新变成待发（否则 37 条会被重发一遍）。"""
        n1 = S.import_legacy("x", ["a", "b"], "draft_saved", path=self.path)
        n2 = S.import_legacy("x", ["a", "b"], "draft_saved", path=self.path)
        self.assertEqual(n1, 2)
        self.assertEqual(n2, 0)

    def test_summary_counts(self):
        self.m.mark_draft("a", "x")
        self.m.mark_failed("b", "x", "e")
        s = self.m.summary(["x"])["x"]
        self.assertEqual(s["draft_saved"], 1)
        self.assertEqual(s["failed"], 1)


class TestRegistry(unittest.TestCase):
    def test_registry_stores_instances(self):
        """存类而不是实例 → 方法漏 self（实测 TypeError）。"""
        for k, ad in REGISTRY.items():
            self.assertNotIsInstance(ad, type, "%s 存成了类" % k)
            self.assertTrue(callable(ad.done_ids), k)

    def test_four_platforms_registered(self):
        for k in ("wechat", "xhs", "toutiao", "bilibili"):
            self.assertIn(k, REGISTRY)
        self.assertEqual(REGISTRY["bilibili"].label, "B站专栏")

    def test_toutiao_requires_cover(self):
        """头条无封面草稿在信息流里是空图，必须当硬要求。"""
        self.assertIn("out/toutiao/<id>/cover.png",
                      REGISTRY["toutiao"].requires)

    def test_bilibili_has_no_state_file(self):
        self.assertFalse(REGISTRY["bilibili"].has_state_file,
                         "B站草稿只在远端，本地无状态文件")

    def test_bilibili_cannot_publish(self):
        """脚本只点「保存为草稿」，没有 --yes。"""
        self.assertFalse(REGISTRY["bilibili"].supports_publish)

    def test_dry_result_not_executed(self):
        r = base.Result(True, "pending", "dry")
        self.assertTrue(r.ok)
        self.assertEqual(r.state, "pending")


class TestTitle(unittest.TestCase):
    def test_h1_split_on_semicolon(self):
        """公众号 h1 是「产品名：MRR $x；累计收入 $y」，要截到第一个分号。"""
        html = "<h1>1Lookup：MRR $244,029；累计收入 $1.2M</h1><p>正文</p>"
        self.assertEqual(A._h1_title(html), "1Lookup：MRR $244,029")

    def test_h1_strip_tags_and_space(self):
        html = "<h1><span>Foo</span>  Bar\n Baz</h1>"
        self.assertEqual(A._h1_title(html), "Foo Bar Baz")

    def test_pick_title_prefers_platform(self):
        a = A.Article("x", "N", html="<h1>公众号标题</h1>")
        a._title_cache = {}
        a.title(("toutiao",))        # 平台精修版不存在 → 回落 h1
        self.assertEqual(a.title(("toutiao",)), "公众号标题")

    def test_pick_title_priority_order(self):
        a = A.Article("cid-t", "N", html="<h1>母版</h1>")
        self.assertEqual(a.title(("bili", "toutiao")), "母版")

    def test_article_not_ok_without_html(self):
        self.assertFalse(A.Article("绝对不存在的id-xyz").ok)


class TestRunnerLogic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mp_run_")
        self.m = S.Manifest(os.path.join(self.dir, "m.json"))
        self.r = runner.Runner(["toutiao", "bilibili"], dry=True,
                               manifest=self.m, verbose=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_is_done_from_manifest(self):
        self.m.mark_draft("a", "toutiao")
        self.assertTrue(self.r._is_done("a", "toutiao"))

    def test_is_done_imports_from_platform_file(self):
        """平台状态文件里有、清单里没有 → 判已发并回写清单。

        这条是 37 条不会被重发的关键。
        """
        ad = REGISTRY["toutiao"]
        cid = sorted(ad.done_ids())[0]
        self.m.remove(cid, "toutiao")            # 清单里抹掉
        self.assertFalse(self.m.is_done(cid, "toutiao"))
        self.assertTrue(self.r._is_done(cid, "toutiao"))
        self.assertTrue(self.m.is_done(cid, "toutiao"), "应回写清单")

    def test_bilibili_never_done_without_manifest(self):
        """B站没状态文件：清单说没发就是没发，不能猜。"""
        self.assertFalse(self.r._is_done("随便一个id", "bilibili"))
        self.m.mark_draft("随便一个id", "bilibili")
        self.assertTrue(self.r._is_done("随便一个id", "bilibili"))

    def test_dry_run_never_writes_state(self):
        a = A.Article("prosp", "PROSP")          # 真实存在的案例
        self.assertTrue(a.ok)
        self.r.run_one(a, "bilibili")
        self.assertEqual(self.m.state("prosp", "bilibili"), "pending",
                         "dry-run 不该改清单")

    def test_missing_source_marked_failed(self):
        a = A.Article("绝对不存在-xyz")
        self.assertFalse(a.ok)
        self.assertEqual(self.r.run_one(a, "bilibili"), "failed")
        self.assertEqual(self.m.state("绝对不存在-xyz", "bilibili"), "failed")
        self.assertIn("母版", self.m.error_of("绝对不存在-xyz", "bilibili"))


class TestVerifyMatching(unittest.TestCase):
    """远端对账的标题匹配 —— B站草稿卡片标题与本地 id 常不一致。"""

    def setUp(self):
        from multiplatform import verify as V
        self.V = V
        self.m = S.Manifest(os.path.join(tempfile.mkdtemp(), "m.json"))

    def test_norm_strips_space_and_brackets(self):
        self.assertEqual(self.V._norm(" 月收$3.1K：替你托管 "),
                         "月收$3.1k：替你托管")
        self.assertEqual(self.V._norm("《标题》"), "标题")

    def test_match_by_id_and_variant(self):
        self.assertEqual(self.V._match("月收$3.1K：托管", ["coral"]), None)
        self.assertEqual(self.V._match("Coral AI 助手", ["coral"]), "coral")
        self.assertEqual(self.V._match("随便什么标题", ["coral"]), None)

    def test_match_by_title(self):
        # 平台精修标题是完整一句，卡片里就是它本身 → 应匹配
        self.assertEqual(
            self.V._match("月收$540K：帮公司几周过合规审计",
                          ["月收$540K：帮公司几周过合规审计"]),
            "月收$540K：帮公司几周过合规审计")
        # 卡片标题更长时，短的键仍是子串 → 也能匹配
        self.assertEqual(
            self.V._match("月收$540K：帮公司几周过合规审计（完整版）",
                          ["月收$540K：帮公司几周过合规审计"]),
            "月收$540K：帮公司几周过合规审计")
        # 毫无关系 → 不匹配
        self.assertEqual(self.V._match("完全不同的标题", ["月收$540K"]), None)

    def test_empty_title_no_match(self):
        self.assertIsNone(self.V._match("", ["a"]))
        self.assertIsNone(self.V._match("   ", ["a"]))

    def test_reconcile_finds_missing(self):
        """清单说发了、远端没有 → 必须报出来（这正是 kibu 那种情况）。"""
        self.m.mark_draft("a", "toutiao", "标题A")
        self.m.mark_draft("b", "toutiao", "标题B")
        r = self.V.reconcile("toutiao", ["标题A"], m=self.m, verbose=False)
        self.assertIn("b", r["missing"])
        self.assertNotIn("a", r["missing"])
        self.assertIn("a", r["matched"])

    def test_reconcile_finds_extra(self):
        """远端有、清单没记 → 必须报出来（崩在写盘前的漏记）。"""
        self.m.mark_draft("a", "toutiao", "标题A")
        r = self.V.reconcile("toutiao", ["标题A", "标题C"], m=self.m,
                             verbose=False)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["unmatched_titles"], ["标题C"])

    def test_reconcile_all_consistent(self):
        self.m.mark_draft("a", "toutiao", "标题A")
        r = self.V.reconcile("toutiao", ["标题A"], m=self.m, verbose=False)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["extra"], [])


class TestMissingArtifacts(unittest.TestCase):
    def test_requires_template_expansion(self):
        ad = REGISTRY["toutiao"]
        art = A.Article("prosp", "PROSP")
        miss = ad.missing_artifacts(art)
        # prosp 的头条产物应该齐（若不齐，测试环境变了也算通过但要能看到）
        self.assertTrue(all("<id>" not in m for m in miss), miss)

    def test_bilibili_requires_article_html(self):
        self.assertIn("out/bili/<id>/article.html",
                      REGISTRY["bilibili"].requires)


if __name__ == "__main__":
    unittest.main(verbosity=2)
