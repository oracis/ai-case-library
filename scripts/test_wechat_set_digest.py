#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wechat_set_digest 回归锁（2026-11-10 新建）。

纯源码/URL 断言 + 不起浏览器、不联网。钉死当天三件事：
  ① 修改页 URL 必须带 token（不带给「请重新登录」，看着像掉登录）
  ② 提交前必须过 preview，且只有 --yes 才 submit（缺省干跑不能写平台）
  ③ 成功判据只能是平台侧原始 publish_info.digest 逐字回读，不是脚本自报
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import wechat_set_digest as W                                # noqa: E402

SRC = io.open(os.path.join(HERE, "wechat_set_digest.py"), encoding="utf-8").read()


class TestTokenUrl(unittest.TestCase):
    def test_url_carries_token_and_appmsgid(self):
        u = W.modify_url("2247485037", "1690322380")
        self.assertIn("masssendmodify?action=edit_new", u)
        self.assertIn("appmsgid=2247485037", u)
        self.assertIn("token=1690322380", u)
        self.assertIn("lang=zh_CN", u)

    def test_docstring_warns_about_fake_login_page(self):
        self.assertIn("请重新登录", W.__doc__)
        self.assertIn("js_description", W.__doc__)


class TestSafetyAndEvidence(unittest.TestCase):
    def test_rejects_over_120_chars(self):
        self.assertIn("len(text) > 120", SRC)
        self.assertIn("拒绝", SRC)

    def test_dry_run_is_default(self):
        # 只有显式 --yes 才走到 submit()；干跑分支先 preview 再恢复
        self.assertIn('add_argument("--yes", action="store_true"', SRC)
        self.assertIn("if not args.yes:", SRC)
        self.assertIn("cdp.eval(RESTORE_JS)", SRC)
        self.assertIn("vm.submit()", SRC)

    def test_submit_chain_and_preview(self):
        self.assertIn("action=content_check", SRC)
        self.assertIn("action=submit", SRC)
        self.assertIn("vm.request(true", SRC)          # preview=1 不消耗次数
        self.assertIn("remain_modify_count", SRC)

    def test_independent_readback_is_the_only_verdict(self):
        self.assertIn("def remote_row", SRC)
        self.assertIn("appmsgpublish?sub=list", SRC)
        self.assertIn('row.get("digest")', SRC)
        self.assertIn('(row.get("digest") or "").strip() == text.strip()', SRC)
        self.assertIn("脚本自报成功一律不算数", W.__doc__)

    def test_notes_cover_anonymous_page_blocked(self):
        # 匿名公开页被「环境异常」墙挡住，不能当摘要回读源
        with io.open(os.path.join(ROOT, "docs", "PLATFORM_NOTES.md"),
                     encoding="utf-8") as f:
            notes = f.read()
        self.assertIn("环境异常", notes)


if __name__ == "__main__":
    unittest.main()
