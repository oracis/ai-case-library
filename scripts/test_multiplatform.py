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
import types                                          # noqa: E402
import ledger as L                                      # noqa: E402


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

    def test_bilibili_can_publish_but_via_send_draft(self):
        """B站的发布入口是 `send-draft`，**不是** `publish`。

        `bilibili_publish.py publish` 是存草稿（`draft/add`），而且**没有
        `--yes`** 这个参数 —— 按基类的写法拼 `publish --case X --yes`
        会直接被 argparse 拒（unrecognized arguments，退出 2）。
        """
        ad = REGISTRY["bilibili"]
        self.assertTrue(ad.supports_publish)
        seen = []

        def fake_run(args, enabled=True, timeout=1800, full=False):
            seen.append(list(args))
            return base.Result(True, "published", "ok")

        old = ad.run
        try:
            ad.run = fake_run
            ad.publish(A.Article("prosp", "PROSP"), yes=True)
            ad.publish(A.Article("prosp", "PROSP"), yes=False)
        finally:
            ad.run = old
        self.assertEqual(seen[0], ["send-draft", "--case", "prosp"])
        self.assertNotIn("--yes", seen[0])
        self.assertNotIn("--yes", seen[1], "基类也不许给 B站 publish 加 --yes")
        self.assertEqual(seen[1][:2], ["publish", "--case"])

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

    def test_missing_source_plans_master_in_dry(self):
        """dry 下缺母版**不写 failed**：实跑会先 make_article 现生成。

        2026-10-19 改：以前缺母版直接判死，于是「一条命令发四平台」对新案例
        永远差半步（得先手跑 make_article）。现在生成交给 runner，
        dry-run 只提示、不动清单。
        """
        a = A.Article("绝对不存在-xyz")
        self.assertFalse(a.ok)
        self.assertEqual(self.r.run_one(a, "bilibili"), "ok")
        self.assertEqual(self.m.state("绝对不存在-xyz", "bilibili"), "pending")

    def test_missing_source_marked_failed(self):
        """生成也救不回来 → failed，原因里必须带「母版」。"""
        r = runner.Runner(["bilibili"], dry=False, manifest=self.m,
                          verbose=False)
        r._ensure_master = lambda cid: False        # 不真跑 make_article
        a = A.Article("绝对不存在-xyz")
        self.assertFalse(a.ok)
        self.assertEqual(r.run_one(a, "bilibili"), "failed")
        self.assertEqual(self.m.state("绝对不存在-xyz", "bilibili"), "failed")
        self.assertIn("母版", self.m.error_of("绝对不存在-xyz", "bilibili"))

    def test_send_on_draft_only_platform_is_reported(self):
        """`--yes` 撞上只能存草稿的平台：状态是 draft_saved，且必须被点名。

        为什么钉这条：默认「发出去」是用户的意图，平台却只能到草稿。
        安静降级 ⇒ 用户以为四条都发了，回头发现两个平台还躺在草稿箱。

        ⚠ B站 2026-10-20 起 `send-draft` 能真发布，`supports_publish` 已经是
        True，**走不到这条分支**。所以这里把它临时伪装成草稿平台
        （patch `supports_publish=False`）—— 要钉的是 runner 那条守卫，
        不是 B站的能力。换成公众号会撞上「prosp 在公众号已登记 → skip」，
        那条路与「安静降级」无关，别混在一起。
        """
        from unittest import mock
        r = runner.Runner(["bilibili"], dry=False, yes=True,
                          manifest=self.m, verbose=False)
        ad = REGISTRY["bilibili"]
        ok = base.Result(True, "draft_saved", "已存草稿")
        with mock.patch.object(ad, "supports_publish", False), \
                mock.patch.object(ad, "missing_artifacts", return_value=[]), \
                mock.patch.object(ad, "build", return_value=ok), \
                mock.patch.object(ad, "publish", return_value=ok):
            self.assertFalse(ad.supports_publish, "伪装必须真的生效")
            self.assertEqual(r.run_one(A.Article("prosp", "PROSP"),
                                       "bilibili"), "ok")
        self.assertEqual(self.m.state("prosp", "bilibili"), "draft_saved")
        self.assertIn(ad.label, r._draft_only, "降级必须当场点名是哪个平台")


