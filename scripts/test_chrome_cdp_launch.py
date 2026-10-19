#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chrome_cdp_launch.py 的纯函数单测（**不启动浏览器**）。

为什么必须钉死这个文件（2026-10-05）
--------------------------------------
`PLAT_PROFILE` / `PLAT_PORT` 定义了却**从没被 `main()` 引用**：
`main()` 硬写公众号的 `PROFILE` + `CDP_PORT`，`build_args(port=)` /
`wait_cdp(port=)` 的端口参数形同虚设。
⇒ 谁直接跑本脚本都开到公众号那个Chrome，去看三平台的草稿箱，
   当然是空的。症状极具误导性：CDP 连得上、页面能开，
   唯一"异常"是草稿箱为空 ⇒ 看起来像"掉登录了"。

这类 bug **单测是唯一便宜的防线**：
它不产生任何远端副作用，但能在有人再犯同样的错时立刻红。
本文件断言的核心是「**平台 → (profile, port)** 这条映射，
以及**选中的 port 一路贯通到启动参数 / CDP 连接 / 登录探测**」。

跑法：
    python -m unittest scripts.test_chrome_cdp_launch
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chrome_cdp_launch as L  # noqa: E402


class TestPlatformTable(unittest.TestCase):
    """平台注册表本身。"""

    def test_all_four_platforms_registered(self):
        # 少一个平台 = 有人加了publish 脚本却忘了在这里注册，
        # 结果是「这个平台只能用公众号的浏览器」→ 空草稿箱。
        for key in ("wechat", "toutiao", "bilibili", "xhs"):
            self.assertIn(key, L.PLATFORMS)

    def test_wechat_uses_chromecdp_profile_and_9222(self):
        key, spec = L.resolve_platform("wechat")
        self.assertEqual(key, "wechat")
        self.assertEqual(spec["port"], 9222)
        self.assertIn("ChromeCDP", spec["profile"])

    def test_three_platforms_share_debug_profile_and_9223(self):
        # ⚠ 头/条/B站/小红书共用一份登录态（同profile、同端口）。
        #    这里逐个钉死，而不是只测一个 —— 曾经就是「只对了一个，
        #    另外三个照样错」，而且没人发现。
        for key in ("toutiao", "bilibili", "xhs"):
            rkey, spec = L.resolve_platform(key)
            self.assertEqual(rkey, key)
            self.assertEqual(spec["port"], 9223, "%s 端口不对" % key)
            self.assertIn("chrome-debug-profile", spec["profile"],
                          "%s profile 不对" % key)

    def test_three_alias_resolves_to_a_three_platform(self):
        # --platform three 不能落到公众号去
        key, spec = L.resolve_platform("three")
        self.assertNotEqual(key, "wechat")
        self.assertEqual(spec["port"], 9223)

    def test_aliases(self):
        for alias, want in (("tt", "toutiao"), ("bili", "bilibili"),
                            ("xhs", "xhs"), ("gzh", "wechat"),
                            ("wx", "wechat"), ("rednote", "xhs")):
            key, _ = L.resolve_platform(alias)
            self.assertEqual(key, want, "别名 %r 解析错了" % alias)

    def test_unknown_platform_raises_instead_of_silent_default(self):
        # ⚠⚠ 这是本bug 的核心教训：**认不出平台时绝不能静默回落
        #    公众号**。静默回落 = 拿错 profile = 空草稿箱 = 误判掉登录。
        for bad in ("", "  ", "douyin", "weixin", None):
            with self.assertRaises(ValueError, msg="输入 %r 应该报错" % bad):
                L.resolve_platform(bad)

    def test_platform_case_insensitive_and_stripped(self):
        for name in ("  WeChat ", "TT", "BILI", "XHS"):
            self.assertTrue(L.resolve_platform(name)[0])

    def test_every_platform_has_probe_and_url(self):
        for key, spec in L.PLATFORMS.items():
            self.assertIn("probe", spec, "%s 缺 probe" % key)
            self.assertTrue(spec["home_url"].startswith("https://"),
                            "%s home_url 不对" % key)
            self.assertTrue(spec["login_markers"], "%s 缺登录判据" % key)
            self.assertIn(spec["probe"], ("wechat", "url"))

    def test_wechat_is_the_only_special_probe(self):
        # 公众号走三级兜底（原地等→点登录→重导航），不能被通用判据替代
        self.assertEqual(L.PLATFORMS["wechat"]["probe"], "wechat")
        for key in ("toutiao", "bilibili", "xhs"):
            self.assertEqual(L.PLATFORMS[key]["probe"], "url")


