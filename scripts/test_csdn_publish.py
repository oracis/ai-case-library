#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csdn_publish 回归锁：签名确定性 / md_to_html / split_md / 台账。

⚠ 只测纯函数，不打真实接口（selftest 不消费真实队列的约定）。
"""
import json
import os
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import csdn_publish as C            # noqa: E402


def test_sign_shape():
    """签名串必须与实测形状逐字节一致；nonce 必须每次变。"""
    h1, ts1 = C.csdn_sign("POST", "/blog-console-api/v3/mdeditor/saveArticle")
    h2, ts2 = C.csdn_sign("POST", "/blog-console-api/v3/mdeditor/saveArticle")
    # 形状（nonce 是随机占位，替换后比对）
    expect = ("POST\n*/*\n\napplication/json;charset=UTF-8\n\n"
              "x-ca-key:203803574\nx-ca-nonce:{n}\n/blog-console-api/v3/mdeditor/saveArticle")
    n1 = h1["x-ca-nonce"]
    assert ts1 == expect.format(n=n1), "to_sign 形状漂移: %r" % ts1
    # nonce 每次新（防重放）
    assert h1["x-ca-nonce"] != h2["x-ca-nonce"], "nonce 未变，会被判重放"
    # 四件套齐全
    assert h1["x-ca-signature-headers"] == "x-ca-key,x-ca-nonce"
    # 同 nonce 同输入 → 签名可复现（HMAC 确定性）
    import base64, hashlib, hmac
    again = base64.b64encode(hmac.new(
        C.SECRET.encode(), ts1.encode(), hashlib.sha256).digest()).decode()
    assert again == h1["x-ca-signature"], "签名与手算不一致"
    # 路径变 → 签名必须变（防「签了个固定串」退化）
    h3, _ = C.csdn_sign("POST", "/other/path")
    assert h3["x-ca-signature"] != h1["x-ca-signature"]


def test_md_to_html():
    html = C.md_to_html("# 标题\n\n**粗** 和 *斜* 与 `code`，[链](https://a.b)。\n\n"
                        "- 甲\n- 乙\n\n1. 一\n2. 二\n\n> 引用\n\n"
                        "```py\nprint(1 < 2)\n```\n\n"
                        "![图](https://img/1.png)\n\n普通段落")
    assert "<h1>标题</h1>" in html
    assert "<strong>粗</strong>" in html and "<em>斜</em>" in html
    assert "<code>code</code>" in html
    assert '<a href="https://a.b">链</a>' in html
    assert "<ul><li>甲</li><li>乙</li></ul>" in html
    assert "<ol><li>一</li><li>二</li></ol>" in html
    assert "<blockquote><p>引用</p></blockquote>" in html
    assert "print(1 &lt; 2)" in html, "代码块内 < 必须 escape"
    assert '<img src="https://img/1.png" alt="图"/>' in html
    assert "<p>普通段落</p>" in html
    # XSS 面收敛：正文里的尖括号要转义
    assert "<script>" not in C.md_to_html("a <script> bad")


def test_split_md():
    t, b = C.split_md("# 我的标题\n\n正文第一段")
    assert t == "我的标题" and b == "正文第一段"
    t2, b2 = C.split_md("没有标题行\n正文")
    assert t2 and b2.startswith("没有标题行"), "无标题行时正文不能被吃掉"


def test_ledger_roundtrip():
    tmp = tempfile.mkdtemp()
    orig = C.DRAFT_FILE
    C.DRAFT_FILE = os.path.join(tmp, "csdn_drafts.json")
    try:
        C.remember("case-a", "123", "标题A", "https://u")
        C.remember("case-b", "456", "标题B")
        with open(C.DRAFT_FILE, encoding="utf-8") as f:
            led = json.load(f)
        assert led["case-a"]["article_id"] == "123"
        assert led["case-b"]["title"] == "标题B"
        C.remember("case-a", "789", "标题A改")     # 同键覆盖，不增条目
        with open(C.DRAFT_FILE, encoding="utf-8") as f:
            led2 = json.load(f)
        assert len(led2) == 2 and led2["case-a"]["article_id"] == "789"
    finally:
        C.DRAFT_FILE = orig


if __name__ == "__main__":
    test_sign_shape()
    test_md_to_html()
    test_split_md()
    test_ledger_roundtrip()
    print("csdn_publish selftest: 4/4 PASS")