class TestBilibiliReader(unittest.TestCase):
    """B站草稿箱读取的三个坑（2026-09-29 全踩了一遍）。

    这三个都是**静默失败**：URL 错 → 错误页读出 0 条；tab 错 → 「全部 0」；
    字段名错 → 空数组。三者都长得像「37 条草稿全丢了」，差点误判。
    """

    def setUp(self):
        from multiplatform import verify as V
        self.V = V

    def test_not_read_draft_error_page(self):
        """`/read/draft` 是错误页，绝不能用。"""
        self.assertNotIn("/read/draft", self.V.BILI_DRAFT)
        self.assertIn("/opus/management/drafts", self.V.BILI_DRAFT)

    def test_uses_official_api_not_dom(self):
        """页面只渲染首屏 10 条、滚动无效 → 必须走列表接口。"""
        self.assertIn("/x/dynamic/feed/article/draft/list", self.V.BILI_API)
        self.assertIn("ps=200", self.V.BILI_API)

    def test_api_field_is_drafts_not_items(self):
        """字段是 `drafts`；写 items 会静默返回空数组。"""
        self.assertIn("j.data.drafts", self.V.BILI_API_JS)
        self.assertNotIn("d.items || d.list", self.V.BILI_API_JS)

    def test_err_flag_distinguishes_failure(self):
        """正常结果也是 dict —— 判异常只能靠 err 字段，不能靠 isinstance。"""
        src = __import__("inspect").getsource(self.V.read_bilibili_drafts)
        self.assertIn('r.get("err")', src)
        self.assertNotIn("if isinstance(r, dict):", src)


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


class TestBatchScheduling(unittest.TestCase):
    """调度按**平台**聚合：支持批量的平台一次提交，不逐条 spawn。

    2026-09-30 改。原因：逐条 spawn 每条都要重起 CDP、重开草稿箱 tab，
    37 条下来既慢又会把页面 WebSocket 拖超时（实测第 2 条就 TimeoutError）。
    """

    def test_bilibili_supports_only(self):
        self.assertTrue(REGISTRY["bilibili"].supports_only)

    def test_other_platforms_default_off(self):
        for k in ("wechat", "toutiao", "xiaohongshu"):
            if k in REGISTRY:
                self.assertFalse(getattr(REGISTRY[k], "supports_only", False), k)

    def test_base_batch_returns_none(self):
        """不支持批量的平台 batch() 返回 None，runner 会退回逐条。"""
        from multiplatform.adapters.base import Adapter
        self.assertIsNone(Adapter().batch(["a", "b"]))

    def test_batch_builds_only_flag(self):
        ad = REGISTRY["bilibili"]

        captured = {}

        def fake_run(args, enabled=True, timeout=1800, full=False):
            captured["args"] = args
            captured["full"] = full
            from multiplatform.adapters.base import Result
            return Result(True, "draft_saved", "", 0, "")

        ad.run = fake_run
        ad.batch(["a", "b"], replace=True, retries=3)
        self.assertIn("--replace", captured["args"])
        self.assertIn("--only", captured["args"])
        self.assertEqual(captured["args"][captured["args"].index("--only") + 1],
                         "a,b")
        self.assertEqual(captured["args"][captured["args"].index("--retries") + 1],
                         "3")
        # full=True 是逐条判定成败的前提
        self.assertTrue(captured["full"])

    def test_batch_without_cids_is_none(self):
        """不传 cids 时返回 None —— 脚本的"全部"是 list_ids() 全集，
        不是 runner 筛出来的未完成集，混用会重发已完成的。"""
        self.assertIsNone(REGISTRY["bilibili"].batch([]))


