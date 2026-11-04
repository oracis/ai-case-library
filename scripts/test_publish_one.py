#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`publish_one.py` 的口径测试（离线，不碰浏览器、不联网、不写台账）。

钉住四条今天真踩过的东西：

1. **平台表不能有洞**：九平台少一个 GATES 条目，那个平台就会「一条闸都不跑」
   直接发布 —— 比报错更危险，因为看起来是成功的。
2. **游戏稿的平台产物不能张冠李戴**：游戏仓的 `platform/<平台>.md` 只能喂
   「`--file` 取稿」的五个平台。头条/小红书/B站的 `--case` 打开的是
   `out/<平台>/<case>/` 那一套二版产物，用 md 顶替会让档期以为有稿、
   发布时打空。
3. **两朵云台账缺 `tc_published_at` 不能被当成「没发过」**（2026-11-03 实撞：
   chessigma / cod-dex 只有 `tc_state`+`tc_article`，老判据漏掉 ⇒ 下一轮重发）。
4. **干跑绝不落子进程**：`--yes` 是唯一会真发的开关。

跑法：cd scripts && python -X utf8 -m unittest test_publish_one -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import publish_one as P      # noqa: E402
import publish_slot as S     # noqa: E402


class PlatformTable(unittest.TestCase):
    """九平台 × 四张表，一个都不能缺。"""

    def test_every_platform_complete(self):
        for key, cn in S.PLATFORMS:
            self.assertIn(key, P.GATES, "%s 没有闸表：等于裸奔发布" % cn)
            self.assertIn(key, P.TERMINAL, "%s 没标终点（草稿/发表）" % cn)
            self.assertIn(key, P.VERIFY_HINT, "%s 没写回读判据" % cn)
            self.assertIn(key, P.GAME_FILE, "%s 缺游戏仓平台稿文件名" % cn)

    def test_gate_names_exist(self):
        for key, names in P.GATES.items():
            for n in names:
                self.assertIn(n, P.GATE_FN, "%s 引用了不存在的闸 %s" % (key, n))

    def test_gates_cover_money_and_links(self):
        # 三条最容易漏的：KaTeX 平台、两朵云、所有平台的外链闸
        for key in ("csdn", "tencentcloud", "aliyun"):
            self.assertIn("katex", P.GATES[key], "%s 是 KaTeX 平台，必须过 katex 闸" % key)
        self.assertIn("link", P.GATES["toutiao"], "头条必须过外链闸（11-02 禁言根因）")
        for key in P.GATES:
            self.assertIn("link", P.GATES[key], "%s 没有外链闸" % key)


class BuildCmd(unittest.TestCase):
    def test_cmd_is_wellformed(self):
        for key, cn in S.PLATFORMS:
            cmd = P.build_cmd(key, "somecase", os.path.join(HERE, "x.md"), False)
            self.assertTrue(all(isinstance(c, str) and c for c in cmd), "%s 命令有空洞" % cn)
            self.assertTrue(cmd[0] and os.path.basename(cmd[0]).lower().startswith("python"),
                            "%s 不是用当前解释器起的" % cn)
            self.assertTrue(any(c.endswith(".py") for c in cmd), "%s 没有入口脚本" % cn)
            joined = " ".join(cmd)
            self.assertTrue("--case" in joined or "--file" in joined or "--slug" in joined,
                            "%s 命令没给稿子/案例" % cn)

    def test_tencent_uses_double_gate(self):
        cmd = " ".join(P.build_cmd("tencentcloud", "x", os.path.join(HERE, "x.md"), True))
        self.assertIn("--publish", cmd)
        self.assertIn("i-said-publish", cmd, "两朵云发表必须带确认口令，否则闸形同虚设")

    def test_aliyun_must_be_headed(self):
        """阿里云必须 --headed：滑块是人机验证，无头窗口用户拖不了。

        2026-11-03 实测踩坑：build_cmd 漏传 --headed ⇒ yun_finalize 自己起了
        无头实例，用户报告「看不到滑块」，连拖 4 次全卡在验证码。
        """
        cmd = " ".join(P.build_cmd("aliyun", "x", os.path.join(HERE, "x.md"), True))
        self.assertIn("--headed", cmd, "阿里云发表漏了 --headed ⇒ 用户看不见滑块")


class GamePack(unittest.TestCase):
    """游戏仓产物只在「--file 取稿」的平台认账。"""

    def setUp(self):
        self._root = S.GAMES_ROOT
        self.tmp = os.path.join(HERE, "_tmp_test_publish_one")
        os.makedirs(os.path.join(self.tmp, "content", "ep-999-x", "platform"),
                    exist_ok=True)
        for plat in S.MD_FILE_PLATFORMS:
            with open(os.path.join(self.tmp, "content", "ep-999-x", "platform",
                                   "%s.md" % plat), "w", encoding="utf-8") as f:
                f.write("# t\n\n正文\n")
        S.GAMES_ROOT = self.tmp

    def tearDown(self):
        S.GAMES_ROOT = self._root
        for plat in S.MD_FILE_PLATFORMS:
            p = os.path.join(self.tmp, "content", "ep-999-x", "platform", "%s.md" % plat)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(os.path.join(self.tmp, "content", "ep-999-x", "platform"))
        os.rmdir(os.path.join(self.tmp, "content", "ep-999-x"))
        os.rmdir(os.path.join(self.tmp, "content"))
        os.rmdir(self.tmp)

    def test_md_platforms_find_game_pack(self):
        for plat in S.MD_FILE_PLATFORMS:
            self.assertTrue(S.pack_of(plat, "ext-ep-999-x"),
                            "%s 应该能拿到游戏仓平台稿" % plat)

    def test_case_platforms_never_borrow_md(self):
        for plat in ("toutiao", "xhs", "bilibili"):
            self.assertIsNone(S.pack_of(plat, "ext-ep-999-x"),
                              "%s 用的是 out/<平台>/<case>/ 产物，md 不能顶替" % plat)

    def test_ai_case_ignores_game_repo(self):
        self.assertIsNone(S.pack_of("csdn", "ep-999-x"),
                          "非 ext- 前缀的 AI case 不该去游戏仓找")


