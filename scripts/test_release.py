#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release.py 的回归测试。

这个文件钉的不是「能不能跑通」，而是**顺序**和**失败即停** ——
这两条错了都不会报错，只会静默产出错的东西：

  · 顺序错：fit 跑在 publish 前 → 补的是上一批案例；score 跑在 fit 前 →
    算的是没补判断的旧数据；build 跑在 score 前 → 打进产物的是空的派生分。
    四种错法全都能正常退出 0，站上也照常打开，只是内容是错的。
  · 不失败即停：fit 挂了还继续 score，等于把第一段的破坏再固化一层。

所以测试分四组：顺序、闸门、跳过逻辑、退出码语义。

不联网、不碰 OSS、不调 server。
"""

import argparse
import io
import contextlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import release as R                                            # noqa: E402


def parse(argv):
    """复用 release.main 的参数定义，拿到一个 Namespace。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket")
    ap.add_argument("--region", default=None)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--env-file")
    ap.add_argument("--out", default="public")
    ap.add_argument("--keep-inbox", action="store_true")
    ap.add_argument("--base")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--skip-publish", action="store_true")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-score", action="store_true")
    ap.add_argument("--skip-deploy", action="store_true")
    ap.add_argument("--yes", action="store_true")
    return ap.parse_args(argv)


class TestStepOrder(unittest.TestCase):
    """顺序是发布链路唯一的正确性来源 —— 错了不报错，所以钉死。

    这里**不是**逐对手写「A 在 B 前」，而是拿步骤表自己声明的 `after`
    去校验实际顺序。区别很重要：手写断言只能抓「改了顺序忘了改声明」，
    声明式校验两种漏法都抓得到（改了声明没改顺序、改了顺序没改声明）。
    """

    def test_six_steps_in_order(self):
        self.assertEqual(R.STEP_KEYS,
                         ("contentpack", "publish", "fit", "score", "build", "deploy"))

    def test_declared_dependencies_match_actual_order(self):
        problems = R.order_problems()
        self.assertEqual(problems, [], "；".join(problems))

    def test_no_cycle(self):
        self.assertIsNone(R.circular())

    def test_actual_order_is_topological(self):
        topo = [k for k in R.topological_order() if k in R.STEP_KEYS]
        self.assertEqual(topo, list(R.STEP_KEYS))

    def test_order_problems_detects_scrambled_order(self):
        """反向验证：把两步对调，order_problems 必须报出来。

        没有这条，上面那些断言可能只是「恒真」—— 永远通过的测试
        比没有测试更糟，因为它给的是假的安心。
        """
        scrambled = tuple(
            {"key": s["key"], "after": s.get("after") or (), "needs": s.get("needs")}
            for s in R.STEPS
        )
        # 把 score 和 fit 对调
        order = list(scrambled)
        i_fit = [n for n, s in enumerate(order) if s["key"] == "fit"][0]
        i_score = [n for n, s in enumerate(order) if s["key"] == "score"][0]
        order[i_fit], order[i_score] = order[i_score], order[i_fit]
        problems = R.order_problems(order)
        self.assertTrue(problems, "顺序被打乱却查不出来")
        self.assertTrue(any("score" in p for p in problems), problems)

    def test_order_problems_detects_unknown_upstream(self):
        steps = ({**s} for s in R.STEPS)
        steps = list(steps)
        steps[1]["after"] = ("没有这一步",)
        self.assertTrue(R.order_problems(steps))

    def test_circular_detects_cycle(self):
        steps = ({"key": "a", "after": ("b",)}, {"key": "b", "after": ("a",)})
        self.assertIsNotNone(R.circular(steps))

    def test_step_keys_are_unique(self):
        self.assertEqual(len(R.STEP_KEYS), len(set(R.STEP_KEYS)))

    def test_every_step_has_runner(self):
        """步骤表与执行器表必须一一对应 —— 加了步骤忘了写执行器，
        那一刻会 KeyError；但反过来（写了执行器没登记步骤）会静默不跑。"""
        self.assertEqual(set(R.STEP_KEYS), set(R.RUNNERS.keys()))

    def test_every_step_declares_needs_reason(self):
        """每步都要写清楚「为什么需要上游」—— 只写 after=("fit",) 的话，
        半年后没人知道那个依赖是必需的还是历史残留。"""
        for s in R.STEPS:
            with self.subTest(step=s["key"]):
                self.assertTrue(str(s.get("needs") or "").strip())

    def test_publish_is_the_only_server_dependency(self):
        self.assertEqual(tuple(R.NEEDS_SERVER), ("publish",))