class TestYesRouting(unittest.TestCase):
    """`--yes`（真发布）的路由：批量只能存草稿，必须逐条发布。

    2026-10-21 修的洞：`_run_batch` 不接收 yes，B站（supports_only=True）
    的 batch 是 `publish --only`（draft/add）。同一条 `run --yes` 走批量
    只存草稿、走逐条才真发，且无任何提示 —— 用户以为发了，回头还在
    草稿箱里躺着。现在 yes + supports_publish 一律走逐条。
    """

    def setUp(self):
        self.ad = REGISTRY["bilibili"]
        self.dir = tempfile.mkdtemp(prefix="mp_yes_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.m = S.Manifest(os.path.join(self.dir, "m.json"))

    def _run_platform_with(self, yes, cids=("x", "y")):
        """跑 _run_platform，把 run_one / batch 换成记录型假件。"""
        r = runner.Runner(["bilibili"], dry=False, yes=yes,
                          manifest=self.m, verbose=False)
        seen = []

        def fake_one(art, plat):
            seen.append(("one", art.cid))
            return "ok"

        def fake_batch(ids, **kw):
            seen.append(("batch", tuple(ids)))
            return base.Result(True, "draft_saved", "ok", 0, "")

        def fake_parse(out):
            return set(cids), set()

        old_one, old_batch = r.run_one, self.ad.batch
        old_parse = self.ad.parse_batch
        try:
            r.run_one, self.ad.batch = fake_one, fake_batch
            self.ad.parse_batch = fake_parse
            arts = {c: A.Article(c, c) for c in cids}
            tally = {}
            r._run_platform(self.ad, "bilibili", list(cids), arts, tally)
        finally:
            r.run_one, self.ad.batch = old_one, old_batch
            self.ad.parse_batch = old_parse
        return seen, tally

    def test_yes时逐条发布不走批量(self):
        seen, tally = self._run_platform_with(yes=True)
        self.assertEqual([s[0] for s in seen], ["one", "one"],
                         "--yes 必须逐条发布（send-draft），不许走批量存草稿")
        self.assertEqual(tally, {"x": True, "y": True})

    def test_无yes时仍走批量存草稿(self):
        """存草稿语义下批量的性能优化必须保留（一次进程跑完 N 条）。"""
        seen, tally = self._run_platform_with(yes=False)
        self.assertEqual(seen, [("batch", ("x", "y"))])
        self.assertEqual(tally, {"x": True, "y": True})

    def test_yes撞上批量且无发布接口的平台仍点名(self):
        """draft-only + 批量平台（今天不存在，防御未来的）也要点名。

        安静降级会让人以为发了，回头发现还在草稿箱里躺着 —— 与
        run_one 的 _draft_only 同口径。
        """

        class FakeDraftOnly(base.Adapter):
            key = "fake-draft-only"
            label = "假平台"
            script = "x.py"
            supports_only = True
            supports_publish = False

            def batch(self, cids, dry=False, replace=False, retries=1):
                return base.Result(True, "draft_saved", "ok", 0, "")

            def parse_batch(self, out):
                return {"x"}, set()

        r = runner.Runner(["bilibili"], dry=False, yes=True,
                          manifest=self.m, verbose=False)
        arts = {"x": A.Article("x", "x")}
        # plat 传已注册的 key（_run_batch 内部按它回注册表取标题来源），
        # 路由行为由传入的假适配器驱动
        r._run_platform(FakeDraftOnly(), "bilibili", ["x"], arts, {})
        self.assertIn("假平台", r._draft_only)


class TestParseBatch(unittest.TestCase):
    """批量结果必须**逐条**判定，不能只看退出码。

    「15 条挂 1 条」整体 rc≠0，但那 14 条已经存好了。按 rc 一刀切会把
    成功的误标 failed，下次又重发一遍 —— B站一条草稿要 1 分钟，代价很高。
    """

    SAMPLE = """publish 15 篇 → B站草稿箱（覆盖已有草稿）（每条重试 2 次）
[1/15] promptmonitor-io
    清空旧内容: focused
  ✓ 远端已回读确认：mtime 已更新、正文前缀匹配
  ✓ 标题《x》正文 999 字（编辑器实测 999 字 / 43 块）已存草稿
[2/15] prosp
    ! TimeoutError: timed out
    ↻ 重试第 1 次
[3/15] rezi
  ✓ 标题《y》正文 100 字（编辑器实测 100 字 / 12 块）已存草稿
完成 13/15
失败 2 条：prosp stan
"""

    def test_splits_done_and_failed(self):
        done, failed = REGISTRY["bilibili"].parse_batch(self.SAMPLE)
        self.assertEqual(done, {"promptmonitor-io", "rezi"})
        self.assertEqual(failed, {"prosp", "stan"})

    def test_last_entry_is_settled(self):
        """最后一条后面没有新的 [i/N] 也要结算，不能漏掉。"""
        done, failed = REGISTRY["bilibili"].parse_batch(
            "[1/1] solo\n  ✓ 标题《z》正文 5 字已存草稿\n完成 1/1")
        self.assertEqual(done, {"solo"})
        self.assertEqual(failed, set())

    def test_empty_input(self):
        done, failed = REGISTRY["bilibili"].parse_batch("")
        self.assertEqual((done, failed), (set(), set()))

    def test_no_success_marks_all_failed(self):
        done, failed = REGISTRY["bilibili"].parse_batch(
            "[1/2] a\n    ! TimeoutError\n[2/2] b\n完成 0/2")
        self.assertEqual(done, set())
        self.assertEqual(failed, {"a", "b"})

    def test_autostart_line_does_not_open_a_case(self):
        """🚨 2026-10-11 踩坑回归钉死。

        旧判据 `s.startswith("[") and "]" in s` 太松：脚本启动时打的
        `[autostart] Chrome 已启动（Chrome for Testing, --no-sandbox）
        port=9223 profile=…` 也以 `[` 开头、含 `]`，于是 cur 被设成
        `autostart`，结算时掉进 failed —— 而 7 条其实全部 `✓ API 新建草稿`
        真存好了。结果是「done 6 / failed ['Chrome', <真失败那条>」，
        台账写 failed ⇒ 队列反复重发 ⇒ 远端多出重复草稿。
        """
        out = (
            "cleared stale LOCK\n"
            "[autostart] Chrome 已启动（Chrome for Testing, --no-sandbox）"
            "port=9223 profile=C:\\Users\\DELL\\chrome-debug-profile\n"
            "仅处理指定 7 条\n"
            "publish 7 篇 → B站草稿箱（每条重试 1 次）\n"
            "[1/7] augora-ai\n"
            "  ✓ API 新建草稿 aid=343497《…》正文 1691 字 / 39 段\n"
            "[2/7] cometly\n"
            "  ✓ API 新建草稿 aid=343498《…》正文 2300 字 / 42 段\n"
            "[7/7] wpconvert-ai-convert-ai-sites-to-wordpress\n"
            "  ✓ API 新建草稿 aid=343503《…》正文 2228 字 / 42 段\n"
            "完成 7/7\n"
        )
        done, failed = REGISTRY["bilibili"].parse_batch(out)
        self.assertEqual(done, {
            "augora-ai", "cometly",
            "wpconvert-ai-convert-ai-sites-to-wordpress"})
        self.assertEqual(failed, set())
        self.assertNotIn("autostart", failed)
        self.assertNotIn("Chrome", failed)


class TestReplaceMode(TmpManifest):
    """`run --replace`：改文案后覆盖重存，已 draft_saved 的也要重跑。

    默认队列会跳过所有已完成的，改了文案想重存就得先 reset 再 run，
    两步且容易忘。`--replace` 一步到位（平台侧传 --replace，调度侧 force）。
    """

    def test_runner_accepts_replace(self):
        import inspect
        sig = inspect.signature(runner.Runner.__init__)
        self.assertIn("replace", sig.parameters)

    def test_replace_requeues_done_ones(self):
        """force=True 时队列要包含已 draft_saved 的。

        ⚠ 必须用**临时清单**：默认 `S.Manifest()` 指向真实的
        `data/publish_manifest.json`，那里 37 条全是 draft_saved，
        队列恒为 0，断言会假失败（第一版就踩了）。
        """
        m = self.m
        total = len(runner.Runner(["bilibili"], dry=True, manifest=m).queue())
        self.assertGreater(total, 0)
        # 把一条标成"已发过"：普通队列应少一条，replace 队列不变
        m.set("coral", "bilibili", "draft_saved", "旧标题")
        r = runner.Runner(["bilibili"], dry=True, manifest=m)
        self.assertEqual(len(r.queue()), total - 1)
        r2 = runner.Runner(["bilibili"], dry=True, manifest=m,
                           replace=True)
        self.assertEqual(len(r2.queue()), total)

    def test_run_one_skips_only_when_not_replace(self):
        import inspect
        src = inspect.getsource(runner.Runner.run_one)
        self.assertIn("and not self.replace", src)

    def test_cli_has_replace(self):
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "publish_multi.py"),
             "run", "--help"],
            capture_output=True, text=True, timeout=60).stdout
        self.assertIn("--replace", out)


