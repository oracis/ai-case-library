#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yun_finalize 回归测试（2026-11-03 新建）。

钉死两个当天踩到的坑：
1. 阿里云发表必须**有头**（--headed）——滑块验证码是人机验证，
   无头窗口用户看不见 ⇒ 拖不了 ⇒ 永远差一步。
2. 弹滑块时必须**原地轮询等待**，不能 SystemExit 让用户重跑
   ——重跑会重新点一次「发布」，验证码刷新，用户上一次拖的成果作废，
   陷入「永远拖不赢」的死循环（当天实际重跑 4 次全卡同一处）。

检查方式：静态断言源码结构（不依赖浏览器，跑得快、可进 CI）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_finalize as F                                       # noqa: E402


def _src():
    with open(os.path.join(HERE, "yun_finalize.py"), encoding="utf-8") as f:
        return f.read()


class CaptchaWait(unittest.TestCase):
    def test_publish_aliyun_has_captcha_wait_param(self):
        import inspect
        sig = inspect.signature(F.publish_aliyun)
        self.assertIn("captcha_wait", sig.parameters,
                      "publish_aliyun 少了 captcha_wait 参数")
        self.assertGreater(sig.parameters["captcha_wait"].default, 0,
                           "captcha_wait 默认必须是正秒数（0 = 不等 = 死循环重现）")

    def test_captcha_branch_waits_instead_of_exiting(self):
        src = _src()
        # 滑块分支里必须出现轮询等待，且不得再是「弹滑块就 SystemExit」
        i = src.find("aliyunCaptcha-top")
        self.assertGreater(i, 0, "找不到滑块检测代码")
        seg = src[i:i + 2500]
        self.assertIn("deadline", seg, "滑块分支没有轮询截止时间")
        self.assertIn("time.sleep(3)", seg, "滑块分支没有轮询等待")
        self.assertIn("captcha_wait", seg, "滑块分支没用 captcha_wait")
        self.assertNotIn(
            "请在 9228 窗口人工拖一次滑块，通过后再重跑本命令",
            seg,
            "还在用「退出让人重跑」的旧文案——那会刷新验证码，死循环")

    def test_cli_passes_headed_for_aliyun(self):
        src = _src()
        # rindex：最后一次出现才是 CLI 里的真调用（前面还有函数定义）
        i = src.rfind("publish_aliyun(cdp")
        self.assertGreater(i, 0, "CLI 里找不到 publish_aliyun 调用")
        seg = src[max(0, i - 1200):i]
        self.assertIn("_connect(headed=a.headed)", seg,
                      "阿里云 CLI 没用 _connect(headed=a.headed) ⇒ 滑块在无头窗口里")

    def test_captcha_wait_happens_before_publish_click(self):
        """顺序铁律：先过滑块，再点「发布文章」。

        2026-11-03 实测根因：滑块在**开草稿页时**就弹，且挡在草稿读取之前
        ⇒ 必须开页面 → 过验证 → 草稿回填 → 再点发布。
        """
        src = _src()
        fn = src[src.find("def publish_aliyun"):]
        fn = fn[:fn.find("\ndef ")]
        i_captcha = fn.find("_wait_captcha(cdp")
        i_click = fn.find("_click_real(cdp, pos)")   # 点「发布文章」
        self.assertGreater(i_captcha, 0, "publish_aliyun 里没有等滑块")
        self.assertGreater(i_click, 0, "publish_aliyun 里没点发布")
        self.assertLess(i_captcha, i_click,
                        "等滑块必须排在点「发布文章」之前（否则又变死循环）")

    def test_content_backfill_check_exists(self):
        """过验证后必须检查草稿有没有回填（空的话用本地稿注入）。"""
        src = _src()
        fn = src[src.find("def publish_aliyun"):]
        fn = fn[:fn.find("\ndef ")]
        self.assertIn("_aliyun_content_state", fn,
                      "publish_aliyun 没检查草稿回填状态")
        self.assertIn("_aliyun_fill_content", fn,
                      "publish_aliyun 没有草稿没回填时的注入兜底")


class ProbeScript(unittest.TestCase):
    def test_probe_exists_and_is_readonly(self):
        p = os.path.join(HERE, "aliyun_probe.py")
        self.assertTrue(os.path.exists(p), "缺 aliyun_probe.py（滑块状态探针）")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        for bad in ("Input.dispatch", "Runtime.evaluate("):
            # 探针只允许 Runtime.evaluate（只读），不许派发输入事件
            if bad == "Input.dispatch":
                self.assertNotIn(bad, src, "探针不该派发输入事件（只读）")
        self.assertIn("aliyunCaptcha-top", src, "探针没查滑块容器")


class DraftLedgerBridge(unittest.TestCase):
    """建稿台账必须同时落 JSON 与 SQLite（2026-11-04 补）。

    为什么钉死：`publish_one.py` 的 `remote` 闸**只读 SQLite**
    （`db.get_post` 取 `draft_id`），`tc_manage`/`yun_finalize` 读 SQLite。
    而 `yun_draft_cdp` 原先只写 `longtail_sync.json` ⇒ 建完草稿后闸仍报
    「台账没有远端草稿 id」，照提示再跑一次 `--upload` 就**建出第二份草稿**，
    两份都可能被发表 —— 这是重复稿的源头，不是显示问题。
    """

    def test_bridge_exists_and_writes_sqlite(self):
        with open(os.path.join(HERE, "yun_draft_cdp.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _remember_db(", src, "缺 _remember_db（SQLite 落账）")
        self.assertIn("db.upsert_post(", src, "没写 platform_posts")
        self.assertRegex(src, r"_remember\(key,\s*\{", "JSON 台账被删了？")
        # 两个台账必须都在 main 里被调用，只调一个就是这次的事故形态
        self.assertIn("_remember_db(case, results", src,
                      "main 没有调用 _remember_db ⇒ SQLite 又空了")

    def test_draft_state_does_not_beat_published(self):
        """state=draft 写回不得覆盖已 published（db 的状态排序兜底）。"""
        import db                                          # noqa: PLC0415
        self.assertLessEqual(db._STATE_RANK.get("draft", 9),
                             db._STATE_RANK.get("published", 9),
                             "draft 的排位不能高于 published")


if __name__ == "__main__":
    unittest.main(verbosity=2)
