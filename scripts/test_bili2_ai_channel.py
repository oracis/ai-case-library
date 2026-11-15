#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站二号 AI 通道回归锁（2026-11-15，PRD #179）。

用户要求「给二号建 AI 通道，像其他平台一样轮流发」。改动分散在四个文件，
任一处退化都会造成**不报错但发错账号 / 记错账 / 交替失效**，所以逐条锁死：

1. 二号不再被 `ONLY_KINDS` 钉死为 game，`decide()` 按上次类型交替；
2. AI case 的产物判据指向**实际发出去的正文源**（`out/articles/<cid>.html`），
   游戏稿仍是 `-b2.html`；
3. AI 候选池排除一号已发 case（两号同稿会被判搬运）；
4. `ledger_slot()` 按**进程账号**分账，不能只看 `-b2` 后缀；
5. `build_cmd()` 对二号 AI 走 `publish-ai`，游戏稿仍走游戏仓 bridge；
6. 档期路径的子进程必须带 9224 + bili2 profile；
7. `title_of()` 对纯正文片段能退回同目录 `note.json`。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bilibili_publish as B          # noqa: E402
import publish_guard as G            # noqa: E402
import publish_one as P              # noqa: E402
import publish_slot as S             # noqa: E402


class SlotAiChannel(unittest.TestCase):
    def test_no_only_kinds_for_bili2(self):
        self.assertNotIn("bilibili2", S.ONLY_KINDS)

    def test_decide_alternates_after_game(self):
        """上次发游戏 ⇒ 今天该发 AI（不再被 ONLY_KINDS 改成 game）。"""
        fake = {
            "records": lambda p: [("2026-11-14 12:49", "ext-ep-011-minetest")]
            if p == "bilibili2" else [],
            "ai_pool": lambda p, ready_only=True: ["upvoty", "cod-dex"],
            "game_pool": lambda p: [],
            "game_missing_pack": lambda p: [],
        }
        with mock.patch.multiple(S, **fake):
            d = S.decide("bilibili2", day="2026-11-15")
        self.assertEqual(d["kind"], "ai")
        self.assertEqual(d["candidates"], ["upvoty", "cod-dex"])
        self.assertNotIn("单一通道", d["reason"])

    def test_ai_pool_excludes_account_one(self):
        """二号候选池必须同时排除一号已发 case，否则两号同稿。"""
        cases = [{"id": "upvoty"}, {"id": "sent-by-one"}, {"id": "fresh"}]
        records = lambda p: ([("2026-11-01 10:00", "sent-by-one")]
                             if p == "bilibili" else [])
        with mock.patch.object(S, "records", side_effect=records), \
             mock.patch.object(S, "_load", return_value=cases), \
             mock.patch.object(S, "pack_exists", return_value=True):
            pool = S.ai_pool("bilibili2")
        self.assertNotIn("sent-by-one", pool)
        self.assertIn("fresh", pool)

    def test_pack_of_ai_points_to_real_body(self):
        """AI case 的产物必须是 save_via_api 真正读的那份 HTML。"""
        with mock.patch.object(S.os.path, "isfile", return_value=True):
            got = S.pack_of("bilibili2", "upvoty")
        self.assertTrue(got.endswith(os.path.join("out", "articles",
                                                  "upvoty.html")))

    def test_pack_of_game_keeps_b2(self):
        with mock.patch.object(S.os.path, "isfile", return_value=True):
            got = S.pack_of("bilibili2", "ext-ep-004-quake")
        self.assertTrue(got.endswith("ext-ep-004-quake-b2.html"))


class LedgerSplit(unittest.TestCase):
    def test_ai_case_on_account_two_goes_to_bili2(self):
        with mock.patch.object(B, "_plat", return_value="bilibili2"):
            self.assertEqual(B.ledger_slot("upvoty"), ("bilibili2", "upvoty"))

    def test_game_id_on_account_two_strips_b2(self):
        with mock.patch.object(B, "_plat", return_value="bilibili2"):
            self.assertEqual(B.ledger_slot("ext-ep-004-quake-b2"),
                             ("bilibili2", "ext-ep-004-quake"))

    def test_account_one_unchanged(self):
        with mock.patch.object(B, "_plat", return_value="bilibili"):
            self.assertEqual(B.ledger_slot("upvoty"), ("bilibili", "upvoty"))

    def test_draft_record_skips_account_one_ledger(self):
        """二号建草稿不许写进一号专属的 bili_published.json。"""
        with mock.patch.object(B, "_plat", return_value="bilibili2"), \
             mock.patch("ledger.set_draft_id") as set_id:
            B._record_draft("upvoty", 12345, title="x")
        set_id.assert_not_called()


class BuildCmdAndEnv(unittest.TestCase):
    def test_ai_uses_publish_ai(self):
        cmd = P.build_cmd("bilibili2", "upvoty", "out/articles/upvoty.html", True)
        self.assertIn("publish-ai", cmd)
        self.assertIn("upvoty", cmd)
        self.assertFalse(any("bridge.py" in str(x) for x in cmd))

    def test_game_still_uses_bridge(self):
        cmd = P.build_cmd("bilibili2", "ext-ep-004-quake",
                          "out/articles/ext-ep-004-quake-b2.html", True)
        self.assertTrue(any("bridge.py" in str(x) for x in cmd))
        self.assertNotIn("publish-ai", cmd)

    def test_run_env_is_account_two(self):
        env = P._fifo_env("bilibili2")
        self.assertEqual(env["CDP_BILI_PORT"], "9224")
        self.assertIn("chrome-bili2-profile", env["CDP_PLAT_PROFILE"])


class TitleFallback(unittest.TestCase):
    def test_note_json_for_plain_fragment(self):
        with tempfile.TemporaryDirectory() as d:
            body = os.path.join(d, "article.html")
            with open(body, "w", encoding="utf-8") as f:
                f.write("<p>只有正文，没有 h1</p>")
            with open(os.path.join(d, "note.json"), "w", encoding="utf-8") as f:
                f.write('{"title": "Upvoty：收集用户反馈并投票排序的看板"}')
            self.assertEqual(G.title_of(body),
                             "Upvoty：收集用户反馈并投票排序的看板")


if __name__ == "__main__":
    unittest.main()