class TestResultCarriesStdout(unittest.TestCase):
    """Result.out 存完整 stdout，供 parse_batch 逐条判定。"""

    def test_out_default_empty(self):
        from multiplatform.adapters.base import Result
        self.assertEqual(Result(True).out, "")

    def test_out_kept_when_requested(self):
        from multiplatform.adapters.base import Result
        r = Result(True, out="[1/1] a\n  ✓ 标题")
        self.assertIn("✓ 标题", r.out)


class TestSubprocessEncoding(unittest.TestCase):
    """子进程 stdout 必须钉死 UTF-8（2026-09-30 修的静默功能 bug）。

    Windows 控制台默认 cp936：子进程按 GBK 编码输出，父进程按 utf-8 解码，
    中文字段全变乱码 ——「  ✓ 标题《MORT》已存草稿」→「  BÕ¾×¨À¸·¢²¼…」。
    后果不是难看而是**判定失效**：B站靠 `startswith("✓ 标题")` 逐条认成败，
    乱码后一条都匹配不上，已经存好的草稿全被误标 failed，下次重跑又建重复。
    实测踩过：mort 重存远端回读都确认成功了，清单仍写 failed。
    """

    def test_run_给子进程设UTF8(self):
        import inspect
        from multiplatform.adapters.base import Adapter
        src = inspect.getsource(Adapter.run)
        self.assertIn("PYTHONIOENCODING", src)
        self.assertIn("utf-8", src)

    def test_乱码输出判不出成功(self):
        """钉住「为什么必须设编码」：乱码流里一条成功都认不出来。"""
        mojibake = ("publish 1 篇 → B站草稿箱\n"
                    "[1/1] mort\n"
                    "  \u00b6\u00d5\u00be\u00d7\u00a8\u00b7\u00a2\u00b4"
                    "\u300aMORT\u300b\u5df2\u5b58\u8349\u7a3f\n"
                    "\u5b8c\u6210 1/1")
        done, failed = REGISTRY["bilibili"].parse_batch(mojibake)
        self.assertEqual(done, set(), "乱码流不该被判成功")
        self.assertEqual(failed, {"mort"}, "会被误标 failed → 下次重发建重复")


