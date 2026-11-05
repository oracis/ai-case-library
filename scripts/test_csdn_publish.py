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
              "x-ca-key:203803574\nx-ca-nonce:%s\n/blog-console-api/v3/mdeditor/saveArticle")
    n1 = h1["x-ca-nonce"]
    assert ts1 == (expect % n1), "to_sign 形状漂移: %r" % ts1
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


def test_browser_sign_post_shape():
    """浏览器内 POST 签名串形状不许漂移（GET 未通，见 `_BROWSER_JS` 注释）。

    ⚠ 这里**不**断言 GET 的「正解」——那三版全是猜的、实测都 mismatch。
    只锁一件事：POST 的签名串仍含完整 path 且带 Content-Type（回归保护）。
    """
    js = C._BROWSER_JS
    assert "x-ca-nonce:' + nonce + '\\n' + signPath" in js, "签名串结尾形状漂移"
    assert "var signPath = a.path.split('?')[0];" in js, "path 取法变了"
    assert "isGet ? '' : CTYPE" in js, "GET/POST 的 Content-Type 分支不见了"
    assert "if (!isGet) hdrs['Content-Type'] = CTYPE;" in js, "HEADERS 里 Content-Type 分支不见了"


def test_draft_payload_is_draft_not_publish():
    """草稿写入必须 `status:2` + 键名 `id` —— 两个都错一次就多发/多建一篇。

    2026-11-04 实锤：`save_article()` 写死 `pubStatus:"publish"`/`status:0`，调用即发表、
    吃当日配额；而草稿要的是页面「保存草稿」那款 payload（键名是 `id`，写 `articleId` 会新建）。
    """
    import inspect
    import csdn_save_draft as D
    src = inspect.getsource(D.save_draft)
    assert '"status": 2' in src, "草稿 payload 的 status 必须是 2（0/publish 就是发表）"
    assert '"id": str(article_id)' in src, "更新键必须是 id，不是 articleId（写错会新建文章）"
    assert "pubStatus" not in src, "草稿 payload 里不该出现 pubStatus（那是发表用的）"
    # 正文与封面同一次提交，避免「改完正文再补封面」触发二次审核
    assert "cover_images" in src and "cover_type" in src, "封面应与正文同一次写回"


def test_cover_selector_primary_is_element_ui():
    """封面上传控件的主选择器必须是 `.el-upload__input`，老选择器只能垫底。

    2026-11-04 症状：一直报「抽屉没开」，其实是 `.cover-upload-box input[type=file]` 恒空。
    """
    import inspect
    import csdn_set_cover as SC
    src = inspect.getsource(SC._feed_and_confirm)
    assert ".el-upload__input" in src, "主选择器丢了"
    assert (src.index(".el-upload__input") < src.index(".cover-upload-box")), \
        "`.el-upload__input` 必须排在老选择器之前（否则又去点那个恒空的 input）"


def test_cover_size_guard():
    """>2MB 封面必须被拦下并缩图，不许硬喂（实测喂进去 40s 无回图）。"""
    import tempfile
    import csdn_set_cover as SC
    assert SC.COVER_MAX_BYTES == 2 * 1024 * 1024
    assert SC.COVER_TARGET_WIDTH <= 960
    # 小图：原样返回，不重写文件
    tmp = tempfile.mkdtemp()
    small = os.path.join(tmp, "s.png")
    with open(small, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                + (64).to_bytes(4, "big") + (64).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    assert SC.ensure_cover(small, log=lambda *a: None) == os.path.abspath(small)
    assert SC.image_size(small) == (64, 64), "PNG 宽高读错了，缩图会算错尺寸"
    # 非图片后缀：必须拒
    bad = os.path.join(tmp, "a.webp")
    with open(bad, "wb") as f:
        f.write(b"RIFF0000WEBP")
    try:
        SC.ensure_cover(bad, log=lambda *a: None)
    except SystemExit:
        pass
    else:
        raise AssertionError("CSDN 不收的格式应当直接拒，别等抽屉里失败")


if __name__ == "__main__":
    test_sign_shape()
    test_md_to_html()
    test_split_md()
    test_ledger_roundtrip()
    test_browser_sign_post_shape()
    test_draft_payload_is_draft_not_publish()
    test_cover_selector_primary_is_element_ui()
    test_cover_size_guard()
    print("csdn_publish selftest: 8/8 PASS")
