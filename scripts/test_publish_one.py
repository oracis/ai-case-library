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
    """两朵云台账的「发过没有」判据。"""

    def setUp(self):
        self._load = S._load

    def tearDown(self):
        S._load = self._load

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