class TestPreflight(unittest.TestCase):
    """发车前的本地账本体检（2026-10-19 加）。

    口径必须钉死，否则这个门禁要么形同虚设、要么被一条老记录永久卡死：
      · ERROR（账本结构坏）⇒ 拦车；
      · WARN（历史欠账：重复、缺远端 ID）⇒ 只提示，不拦；
      · INFO（产物缺失）⇒ 连提示都不占版面；
      · --no-preflight ⇒ 明确绕过（明知有欠账还要发的时候）。
    """

    def setUp(self):
        self._old = L.DATA
        self.dir = tempfile.mkdtemp(prefix="preflight_")
        L.DATA = self.dir
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(setattr, L, "DATA", self._old)
        for name in ("toutiao_published.json", "toutiao_drafts.json",
                     "bili_published.json", "bili_drafts.json",
                     "xhs_published.json", "xhs_drafts.json",
                     "wechat_published.json"):
            with open(os.path.join(self.dir, name), "w",
                      encoding="utf-8") as f:
                f.write("[]" if "wechat" not in name else "{}")

    def _write(self, name, obj):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_clean_passes(self):
        import publish_multi as PM
        self._write("toutiao_published.json",
                    [{"id": "a", "at": "t", "gid": "1"}])
        self._write("toutiao_drafts.json", [])
        self.assertTrue(PM._preflight(["toutiao"]))

    def test_warn_does_not_block(self):
        """缺远端 ID 是历史欠账 —— 拿它拦车，新车永远发不出去。"""
        import publish_multi as PM
        self._write("toutiao_published.json", [{"id": "a", "at": "t"}])
        self.assertTrue(PM._preflight(["toutiao"]))

    def test_error_blocks(self):
        """台账文件坏 ⇒ 拦车。

        别拿「状态非法」当用例：`load()` 会用文件位置覆盖 state，BAD_STATE 那类
        ERROR 根本到不了（见 publish_multi._file_health 的注释）。真会发生的是
        文件写坏 —— 而 `_read_file` 解析失败时静默返回空账，比崩掉危险得多。
        """
        import publish_multi as PM
        with open(os.path.join(self.dir, "toutiao_published.json"),
                  "w", encoding="utf-8") as f:
            f.write('[{"id": "a", "at": ')          # 写了一半
        self.assertFalse(PM._preflight(["toutiao"]))

    def test_broken_file_silently_reads_as_empty(self):
        """顺手钉住那个危险本身：坏文件 load 出来是空的，不是异常。"""
        import publish_multi as PM
        with open(os.path.join(self.dir, "toutiao_published.json"),
                  "w", encoding="utf-8") as f:
            f.write('[{"id": "a", "at": ')
        self.assertEqual(L.load("toutiao"), {},
                         "坏文件被读成空账 ⇒ 台账归零 ⇒ 会按『没发过』重发")
        self.assertFalse(PM._preflight(["toutiao"]))

    def test_run_aborts_on_error(self):
        import publish_multi as PM
        from unittest import mock
        with open(os.path.join(self.dir, "toutiao_published.json"),
                  "w", encoding="utf-8") as f:
            f.write('[{"id": "a", "at": ')
        args = types.SimpleNamespace(platforms="toutiao", dry=True, yes=False,
                                     retries=0, replace=False, case=None,
                                     near=0, no_preflight=False)
        with mock.patch.object(PM, "Runner") as R:
            rc = PM.cmd_run(args)
        self.assertEqual(rc, 1, "体检不过就不该去建 Runner")
        R.assert_not_called()

    def test_no_preflight_bypasses(self):
        import publish_multi as PM
        from unittest import mock
        with open(os.path.join(self.dir, "toutiao_published.json"),
                  "w", encoding="utf-8") as f:
            f.write('[{"id": "a", "at": ')
        args = types.SimpleNamespace(platforms="toutiao", dry=True, yes=False,
                                     retries=0, replace=False, case=None,
                                     near=0, no_preflight=True)
        with mock.patch.object(PM, "Runner") as R:
            rc = PM.cmd_run(args)
        self.assertEqual(rc, 0)
        R.assert_called_once()