class TestBuildArgs(unittest.TestCase):
    """端口/profile 必须真的进到 Chrome 命令行里。"""

    def test_port_is_actually_in_args(self):
        # ⚠ 以前 main() 从不传 port，于是这里永远返回 9222。
        #    断言的是「传进去就用」，不是「默认值对」。
        for port in (9222, 9223, 9333, 12345):
            args = L.build_args("chrome.exe", r"C:\p", port=port)
            self.assertIn("--remote-debugging-port=%d" % port, args)

    def test_profile_is_in_args(self):
        args = L.build_args("chrome.exe", r"C:\some\profile", port=9223)
        self.assertIn(r"--user-data-dir=C:\some\profile", args)

    def test_no_sandbox_is_always_present(self):
        # 不带 --no-sandbox，Chrome 2秒内自杀（exit 3），端口根本不开。
        # 这是本机硬性要求，别当普通 flag 删掉。
        for port in (9222, 9223):
            self.assertIn("--no-sandbox",
                          L.build_args("chrome.exe", r"C:\p", port=port))

    def test_default_port_falls_back_to_cdp_port(self):
        args = L.build_args("chrome.exe", r"C:\p")
        self.assertIn("--remote-debugging-port=%d" % L.CDP_PORT, args)

    def test_extra_args_are_appended(self):
        args = L.build_args("chrome.exe", r"C:\p", extra=["--mute-audio"],
                            port=9223)
        self.assertEqual(args[-1], "--mute-audio")


class _FakeProc(object):
    def __init__(self, exit_code=None):
        self._code = exit_code

    def poll(self):
        return self._code


class _FakeCDP(object):
    """记录被问过的 URL / 挂过的 target。"""

    def __init__(self, href="https://mp.toutiao.com/", body="", fail_eval=()):
        self._href = href
        self._body = body
        self._fail_eval = set(fail_eval)
        self.new_targets = []
        self.connected = []
        self.enabled = False
        self.closed = []

    def new_target(self, url):
        self.new_targets.append(url)
        return {"id": "T1"}

    def connect_target(self, tid):
        self.connected.append(tid)
        return True

    def send(self, method, params=None):
        if method == "Page.enable":
            self.enabled = True

    def eval(self, expr, refresh_context=False):
        if "location.href" in expr and "location.href" in self._fail_eval:
            return self._href
        if "location.href" in expr:
            return self._href
        if "innerText" in expr:
            return self._body
        return ""

    def close_target(self, tid):
        self.closed.append(tid)


