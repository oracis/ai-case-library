#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cnblogs_publish 回归锁：post 结构 / 配置校验 / 台账 / 复用函数一致性。

⚠ 只测纯函数与本地 IO，不打真实接口（selftest 不消费真实队列的约定）。
"""
import json
import os
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import cnblogs_publish as C       # noqa: E402
import csdn_publish as D          # noqa: E402


def test_post_struct_markdown_default():
    """默认：MD 原文 + [Markdown] 分类（否则博客园当 HTML 解析——铁律）。"""
    st = C._post_struct("标题", "# 正文\n\n段落")
    assert st["title"] == "标题"
    assert st["description"] == "# 正文\n\n段落", "默认应传 MD 原文"
    assert "[Markdown]" in st["categories"], "缺 [Markdown] 分类会被当 HTML 解析"


def test_post_struct_html_mode():
    """--html 模式：正文转 HTML，且不注入 [Markdown] 分类。"""
    st = C._post_struct("标题", "**粗**", as_html=True)
    assert "<strong>粗</strong>" in st["description"]
    assert "[Markdown]" not in st["categories"]


def test_reuses_csdn_pure_functions():
    """split_md/md_to_html 必须与 csdn_publish 同一实现（复用而非复制）。"""
    assert C.split_md is D.split_md
    assert C.md_to_html is D.md_to_html
    t, b = C.split_md("# 标题\n\n正文")
    assert t == "标题" and b == "正文"


def test_config_validation():
    """缺字段必须报 ConfigError 且给出拿 token 的指引。"""
    tmp = tempfile.mkdtemp()
    orig = C.CONFIG_FILE
    C.CONFIG_FILE = os.path.join(tmp, "cfg.json")
    try:
        with open(C.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": "u", "token": ""}, f)
        try:
            C.load_config()
            raise AssertionError("缺 blog_name 应报错")
        except C.ConfigError as e:
            assert "MetaWeblog" in str(e), "报错信息要包含 token 获取指引"
    finally:
        C.CONFIG_FILE = orig


def test_ledger_roundtrip():
    tmp = tempfile.mkdtemp()
    orig = C.DRAFT_FILE
    C.DRAFT_FILE = os.path.join(tmp, "cnblogs_drafts.json")
    try:
        C.remember("case-a", "123", "标题A", "https://u", status="draft")
        C.remember("case-b", "456", "标题B", status="published")
        with open(C.DRAFT_FILE, encoding="utf-8") as f:
            led = json.load(f)
        assert led["case-a"]["status"] == "draft"
        assert led["case-b"]["status"] == "published"
        C.remember("case-a", "789", "标题A改", status="published")  # 同键覆盖
        with open(C.DRAFT_FILE, encoding="utf-8") as f:
            led2 = json.load(f)
        assert len(led2) == 2 and led2["case-a"]["post_id"] == "789"
    finally:
        C.DRAFT_FILE = orig


if __name__ == "__main__":
    test_post_struct_markdown_default()
    test_post_struct_html_mode()
    test_reuses_csdn_pure_functions()
    test_config_validation()
    test_ledger_roundtrip()
    print("cnblogs_publish selftest: 5/5 PASS")