class TestPublishMultiCli(unittest.TestCase):
    """publish_multi.py 的 CLI 参数接线回归（2026-10-10 实测三连崩）。

    背景：`retry` 子命令最后复用 `cmd_run`，但子解析器只定义了
    case/dry/yes/retries —— cmd_run 里引用的 `args.replace`、
    `args.near` 根本不存在，一跑到就 AttributeError。
    第二个坑：`cmd_reset` 把 `parse_plats()` 的**列表**直接塞给
    `Manifest.remove(cid, plat)`（只收单平台字符串做 dict key）
    → TypeError: unhashable type: 'list'。
    """

    def _tmp_manifest(self):
        d = tempfile.mkdtemp(prefix="pmcli_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return S.Manifest(os.path.join(d, "manifest.json"))

    def test_retry_namespace_covers_cmd_run_needs(self):
        """retry 的命名空间必须带齐 cmd_run 引用的全部属性。"""
        import publish_multi as PM
        from unittest import mock
        m = self._tmp_manifest()
        m.mark_failed("kibu", "xhs", "模拟失败", "标题")
        with mock.patch.object(PM.S, "Manifest", return_value=m):
            # 旧实现这里崩 AttributeError: 'Namespace' object has no
            # attribute 'replace'（修完 replace 还会崩 'near'）。
            rc = PM.main(["retry", "--platforms", "xhs", "--dry"])
        self.assertEqual(rc, 0)

    def test_reset_accepts_platform_list(self):
        """--platforms xhs 解析出来是列表，reset 必须逐平台 remove。"""
        import publish_multi as PM
        from unittest import mock
        import types
        m = self._tmp_manifest()
        m.mark_building("kibu", "xhs", "标题")
        args = types.SimpleNamespace(case="kibu", all_platforms=False,
                                     platforms="xhs")
        with mock.patch.object(PM.S, "Manifest", return_value=m):
            rc = PM.cmd_reset(args)   # 旧实现 TypeError: unhashable 'list'
        self.assertEqual(rc, 0)
        self.assertEqual(m.state("kibu", "xhs"), "pending")


class TestVerifyPortAndKeyGuard(unittest.TestCase):
    """verify.py 端口分流 + 前缀键偷匹配的回归（2026-10-25 实测两个假阴性）。

    ⚠️ 类名**不要**叫 TestVerifyMatching —— 本文件已有同名类，重名会被
    静默遮蔽（旧测试不跑、新测试报绿，双重假象）。
    """

    def test_端口按平台分流(self):
        """真相源是 chrome_cdp_launch.PLATFORMS：wechat 9222，三平台 9223。"""
        from multiplatform import verify as V
        self.assertEqual(V._port_for("wechat"), 9222)
        self.assertEqual(V._port_for("toutiao"), 9223)
        self.assertEqual(V._port_for("bilibili"), 9223)
        self.assertEqual(V._port_for("xhs"), 9223)

    def test_读取器默认端口不写死(self):
        """默认值必须是 None（运行时按平台解析），不能是模块级 9222。"""
        from multiplatform import verify as V
        import inspect
        self.assertIsNone(
            inspect.signature(V.read_toutiao_drafts).parameters["port"].default)
        self.assertIsNone(
            inspect.signature(V.read_bilibili_drafts).parameters["port"].default)

    def test_AI前缀键不偷匹配(self):
        """`ai-prank-call` 的前缀键 `AI` 不能把 ai-voice-calls 的标题偷走。

        线上事故形态：B站草稿箱回读 53 条，对账却报 40 条 missing，
        其中包含逐条确认过在箱里的 ai-prank-call 等。
        这里用 cases.json 里的案例名当标题（keys_for 的候选键之一），
        不依赖 out/bili 产物。
        """
        from multiplatform import verify as V
        cases = {c.get("id"): c.get("name", "") for c in A.load_cases()}
        n1, n2 = cases.get("ai-prank-call"), cases.get("ai-voice-calls")
        if not n1 or not n2:
            self.skipTest("cases.json 缺这两个案例的 name")
        m = S.Manifest(os.path.join(tempfile.mkdtemp(), "m.json"))
        # 偷匹配方先入清单（reconcile 按清单顺序逐个试键）
        m.mark_draft("ai-prank-call", "bilibili", n1)
        m.mark_draft("ai-voice-calls", "bilibili", n2)
        r = V.reconcile("bilibili", [n1, n2], m=m, verbose=False)
        self.assertEqual(r["missing"], [])
        self.assertEqual(sorted(r["matched"]),
                         ["ai-prank-call", "ai-voice-calls"])
        self.assertEqual(r["unmatched_titles"], [])

    def test_单字符id仍可匹配(self):
        """前缀键过滤不能误伤 id 本身：合成用例 `a` 要能匹配 `标题A`。"""
        from multiplatform import verify as V
        m = S.Manifest(os.path.join(tempfile.mkdtemp(), "m.json"))
        m.mark_draft("a", "toutiao", "标题A")
        r = V.reconcile("toutiao", ["标题A"], m=m, verbose=False)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["matched"], {"a": "标题A"})