class TestProbeLogin(unittest.TestCase):
    """三态判定：OK / NO_LOGIN / UNKNOWN，不能把UNKNOWN 当未登录。"""

    def test_login_redirect_is_no_login(self):
        spec = L.PLATFORMS["toutiao"]
        cdp = _FakeCDP(href="https://mp.toutiao.com/auth/page/login?x=1")
        state, detail = L.probe_login(cdp, spec)
        self.assertEqual(state, "NO_LOGIN")
        self.assertIn("登录页", detail)

    def test_healthy_toutiao_is_not_no_login(self):
        # 不在登录页 + 没有 logged_marker ⇒ UNKNOWN，不是 NO_LOGIN。
        # ⚠ 这条是最容易被改坏的：把 UNKNOWN 归成 NO_LOGIN 会让人
        #    白扫一次码，而真因可能只是页面没渲染完。
        spec = L.PLATFORMS["toutiao"]
        cdp = _FakeCDP(href="https://mp.toutiao.com/profile_v4/graphic/publish")
        state, _ = L.probe_login(cdp, spec)
        self.assertEqual(state, "UNKNOWN")

    def test_logged_marker_confirms_ok(self):
        spec = L.PLATFORMS["xhs"]
        cdp = _FakeCDP(href="https://creator.xiaohongshu.com/new/home",
                       body="小红薯6474A1BD创作中心首页")
        state, detail = L.probe_login(cdp, spec)
        self.assertEqual(state, "OK")
        self.assertIn("小红薯", detail)

    def test_missing_marker_is_unknown_not_no_login(self):
        spec = L.PLATFORMS["xhs"]
        cdp = _FakeCDP(href="https://creator.xiaohongshu.com/new/home",
                       body="加载中…")
        state, _ = L.probe_login(cdp, # None 不走 marker 分支
                                 spec)
        self.assertEqual(state, "UNKNOWN")

    def test_probe_opens_the_right_home_url(self):
        for key, url in (("toutiao", "https://mp.toutiao.com/"),
                         ("bilibili",
                          "https://member.bilibili.com/platform/home"),
                         ("xhs",
                          "https://creator.xiaohongshu.com/new/home")):
            cdp = _FakeCDP(href=url)
            L.probe_login(cdp, L.PLATFORMS[key])
            self.assertEqual(cdp.new_targets, [url], "%s 开错首页" % key)

    def test_page_enable_comes_after_connect(self):
        # ⚠ Page.enable 必须在 connect_target 之后，否则 CDP 类直接抛
        # 「未连接页面 target」。这里断言调用顺序没被写反。
        cdp = _FakeCDP(href="https://mp.toutiao.com/")
        L.probe_login(cdp, L.PLATFORMS["toutiao"])
        self.assertEqual(cdp.connected, ["T1"])
        self.assertTrue(cdp.enabled)

    def test_bilibili_passport_redirect_is_no_login(self):
        spec = L.PLATFORMS["bilibili"]
        cdp = _FakeCDP(href="https://passport.bilibili.com/login")
        state, _ = L.probe_login(cdp, spec)
        self.assertEqual(state, "NO_LOGIN")

    def test_probe_never_touches_wechat_token_for_three_platforms(self):
        #三平台绝不能调公众号的 _connect_mp：那会打mp.weixin 的
        # 登录兜底流程，跑在头条的 profile 上，纯属噪音且可能改状态。
        import wechat_publish as wp
        called = []
        orig = wp._connect_mp
        try:
            wp._connect_mp = lambda c: called.append(1) or ("tid", "tok")
            for key in ("toutiao", "bilibili", "xhs"):
                cdp = _FakeCDP(href=L.PLATFORMS[key]["home_url"])
                L.probe_login(cdp, L.PLATFORMS[key])
        finally:
            wp._connect_mp = orig
        self.assertEqual(called, [], "三平台不该碰公众号 token 检查")

    def test_wechat_probe_returns_token(self):
        import wechat_publish as wp
        orig = wp._connect_mp
        try:
            wp._connect_mp = lambda c: ("TID", "TOK")
            state, detail = L.probe_login(_FakeCDP(),
                                           L.PLATFORMS["wechat"])
        finally:
            wp._connect_mp = orig
        self.assertEqual(state, "OK")
        self.assertIn("TOK", detail)

    def test_wechat_probe_unwraps_tuple_correctly(self):
        # ⚠⚠ _connect_mp 返回 (tid, token) 二元组。按单值接拿到 tid
        #    ⇒ token 恒 None ⇒ 无论登录态多好都误报「掉登录」。踩过两次。
        import wechat_publish as wp
        orig = wp._connect_mp
        try:
            wp._connect_mp = lambda c: ("TID", "TOK")
            self.assertEqual(L._wechat_token(_FakeCDP()), "TOK")
            wp._connect_mp = lambda c: "BARE"
            self.assertEqual(L._wechat_token(_FakeCDP()), "BARE")
        finally:
            wp._connect_mp = orig


class TestWaitCdp(unittest.TestCase):
    def test_wait_cdp_probes_the_given_port(self):
        # ⚠ 以前 main() 不传port，wait_cdp 永远去敲 9222。
        #    这里用一个不存在的端口，只断言「它去敲了哪个端口」。
        self.assertFalse(L.wait_cdp(_FakeProc(exit_code=1), timeout=0.1,
                                    port=9223))
        seen = []
        import urllib.request

        class _Opener(object):
            def open(self, url, timeout=None):
                seen.append(url)
                raise OSError("refused")

        real = urllib.request.build_opener
        urllib.request.build_opener = lambda *a, **k: _Opener()
        try:
            L.wait_cdp(None, timeout=0.2, port=9223)
        finally:
            urllib.request.build_opener = real
        self.assertTrue(seen, "wait_cdp 根本没发请求")
        self.assertIn(":9223", seen[0])

    def test_early_exit_fails_fast(self):
        # Chrome 启动即退（exit 3 = 漏 --no-sandbox）时不该死等 timeout
        self.assertFalse(L.wait_cdp(_FakeProc(exit_code=3), timeout=30,
                                    port=9223))


