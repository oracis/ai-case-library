# -*- coding: utf-8 -*-
"""xhs_publish 纯函数测试（文案改写 / 标题预算 / 金额压缩 / HTML 转义）。"""
import sys
import os
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as x


def _mkc(**kw):
    c = {"id": "t1", "name": "Test", "name_en": "Test",
         "one_liner": "一句话定位", "what_it_does": "做什么",
         "how_it_makes_money": "怎么赚钱",
         "why_it_works": ["因为A", "因为B", "因为C", "因为D"],
         "playbook": ["学点1", "学点2"], "category": "AI 工具",
         "metrics": {"headline": "收入 $1.6K / 售价 $60K / 倍数 3.1x"},
         "replicability": {"tech": 4, "distribution": 3, "capital": 3,
                           "timing": 2},
         "solo_fit": {"score": 55.2, "rank": 29},
         "china_fit": {"score": 41.4, "rank": 35, "blocker": "牌照"}}
    c.update(kw)
    return c


class TestAmount(unittest.TestCase):
    def test_compact(self):
        self.assertEqual(x.compact_amount("$128,000"), "$128K")
        self.assertEqual(x.compact_amount("$1,032,000"), "$1.03M")
        self.assertEqual(x.compact_amount("$1.6K"), "$1.6K")
        self.assertEqual(x.compact_amount("$842"), "$842")
        self.assertEqual(x.compact_amount("$60,000"), "$60K")
        self.assertEqual(x.compact_amount("$85,000"), "$85K")

    def test_first_amount_skips_mrr_word(self):
        # "MRR" 的 M 不能被 [KM]? 吃进去
        h = "$128,000 MRR（该数据集内最高当前 MRR）"
        self.assertEqual(x.first_amount(h), "$128K")

    def test_first_amount_missing(self):
        self.assertEqual(x.first_amount("无金额口径"), "")


class TestTitle(unittest.TestCase):
    def test_basic_within_budget(self):
        t = x.make_xhs_title(_mkc())
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)
        self.assertIn("$1.6K", t)
        self.assertNotIn("…", t)

    def test_cheng_jiao_caliber(self):
        c = _mkc(id="pm", name="Promptmonitor.io",
                 one_liner="监测品牌在各类 AI 模型回答中的曝光情况",
                 metrics={"headline": "以 $85,000 成交（TrustMRR 平台公开案例）"})
        t = x.make_xhs_title(c)
        self.assertTrue(len(t) <= x.TITLE_MAX)
        self.assertIn("$85K", t)
        self.assertIn("成交", t)

    def test_mrr_caliber(self):
        c = _mkc(id="prosp", one_liner="（待补充：产品定位未明）",
                 metrics={"headline": "$128,000 MRR（最高当前 MRR）"})
        t = x.make_xhs_title(c)
        self.assertTrue(len(t) <= x.TITLE_MAX)
        self.assertIn("$128K", t)

    def test_long_name_falls_back(self):
        c = _mkc(id="x", name="VeryLongProductName exceeding budget",
                 one_liner="一个特别特别长的定位描述" * 3)
        t = x.make_xhs_title(c)
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)
        self.assertNotIn("…", t)

    def test_no_amount(self):
        c = _mkc(id="y", metrics={"headline": ""})
        t = x.make_xhs_title(c)
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX)

    def test_dedup_falls_to_next_candidate(self):
        c = _mkc(id="p", one_liner="（待补充：产品定位未明）")
        t1 = x.make_xhs_title(c)
        t2 = x.make_xhs_title(c, used={t1})
        self.assertNotEqual(t1, t2)
        self.assertTrue(len(t2) <= x.TITLE_MAX)


class TestBody(unittest.TestCase):
    def test_budget(self):
        c = _mkc(
            what_it_does="做" * 200,
            how_it_makes_money="赚" * 300,
            why_it_works=["因" * 60] * 4,
            playbook=["学" * 80] * 4,
            metrics={"headline": "收入 $2.4K / 售价 $28K" * 4})
        note = x.build_note(c)
        self.assertLessEqual(len(note["body"]), x.BODY_MAX)
        self.assertLessEqual(len(note["title"]), x.TITLE_MAX)

    def test_tags_format(self):
        tags = x.build_tags(_mkc())
        self.assertTrue(all(t.startswith("#") for t in tags))
        self.assertIn("#AI工具", tags)
        self.assertEqual(len(tags), len(set(tags)))

    def test_caliber_word_kept(self):
        c = _mkc(id="z", metrics={"headline": "MRR $2,869；累计收入 $62,352"})
        body = x.build_body(c)
        self.assertIn("MRR", body)   # 口径词不许被改写丢掉