class TestBlockedRule(TmpManifest):
    """被拦/封的处置规则（2026-10-28 用户口径）。

    被拦 → 改一版仍无效 → 记 blocked（终止态）→ 顺延下一篇 + 剔除草稿。
    """

    def test_blocked_is_terminal_and_keeps_reason(self):
        self.m.mark_blocked("a", "toutiao", "审核不通过：内容涉及宗教",
                            "标题A")
        self.assertTrue(self.m.is_blocked("a", "toutiao"))
        self.assertFalse(self.m.is_published("a", "toutiao"))
        # 不能算 done：verify 的「清单说发了」不能把被拦的算进去
        self.assertFalse(self.m.is_done("a", "toutiao"))
        self.assertIn("宗教", self.m.blocked_reason("a", "toutiao"))
        # 落盘后仍然是 blocked 且原因还在（不能像 failed 那样被 --retry 洗掉）
        again = S.Manifest(self.path)
        self.assertTrue(again.is_blocked("a", "toutiao"))
        self.assertIn("宗教", again.blocked_reason("a", "toutiao"))

    def test_blocked_not_requeued_even_with_replace(self):
        """--replace 也不该把 blocked 的稿重新捡起来 —— 重发还是被拦。"""
        self.m.mark_blocked("ai-prank-call", "toutiao", "违规")
        r = runner.Runner(["toutiao"], dry=True, manifest=self.m,
                          verbose=False, replace=True)
        queue = r.queue(only="ai-prank-call")
        self.assertEqual(queue, [], "blocked 的 case 必须完全不在队列里")

    def test_reset_can_revive_blocked(self):
        """确实改好了要重试时，唯一入口是 reset（人工确认的动作）。"""
        self.m.mark_blocked("a", "toutiao", "违规")
        self.m.remove("a", "toutiao")
        self.assertEqual(self.m.state("a", "toutiao"), "pending")