class TestMainPlumbing(unittest.TestCase):
    """main() 必须把平台选出的 port 一路传下去。"""

    def test_list_flag_prints_mapping_and_exits_zero(self):
        rc, out, _ = self._run_main_isolated(["--list"])
        self.assertEqual(rc, 0)
        self.assertIn("9222", out)
        self.assertIn("9223", out)
        self.assertIn("chrome-debug-profile", out)

    def test_unknown_platform_exits_usage_code(self):
        rc, out, seen = self._run_main_isolated(
            ["--platform", "douyin", "--probe-only"])
        self.assertEqual(rc, 64)
        self.assertIn("未知平台", out)
        self.assertEqual(seen["ports"], [], "参数非法还去连 CDP 了")

    def test_default_platform_is_wechat(self):
        # 不给 --platform 必须是公众号（保持 wechat_publish autostart
        # 路径的历史行为不变）。断言的是 argparse 默认值，不能用
        # resolve_platform(None) —— 那个按设计就该抛错。
        self.assertEqual(L.build_parser().parse_args([]).platform, "wechat")
        # 短选项 -p 也要能用（`--platform` 太长，日常敲命令要短写）
        self.assertEqual(
            L.build_parser().parse_args(["-p", "xhs"]).platform, "xhs")

    def test_probe_only_three_platform_uses_9223(self):
        rc, out, seen = self._run_main_isolated(
            ["--platform", "xhs", "--probe-only"],
            href="https://creator.xiaohongshu.com/new/home",
            body="小红薯6474A1BD")
        self.assertIn("9223", out)
        self.assertIn("chrome-debug-profile", out)
        self.assertIn("小红书", out)
        self.assertEqual(seen["ports"], [9223], "连错端口了")
        self.assertEqual(rc, 0)

    def test_probe_only_wechat_uses_9222(self):
        # ⚠ 公众号走真实 `_connect_mp`（三级兜底不能 mock 掉，否则
        #    测的就不是那条路径了）。这里只断言**端口选对了**。
        rc, out, seen = self._run_main_isolated(
            ["--platform", "wechat", "--probe-only"], patch_connect=False)
        self.assertIn("9222", out)
        self.assertIn("ChromeCDP", out)
        self.assertEqual(seen["ports"], [9222], "连错端口了")
        # 连不上/没token 一律非 0，绝不假绿
        self.assertNotEqual(rc, 0)

    def test_every_platform_flag_reaches_its_own_port(self):
        # 逐个平台跑一遍，断言「--platform X ⇒ 连的是 X 的端口」。
        # 这条是本 bug 的正面判据：以前不管传什么都连 9222。
        want = {"wechat": 9222, "toutiao": 9223,
                "bilibili": 9223, "xhs": 9223}
        for name, port in want.items():
            rc, out, seen = self._run_main_isolated(
                ["--platform", name, "--probe-only"],
                href=L.PLATFORMS[name]["home_url"])
            self.assertEqual(seen["ports"], [port],
                             "--platform %s 连了 %s，应为 %d"
                             % (name, seen["ports"], port))
            self.assertIn("端口 %d" % port, out)

    def _run_main_isolated(self, argv, href="", body="",
                           patch_connect=True):
        """跑 main() 但把 CDP 换掉，记录它连了哪个端口。

        ⚠ 单测**绝不能碰真实浏览器**：本机 9222/9223 常常真开着，
        真连会既慢又不确定（别人可能正在用）⇒ 假绿或假红。
        ⚠ `patch_connect=False` 时保留真实 `_connect_mp`（公众号那条
        路径就是它，必须真跑），此时只 mock CDP 传输层。
        """
        import io
        import contextlib
        import wechat_publish as wp

        seen = {"ports": []}
        orig_cls, orig_rm = wp.CDP, L.remove_lock
        orig_pick, orig_clear = L.pick_chrome, L.clear_proxy
        orig_conn = wp._connect_mp

        class _CDP(_FakeCDP):
            def __init__(self, port=None, *a, **k):
                seen["ports"].append(port)
                _FakeCDP.__init__(self, href=href, body=body)

        wp.CDP = _CDP
        if patch_connect:
            wp._connect_mp = lambda c: (None, None)
        L.remove_lock = lambda *a, **k: False
        L.pick_chrome = lambda: (None, None)   # 触发「找不到 Chrome」早退
        L.clear_proxy = lambda: None
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv = ["chrome_cdp_launch.py"] + argv
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = L.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out
            wp.CDP = orig_cls
            wp._connect_mp = orig_conn
            L.remove_lock, L.pick_chrome = orig_rm, orig_pick
            L.clear_proxy = orig_clear
        return rc, buf.getvalue(), seen


if __name__ == "__main__":
    unittest.main()
