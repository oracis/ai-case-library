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
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_publish as W                                      # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
