#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIFO 内容形态闸回归锁（PRD #124，2026-11-10）。

守的是 11-10 B站一号 FIFO 把 10-20 积压旧稿发出去、正文带「我去核了一遍」
内部核验注记这件事：PRD #90 只补了 case 级去重，`publish_one.run()` 的
FIFO 分支**仍然整条绕过 run_gates()**。所以闸下沉到四个入口的发布动作之前。

全离线：不联网、不碰浏览器、不写真台账。
跑法：cd scripts && python -X utf8 -m unittest test_fifo_content_gate -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import fifo_content_gate as F  # noqa: E402
import publish_guard as G      # noqa: E402

DIRTY_MD = ("# 品牌开始问「AI 回答里提不提我」\n\n"
            "> 我去核了一遍：所给原文中不存在任何 TrustMRR 成交记录，"
            "该成交价目前无任何来源\n\n## 它是干什么的\n\n监测品牌曝光。\n")
DIRTY_HTML = ('<h1>标题</h1>\n<blockquote style="color:#57606a;">'
              '我去核了一遍：所给原文中不存在任何成交记录</blockquote>'
              '\n<p>正文</p>')
CLEAN = "# 标题\n\n## 问题边界\n\n具体的工程取舍，正文干净。\n"


def _func_src(filename, funcname):
    """读单个函数源码做接入断言，不 import 重模块（避免顶层副作用）。"""
    with io.open(os.path.join(HERE, filename), encoding="utf-8") as f:
        txt = f.read()
    i = txt.index("def " + funcname + "(")
    j = txt.find("\ndef ", i + 5)
    return txt[i:j if j > 0 else len(txt)]


class Guard(unittest.TestCase):
    def test_platform_text_dirty_is_blocked(self):
        """B站传 draft/view 的纯文本正文 ⇒ 必须拦下。"""
        v = F.guard("bilibili", None, "标题", text=DIRTY_MD.replace("> ", ""))
        self.assertTrue(v["checked"])
        self.assertFalse(v["ok"])
        self.assertIn("内部核验注记", [b["rule"] for b in v["blocks"]])
        self.assertEqual(v["source"], "platform:draft/view")

    def test_clean_text_passes(self):
        v = F.guard("xhs", None, "标题", text=CLEAN)
        self.assertTrue(v["checked"])
        self.assertTrue(v["ok"], v["blocks"])
        self.assertEqual(v["blocks"], [])

    def test_no_source_is_unchecked_and_warned(self):
        """孤儿草稿认不出 case ⇒ 放行，但必须明确说闸未生效，不许假装验过。"""
        v = F.guard("wechat", "wx-no-such-draft-998877", "标题")
        self.assertFalse(v["checked"])
        self.assertTrue(v["ok"])
        self.assertIsNone(v["source"])
        self.assertIn("未生效", v["detail"])

    def test_local_html_is_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "case.html")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(DIRTY_HTML)
            with mock.patch.object(F, "local_sources", return_value=[p]):
                v = F.guard("wechat", "some-case", "标题")
        self.assertTrue(v["checked"])
        self.assertFalse(v["ok"])
        self.assertIn("内部核验注记", [b["rule"] for b in v["blocks"]])

    def test_local_note_json_body_is_scanned(self):
        """小红书正文在 note.json 的 body 里，不能只扫 title。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "note.json")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"title": "干净标题", "body": DIRTY_MD},
                                   ensure_ascii=False))
            with mock.patch.object(F, "local_sources", return_value=[p]):
                v = F.guard("xhs", "some-case", "干净标题")
        self.assertTrue(v["checked"])
        self.assertFalse(v["ok"])

    def test_platform_text_wins_over_local_source(self):
        """平台实际正文优先：本地脏但平台干净，按平台判（不是按本地误拦）。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "case.html")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(DIRTY_HTML)
            with mock.patch.object(F, "local_sources", return_value=[p]):
                v = F.guard("bilibili", "some-case", "标题", text=CLEAN)
        self.assertTrue(v["ok"])
        self.assertEqual(v["source"], "platform:draft/view")

    def test_html_comment_alone_never_blocks(self):
        """生成器的 `<!-- 核对用来源 … -->` 尾巴不渲染，不能误伤整批新稿。"""
        text = CLEAN + "\n<!--\n核对用来源（发布前逐个点开确认，别进正文）：官网\n-->\n"
        v = F.guard("wechat", None, "标题", text=text)
        self.assertTrue(v["ok"], v["blocks"])
        self.assertEqual(v["blocks"], [])

    def test_guard_is_read_only(self):
        """闸里不许出现写盘/起浏览器/发请求的动作。"""
        src = _func_src("fifo_content_gate.py", "guard")
        for bad in ("subprocess", "requests", "write_json", "urllib",
                    "os.remove", "CDP("):
            self.assertNotIn(bad, src, "guard 不该有 %s" % bad)

    def test_report_unchecked_says_not_checked(self):
        import contextlib
        buf = io.StringIO()
        v = F.guard("wechat", "wx-no-such-998877", "标题")
        with contextlib.redirect_stdout(buf):
            F.report(v, "wechat", "标题")
        self.assertIn("未生效", buf.getvalue())

    def test_today_repro_case_is_blocked(self):
        """11-10 用户点名的那篇：out/articles/promptmonitor-io.html。"""
        p = os.path.join(ROOT, "out", "articles", "promptmonitor-io.html")
        if not os.path.isfile(p):
            self.skipTest("复现稿已按授权清理，跳过真实稿断言")
        v = F.guard("bilibili", "promptmonitor-io",
                    "品牌开始问「AI 回答里提不提我」")
        self.assertTrue(v["checked"])
        self.assertFalse(v["ok"])
        self.assertIn("内部核验注记", [b["rule"] for b in v["blocks"]])


