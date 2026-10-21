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
import json
import inspect
import shutil
import tempfile
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


class TestIssueNumbers(unittest.TestCase):
    """篇号注册表：篇号是**历史编号**，不是当前排序位置。

    守的线（2026-09-24 事故）：新案例入库是往 cases.json 头部插入的，
    按位置编号会让老文章整体挪号 —— 已发草稿（第 1–30 篇）与本地重生成的
    HTML 对不上，新发的 9 篇还被编成第 1–9 篇与老的撞号。
    """

    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="issueno_")
        self._old = W.ISSUE_PATH
        W.ISSUE_PATH = os.path.join(self._d, "issue_numbers.json")

    def tearDown(self):
        W.ISSUE_PATH = self._old
        shutil.rmtree(self._d, ignore_errors=True)

    def test_老案例不动_新案例续号(self):
        reg = {"a": 1, "b": 2, "c": 3}
        # 新案例被插到最前面：c 是老三篇，绝不能因为位置变了就改号
        cases = [{"id": "new1"}, {"id": "new2"}, {"id": "a"},
                 {"id": "b"}, {"id": "c"}]
        reg, new = W.assign_issue_numbers(cases, reg=reg)
        self.assertEqual(reg["a"], 1)
        self.assertEqual(reg["c"], 3)
        self.assertEqual(new, [("new1", 4), ("new2", 5)])

    def test_重复调用幂等(self):
        cases = [{"id": "x"}, {"id": "y"}]
        reg, new = W.assign_issue_numbers(cases)
        self.assertEqual(reg, {"x": 1, "y": 2})
        reg2, new2 = W.assign_issue_numbers(cases, reg=reg)
        self.assertEqual(reg2, reg)
        self.assertEqual(new2, [])

    def test_跳过已占号_容忍空洞(self):
        # 手工删过条目留下空洞（1、3 被占），新号应从 4 起，别撞 2
        reg = {"a": 1, "c": 3}
        reg, new = W.assign_issue_numbers([{"id": "d"}], reg=reg)
        self.assertEqual(reg["d"], 4)

    def test_补种按历史发布顺序_不是cases顺序(self):
        cases = [{"id": "new1"}, {"id": "old1"}, {"id": "old2"}]
        published = {"old1": {}, "old2": {}}          # 只追加，插入顺序=发布顺序
        reg = W.initial_issue_numbers(cases, published=published)
        self.assertEqual((reg["old1"], reg["old2"]), (1, 2))
        self.assertEqual(reg["new1"], 3)              # 新案例排在老案例之后

    def test_落盘再读回一致(self):
        reg, _ = W.assign_issue_numbers([{"id": "b"}, {"id": "a"}])
        W.save_issue_numbers(reg)
        self.assertEqual(W.load_issue_numbers(), reg)
        # 文件里按篇号升序，便于人工 diff
        with open(W.ISSUE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(list(raw.keys()), ["b", "a"])

    def test_文件缺失或损坏不炸(self):
        self.assertEqual(W.load_issue_numbers(), {})
        with open(W.ISSUE_PATH, "w", encoding="utf-8") as f:
            f.write("{ 不是 json")
        self.assertEqual(W.load_issue_numbers(), {})

    def test_篇号真的写进正文与封面(self):
        c = {"id": "kibu", "name": "Kibu", "one_liner": "一句话"}
        self.assertIn("拆解海外 · 第 29 篇", W.cover_html(c, 29))
        self.assertNotIn("拆解海外 · 第 0 篇", W.cover_html(c, 29))


class TestVerdictFor(unittest.TestCase):
    """refresh 的「编辑器打不开」诊断判据（_verdict_for）。

    守的线：**UNKNOWN 不能升格成 PUBLISHED/MISSING**。
    背景（2026-10-16）：visualizee-ai 的草稿从服务端消失，refresh 只报
    「编辑器未就绪(320003)」—— 限流 / 用户群发过 / 草稿被删三种成因
    页面上一模一样，人肉排查半小时还得问用户。_verdict_for 把三种分清，
    且比 wechat_sync.classify 更严：**草稿箱没读成功时，即使已发表列表
    靠标题匹配上了，也只敢判 UNKNOWN**（远端为空 ≠ 草稿箱无）。
    """
    def setUp(self):
        # title_matcher 用 title_want，不依赖发布脚本
        self.cases = {
            "pub": {"id": "pub", "title_want": "标题-pub"},
            "gone": {"id": "gone", "title_want": "标题-gone"},
            "alive": {"id": "alive", "title_want": "标题-alive"},
        }

        def matcher(cid, c):
            return c.get("title_want")

        self.matcher = matcher
        self.draft = {"9": {"title": "标题-alive", "appmsgid": 9}}
        self.pub = [{"appmsg_id": 2, "title": "标题-pub"}]

    def _diag(self, cid, rec, draft=None, pub=None, draft_ok=True, pub_ok=True):
        """跑真 classify，包成 _verdict_for 要的 (diffs, meta)。"""
        import wechat_sync as S
        local = {cid: rec}
        diffs = S.classify(local, self.draft if draft is None else draft,
                           self.pub if pub is None else pub, self.cases,
                           draft_ok=draft_ok, pub_ok=pub_ok,
                           title_matcher=self.matcher)
        return (diffs, {"draft_ok": draft_ok, "pub_ok": pub_ok})

    def test_草稿没了但已发表_判PUBLISHED(self):
        d = self._diag("pub", {"status": "draft", "title": "标题-pub",
                               "appmsgid": "2"})
        v, detail = W._verdict_for(d, "pub")
        self.assertEqual(v, "PUBLISHED")
        self.assertIn("群发", detail)

    def test_两侧都读成功且都没有_判MISSING(self):
        d = self._diag("gone", {"status": "draft", "title": "标题-gone",
                                "appmsgid": "3"})
        v, _ = W._verdict_for(d, "gone")
        self.assertEqual(v, "MISSING")

    def test_草稿还在箱里_判RATE_LIMIT(self):
        d = self._diag("alive", {"status": "draft", "title": "标题-alive",
                                 "appmsgid": "9"})
        v, detail = W._verdict_for(d, "alive")
        self.assertEqual(v, "RATE_LIMIT")
        self.assertIn("限流", detail)

    def test_草稿箱读不到时即使已发表匹配上也只判UNKNOWN(self):
        # 最危险的一条：草稿箱 XHR 挂了 ⇒ remote_draft={}，
        # classify 仍会按「已发表有同标题」出 FIX；这里必须压回 UNKNOWN。
        d = self._diag("pub", {"status": "draft", "title": "标题-pub",
                               "appmsgid": "2"},
                       draft={}, draft_ok=False)
        v, detail = W._verdict_for(d, "pub")
        self.assertEqual(v, "UNKNOWN")
        self.assertIn("草稿箱", detail)

    def test_已发表列表读不到判UNKNOWN(self):
        d = self._diag("gone", {"status": "draft", "title": "标题-gone",
                                "appmsgid": "3"},
                       pub=[], pub_ok=False)
        v, _ = W._verdict_for(d, "gone")
        self.assertEqual(v, "UNKNOWN")

    def test_对账本身挂了也判UNKNOWN(self):
        for bad in (None, False):
            v, detail = W._verdict_for(bad, "gone")
            self.assertEqual(v, "UNKNOWN")
            self.assertTrue(detail)

    def test_别的case的diff不干扰本条判定(self):
        # 一批里同时有「已群发」和「草稿还在」两种，各判各的
        import wechat_sync as S
        local = {
            "pub": {"status": "draft", "title": "标题-pub", "appmsgid": "2"},
            "alive": {"status": "draft", "title": "标题-alive",
                      "appmsgid": "9"},
        }
        diffs = S.classify(local, self.draft, self.pub, self.cases,
                           title_matcher=self.matcher)
        diag = (diffs, {"draft_ok": True, "pub_ok": True})
        self.assertEqual(W._verdict_for(diag, "pub")[0], "PUBLISHED")
        self.assertEqual(W._verdict_for(diag, "alive")[0], "RATE_LIMIT")


class TestDeleteApi(unittest.TestCase):
    """删除接口形态锁定（2026-10-16 抓包实证，推翻 9-25 错误记忆）。"""

    def test_URL是operate_appmsg不是appmsg(self):
        u = W._del_api_url("TOK123")
        self.assertIn("cgi-bin/operate_appmsg", u)
        self.assertIn("sub=del", u)
        self.assertIn("t=ajax-response", u)
        self.assertIn("token=TOK123", u)
        # 老错误记忆的接口身不许回来
        self.assertNotIn("appmsg?action=del", u)

    def test_body带AppMsgId和固定尾巴(self):
        b = W._del_api_body("100000220", "TOK123", "fp123")
        self.assertEqual(
            b, "AppMsgId=100000220&fingerprint=fp123&token=TOK123"
               "&lang=zh_CN&f=json&ajax=1")

    def test_fingerprint可空(self):
        # 实测服务端校验宽松：fp 读不到时空着也要能发请求
        b = W._del_api_body("100000208", "TOK123", "")
        self.assertIn("AppMsgId=100000208", b)
        self.assertIn("fingerprint=&", b)


class TestGenCoverViewport(unittest.TestCase):
    """封面截屏的视口/目标校验（2026-10-17 修的真 bug）。

    症状：封面「变小」+ 右下角「拆解海外 · 第 X 篇」丢失，
    而 **PNG 尺寸仍是 2160x920 完全正常**，只看尺寸发现不了。
    """

    def test_必须撑视口到画布大小(self):
        """headless 视口约 780px < 封面 1080px ⇒ clip 被裁到视口内，
        右侧变成黑边、设计被塞进左上角 70%。"""
        src = inspect.getsource(W._gen_cover)
        self.assertIn("setDeviceMetricsOverride", src)
        self.assertIn('"width": COVER_W', src)
        self.assertIn('"height": COVER_H', src)
        # clip 的 scale 交给 deviceScaleFactor，别再叠一个 scale:2
        self.assertIn('"scale": 1', src)
        self.assertIn("captureBeyondViewport", src)

    def test_必须校验真的切到封面页(self):
        """connect_target 失败会静默留在上一个 target ⇒ 截到编辑器页面。"""
        src = inspect.getsource(W._gen_cover)
        self.assertIn("if not cover_cdp.connect_target(cover_tid)", src)
        self.assertIn('href.startswith("data:")', src)
        self.assertIn("t-name", src)          # 页面里得有我们自己的节点

    def test_tag仍在封面右下(self):
        """封面 HTML 必须带「拆解海外 · 第 X 篇」（铁律：篇号随图走）。"""
        c = {"id": "x", "name": "X"}
        self.assertIn("拆解海外 · 第 7 篇", W.cover_html(c, 7))


def _code_lines(src):
    """只看可执行行：注释里写旧 bug 模式不算数（踩过这个坑）。"""
    return [ln for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


class TestTokenEntry(unittest.TestCase):
    """拿 token 的入口必须是根域名。

    2026-10-18 实测：会话完全有效时，根域名 302 到 `home?...&token=827333358`；
    同一个 Chrome 里直接开 `MP_HOME`（无 token）稳定落到「请重新登录」壳页。
    历史上 `_open_editor` 用的正是 MP_HOME ⇒ 新开页抠不到 token。
    """

    def test_MP_ENTRY是根域名(self):
        self.assertEqual(W.MP_ENTRY, "https://mp.weixin.qq.com/")

    def test_open_editor用根域名起页(self):
        src = "\n".join(_code_lines(inspect.getsource(W._open_editor)))
        self.assertIn("new_target(MP_ENTRY)", src)
        self.assertNotIn("new_target(MP_HOME)", src)

    def test_无token时的兜底页也用根域名(self):
        """token 发现函数在「一个可复用 tab 都没有」时的兜底 new_target。"""
        src = "\n".join(_code_lines(inspect.getsource(W)))
        hits = [ln for ln in src.splitlines() if "new_target(" in ln]
        self.assertTrue(hits)
        for ln in hits:
            self.assertNotIn("new_target(MP_HOME)", ln)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ======================================================================
# 「发表」自动化（2026-10-19）：判定纯函数 + CLI 计划 + 记账约定
# ======================================================================
class TestEvaluateSend(unittest.TestCase):
    """发表结果的判定。守的是「两条证据同时成立才落账」这条纪律。"""

    def test_新记录且标题一致_ok(self):
        st, nid, why = W.evaluate_send(
            set(), [{"appmsg_id": "2247484064",
                     "title": "Bustem：帮电商品牌全网打假"}],
            True, "Bustem：帮电商品牌全网打假")
        self.assertEqual(st, "ok")
        self.assertEqual(nid, "2247484064")
        self.assertIn("标题一致", why)

    def test_新记录但草稿还在_不记published(self):
        st, nid, why = W.evaluate_send(
            set(), [{"appmsg_id": "2247484064", "title": "别的稿"}],
            False, "Bustem：帮电商品牌全网打假")
        self.assertEqual(st, "unverified")
        self.assertIn("草稿还在", why)

    def test_草稿没了但列表没新记录_不记published(self):
        st, _, why = W.evaluate_send(set(), [], True, "任何标题")
        self.assertEqual(st, "unverified")
        self.assertIn("没读到新记录", why)

    def test_两边都没动_pending(self):
        st, _, _ = W.evaluate_send({"1"}, [{"appmsg_id": "1", "title": "x"}],
                                   False, "y")
        self.assertEqual(st, "pending")

    def test_标题含全角空格也能比上(self):
        """远端标题常带全角/不间断空格，归一化后必须仍算一致。"""
        st, _, _ = W.evaluate_send(
            set(), [{"appmsg_id": "9", "title": "Bustem：帮电商品牌　全网打假"}],
            True, "Bustem：帮电商品牌 全网打假")
        self.assertEqual(st, "ok")

    def test_没有期望标题时草稿消失即ok(self):
        st, _, _ = W.evaluate_send(set(), [{"appmsg_id": "9", "title": "?"}],
                                   True, "")
        self.assertEqual(st, "ok")


class _SendArgs(object):
    def __init__(self, **kw):
        self.case = ""
        self.appmsgid = ""
        self.all = False
        self.limit = 0
        self.notify = False
        self.timeout = 180
        self.dry = True
        self.port = 9222
        for k, v in kw.items():
            setattr(self, k, v)


class TestSendCli(unittest.TestCase):
    """send 子命令的计划与记账。全部离线：CDP / 台账 / ledger 都打桩。"""

    def setUp(self):
        self.pub = {"bustem": {"title": "T", "status": "draft",
                               "appmsgid": "100000546", "file": "f.html"}}
        self.cases = [{"id": "bustem", "name": "Bustem"}]
        self.saved = {}
        self._orig = (W.load_published, W.save_published, W.load_cases,
                      W.make_wechat_title)
        W.load_published = lambda: self.pub
        W.load_cases = lambda: self.cases
        W.make_wechat_title = lambda c: "T:%s" % c.get("id", "")
        W.save_published = lambda p: self.saved.update(p)
        # ledger 是在 cmd_send 里 `import ledger as ld` 的 ⇒ 塞 sys.modules 才拦得住。
        # 不拦的话测试会写真实的 data/wechat_published.json（wechat_sync.save_local
        # 的注释里记着同类事故：测试覆盖真实台账，靠 git show 才恢复）。
        import types
        stub = types.ModuleType("ledger")
        stub.PUBLISHED = "published"
        stub.mark_calls = []
        stub.mark = lambda *a, **k: stub.mark_calls.append((a, k))
        self._ledger_stub = stub
        self._real_ledger = sys.modules.get("ledger")
        sys.modules["ledger"] = stub

    def tearDown(self):
        (W.load_published, W.save_published, W.load_cases,
         W.make_wechat_title) = self._orig
        if self._real_ledger is None:
            sys.modules.pop("ledger", None)
        else:
            sys.modules["ledger"] = self._real_ledger

    def _out(self, **kw):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            W.cmd_send(_SendArgs(**kw))
        return buf.getvalue()

    # ---- 计划（dry） ----
    def test_dry按case找到草稿id(self):
        out = self._out(case="bustem")
        self.assertIn("100000546", out)
        self.assertIn("发布（不推送", out)
        self.assertEqual(self.saved, {}, "dry 不许写台账")

    def test_notify模式文案不同(self):
        out = self._out(case="bustem", notify=True)
        self.assertIn("群发（推送粉丝", out)
        self.assertIn("需管理员扫码", out)

    def test_case没有appmsgid时报错(self):
        self.pub["bustem"].pop("appmsgid")
        out = self._out(case="bustem")
        self.assertIn("先跑 publish", out)

    def test_appmsgid台账查不到时报错(self):
        out = self._out(appmsgid="999999")
        self.assertIn("找不到 appmsgid=999999", out)

    def test_all只收draft和partial(self):
        self.pub["done"] = {"status": "published", "appmsgid": "1"}
        self.pub["gone"] = {"status": "draft", "appmsgid": "2", "missing": True}
        self.pub["noid"] = {"status": "draft"}
        out = self._out()
        self.assertIn("bustem", out)
        self.assertNotIn("done", out)
        self.assertNotIn("gone", out)

    def test_limit截断(self):
        for i in range(3):
            self.pub["c%d" % i] = {"status": "draft", "appmsgid": str(i)}
        out = self._out(limit=2)
        self.assertIn("待发表 2 条", out)

    # ---- 记账（mock 掉浏览器三件套） ----
    def _mock_send(self, status="ok", new_id="2247484064"):
        self.sent = []

        def fake_send_one(cdp, c, appmsgid, tok, home_tid=None, notify=False,
                          timeout=180):
            self.sent.append((c["id"], appmsgid, notify))
            return {"status": status, "appmsgid": new_id,
                    "publish_type": "群发" if notify else "发布",
                    "reason": "测试"}

        class FakeCDP(object):
            def __init__(self, *a, **k):
                pass
        W.send_one = fake_send_one
        W.CDP = FakeCDP
        W._connect_mp = lambda cdp, debug=False: ("tid", "tok")

    def test_成功后不覆盖草稿appmsgid(self):
        self._mock_send()
        out = self._out(case="bustem", dry=False)
        rec = self.saved["bustem"]
        self.assertEqual(rec["appmsgid"], "100000546",
                         "草稿 id 不能覆盖 —— refresh/fix-cover 靠它定位草稿")
        self.assertEqual(rec["published_appmsgid"], "2247484064",
                         "发表后的新 id 必须单独记（群发/发布后 id 必变）")
        self.assertEqual(rec["status"], "published")
        self.assertEqual(rec["publish_type"], "publish")
        self.assertEqual(rec["sent_via"], "send")
        self.assertIn("✓ 已发表", out)

    def test_群发模式记masssend(self):
        self._mock_send()
        self._out(case="bustem", dry=False, notify=True)
        self.assertEqual(self.saved["bustem"]["publish_type"], "masssend")
        self.assertEqual(self.sent[0][2], True, "notify 要传到 send_one")

    def test_没判定ok就不记published(self):
        self._mock_send(status="timeout")
        out = self._out(case="bustem", dry=False)
        self.assertNotIn("bustem", self.saved,
                         "没判定 ok 就一次都不该写台账")
        self.assertIn("没敢记 published", out)

    def test_ledger用草稿id提升状态(self):
        self._mock_send()
        self._out(case="bustem", dry=False)
        calls = sys.modules["ledger"].mark_calls
        self.assertTrue(calls)
        a, k = calls[0]
        self.assertEqual(a[0], "wechat")
        self.assertEqual(a[1], "bustem")
        self.assertEqual(a[2], "published")
        self.assertEqual(k.get("remote_id"), "100000546")


class TestSendDialogHelpers(unittest.TestCase):
    """弹窗辅助函数的 JS 必须钉在实测出来的选择器上（防 regression 成猜的）。"""

    def test_弹窗选择器是实测那个(self):
        self.assertEqual(W.SEND_DIALOG_SEL, ".new_mass_send_dialog")

    def test_开关读取按label文本定位(self):
        src = inspect.getsource(W._notify_switch_state)
        self.assertIn("群发通知", src)
        self.assertIn("weui-desktop-form__label", src)

    def test_关弹窗判可见看内层wrp(self):
        """外层容器关弹窗后仍在 DOM 里，只看它会把关着的当开着的。"""
        src = inspect.getsource(W._send_dialog_text)
        self.assertIn("weui-desktop-dialog__wrp", src)
        self.assertIn("display==='none'", src)


class _FakeEditorCdp(object):
    """按脚本演「草稿编辑页」：ready 判据 + 页面 HTML 都由测试给定。"""

    def __init__(self, html="", ready=False):
        self.html = html
        self.ready = ready
        self.closed = []
        self.opened = []

    def new_target(self, url):
        self.opened.append(url)
        return {"id": "t1"}

    def connect_target(self, tid):
        return True

    def close_target(self, tid):
        self.closed.append(tid)
        return True

    def eval(self, expr, refresh_context=False, **kw):
        if "querySelector('#title')" in expr:
            return self.ready
        if "outerHTML" in expr:
            return self.html
        return None


class TestDraftEditorState(unittest.TestCase):
    """「打不开编辑器」必须分已发表/已删除——处理方式完全不同。

    2026-10-19 实测（用户截图 + 页面 HTML）：appmsgid=100000783 的页面
    写着「此草稿已被删除／此草稿已发表，可前往发表记录查看」，
    已发表列表里确实有它（appmsg_id=2247484219）⇒ 是后台发过，
    不是损坏。此前共用一个「编辑器未就绪」错误，导向了错误的结论。
    """

    def test_已发表优先于已删除(self):
        """真实页面两句话都有，必须先判已发表（顺序反了会误判成删除）。"""
        cdp = _FakeEditorCdp("<div>此草稿已被删除</div>"
                             "<div>此草稿已发表，可前往发表记录查看</div>")
        state, _tid, page = W._open_draft_editor_for_send(
            cdp, "100000783", "tok", timeout=3)
        self.assertEqual(state, "published")
        self.assertIn("已发表", page)
        self.assertEqual(cdp.closed, [], "published 由调用方决定何时关")

    def test_纯删除页面判deleted(self):
        cdp = _FakeEditorCdp("<div>此草稿已被删除</div>")
        state, _, _ = W._open_draft_editor_for_send(cdp, "1", "tok", timeout=3)
        self.assertEqual(state, "deleted")

    def test_编辑器就绪(self):
        cdp = _FakeEditorCdp("", ready=True)
        state, _, _ = W._open_draft_editor_for_send(cdp, "1", "tok", timeout=3)
        self.assertEqual(state, "ready")

    def test_两者都没有超时(self):
        cdp = _FakeEditorCdp("<div> loading </div>")
        state, _, _ = W._open_draft_editor_for_send(cdp, "1", "tok", timeout=2)
        self.assertEqual(state, "timeout")


class TestSendAlreadyPublished(unittest.TestCase):
    """send_one 发现「后台已发表」时的行为：不重发 + 尽量关联新 id。"""

    PUB_JSON = {
        "publish_page": json.dumps({
            "publish_list": [{
                "publish_type": 101,
                "publish_info": json.dumps({"appmsg_info": [
                    {"appmsgid": 2247484219,
                     "title": "Visualizee.ai：给建筑师和室内设计师做效果图渲染，"
                              "两年卡在 $150/月，一次转型跳到 $8.6K"}]}),
            }],
        }),
    }

    def _run(self, case, patch_state="published"):
        cdp = _FakeEditorCdp("<div>此草稿已发表，可前往发表记录查看</div>")
        orig_open = W._open_draft_editor_for_send
        orig_list = W._list_from_home
        W._open_draft_editor_for_send = (
            lambda cdp, aid, tok, timeout=30: (patch_state, "t1", "page"))
        W._list_from_home = lambda cdp, home_tid, url: self.PUB_JSON
        try:
            return W.send_one(cdp, case, "100000783", "tok")
        finally:
            W._open_draft_editor_for_send = orig_open
            W._list_from_home = orig_list

    def test_已发表时不重发(self):
        r = self._run({"id": "visualizee-ai", "name": "Visualizee.ai"})
        self.assertEqual(r["status"], "already_published")
        self.assertIn("已发表", r["reason"])
        self.assertNotIn("ok", r["status"], "不能当成新发成功")

    def test_标题被后台改过靠产品名兜住(self):
        """本地标题对不上（后台改过），产品名包含仍能关联到新 id。"""
        r = self._run({"id": "visualizee-ai", "name": "Visualizee.ai"})
        self.assertEqual(r["appmsgid"], "2247484219")
        self.assertIn("需人工确认", r["reason"])

    def test_标题精确相等时置信度高(self):
        case = {"id": "x", "name": "别的"}
        orig_title = W.make_wechat_title
        W.make_wechat_title = lambda c: ("Visualizee.ai：给建筑师和室内设计师"
                                         "做效果图渲染，两年卡在 $150/月，"
                                         "一次转型跳到 $8.6K")
        try:
            r = self._run(case)
        finally:
            W.make_wechat_title = orig_title
        self.assertEqual(r["appmsgid"], "2247484219")
        self.assertIn("标题精确", r["reason"])
        self.assertNotIn("需人工确认", r["reason"])


class TestSendCliAlreadyPublished(unittest.TestCase):
    """cmd_send 对 already_published 的记账：修 status，绝不重发。"""

    def test_后台已发表则修台账且不当新发(self):
        pub = {"bustem": {"title": "T", "status": "draft",
                          "appmsgid": "100000546", "file": "f.html"}}
        saved = {}
        orig = (W.load_published, W.save_published, W.load_cases,
                W.make_wechat_title, W.send_one, W.CDP, W._connect_mp)
        W.load_published = lambda: pub
        W.load_cases = lambda: [{"id": "bustem", "name": "Bustem"}]
        W.make_wechat_title = lambda c: "T:%s" % c.get("id", "")
        W.save_published = lambda p: saved.update(p)
        W.send_one = lambda *a, **k: {
            "status": "already_published", "appmsgid": "2247484064",
            "publish_type": "unknown",
            "reason": "远端编辑器显示此草稿已发表；关联到已发表记录 2247484064（标题精确）"}

        class FakeCDP(object):
            def __init__(self, *a, **k):
                pass
        W.CDP = FakeCDP
        W._connect_mp = lambda cdp, debug=False: ("tid", "tok")
        import io
        import contextlib
        import types
        stub = types.ModuleType("ledger")
        stub.PUBLISHED = "published"
        stub.mark_calls = []
        stub.mark = lambda *a, **k: stub.mark_calls.append((a, k))
        real = sys.modules.get("ledger")
        sys.modules["ledger"] = stub
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                W.cmd_send(_SendArgs(case="bustem", dry=False))
        finally:
            (W.load_published, W.save_published, W.load_cases,
             W.make_wechat_title, W.send_one, W.CDP, W._connect_mp) = orig
            if real is None:
                sys.modules.pop("ledger", None)
            else:
                sys.modules["ledger"] = real
        rec = saved["bustem"]
        self.assertEqual(rec["status"], "published")
        self.assertEqual(rec["sent_via"], "backend_manual")
        self.assertEqual(rec["published_appmsgid"], "2247484064")
        self.assertEqual(rec["appmsgid"], "100000546", "草稿 id 仍不动")
        out = buf.getvalue()
        self.assertIn("后台早已发表", out)
        self.assertIn("未重发", out)
        self.assertIn("新发表 0 条", out)


class TestScanDialogWait(unittest.TestCase):
    """扫码框（.dialog_wrp）出现时必须「停下来等」，绝不能去点它。

    2026-10-19 实测：发布模式也要管理员扫码。扫码框是第三种弹窗容器，
    轮询只认 `.new_mass_send_dialog` 时会完全错过它，报一个莫名的 timeout。
    """

    def test_扫码文本被识别(self):
        cdp = _FakeEditorCdp()
        cdp.eval = lambda expr, **kw: "微信验证 扫码后，请联系管理员进行验证"
        self.assertIn("扫码", W._scan_dialog_text(cdp))

    def test_没有扫码框返回None(self):
        cdp = _FakeEditorCdp()
        cdp.eval = lambda expr, **kw: None
        self.assertIsNone(W._scan_dialog_text(cdp))

    def test_空串也返回None(self):
        cdp = _FakeEditorCdp()
        cdp.eval = lambda expr, **kw: ""
        self.assertIsNone(W._scan_dialog_text(cdp))

    def test_扫码时只等待不乱点(self):
        """轮询循环见到扫码框：提示 + 等，绝不点弹窗里的任何按钮。"""
        import json as _json
        clicks = []
        phase = {"n": 0}          # _click_in_dialog 点了几次

        def fake_list(cdp, home_tid, url):
            if "appmsgpublish" in url:
                return {"publish_page": _json.dumps({"publish_list": []})}
            return {"app_msg_list": [{"appmsgid": 100000526,
                                      "title": "Meerkats"}]}

        class FakeCdp(object):
            def close_target(self, tid):
                return True

        def fake_click(cdp, t):
            clicks.append(t)
            phase["n"] += 1
            return "CLICKED"

        def fake_send_dlg(cdp):
            # 前两层确认都在 .new_mass_send_dialog 里；点过两次之后它让位给扫码框
            return "发表 群发通知 发表 取消" if phase["n"] < 2 else None

        def fake_scan(cdp):
            return ("微信验证 扫码后，请联系管理员进行验证"
                    if phase["n"] >= 2 else None)

        class _FakeTime(object):
            """假时钟：每次调用 +5s，sleep 空转 ⇒ 轮询循环快速确定性地跑完。"""

            def __init__(self):
                self.t = 1000.0

            def time(self):
                self.t += 5
                return self.t

            def sleep(self, _s):
                pass

        orig = (W._open_draft_editor_for_send, W._click_by_text, W._set_notify,
                W._click_in_dialog, W._scan_dialog_text, W._send_dialog_text,
                W._list_from_home, W.make_wechat_title, W.time)
        W._open_draft_editor_for_send = (
            lambda cdp, aid, tok, timeout=30: ("ready", "t1", ""))
        W._click_by_text = lambda cdp, t: "CLICKED"
        W._set_notify = lambda cdp, on: (True, on)
        W._click_in_dialog = fake_click
        W._scan_dialog_text = fake_scan
        W._send_dialog_text = fake_send_dlg
        W._list_from_home = fake_list
        W.make_wechat_title = lambda c: "T"
        W.time = _FakeTime()
        try:
            r = W.send_one(FakeCdp(), {"id": "meerkats-ai"}, "100000526",
                           "tok", timeout=30)
        finally:
            (W._open_draft_editor_for_send, W._click_by_text, W._set_notify,
             W._click_in_dialog, W._scan_dialog_text, W._send_dialog_text,
             W._list_from_home, W.make_wechat_title, W.time) = orig
        self.assertEqual(r["status"], "timeout")
        self.assertIn("一直在等扫码", r["reason"])
        self.assertEqual(clicks, ["发表", "继续发表"],
                         "两层确认各点一次；扫码框不能被点上")


class TestSendTodoOrder(unittest.TestCase):
    """`send --all` 的待办必须按**篇号**升序（2026-10-21 修）。

    账本 wechat_published.json 是**只追加**的：后补/重存的草稿会被追加到
    尾部。此前 cmd_send 直接迭代账本，编号顺序口径靠「碰巧升序」维持 ——
    任何一次后补都让「续发下一条」悄悄换人。判据固化在 build_send_todo。
    """

    def test_按篇号升序而非账本顺序(self):
        published = {
            "zz-new": {"status": "draft", "appmsgid": "900"},   # 篇号最小却在最后
            "aa-old": {"status": "draft", "appmsgid": "100"},
            "mm-mid": {"status": "draft", "appmsgid": "500"},
        }
        issues = {"aa-old": 10, "mm-mid": 20, "zz-new": 30}
        todo = W.build_send_todo(published, issues=issues)
        self.assertEqual([c for c, _ in todo], ["aa-old", "mm-mid", "zz-new"])

    def test_没篇号的沉到最后且保持账本原序(self):
        published = {
            "a": {"status": "draft", "appmsgid": "1"},
            "b": {"status": "draft", "appmsgid": "2"},
            "c": {"status": "draft", "appmsgid": "3"},
        }
        issues = {"c": 5}
        todo = W.build_send_todo(published, issues=issues)
        self.assertEqual([c for c, _ in todo], ["c", "a", "b"])

    def test_已发表_缺失_无appmsgid都被剔除(self):
        published = {
            "ok": {"status": "draft", "appmsgid": "1"},
            "pub": {"status": "published", "appmsgid": "2"},
            "gone": {"status": "draft", "appmsgid": "3", "missing": True},
            "noaid": {"status": "draft"},
            "part": {"status": "partial", "appmsgid": "5"},      # 半成品要捞回
        }
        todo = W.build_send_todo(published, issues={})
        self.assertEqual([c for c, _ in todo], ["ok", "part"])

    def test_limit截断按篇号后的前N条(self):
        published = {c: {"status": "draft", "appmsgid": c} for c in "cba"}
        issues = {"a": 1, "b": 2, "c": 3}
        todo = W.build_send_todo(published, issues=issues, limit=2)
        self.assertEqual([c for c, _ in todo], ["a", "b"])


class TestFindArticle(unittest.TestCase):
    """find_article 只认精确文件名（2026-10-21 修）。

    修的洞：子串命中即返回，find_article("storyshort") 实测领走
    公众号-storyshort-ai拆解.html（storyshort-ai 是另一个已发表 case）——
    标题正文张冠李戴，账还记到 storyshort 名下。「发错」比「没发」恶劣。
    """

    def setUp(self):
        self._root = W.ROOT
        self.tmp = tempfile.mkdtemp()
        W.ROOT = self.tmp

    def tearDown(self):
        W.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, fname):
        open(os.path.join(self.tmp, fname), "w", encoding="utf-8").close()

    def test_精确名命中(self):
        self._mk("公众号-bustem拆解.html")
        p = W.find_article("bustem")
        self.assertTrue(p and p.endswith("公众号-bustem拆解.html"))

    def test_子串近名不许认领(self):
        # 只有 storyshort-ai 存在时，storyshort 必须拿 None 走 build，
        # 而不是领走别的 case 的文章。
        self._mk("公众号-storyshort-ai拆解.html")
        self.assertIsNone(W.find_article("storyshort"))
        # storyshort-ai 自己仍然找得到
        self.assertTrue(W.find_article("storyshort-ai"))

    def test_name兜底也走精确(self):
        self._mk("公众号-coral拆解.html")
        self.assertTrue(W.find_article("no-such-id", name="coral"))
        self.assertIsNone(W.find_article("no-such-id", name="coralflavor"))

    def test_什么都没有返回None(self):
        self.assertIsNone(W.find_article("ghost"))