class TestDesc(unittest.TestCase):
    def test_placeholder_falls_back(self):
        self.assertEqual(x.short_desc(_mkc(one_liner="（待补充：产品定位未明）")),
                         "AI 工具小生意")

    def test_paren_and_suffix_removed(self):
        self.assertEqual(
            x.short_desc(_mkc(one_liner="跨境云通信（VoIP + SMS）服务")),
            "跨境云通信")


class TestCoverHeadline(unittest.TestCase):
    def test_whole_segments_only(self):
        h = "收入 $6.2K / 售价 $15K / 倍数 0.2x"
        out = x.cover_headline(h)
        self.assertEqual(out, "收入 $6.2K / 售价 $15K")
        self.assertFalse(out.rstrip().endswith(("倍数", "/")))

    def test_paren_stripped(self):
        h = "MRR $2,869；累计收入 $62,352（RevenueCat API 验证）"
        out = x.cover_headline(h)
        self.assertNotIn("（", out)

    def test_budget(self):
        self.assertTrue(len(x.cover_headline("收入 $1.6K / 售价 $60K / 倍数 3.1x")) <= 24)


class TestHtml(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(x.esc('<a & "b"'), "&lt;a &amp; &quot;b&quot;")

    def test_card_html_wellformed(self):
        import xml.etree.ElementTree as ET
        c = _mkc(id="t2", name="T<2>")
        for p in range(1, 6):
            html = x.card_html(c, p, 5)
            self.assertIn('charset="utf-8"', html)
            # body 部分按 XML 校验（style 内无 < >，body 文本已转义）
            body = html.split("</style></head><body>")[1]
            self.assertEqual(
                ET.fromstring(body.replace("<body>", "").replace(
                    "</body>", "").replace("</html>", "").strip()).tag, "div")
            self.assertNotIn("archaic", html)   # 防再混入乱码


class TestDraft(unittest.TestCase):
    """草稿（离开编辑页自动暂存）相关的纯逻辑。"""

    def test_signal_js_returns_object(self):
        js = x._JS_DRAFT_SIGNAL
        self.assertIn("草稿箱中有未发布的作品", js)
        self.assertTrue(js.startswith("(function(){"))
        self.assertIn("return {hint:", js)

    def test_draft_signal_dict(self):
        class S:
            def eval(self, expr, refresh_context=False):
                return {"hint": True, "n": -1}
        self.assertEqual(x._draft_signal(S()), {"hint": True, "n": -1})

    def test_draft_signal_json_str(self):
        class S:
            def eval(self, expr, refresh_context=False):
                return '{"hint": false, "n": 3}'
        self.assertEqual(x._draft_signal(S()), {"hint": False, "n": 3})

    def test_draft_signal_none(self):
        class S:
            def eval(self, expr, refresh_context=False):
                raise RuntimeError("ctx gone")
        self.assertIsNone(x._draft_signal(S()))


class TestPickNext(unittest.TestCase):
    """「发最近的 n 条没发过的」：待发队列与模糊找案例。"""

    def _with_cards(self, tmp, ids):
        """在 tmp 下造 note.json，让 pending_cases 认它们是「已建卡片」。"""
        for i in ids:
            d = os.path.join(tmp, i)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "note.json"), "w", encoding="utf-8") as f:
                json.dump({"id": i}, f)

    def test_pending_excludes_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = x.OUT
            x.OUT = tmp
            try:
                self._with_cards(tmp, ["a", "b", "c"])
                cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
                x.load_cases = lambda: cases
                x._published_ids = lambda: ["b"]
                x._drafted_ids = lambda: []
                # 默认只要已建卡片的，且排除已发布/已进草稿的
                self.assertEqual([c["id"] for c in x.pending_cases()], ["c", "a"])
            finally:
                x.OUT = old

    def test_pending_skips_drafted_and_voklit(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = x.OUT
            x.OUT = tmp
            try:
                self._with_cards(tmp, ["a"])
                cases = [{"id": "a"}, {"id": "voklit"}]
                x.load_cases = lambda: cases
                x._published_ids = lambda: []
                x._drafted_ids = lambda: ["a"]
                self.assertEqual(x.pending_cases(), [])
            finally:
                x.OUT = old

    def test_pending_fresh_ignores_missing_cards(self):
        cases = [{"id": "a"}, {"id": "b"}]
        x.load_cases = lambda: cases
        x._published_ids = lambda: []
        x._drafted_ids = lambda: []
        # need_cards=False：没 note.json 也算，交给出发前现造
        self.assertEqual([c["id"] for c in x.pending_cases(need_cards=False)],
                         ["b", "a"])

    def test_fuzzy_by_exact_id(self):
        cases = [{"id": "bustem", "name": "Bustem"}]
        x.load_cases = lambda: cases
        self.assertEqual(x.find_case_fuzzy("bustem")["id"], "bustem")

    def test_fuzzy_by_name_fragment(self):
        cases = [{"id": "kibu", "name": "Kibu"},
                 {"id": "bustem", "name": "Bustem"}]
        x.load_cases = lambda: cases
        self.assertEqual(x.find_case_fuzzy("buste")["id"], "bustem")

    def test_fuzzy_case_insensitive(self):
        cases = [{"id": "kibu", "name": "Kibu"}]
        x.load_cases = lambda: cases
        self.assertEqual(x.find_case_fuzzy("KIBU")["id"], "kibu")

    def test_fuzzy_ambiguous_raises(self):
        cases = [{"id": "aa-x", "name": "A x"}, {"id": "aa-y", "name": "A y"}]
        x.load_cases = lambda: cases
        with self.assertRaises(SystemExit):
            x.find_case_fuzzy("aa")

    def test_fuzzy_no_match_raises(self):
        x.load_cases = lambda: [{"id": "kibu", "name": "Kibu"}]
        with self.assertRaises(SystemExit):
            x.find_case_fuzzy("不存在的东西")


class TestDraftMatching(unittest.TestCase):
    """草稿箱 ←→ 案例的对账（标题被改写过，靠 note.json 的 title 认亲）。"""

    def test_norm_title_strips_punct(self):
        a = x.norm_title("月收$398K：海外小生意")
        self.assertEqual(a, x.norm_title("月收 $398K : 海外小生意"))
        self.assertEqual(a, x.norm_title("月收$398K：海外小生意 #话题#"))

    def test_norm_title_lowercases(self):
        self.assertEqual(x.norm_title("AI 一键生成演示文稿"),
                         x.norm_title("ai一键生成演示文稿"))

    def test_match_exact(self):
        drafts = [{"title": "月收$6.2K：网页小组件", "saved": "21:54:44"}]
        cases = [{"id": "divine-widgets", "name": "网页小组件"}]
        with tempfile.TemporaryDirectory() as tmp:
            old = x.OUT
            x.OUT = tmp
            try:
                d = os.path.join(tmp, "divine-widgets")
                os.makedirs(d)
                json.dump({"id": "divine-widgets",
                           "title": "月收$6.2K：网页小组件"},
                          open(os.path.join(d, "note.json"), "w",
                               encoding="utf-8"))
                m, o = x.match_drafts(drafts, cases)
            finally:
                x.OUT = old
        self.assertEqual([d2["id"] for d2 in m], ["divine-widgets"])
        self.assertEqual(o, [])

    def test_match_orphan(self):
        drafts = [{"title": "一条认不出来的稿子", "saved": "21:54:44"}]
        matched, orphans = x.match_drafts(drafts, [])
        self.assertEqual(matched, [])
        self.assertEqual(len(orphans), 1)

    def test_match_fuzzy_contains(self):
        drafts = [{"title": "月收$1.03M：海外小生意", "saved": "21:39:20"}]
        cases = [{"id": "marc-lou-portfolio", "name": "Marc Lou 的产品矩阵"}]
        with tempfile.TemporaryDirectory() as tmp:
            old = x.OUT
            x.OUT = tmp
            try:
                d = os.path.join(tmp, "marc-lou-portfolio")
                os.makedirs(d)
                json.dump({"id": "marc-lou-portfolio",
                           "title": "月收$1.03M 海外小生意"},
                          open(os.path.join(d, "note.json"), "w",
                               encoding="utf-8"))
                m, _ = x.match_drafts(drafts, cases)
            finally:
                x.OUT = old
        self.assertEqual([d2["id"] for d2 in m], ["marc-lou-portfolio"])

    def test_norm_title_saved_time(self):
        # 卡片里「保存于2026-09-26 21:54:44」要能抠出时间
        import re
        m = re.search(r"保存于\s*([0-9:\-\s]+)", "保存于2026-09-26 21:54:44")
        self.assertTrue(m)
        self.assertEqual(m.group(1).strip(), "2026-09-26 21:54:44")


class TestNoExternalBrand(unittest.TestCase):
    """小红书账号昵称仍是默认 id：卡片/正文不得出现公众号名或其他平台名。

    2026-09-28 判罚复盘：水印印了「万物解释者」（公众号名）＝展示其他平台
    信息，与「完整拆解 → 公众号」同款站外导流风险。
    """

    def _card(self, page, total=5):
        return x.card_html(_mkc(), page, total)

    def test_card_has_no_wechat_name(self):
        for p in range(1, 6):
            html = self._card(p)
            self.assertNotIn("万物解释者", html, "第 %d 张卡片印了公众号名" % p)
            self.assertNotIn("公众号", html, "第 %d 张卡片有公众号字样" % p)

    def test_card_keeps_platform_free_slogan(self):
        html = self._card(1)
        self.assertIn("拆解海外", html)   # 有水印，但不是任何平台名

    def test_last_page_footer_has_no_platform(self):
        self.assertNotIn("公众号", self._card(5, 5))

    def test_preview_title_clean(self):
        import re as _re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "xhs_publish.py"), encoding="utf-8").read()
        m = _re.search(r"<title>([^<]*)</title>", src)
        self.assertTrue(m)
        self.assertNotIn("万物解释者", m.group(1))