class Backlog(unittest.TestCase):
    def test_scan_excludes_newbatch_drafts(self):
        """新写路径的 66 篇产物必须 0 命中（11-10 全仓实测口径）。"""
        hit = F.scan_backlog()
        self.assertFalse([p for p in hit if "drafts/newbatch/" in p], hit)

    def test_scan_is_idempotent(self):
        self.assertEqual(F.scan_backlog(), F.scan_backlog())

    def test_scan_strips_html_comments(self):
        tmp = os.path.join(ROOT, "tmp")
        if not os.path.isdir(tmp):
            self.skipTest("tmp 目录不存在")
        p = os.path.join(tmp, "fifo_gate_comment_only.html")
        try:
            with io.open(p, "w", encoding="utf-8") as f:
                f.write("<p>正文</p><!-- 核对用来源（别进正文） -->")
            with io.open(p, encoding="utf-8") as f:
                txt = f.read()
            self.assertIsNone(G.VERIFY_NOTE.search(F.COMMENT.sub("", txt)))
        finally:
            if os.path.isfile(p):
                os.remove(p)


class Wiring(unittest.TestCase):
    """最容易回退的地方：只改纯模块、发布路径忘了接 ⇒ 等于没修。"""

    def test_bilibili_send_draft_guards_before_ui_publish(self):
        src = _func_src("bilibili_publish.py", "send_draft")
        self.assertIn("FCG.guard", src)
        self.assertIn("view_draft", src, "必须验平台实际正文，不能只看本地产物")
        self.assertLess(src.index("FCG.guard"),
                        src.index("ok, why = ui_publish_draft(cdp=cdp, aid=aid, cid=target"),
                        "闸必须在点发布之前")
        self.assertIn("return (False, None, aid, title)", src)

    def test_xhs_guards_before_click_publish(self):
        """闸必须在点发布之前，且**验的是平台正文**。

        2026-11-10 改：闸那一小段抽成了 `gate_draft(sub, d)`（原来埋在
        `_send_draft_on` 里 60 行发布流程中间，除了 grep 源码没法测）。
        断言随之改严，不是放宽：
          · 发布路径必须调 `gate_draft`；
          · `gate_draft` 里必须 `FCG.guard` **且带 `text=`** —— 只传 case 不传
            text 正是这个洞本身（闸只能反查本地产物，平台草稿脏时直接放行）。
        """
        src = _func_src("xhs_publish.py", "_send_draft_on")
        self.assertIn("gate_draft(sub, d)", src)
        self.assertLess(src.index("_verdict = gate_draft(sub, d)"),
                        src.index("strategy, clicked = _click_publish"),
                        "闸必须在点发布之前")
        self.assertIn("if not _verdict[\"ok\"]", src)

        gate = _func_src("xhs_publish.py", "gate_draft")
        self.assertIn("FCG.guard", gate)
        self.assertIn("text=", gate, "必须把平台正文交给闸，不能只给 case")
        self.assertIn("read_editor_body", gate,
                      "正文必须从编辑器现读（平台侧真值），不能只信本地产物")

    def test_wechat_guards_before_ensure_local_record(self):
        """拦下时不许留下「补登」写盘。"""
        src = _func_src("wechat_fifo.py", "main")
        self.assertIn("FCG.guard", src)
        self.assertLess(src.index("_verdict = FCG.guard"),
                        src.index("cid, created = ensure_local_record"))
        self.assertIn("return 3", src)

    def test_publish_one_fifo_prints_gate_notice(self):
        src = _func_src("publish_one.py", "run")
        self.assertIn("PRD #124", src)
        self.assertIn("不发布、不记账", src)

    def test_single_judgement_source(self):
        """不许另立判据：守卫只调 publish_guard.scan_content。"""
        with io.open(os.path.join(HERE, "fifo_content_gate.py"),
                     encoding="utf-8") as f:
            src = f.read()
        self.assertIn("G.scan_content", src)
        self.assertNotIn("re.compile(r\"我去核了一遍", src,
                         "判据只能在 publish_guard 里定义一次")