class YunLedger(unittest.TestCase):
    """两朵云台账的「发过没有」判据。

    ⚠ 2026-11-03 切 SQLite 后：`_rows_yun` 是「db 优先 + JSON fallback」，
    本类测的是 fallback 分支的时点回退 —— mock db 返回空强制走 fallback，
    再 mock `_load` 注入 fake JSON。
    """

    def setUp(self):
        self._load = S._load
        self._get_posts = S.db.get_posts_by_platform
        S.db.get_posts_by_platform = lambda conn, platform, **kw: []

    def tearDown(self):
        S._load = self._load
        S.db.get_posts_by_platform = self._get_posts

    def test_published_without_timestamp_counts(self):
        fake = {"chessigma": {"tc_draft": "339489", "tc_article": "2774552",
                              "tc_state": "published",
                              "tc_state_at": "2026-11-03 11:20"},
                "only_draft": {"tc_draft": "339999"}}
        S._load = lambda name: fake if name == "yun_finalize.json" else {}
        rows = S._rows_yun("tencentcloud")
        ids = [cid for _, cid in rows]
        self.assertIn("chessigma", ids,
                      "有 tc_article + state=published 就是发过了，缺时点不能当没发")
        self.assertNotIn("only_draft", ids, "只有草稿 id 的不算发过")
        self.assertEqual(dict((c, a) for a, c in rows)["chessigma"], "2026-11-03 11:20",
                         "时点要按 published_at → state_at → updated 回退")


class DryRun(unittest.TestCase):
    """干跑不许落子进程。"""

    def setUp(self):
        self._call = P.subprocess.call

    def tearDown(self):
        P.subprocess.call = self._call

    def test_dry_run_never_spawns(self):
        def boom(*a, **kw):
            raise AssertionError("干跑居然真的执行了发布命令")
        P.subprocess.call = boom
        P.run("csdn", "ext-ep-001-super-mario-bros", yes=False)


class FifoQueue(unittest.TestCase):
    """B站/小红书是 FIFO 队列（用户定，2026-11-03）：发最久草稿，箱空才新写。

    钉两条：① 默认走 send-draft（不选稿、不过闸、不落 --yes 之外的东西）；
    ② --new 或显式 --case 才回到「按档期新写」的老路径——否则箱里积压的稿
    会被晾着，额度拿去发新稿。
    """

    def test_default_is_send_draft(self):
        self.assertEqual(P.FIFO_PLATFORMS, ("bilibili", "xhs"))

    def test_fifo_dry_run_never_spawns_and_never_picks(self):
        def boom(*a, **kw):
            raise AssertionError("FIFO 干跑居然真的执行了 send-draft")
        P.subprocess.call = boom
        # 不该走到 pick/闸：传一个必然让 pick 报错的假 day 也照样走 FIFO
        P.run("bilibili", None, yes=False, day="1999-01-01")

    def test_new_flag_bypasses_fifo(self):
        # --new 必须走到「按档期选稿」；FIFO 默认路径则绝不碰 pick
        def boom_pick(*a, **kw):
            raise AssertionError("--new 居然没走选稿")
        orig = P.pick
        P.pick = boom_pick
        try:
            self.assertRaises(AssertionError, P.run,
                              "bilibili", None, yes=False, new=True)
        finally:
            P.pick = orig


class UploadRemote(unittest.TestCase):
    """两朵云 --upload 必须走 CDP 通道（2026-11-04）。

    踩坑：upload_remote 漏传 --via，sync_longtail 默认 mcp ⇒ 扩展 WS 没连上
    时 remote 闸永远补不上，而项目里 10-30 就有不依赖扩展的 CDP 建稿。
    钉住命令带 --via cdp，且干跑不落子进程。
    """

    def setUp(self):
        self._call = P.subprocess.call

    def tearDown(self):
        P.subprocess.call = self._call

    def test_upload_uses_cdp_channel(self):
        seen = {}

        def fake_call(cmd, **kw):
            seen["cmd"] = cmd
            return 0

        P.subprocess.call = fake_call
        self.assertEqual(P.upload_remote("aliyun", "demo", dry=False), 0)
        cmd = seen["cmd"]
        self.assertIn("--via", cmd)
        self.assertEqual(cmd[cmd.index("--via") + 1], "cdp")
        self.assertIn("sync_longtail.py", " ".join(cmd))
        self.assertIn("aliyun", cmd)

    def test_upload_dry_never_spawns(self):
        def boom(*a, **kw):
            raise AssertionError("干跑居然真的执行了上传")
        P.subprocess.call = boom
        self.assertEqual(P.upload_remote("tencentcloud", "demo", dry=True), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