class TestSelfcheck(unittest.TestCase):
    def test_selfcheck_passes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = R.selfcheck()
        self.assertEqual(rc, 0, buf.getvalue())

    def test_selfcheck_reports_what_it_checked(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            R.selfcheck()
        out = buf.getvalue()
        for label in ("六步齐全", "声明的依赖与实际顺序一致", "依赖无环",
                      "实际顺序就是拓扑序"):
            self.assertIn(label, out)

    def test_selfcheck_fails_on_scrambled_order(self):
        """自检本身必须对乱序报警 —— 它是 CI 里唯一的顺序守卫，
        一个永远说 OK 的自检等于没有自检。"""
        real = R.STEPS
        order = list(real)
        i_fit = [n for n, s in enumerate(order) if s["key"] == "fit"][0]
        i_score = [n for n, s in enumerate(order) if s["key"] == "score"][0]
        order[i_fit], order[i_score] = order[i_score], order[i_fit]
        R.STEPS = tuple(order)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = R.selfcheck()
        finally:
            R.STEPS = real
        self.assertNotEqual(rc, 0, buf.getvalue())
        self.assertIn("[!!]", buf.getvalue())


class TestOutputDir(unittest.TestCase):
    """对外产物目录不许是 dist —— 2026-09-20 曾用默认参数把带 441 条
    未核实素材的 dist 推上公网。"""

    def test_default_is_public(self):
        a = parse([])
        self.assertEqual(R.output_dir(a), os.path.join(ROOT, "public"))

    def test_not_dist(self):
        a = parse([])
        self.assertNotIn("dist", R.output_dir(a))

    def test_respects_out_arg(self):
        a = parse(["--out", "somewhere"])
        self.assertEqual(R.output_dir(a), os.path.join(ROOT, "somewhere"))


class TestSkipSet(unittest.TestCase):
    def test_no_skip_by_default(self):
        self.assertEqual(R.skip_set(parse([])), set())

    def test_each_skip_flag(self):
        self.assertEqual(R.skip_set(parse(["--skip-publish"])), {"publish"})
        self.assertEqual(R.skip_set(parse(["--skip-build"])), {"build"})
        self.assertEqual(R.skip_set(parse(["--skip-score"])), {"score"})
        self.assertEqual(R.skip_set(parse(["--skip-deploy"])), {"deploy"})

    def test_skips_combine(self):
        got = R.skip_set(parse(["--skip-publish", "--skip-deploy"]))
        self.assertEqual(got, {"publish", "deploy"})

    def test_only_selects_exactly(self):
        got = R.skip_set(parse(["--only", "fit,score"]))
        self.assertEqual(got, {"contentpack", "publish", "build", "deploy"})

    def test_only_single(self):
        got = R.skip_set(parse(["--only", "build"]))
        self.assertEqual(got, set(R.STEP_KEYS) - {"build"})

    def test_only_and_skip_together(self):
        """--only fit --skip-deploy：跳过集要合并，不是互相覆盖。"""
        got = R.skip_set(parse(["--only", "fit", "--skip-publish"]))
        self.assertEqual(got, {"contentpack", "publish", "score", "build", "deploy"})


class TestCallHelper(unittest.TestCase):
    """_call 把 SystemExit 归一成退出码 —— 它是「失败即停」的判决输入，
    归一错了整条链路的行为就跟着错。"""

    def test_returns_zero_on_clean_run(self):
        rc, _ = R._call(lambda: None, ["x"])
        self.assertEqual(rc, 0)

    def test_system_exit_none_is_zero(self):
        def boom():
            raise SystemExit()
        rc, _ = R._call(boom, ["x"])
        self.assertEqual(rc, 0)

    def test_system_exit_int_passes_through(self):
        def boom():
            raise SystemExit(3)
        rc, _ = R._call(boom, ["x"])
        self.assertEqual(rc, 3)

    def test_system_exit_str_is_failure(self):
        """SystemExit("消息") 的 code 是字符串，不能当成 0。"""
        def boom():
            raise SystemExit("坏了")
        rc, _ = R._call(boom, ["x"])
        self.assertEqual(rc, 1)

    def test_unexpected_exception_is_failure(self):
        def boom():
            raise ValueError("x")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc, _ = R._call(boom, ["x"])
        self.assertEqual(rc, 1)
        self.assertIn("ValueError", buf.getvalue())

    def test_argv_is_restored(self):
        """跑完必须还原 sys.argv —— 不还原会污染调用方（比如测试之间互相干扰）。"""
        saved = list(sys.argv)
        R._call(lambda: None, ["something", "--else"])
        self.assertEqual(sys.argv, saved)

    def test_argv_is_restored_on_exception(self):
        saved = list(sys.argv)

        def boom():
            raise SystemExit(1)
        R._call(boom, ["x"])
        self.assertEqual(sys.argv, saved)

    def test_capture_swallows_stdout(self):
        """capture=True 时子脚本的输出不能漏到外面 ——
        上游用「先探一枪」来决定要不要往下走，漏出来就变成噪音。"""
        def talky():
            print("这句话不该出现")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc, captured = R._call(talky, ["x"], capture=True)
        self.assertEqual(rc, 0)
        self.assertIn("这句话不该出现", captured)
        self.assertNotIn("这句话不该出现", buf.getvalue())


class TestPublishGate(unittest.TestCase):
    """有候选要发但 server 没起来时，必须在**第一步之前**就停下。

    以前的做法是等到 publish 那一步连不上才报错 —— 那时 contentpack
    已经写盘了，等于白改一遍数据。
    """

    def test_blocks_when_pending_and_no_server(self):
        real_alive = R.admin_alive
        real_pending = R.pending_publish
        R.admin_alive = lambda *a, **k: (False, "connection refused")
        R.pending_publish = lambda: ([{"id": "x"}], [])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = R.preflight(parse(["--bucket", "b", "--region", "cn-hongkong"]))
        finally:
            R.admin_alive = real_alive
            R.pending_publish = real_pending
        self.assertFalse(ok)
        self.assertIn("server", buf.getvalue())

    def test_allows_when_no_pending_even_if_no_server(self):
        """没有可发的东西时，server 没起不算问题 —— 补判断/重建/部署
        这几步都不需要它。六步是幂等的，重复跑不该失败。"""
        real_alive = R.admin_alive
        real_pending = R.pending_publish
        R.admin_alive = lambda *a, **k: (False, "refused")
        R.pending_publish = lambda: ([], [{"id": "y", "gaps": []}])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = R.preflight(parse([]))
        finally:
            R.admin_alive = real_alive
            R.pending_publish = real_pending
        self.assertTrue(ok)

    def test_skip_publish_bypasses_server_check(self):
        real_alive = R.admin_alive
        real_pending = R.pending_publish
        R.admin_alive = lambda *a, **k: (False, "refused")
        R.pending_publish = lambda: ([{"id": "x"}], [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ok = R.preflight(parse(["--skip-publish"]))
        finally:
            R.admin_alive = real_alive
            R.pending_publish = real_pending
        self.assertTrue(ok)

    def test_only_build_deploy_is_not_blocked_by_server_check(self):
        """`--only build,deploy` 不打算发布，不该被 server 闸门拦。

        「这一步会不会跑」有两个来源：`--skip-xxx` 和 `--only`。
        这处闸门一度只看 `args.skip_publish`，于是 `--only build,deploy`
        被误拦，而等价的 `--skip-publish` 却放行 —— 两个入口语义不一致，
        而且 CI 正是靠 `--only build,deploy` 只跑最后两步，误拦会直接把
        自动部署卡死。判定必须和 deploy 那处一样走 skip_set()。
        """
        real_alive = R.admin_alive
        real_pending = R.pending_publish
        R.admin_alive = lambda *a, **k: (False, "refused")
        R.pending_publish = lambda: ([{"id": "x"}], [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ok = R.preflight(parse(["--only", "build,deploy"]))
        finally:
            R.admin_alive = real_alive
            R.pending_publish = real_pending
        self.assertTrue(ok, "--only build,deploy 不该被 server 闸门拦")

    def test_only_publish_is_still_blocked(self):
        """反向：`--only publish` 确实要发布，就该被拦 ——
        放宽不能放宽到把真该拦的也放过去。"""
        real_alive = R.admin_alive
        real_pending = R.pending_publish
        R.admin_alive = lambda *a, **k: (False, "refused")
        R.pending_publish = lambda: ([{"id": "x"}], [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ok = R.preflight(parse(["--only", "publish"]))
        finally:
            R.admin_alive = real_alive
            R.pending_publish = real_pending
        self.assertFalse(ok, "--only publish 要发布，必须拦")


class TestRunFunctions(unittest.TestCase):
    """各步的执行函数在缺配置时的行为。**dry-run 与真跑要区别对待**：
    预演是来看「会怎样」的，报出缺什么比直接退出有用。"""

    def test_deploy_without_bucket_fails_for_real(self):
        rc = R.run_deploy(parse([]))
        self.assertNotEqual(rc, 0)

    def test_deploy_without_bucket_ok_in_dry_run(self):
        rc = R.run_deploy(parse(["--dry-run"]))
        self.assertEqual(rc, 0)

    def test_deploy_without_region_fails_for_real(self):
        rc = R.run_deploy(parse(["--bucket", "b"]))
        self.assertNotEqual(rc, 0)

    def test_deploy_without_region_ok_in_dry_run(self):
        rc = R.run_deploy(parse(["--bucket", "b", "--dry-run"]))
        self.assertEqual(rc, 0)

    def test_publish_skips_when_nothing_pending(self):
        real = R.pending_publish
        R.pending_publish = lambda: ([], [])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = R.run_publish(parse([]))
        finally:
            R.pending_publish = real
        self.assertEqual(rc, 0)
        self.assertIn("跳过", buf.getvalue())

    def test_build_rejects_missing_data_json(self):
        """构建产物读不出 inbox 数时必须失败，不能当成「干净」放过。
        第一版 inbox_count 把「读不出来」返回 0，把这个真 bug 吞掉了。"""
        real = R.build_static.main
        real_out = R.output_dir
        R.build_static.main = lambda: None          # 假装构建成功
        R.output_dir = lambda a: os.path.join(ROOT, "dist-nonexistent-zzz")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = R.run_build(parse([]))
        finally:
            R.build_static.main = real
            R.output_dir = real_out
        self.assertNotEqual(rc, 0)


class TestGuardOrder(unittest.TestCase):
    """main() 里的两道参数闸门要在做任何事之前生效 ——
    它们不该等到跑完五步才发现参数不对。

    这两条走子进程而不是直接调 R.main()：main() 会跑完整条链路（真的要
    构建、要连 OSS），没法在单测里调。而这两道闸门在真正做事**之前**就
    return，所以子进程跑它们既快又不产生副作用。
    """

    def _run(self, *extra):
        import subprocess
        return subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(ROOT, "scripts", "release.py")] + list(extra),
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)

    def test_only_typo_is_rejected(self):
        p = self._run("--only", "fit,scroe")
        self.assertEqual(p.returncode, 2)
        self.assertIn("scroe", p.stdout)
        self.assertIn("可用", p.stdout)

    def test_keep_inbox_with_deploy_is_rejected(self):
        p = self._run("--keep-inbox")
        self.assertEqual(p.returncode, 2)
        self.assertIn("未核实队列", p.stdout)

    def test_keep_inbox_without_deploy_is_allowed(self):
        """--keep-inbox --skip-deploy 是合法组合：本地预览要带 inbox，
        而只要不部署就不会把未核实素材推上公网。"""
        p = self._run("--keep-inbox", "--skip-deploy", "--skip-build",
                      "--skip-score", "--skip-publish")
        self.assertNotEqual(p.returncode, 2, p.stdout)


class TestExitCodes(unittest.TestCase):
    """退出码语义：非 0 = 停在第几步。CI 用得到，也方便人看日志对号。"""

    def test_selfcheck_exit_code_via_cli(self):
        import subprocess
        p = subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(ROOT, "scripts", "release.py"), "--selfcheck"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_deploy_failure_reports_step_six(self):
        """用 --only deploy 且不给 bucket：应停在第 6 步（退出码 6）。"""
        import subprocess
        p = subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(ROOT, "scripts", "release.py"), "--only", "deploy"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(p.returncode, 6, p.stdout)
        self.assertIn("第 6 步", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