class EndToEnd(unittest.TestCase):
    """行为级（不只源码断言）：脏稿不许点发布，干净稿不许被误拦。"""

    def _run_send_draft(self, remote_content):
        import bilibili_publish as bp
        drafts = [{"article_id": "53842967", "title": "品牌开始问 AI 回答里提不提我",
                   "mtime": 1}]
        with mock.patch.object(bp, "_api_tab"), \
                mock.patch.object(bp.bili_api, "list_drafts",
                                  return_value=list(drafts)), \
                mock.patch.object(bp.bili_api, "view_draft",
                                  return_value={"content": remote_content}), \
                mock.patch.object(bp, "_load_manifest", return_value={}), \
                mock.patch.object(bp, "_plat", return_value="bilibili"), \
                mock.patch.object(bp, "load_cases", return_value=[]), \
                mock.patch("fifo_dedupe.published_case_ids", return_value=set()), \
                mock.patch("fifo_dedupe.filter_rows",
                           side_effect=lambda rows, *a, **kw: (rows, [])), \
                mock.patch.object(bp, "_draft_case_id",
                                  return_value="promptmonitor-io"), \
                mock.patch.object(bp, "_pick_target_draft",
                                  return_value=(drafts[0], "指定 case")), \
                mock.patch.object(bp, "ui_publish_draft",
                                  return_value=(True, "published")) as ui:
            rc = bp.send_draft(cdp=object(), dry=False, cid="promptmonitor-io")
        return rc, ui

    def test_dirty_platform_body_stops_before_ui_publish(self):
        rc, ui = self._run_send_draft(DIRTY_MD.replace("> ", ""))
        self.assertFalse(rc[0], "脏稿不该判发布成功")
        ui.assert_not_called()      # 点发布动作一次都没发生

    def test_clean_platform_body_still_publishes(self):
        rc, ui = self._run_send_draft(CLEAN)
        self.assertTrue(rc[0])
        ui.assert_called_once()


class TitleDashGate(unittest.TestCase):
    """PRD #135：FIFO 发的草稿箱旧稿，标题也要过破折号闸。

    2026-11-11 公众号 FIFO 发出《Coral…——卖的是「配置」》，因为
    `run_gates()` 的 title 闸整条被绕过、而旧 guard 只验正文。
    """

    DASH_TITLE = ("Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统"
                  "——卖的是「配置」，不是模型")

    def test_dash_title_blocked_when_body_present(self):
        v = F.guard("wechat", None, self.DASH_TITLE, text=CLEAN)
        self.assertTrue(v["checked"])
        self.assertFalse(v["ok"])
        self.assertIn("标题含破折号", [b["rule"] for b in v["blocks"]])

    def test_dash_title_blocked_even_without_body(self):
        """孤儿草稿 checked=False 也不许把标题闸一起跳过（最关键的一条）。"""
        v = F.guard("wechat", "wx-no-such-draft-998877", "标题——副题")
        self.assertFalse(v["checked"])
        self.assertFalse(v["ok"])
        self.assertIn("标题含破折号", [b["rule"] for b in v["blocks"]])
        self.assertIn("标题破折号闸已独立生效", v["detail"])

    def test_clean_title_passes(self):
        v = F.guard("xhs", None, "Coral：托管你的开源 AI 助手", text=CLEAN)
        self.assertTrue(v["ok"], v["blocks"])
        self.assertEqual(v["blocks"], [])

    def test_single_ascii_hyphen_allowed(self):
        """AI-native 这类单个 ASCII 连字符是正当写法，不许误杀。"""
        v = F.guard("xhs", None, "AI-native 工具", text=CLEAN)
        self.assertTrue(v["ok"], v["blocks"])

    def test_all_dash_variants_blocked(self):
        for dash in ("——", "—", "–", "－", "--"):
            v = F.guard("bilibili", None, "标题" + dash + "副题", text=CLEAN)
            self.assertFalse(v["ok"], dash)
            self.assertIn("标题含破折号", [b["rule"] for b in v["blocks"]])

    def test_report_shows_block_even_when_unchecked(self):
        """report 不能只打印「闸未生效」就把拦下原因吞掉。"""
        v = F.guard("wechat", "wx-no-such-draft-998877", "标题——副题")
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            F.report(v, "wechat", "标题——副题")
        out = buf.getvalue()
        self.assertIn("标题含破折号", out)
        self.assertIn("不发布、不记账", out)


if __name__ == "__main__":
    unittest.main()
