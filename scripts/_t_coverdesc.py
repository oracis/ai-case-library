#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cover_desc_ai.validate() 的门禁回归测试。

⚠ 为什么要写成文件而不是 `python -c`：**Bash 双引号会把 `$7,926` 里的
   `$7` 当shell 变量展开成空**，导致测试数据被悄悄改写、门禁看起来
   「失效」——2026-10-05 实测踩过：`[$€£¥￥]\s?\d` 的断言项测出来的
   字符串里根本没有 `$`。

用 `python -m unittest` 真跑（有自建 runner 会输出计数）；
⚠ 别用 `| tail -1`，OK 行会被截掉只剩 `Ran N tests`。
"""
import importlib.util
import io
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "cover_desc_ai", os.path.join(HERE, "cover_desc_ai.py"))
CD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CD)

ONE_LINER = "法语说唱与说唱歌手的自动混音工具：上传人声和伴奏、选一个风格预设，一键出可直接上架的成品。"


class TestValidate(unittest.TestCase):
    def test_accepts_good(self):
        ok, why = CD.validate("上传人声和伴奏，一键出能直接上架的混音成品",
                              ONE_LINER)
        self.assertTrue(ok, why)

    def test_rejects_money_symbol(self):
        for s in ("近30天 $7,926 累计 $37,012 的混音工具",
                  "月入 25 万美元的自动化 SEO 引擎",
                  "客单价 £300 每月的房贷预警工具"):
            ok, why = CD.validate(s, ONE_LINER)
            self.assertFalse(ok, "应该拦下金额：%r" % s)
            self.assertIn("金额", why)

    def test_rejects_mrr_word(self):
        ok, why = CD.validate("把 MRR 做到七千美元的英国房产数据订阅",
                              ONE_LINER)
        self.assertFalse(ok)
        self.assertIn("金额", why)

    def test_no_false_positive_on_b2b(self):
        """⚠ 封面不能误报：B2B / SaaS 含大写 B，不是金额。"""
        ok, why = CD.validate("面向 B2B 团队的 SaaS 排期工具", ONE_LINER)
        self.assertTrue(ok, why)

    def test_length_bounds(self):
        self.assertFalse(CD.validate("太短", ONE_LINER)[0])
        self.assertFalse(CD.validate("一" * 31, ONE_LINER)[0])
        self.assertTrue(CD.validate("一" * 12, ONE_LINER)[0])
        self.assertTrue(CD.validate("一" * 30, ONE_LINER)[0])

    def test_rejects_identical_to_one_liner(self):
        ok, why = CD.validate(ONE_LINER, ONE_LINER)
        self.assertFalse(ok)
        self.assertIn("逐字相同", why)

    def test_rejects_bang(self):
        self.assertFalse(CD.validate("上传人声就能出成品，太快了！", ONE_LINER)[0])


class TestMoneyRegex(unittest.TestCase):
    def test_symbol_pattern_matches(self):
        self.assertTrue(CD._MONEY.search("$7,926"))
        self.assertTrue(CD._MONEY.search("€250"))
        self.assertTrue(CD._MONEY.search("£300 每月"))

    def test_wan_suffix(self):
        self.assertTrue(CD._MONEY.search("月入 30 万"))
        self.assertTrue(CD._MONEY.search("2百万 流水"))


if __name__ == "__main__":
    unittest.main(verbosity=2)