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


if __name__ == "__main__":
    unittest.main(verbosity=2)
