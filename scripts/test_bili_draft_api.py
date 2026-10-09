#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili_draft_api.py 的纯函数单测（不连浏览器）。

跑法（在 scripts/ 目录下）：
    python -m unittest test_bili_draft_api

⚠ 本文件里钉死的是**2026-10-01 hook 真实请求实测出来的请求形状**。
   那次之前脚本里三处写法全错（form-urlencoded / csrf 放 body /
   `{type,text}` 节点），实测一律 `-400请求错误`。这些断言就是防止
   再改回去 —— 别为了让某个断言好过就改断言，要改先重新抓包。
"""
import hashlib
import json
import os
import sys
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bili_draft_api as api  # noqa: E402

BODY = """<h2>它是干什么的</h2>
<p>第一段正文。</p>
<blockquote>导语：一门接口生意。</blockquote>
<p>第二段，提到微信小程序的事实性表述。</p>"""


class TestMixinKey(unittest.TestCase):
    def test_matches_real_nav_keys(self):
        """2026-10-01 从 /x/web-interface/nav 实拿的两个 URL。"""
        k = api.mixin_key(
            "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png")
        self.assertEqual(len(k), 32)
        self.assertEqual(k, "7ad49814910771d30a34a403af7ffb02")

    def test_is_deterministic(self):
        a = api.mixin_key("https://x/aaa.png", "https://x/bbb.png")
        b = api.mixin_key("https://x/aaa.png", "https://x/bbb.png")
        self.assertEqual(a, b)


class TestWbiSign(unittest.TestCase):
    KEY = "7ad49814910771d30a34a403af7ffb02"

    def test_contains_wts_and_wrid(self):
        qs = api.wbi_sign({"csrf": "abc"}, self.KEY)
        self.assertIn("wts=", qs)
        self.assertIn("w_rid=", qs)

    def test_wrid_is_md5_of_sorted_query_plus_key(self):
        """签名必须等于 md5(排序后的 query + mixin_key)，否则服务端 400。"""
        qs = api.wbi_sign({"csrf": "abc", "foo": "bar"}, self.KEY)
        p = dict(urllib.parse.parse_qsl(qs))
        wts, rid = p["wts"], p["w_rid"]
        # 复算：键排序、值剔!'()*、逐项全编码
        items = []
        for k in sorted(["csrf", "foo", "wts"]):
            v = str({"csrf": "abc", "foo": "bar", "wts": wts}[k])
            v = "".join(c for c in v if c not in "!'()*")
            items.append((k, urllib.parse.quote(v, safe="")))
        expect = hashlib.md5(
            (urllib.parse.urlencode(items) + self.KEY).encode()).hexdigest()
        self.assertEqual(rid, expect)

    def test_keys_are_sorted(self):
        qs = api.wbi_sign({"z": 1, "a": 2, "m": 3}, self.KEY)
        keys = [kv.split("=")[0] for kv in qs.split("&")]
        self.assertEqual(keys, sorted(keys), keys)

    def test_strips_special_chars_for_signing_only(self):
        """值里的 !'()* 剔掉**只用于算签名**，发出去的仍是原值。

        WBI 规范：签名串用剔过的值，实际 query 传原值。
        （2026-10-01 实测确认 —— 抓包里 title 里的 `'` 原样出现。）
        """
        qs = api.wbi_sign({"title": "a!b'c(d)e*f"}, self.KEY)
        p = dict(urllib.parse.parse_qsl(qs))
        self.assertEqual(p["title"], "a!b'c(d)e*f")


class TestBuildArg(unittest.TestCase):
    def test_top_level_shape(self):
        """顶层必须含 type/template_id/category_id，缺一个就 -400。"""
        arg = api.build_arg("标题", BODY)
        for k in ("type", "template_id", "category_id", "title", "private_pub",
                  "reprint", "original", "list_id", "comment_selected",
                  "up_closed_reply", "timer_pub_time", "only_fans_level",
                  "only_fans_dnd", "summary", "opus"):
            self.assertIn(k, arg, k)
        self.assertEqual(arg["type"], 4)
        self.assertEqual(arg["template_id"], 1)
        self.assertEqual(arg["category_id"], 15)

    def test_private_pub_defaults_to_2(self):
        """抓包实测编辑器自己存草稿发的是 2（自己可见），不是 0。"""
        self.assertEqual(api.build_arg("t", BODY)["private_pub"], 2)

    def test_opus_shape(self):
        opus = api.build_arg("标题", BODY)["opus"]
        self.assertEqual(opus["opus_source"], 2)
        self.assertEqual(opus["title"], "标题")
        self.assertEqual(opus["pub_info"]["editor_version"], "eva3-4.0.0")
        self.assertEqual(opus["attachments"], {"is_aigc": 0})
        self.assertIn("paragraphs", opus["content"])

    def test_node_is_word_shape_not_type_text(self):
        """⚠ 核心回归：节点是 {node_type, word:{words,...}}。

        旧版误写成 {"type":"text","text":...} —— 服务端一律 -400。
        """
        arg = api.build_arg("标题", BODY)
        plain = [p for p in arg["opus"]["content"]["paragraphs"]
                 if p["para_type"] == api.TEXT_PARA]
        self.assertTrue(plain, "应至少有一个普通段落")
        n = plain[0]["text"]["nodes"][0]
        self.assertEqual(n["node_type"], 1)
        self.assertNotIn("type", n)
        self.assertNotIn("text", n)
        w = n["word"]
        # word 的 6 个键一个都不能少（少font_size 也是 -400）
        for k in ("words", "font_size", "color", "dark_color", "style",
                  "font_level"):
            self.assertIn(k, w, k)
        self.assertEqual(w["font_size"], 17)
        self.assertEqual(w["color"], "")
        self.assertEqual(w["dark_color"], "")
        self.assertEqual(w["style"], {})
        self.assertEqual(w["font_level"], "regular")

    def test_format_has_indent_object(self):
        """format 要是 {indent:{first_line_indent,indent}}，不是空串。"""
        arg = api.build_arg("标题", BODY)
        for p in arg["opus"]["content"]["paragraphs"]:
            self.assertIsInstance(p["format"], dict, p["para_type"])
            self.assertEqual(p["format"]["indent"],
                             {"first_line_indent": 0, "indent": 0})

    def test_para_types(self):
        paras = api.build_arg("标题", BODY)["opus"]["content"]["paragraphs"]
        types = [p["para_type"] for p in paras]
        self.assertEqual(api.H_PARA, 9)
        self.assertEqual(api.QUOTE_PARA, 4)
        self.assertEqual(api.TEXT_PARA, 1)
        self.assertIn(api.QUOTE_PARA, types, "blockquote 应成引用段")
        self.assertIn(api.H_PARA, types, "h2 应成标题段")

    def test_quote_para_keeps_para_type_4(self):
        """⚠ 回归：兜底分支吃掉blockquote 的bug（2026-10-01）。

        旧正则在 <p> 后面挂了兜底分支 `(.*?)</...>`，左most匹配让兜底
        抢先命中，把后面真正的 <p>/<blockquote> 整段吞进「裸文本」分支
        —— 表现是标题降级成普通段落、引用段数量变 0。
        """
        paras = api.build_arg("标题", BODY)["opus"]["content"]["paragraphs"]
        quotes = [p for p in paras if p["para_type"] == api.QUOTE_PARA]
        self.assertEqual(len(quotes), 1, "应恰好 1 个引用段")
        self.assertEqual(quotes[0]["text"]["nodes"][0]["word"]["words"],
                         "导语：一门接口生意。")

    def test_heading_not_downgraded(self):
        """h2 的文本不能出现在普通段落里（曾被兜底分支降级）。"""
        paras = api.build_arg("标题", BODY)["opus"]["content"]["paragraphs"]
        heads = [p for p in paras if p["para_type"] == api.H_PARA]
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0]["text"]["nodes"][0]["word"]["words"],
                         "它是干什么的")
        self.assertEqual(
            heads[0]["text"]["nodes"][0]["word"]["font_level"], "h2")

    def test_no_block_swallowed_into_bare_text(self):
        """整块扫描后不该出现「把标签当文本」的段落。"""
        paras = api.build_arg("标题", BODY)["opus"]["content"]["paragraphs"]
        for p in paras:
            w = p["text"]["nodes"][0]["word"]["words"]
            self.assertNotIn("<", w, w)
            self.assertNotIn(">", w, w)

    def test_para_words_match_text(self):
        arg = api.build_arg("标题", BODY)
        words = [p["text"]["nodes"][0]["word"]["words"]
                 for p in arg["opus"]["content"]["paragraphs"]]
        self.assertIn("第一段正文。", words)
        self.assertIn("导语：一门接口生意。", words)

    def test_blockquote_br_splits_into_multiple_quotes(self):
        """引用块里的 <br> 要拆成多段（与 B站 UI 行为一致）。"""
        paras = api.build_arg("标题",
                              "<blockquote>第一行<br>第二行</blockquote>"
                              )["opus"]["content"]["paragraphs"]
        quotes = [p["text"]["nodes"][0]["word"]["words"]
                  for p in paras if p["para_type"] == api.QUOTE_PARA]
        self.assertEqual(quotes, ["第一行", "第二行"])

    def test_article_id_only_when_given(self):
        self.assertNotIn("article_id", api.build_arg("t", BODY))
        self.assertEqual(api.build_arg("t", BODY, article_id=342)["article_id"],
                         342)

    def test_image_urls_capped_to_one(self):
        """B站只认首图作封面。"""
        arg = api.build_arg("t", BODY,
                            image_urls=["http://a/1.png", "http://a/2.png"])
        self.assertEqual(arg["image_urls"], ["http://a/1.png"])

    def test_no_image_urls_key_when_absent(self):
        self.assertNotIn("image_urls", api.build_arg("t", BODY))

    def test_summary_truncated_to_250(self):
        long_body = "<p>" + "字" * 900 + "</p>"
        arg = api.build_arg("t", long_body)
        self.assertLessEqual(len(arg["summary"]), 250)

    def test_empty_body_still_produces_paragraph(self):
        arg = api.build_arg("t", "")
        self.assertEqual(len(arg["opus"]["content"]["paragraphs"]), 1)

    def test_no_empty_paragraphs(self):
        """空行不能变成空段落 —— 服务端会拒。"""
        arg = api.build_arg("t", "<p>  </p><p></p><p>有内容</p>")
        for p in arg["opus"]["content"]["paragraphs"]:
            self.assertTrue(p["text"]["nodes"][0]["word"]["words"].strip())

    def test_arg_is_json_serialisable(self):
        """整个 arg 必须能 JSON 序列化（它要直接当 body 发）。"""
        arg = api.build_arg("标题", BODY, article_id=1,
                            image_urls=["http://a/1.png"])
        json.dumps(arg, ensure_ascii=False)


class TestStripTags(unittest.TestCase):
    def test_br_becomes_newline(self):
        self.assertEqual(api._strip_tags("a<br>b"), "a\nb")

    def test_entities_decoded(self):
        self.assertEqual(api._strip_tags("a&amp;b&nbsp;c"), "a&b c")

    def test_tags_removed(self):
        self.assertEqual(api._strip_tags("<p>x <b>y</b></p>"), "x y")

    # --- 2026-10-09：块级闭标签必须留换行（用户反馈「表格样式不好看」的真根因）

    def test_块级闭标签留换行(self):
        """</p> 等闭标签要变成换行，否则 build_arg 的 split("\\n") 失效。

        `build_arg` 的 blockquote 分支靠 `txt.split("\\n")` 拆成多段。
        原来 `_strip_tags` 只把 `<br>` 换成换行，其余标签删掉不占位，
        于是 blockquote 里的 6 个 `<p>` 被剥成一大坨：
            `付费意愿 ｜ 3/5支付可达 ｜ 3/5合规空间 ｜ 2/5…`
        接口路线（save_via_api，publish_one 默认）就是这么存的，
        所以线上 B站草稿的「表格」全是粘成一行的一坨。
        """
        self.assertEqual(api._strip_tags("<p>a</p><p>b</p>"), "a\nb")
        self.assertEqual(
            api._strip_tags("<blockquote><p>a</p><p>b</p></blockquote>"),
            "a\nb")

    def test_多行评分不再粘连(self):
        """6 行评分必须拆成 6 段，而不是一坨。"""
        rows = "".join("<p>标签%d ｜ <strong>%d/5</strong></p>" % (i, i)
                       for i in range(1, 7))
        arg = api.build_arg("t", "<blockquote>%s</blockquote>" % rows)
        paras = arg["opus"]["content"]["paragraphs"]
        self.assertEqual(len(paras), 6, [p["text"]["nodes"][0]["word"]["words"]
                                         for p in paras])
        for i, p in enumerate(paras, 1):
            t = "".join(n["word"]["words"] for n in p["text"]["nodes"])
            self.assertIn("标签%d ｜ " % i, t)
            self.assertTrue(t.endswith("%d/5" % i), t)

    def test_补空格在剥标签后仍保留(self):
        """对齐依赖的半角空格不能被剥标签顺手吃掉。"""
        out = api._strip_tags("<p>启动轻     ｜ <strong>5/5</strong></p>")
        self.assertEqual(out, "启动轻     ｜ 5/5")

    def test_全库526行不丢(self):
        """端到端：clean_body → build_arg，每一行都得独立成段。

        41 篇真实产物逐篇比对，任何一篇丢行都会在这里暴露。
        """
        import os
        import bilibili_publish as bp
        checked = 0
        for cid in bp.list_ids():
            h = bp.load_article_html(cid)
            if not h:
                continue
            body = bp.clean_body(h)
            want = body.count("｜")
            if not want:
                continue
            checked += 1
            arg = api.build_arg("t", body)
            got = 0
            for p in arg["opus"]["content"]["paragraphs"]:
                t = "".join(n["word"]["words"] for n in p["text"]["nodes"])
                if "｜" in t:
                    got += 1
            self.assertEqual(got, want, "%s 丢行：html %d → api %d"
                             % (cid, want, got))
        self.assertGreater(checked, 30, "真实样本太少，测试没意义")

    def test_首尾空白仍被strip(self):
        """新加的换行占位不能漏出首尾空白。"""
        self.assertEqual(api._strip_tags("<p>  x  </p>"), "x")


if __name__ == "__main__":
    unittest.main()