# -*- coding: utf-8 -*-
"""wechat_publish.py 的样式清洗测试（离线，不碰浏览器）。

守的线：**样式白名单的方向不能改回去**。

背景（2026-09-21 实测，tmp/probe_wxstyle*.py）：WX_STYLE_DROP 里原先拉黑了
display / float / width / border-radius，理由是「微信一定会吃」—— 那是猜的。
在真实编辑器里逐条验证（注入 → 保存草稿 → 重载读回，编辑器解析结果与服务端
存储结果完全一致）后发现微信**全部保留**。因为这份黑名单，键值行的「值」只能
靠「······」点线凑对齐，白白丑了很久。

反过来的风险同样要守：白名单一旦放得太开，未来有人写了 position / transform /
渐变，会静默失效（保存后样式没了，且不报错）。所以两边都钉死。

用法：python -m unittest scripts.test_wechat_publish
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_article as MA                                      # noqa: E402
import wechat_publish as W                                     # noqa: E402


def style_of(html):
    """从 pm_safe_body 输出里抠出第一段 style="…"。"""
    i = html.find('style="')
    if i < 0:
        return ""
    j = html.find('"', i + 7)
    return html[i + 7:j]


class TestSanitizeStyle(unittest.TestCase):
    def test_layout_props_survive(self):
        """实测确认微信保留的布局属性，不许再被砍。"""
        s = W.sanitize_style(
            "display:flex;justify-content:space-between;float:right;clear:both;"
            "width:5.2em;height:6px;max-width:100%;opacity:0.6;vertical-align:middle")
        for prop in ("display:flex", "justify-content:space-between", "float:right",
                     "clear:both", "width:5.2em", "height:6px", "max-width:100%",
                     "opacity:0.6", "vertical-align:middle"):
            self.assertIn(prop, s, "%s 应该保留（实测微信认）" % prop)

    def test_radius_family_survives(self):
        """四角圆角是拼「一张表」的关键：首行圆上角、末行圆下角。"""
        s = W.sanitize_style("border-top-left-radius:8px;border-top-right-radius:8px;"
                             "border-bottom-left-radius:8px;"
                             "border-bottom-right-radius:8px")
        self.assertEqual(s.count("border-"), 4, s)

    def test_position_and_decoration_still_dropped(self):
        """定位/装饰类实测留不住，且留一半会错位 —— 必须继续砍。"""
        s = W.sanitize_style(
            "position:absolute;top:1px;left:2px;z-index:9;transform:scale(2);"
            "box-shadow:0 0 4px #000;background-image:linear-gradient(#fff,#000);"
            "overflow:hidden;filter:blur(2px)")
        self.assertEqual(s, "", s)

    def test_flex_shorthand_dropped(self):
        """`flex:1` 是简写属性，不在白名单里；display:flex 才是要的那个。"""
        self.assertEqual(W.sanitize_style("flex:1;flex-shrink:0"), "")

    def test_display_value_whitelist(self):
        ok = W.sanitize_style("display:flex")
        self.assertEqual(ok, "display:flex")
        self.assertEqual(W.sanitize_style("display:inline-block"),
                         "display:inline-block")
        # display:none 会把内容藏掉，不许放行
        self.assertEqual(W.sanitize_style("display:none"), "")

    def test_float_value_whitelist(self):
        self.assertEqual(W.sanitize_style("float:right"), "float:right")
        self.assertEqual(W.sanitize_style("float:none"), "float:none")
        self.assertEqual(W.sanitize_style("float:inline-start"), "")

    def test_important_flag_tolerated(self):
        self.assertEqual(W.sanitize_style("display:flex !important"), "display:flex")
        self.assertEqual(W.sanitize_style("display:none !important"), "")

    def test_vendor_prefix_and_url_dropped(self):
        self.assertEqual(W.sanitize_style("-webkit-box-shadow:0 0 2px #000"), "")
        self.assertEqual(W.sanitize_style("background:url(http://x/y.png)"), "")


class TestPmSafeBody(unittest.TestCase):
    ROW = ('<p style="margin:0;background:#f6f8fa;border-left:3px solid #e5b567;'
           'padding:8px 12px;font-size:15px;line-height:1.7;display:flex;'
           'justify-content:space-between;border-top-left-radius:8px;">'
           '<span style="color:#57606a;white-space:nowrap;">付费意愿</span>'
           '<span style="color:#8a5a00;font-weight:bold;text-align:right;'
           'margin-left:12px;">3/5</span></p>')

    def test_card_row_survives_cleaning(self):
        """成品卡片行经过清洗后，两端对齐/圆角/nowrap 都得还在，否则又退回点线。"""
        out = W.pm_safe_body(self.ROW)
        for prop in ("display:flex", "justify-content:space-between",
                     "border-top-left-radius:8px", "white-space:nowrap",
                     "margin-left:12px"):
            self.assertIn(prop, out, "%s 没活下来" % prop)
        self.assertNotIn("······", out)

    def test_container_unwrapped_and_table_collapsed(self):
        out = W.pm_safe_body(
            '<div class="wrap" id="a"><section><table><tr><td>x</td></tr></table>'
            '</section></div>')
        self.assertNotIn("<div", out)
        self.assertNotIn("<section", out)
        self.assertNotIn("<table", out)
        self.assertNotIn("class=", out)
        self.assertNotIn("id=", out)
        self.assertIn("x", out)

    def test_comment_and_script_stripped(self):
        out = W.pm_safe_body("<p>a</p><!-- 内部备注 --><script>alert(1)</script>")
        self.assertNotIn("内部备注", out)
        self.assertNotIn("alert", out)

    def test_style_attr_removed_when_all_dropped(self):
        """一行样式全被砍时，留下的应该是干净标签，而不是空的 style=""。"""
        out = W.pm_safe_body('<p style="position:absolute">x</p>')
        self.assertNotIn("style=", out)
        self.assertIn("x", out)


class TestBodyBaseline(unittest.TestCase):
    def test_baseline_on_paragraph_after_publish(self):
        """正文统一基线（font-size:17px / line-height:1.8 / rgba(0,0,0,0.9)）
        必须落到每个 <p>，不能只挂外层 <section>。

        原因（2026-09-21）：pm_safe_body 会把 div/section 整块 unwrap，
        挂外层的话基线随标签一起消失，正文退回微信默认字号；只有评分表
        和标题有显式样式。所以基线要内联到每个段落。这条测试用真实的
        make_article.render_html 产物跑完整链路，防止基线被挪回外层。
        """
        case = {}
        titles = ["测试标题"]
        secs = [
            ("它是干什么的", ["这是正文第一段。", "这是第二段。"]),
            ("钱从哪来", ["- 收入模式：订阅。"]),
            (None, ["钩子导语段。"]),
        ]
        html, _ = MA.render_html(case, titles, True, secs, 1.0)
        out = W.pm_safe_body(html)
        self.assertNotIn("<section", out)            # 外层已被剥
        body_ps = re.findall(r'<p style="([^"]*)"', out)
        self.assertTrue(body_ps, "没生成任何正文 <p>")
        for st in body_ps:
            if "font-size:14px" in st:      # 结尾注脚，设计上就更小更灰，跳过
                continue
            self.assertIn("font-size:17px", st, "正文段缺字号基线: %s" % st)
            self.assertIn("line-height:1.8", st, "正文段缺行高基线: %s" % st)
            self.assertIn("rgba(0,0,0,0.9)", st, "正文段缺颜色基线: %s" % st)


class TestResumeTodo(unittest.TestCase):
    """待办构造必须把「半成品」捞回来。

    回归点（2026-09-21 实测）：第 13 篇 shipfast 在「原创声明」那步被
    瞬时断连打断 —— 草稿建好了、没收尾、没记账。补记账后重跑 publish，
    它却**没被处理**：因为 `compute_queue()` 会把 published 里的条目整条
    剔掉，半成品就这样被永远当成「已发」跳过。
    """

    def setUp(self):
        self._orig = W.find_article
        W.find_article = lambda cid, name=None: "art-%s.html" % cid
        self.addCleanup(lambda: setattr(W, "find_article", self._orig))

    def test_半成品不被当已发跳过(self):
        cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        published = {
            "a": {"status": "draft"},                    # 真发完了 → 跳过
            "b": {"status": "partial", "appmsgid": "170"},   # 半成品 → 就地补完
        }
        ready = [(cases[2], "art-c.html")]               # compute_queue 只剩 c
        todo = W.build_todo(cases, published, ready)
        self.assertEqual([(t[0]["id"], t[2]) for t in todo],
                         [("b", "170"), ("c", None)])

    def test_partial但缺appmsgid时不当成补完(self):
        """没记 appmsgid 就没法就地补完，只能当新发（宁可重发也别卡住）。"""
        cases = [{"id": "a"}]
        published = {"a": {"status": "partial"}}
        todo = W.build_todo(cases, published, [])
        self.assertEqual([(t[0]["id"], t[2]) for t in todo], [("a", None)])


class TestFinishStatus(unittest.TestCase):
    """收尾结论：封面与原创都到位才 ok。

    回归点（2026-09-21 实测）：`publish_one` 曾**无条件** return ok，
    于是「封面没设上 / 原创声明失败」也被记成 status=draft，
    续完逻辑（build_todo 只挑 partial）再也碰不到它们 —— 15 篇里有
    comp-ai/outrank/bustem/kibu 四篇就这么漏了一轮，得手工改状态才补上。
    """

    def test_都到位才ok(self):
        self.assertEqual(W._finish_status(True, True), "ok")

    def test_封面缺算partial(self):
        self.assertEqual(W._finish_status(False, True), "partial")

    def test_原创缺算partial(self):
        self.assertEqual(W._finish_status(True, False), "partial")

    def test_都缺算partial(self):
        self.assertEqual(W._finish_status(False, False), "partial")


class TestPickDialogButton(unittest.TestCase):
    """弹窗按钮选择：不能被正文里的同名字文字骗走。

    回归点（2026-09-21 实测）：批量发布里有三篇（outrank/bustem/kibu）封面没设上
    且原创声明失败，根因是点封面「确认」时用 `_click_visible(...,"确认")`
    **全文档**按「包含」搜、按 DOM 顺序取第一个 —— 那三篇正文里恰好出现
    「外部无法确认哪个是当前值」这类句子（另外 27 篇一次都没出现「确认」），
    于是先命中正文段落：封面没设上、弹窗残留、连原创也被残留弹窗挡住。
    """

    def _el(self, t, leaf=True, left=100, top=100, w=60, h=30,
            disp="block", vis="visible"):
        return {"t": t, "leaf": leaf, "left": left, "top": top,
                "w": w, "h": h, "disp": disp, "vis": vis}

    def test_正文段落不会被当成确认按钮(self):
        """正文段落「包含」确认、但不是叶子、且在视口外 —— 必须被跳过。"""
        cands = [
            # 正文段落：文字很长、是容器、在视口外（y=-1345 那种）
            self._el("Tibo Louis-Lucas 报告 $274，外部无法确认哪个是当前值",
                     leaf=False, left=999, top=-1345, w=400, h=200),
            self._el("确认", leaf=True, left=600, top=400),
        ]
        pick = W._pick_dialog_button(cands, "确认")
        self.assertIsNotNone(pick)
        self.assertEqual(pick["t"], "确认")

    def test_文本不等只是包含时排在精确匹配之后(self):
        cands = [
            self._el("请确认封面设置", leaf=True, left=10, top=10),   # 只包含
            self._el("确认", leaf=True, left=600, top=400),          # 精确
        ]
        pick = W._pick_dialog_button(cands, "确认")
        self.assertEqual(pick["t"], "确认")

    def test_视口外的候选不选(self):
        cands = [self._el("确认", leaf=True, left=999, top=-1345)]
        self.assertIsNone(W._pick_dialog_button(cands, "确认"))

    def test_隐藏或过小的候选不选(self):
        cands = [
            self._el("确认", disp="none"),
            self._el("确认", vis="hidden"),
            self._el("确认", w=2, h=2),
        ]
        self.assertIsNone(W._pick_dialog_button(cands, "确认"))

    def test_没有候选返回None(self):
        self.assertIsNone(W._pick_dialog_button([], "确认"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