class TestPurgeStateRollback(unittest.TestCase):
    """purge 删掉远端草稿后，本地「已发过」名单必须同步回退（2026-09-30 补）。

    `mark_drafted` 只增不减，而 `_drafted_ids` 正是 publish 的幂等判据 ——
    删了草稿却留着记录，这条就永远不会再发出去。实测踩过：purge 删掉 MORT
    的小红书草稿后，`publish_multi run mort --platforms xhs` 直接回
    「没有待发的（所选平台都发过了）」。
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8")
        self.path = self.tmp.name
        self.tmp.close()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "at": "t"}, {"id": "b", "at": "t"}], f)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_unmark去掉指定id(self):
        self.assertTrue(x._unmark(self.path, "a"))
        d = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual([i["id"] for i in d], ["b"])

    def test_unmark幂等(self):
        self.assertTrue(x._unmark(self.path, "a"))
        self.assertFalse(x._unmark(self.path, "a"), "第二次应返回 False")

    def test_unmark不误删别人(self):
        x._unmark(self.path, "a")
        d = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual([i["id"] for i in d], ["b"], "b 必须留着")

    def test_unmark支持dict形态(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"a": {"at": "t"}, "b": {"at": "t"}}, f)
        self.assertTrue(x._unmark(self.path, "a"))
        self.assertEqual(list(json.load(open(self.path, encoding="utf-8"))), ["b"])

    def test_purge会调用_unmark(self):
        """漏了这个调用，上面全部白测。"""
        import inspect
        src = inspect.getsource(x.cmd_purge)
        self.assertIn("_unmark", src)
        self.assertIn("_card_to_cid", src)

    def test_标题反查认得真实草稿标题(self):
        """草稿标题是传播力钩子，不是产品名 —— 反查靠 out/xhs/*/note.json。

        ⚠ **断言必须从 note.json 现读标题，不能写死字面量**：
        quran-unlock 的标题已从「月收$676：移动 app」改成
        「月收$659：信仰类习惯打卡App」（$676 被 TrustMRR 复核推翻）。
        写死的旧标题会让这条测试在改文案后必然失败，且失败信息
        完全指不到真正原因（跟反查逻辑无关）。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        note = os.path.join(root, "out", "xhs", "quran-unlock", "note.json")
        with open(note, encoding="utf-8") as f:
            real_title = json.load(f)["title"]
        self.assertEqual(x._card_to_cid("月收$84：AI"), "mort")
        self.assertEqual(x._card_to_cid(real_title), "quran-unlock")

    def test_标题反查查不到就返回None(self):
        """查不到宁可不改状态文件 —— 错删记录会让已发过的案例被重复发布。"""
        self.assertIsNone(x._card_to_cid("完全不存在的草稿标题 zzz"))
        self.assertIsNone(x._card_to_cid(""))


if __name__ == "__main__":
    unittest.main()