class TestBlockedNextTarget(unittest.TestCase):
    """顺延口径：公众号按编号、其余按草稿存得最久；都要跳过 blocked。"""

    def setUp(self):
        from multiplatform import blocked as B
        self.B = B
        self.dir = tempfile.mkdtemp(prefix="mp_blk_")
        self.m = S.Manifest(os.path.join(self.dir, "m.json"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_wechat_target_has_issue_number(self):
        cid, title, why = self.B.next_target("wechat", self.m)
        if not cid:
            self.skipTest("公众号草稿箱为空，跳过")
        self.assertIn("编号", why)

    def test_others_target_is_oldest(self):
        for plat in ("toutiao", "xhs", "bilibili", "juejin"):
            cid, title, why = self.B.next_target(plat, self.m)
            if not cid:
                continue
            self.assertIn("存得最久", why, "%s 的顺延口径应是草稿最久" % plat)

    def test_blocked_is_skipped_when_picking_next(self):
        cid, _, _ = self.B.next_target("toutiao", self.m)
        if not cid:
            self.skipTest("头条草稿箱为空，跳过")
        self.m.mark_blocked(cid, "toutiao", "被拦")
        nxt, _, _ = self.B.next_target("toutiao", self.m)
        self.assertNotEqual(nxt, cid, "被拦的那条不能再被选中")

    def test_match_key_is_stable_product_name(self):
        """删草稿的匹配键要用产品名（标题后半截会变，产品名不变）。"""
        key = self.B.match_key("quran-unlock", "toutiao")
        self.assertEqual(key, "Quran Unlock")
        # 产物标题确实变了也不影响匹配键
        self.assertNotEqual(key, self.B.title_of("quran-unlock", "toutiao"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
