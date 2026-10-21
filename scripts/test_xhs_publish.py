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
        self.assertNotIn("…", t)
        self.assertIn("Test", t)          # 品牌名必须保住
        self.assertIn("一句话定位", t)

    def test_title_money_redline(self):
        """红线（2026-10-14 定）：标题里**不许**出现金额 / MRR / ARR / 月收。

        这条替换了 10-05 写的三个 `assertIn("$1.6K" / "$85K" / "$128K")`。
        那三个是旧口径（标题带金额做钩子），10-14 定了「标题禁金额」之后
        它们必然失败 —— 不是回归，是测试没跟着改（2026-10-18 实测确认：
        `make_xhs_title` 出口的 `strip_title_money` 会删掉所有金额）。
        """
        cases = [
            _mkc(),
            _mkc(id="_t_nonexistent_chengjiao", name="Promptmonitor.io",
                 one_liner="监测品牌在各类 AI 模型回答中的曝光情况",
                 metrics={"headline": "以 $85,000 成交（TrustMRR 平台公开案例）"}),
            _mkc(id="_t_nonexistent_mrr", one_liner="（待补充：产品定位未明）",
                 metrics={"headline": "$128,000 MRR（最高当前 MRR）"}),
        ]
        for c in cases:
            t = x.make_xhs_title(c)
            self.assertTrue(len(t) <= x.TITLE_MAX, "超字数：%r" % t)
            self.assertNotRegex(t, r"[¥€£$]\s*\d", "标题不该带金额：%r" % t)
            for w in ("MRR", "ARR", "月收"):
                self.assertNotIn(w, t, "标题不该带 %s：%r" % (w, t))

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
    """「发最近的 n 条没发过的」：待发队列与模糊找案例。

    ⚠ 打桩一律走 `_patch()` 登记还原（2026-10-21 修）：`x.load_cases` 是
    **模块级全局**，此前几个用例直接赋值不还原 —— 全量跑时污染同进程后续
    模块（实测让 `test_multiplatform` 的 replace 队列断言假失败，单跑全绿）。
    """

    def _patch(self, **kw):
        for name, fn in kw.items():
            old = getattr(x, name)
            setattr(x, name, fn)
            self.addCleanup(setattr, x, name, old)

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
            self.addCleanup(setattr, x, "OUT", old)
            self._with_cards(tmp, ["a", "b", "c"])
            # 队列口径：最新录入的排最前（见 multiplatform/queue.py）
            cases = [{"id": "a", "updated_at": "2026-10-15"},
                     {"id": "b", "updated_at": "2026-10-10"},
                     {"id": "c", "updated_at": "2026-10-01"}]
            self._patch(load_cases=lambda: cases,
                        _published_ids=lambda: ["b"],
                        _drafted_ids=lambda: [])
            # 默认只要已建卡片的，且排除已发布/已进草稿的 ⇒ 剩 a、c，新的在前
            self.assertEqual([c["id"] for c in x.pending_cases()], ["a", "c"])

    def test_pending_has_no_hardcoded_case_id(self):
        """待发判定只能看 published/drafted 两个台账，不能硬编码任何案例 id。

        曾经把 voklit 写死进skip（| {"voklit"}）当作临时跳过，
        结果它永久无法再被 queue / publish --all 选中，只能手动 --case。
        voklit 合规重发后已移除，这里钉死。
        """
        with tempfile.TemporaryDirectory() as tmp:
            old = x.OUT
            x.OUT = tmp
            self.addCleanup(setattr, x, "OUT", old)
            self._with_cards(tmp, ["a", "voklit"])
            cases = [{"id": "a"}, {"id": "voklit"}]
            self._patch(load_cases=lambda: cases,
                        _published_ids=lambda: [],
                        _drafted_ids=lambda: ["a"])
            # voklit 没进过草稿台账 → 必须出现在待发队列里
            self.assertEqual([c["id"] for c in x.pending_cases()], ["voklit"])

    def test_pending_fresh_ignores_missing_cards(self):
        cases = [{"id": "a", "updated_at": "2026-09-01"},
                 {"id": "b", "updated_at": "2026-10-01"}]
        self._patch(load_cases=lambda: cases,
                    _published_ids=lambda: [],
                    _drafted_ids=lambda: [])
        # need_cards=False：没 note.json 也算，交给出发前现造；b 更新，排最前
        self.assertEqual([c["id"] for c in x.pending_cases(need_cards=False)],
                         ["b", "a"])

    def test_fuzzy_by_exact_id(self):
        cases = [{"id": "bustem", "name": "Bustem"}]
        self._patch(load_cases=lambda: cases)
        self.assertEqual(x.find_case_fuzzy("bustem")["id"], "bustem")

    def test_fuzzy_by_name_fragment(self):
        cases = [{"id": "kibu", "name": "Kibu"},
                 {"id": "bustem", "name": "Bustem"}]
        self._patch(load_cases=lambda: cases)
        self.assertEqual(x.find_case_fuzzy("buste")["id"], "bustem")

    def test_fuzzy_case_insensitive(self):
        cases = [{"id": "kibu", "name": "Kibu"}]
        self._patch(load_cases=lambda: cases)
        self.assertEqual(x.find_case_fuzzy("KIBU")["id"], "kibu")

    def test_fuzzy_ambiguous_raises(self):
        cases = [{"id": "aa-x", "name": "A x"}, {"id": "aa-y", "name": "A y"}]
        self._patch(load_cases=lambda: cases)
        with self.assertRaises(SystemExit):
            x.find_case_fuzzy("aa")

    def test_fuzzy_no_match_raises(self):
        self._patch(load_cases=lambda: [{"id": "kibu", "name": "Kibu"}])
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
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(list(json.load(fh)), ["b"])

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

        def _real_title(cid):
            note = os.path.join(root, "out", "xhs", cid, "note.json")
            with open(note, encoding="utf-8") as f:
                return json.load(f)["title"]

        self.assertEqual(x._card_to_cid(_real_title("mort")), "mort")
        self.assertEqual(x._card_to_cid(_real_title("quran-unlock")),
                         "quran-unlock")

    def test_标题反查查不到就返回None(self):
        """查不到宁可不改状态文件 —— 错删记录会让已发过的案例被重复发布。"""
        self.assertIsNone(x._card_to_cid("完全不存在的草稿标题 zzz"))
        self.assertIsNone(x._card_to_cid(""))


class TestCompliance(unittest.TestCase):
    """合规预检（2026-10-01，因voklit 被判「文本+图片违规」而加）。

    背景：那篇笔记被小红书判四条全中（引流 / 提供通信资源 / 推广非正规工具 /
    账号异常操作），处置结果「已不可被他人查看」。根因是**发布前没有任何
    小红书合规检查** —— 头条有 risk_words()，小红书以前没有。
    """

    def _case(self, cid):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cases = json.load(open(os.path.join(root, "data", "cases.json"),
                              encoding="utf-8"))
        return next(c for c in cases if c.get("id") == cid)

    def test_voklit最终产物无违规词(self):
        """覆盖表必须把通信资源类目的话术全换掉 —— 正文**和卡片**都要换。

        ⚠ 卡片是最容易漏的：只改正文不覆盖 cards，等于把违规措辞重新印一遍
        发出去，那才是「图片违规」的来源。
        """
        c = self._case("voklit")
        note = x.build_note(c)
        note["_cards_text"] = x.cards_text(c)
        hits = x.xhs_compliance(note)
        self.assertEqual(
            hits, [],
            "voklit 覆盖后仍命中 %r —— 小红书对通信资源是禁售类目，"
            "改措辞救不回来，必须换平台或整体改写意图" % (hits,))

    def test_覆盖表不改动四平台共用源(self):
        """方案 A 的核心约束：只救小红书，不动 cases.json。

        动 cases.json 会牵连公众号 —— 要重建全部 37 条 HTML 并重跑 refresh。
        这里从磁盘读原始 JSON 断言违规原文仍在，而不是只对比函数输出。
        """
        import json
        import os
        path = os.path.join(x.ROOT, "data", "cases.json")
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertIn("虚拟电话号码", raw,
                      "cases.json 里的违规原文被改了，方案 A 越界了")
        self.assertIn("VoIP", raw)

        # 落盘产物用的是覆盖后的正文，与源数据里的原文必须不同
        c = self._case("voklit")
        note = x.build_note(c)
        self.assertNotIn("虚拟电话号码", note["body"])
        self.assertNotIn("VoIP", note["body"])

        # 覆盖表本身不能写进 cases.json
        self.assertNotIn("xhs_note_overrides", raw)

    def test_原始cases数据会被预检拦下(self):
        """反向验证预检有效：不带覆盖表时，voklit 原文必须命中通信资源类目。

        没有这条，无法证明「0 命中」是覆盖生效而不是预检失灵。
        """
        c = self._case("voklit")
        blob = "\n".join([
            c.get("what_it_does") or "",
            c.get("how_it_makes_money") or "",
        ] + list(c.get("why_it_works") or []) + list(c.get("playbook") or []))
        hits = [(cat, w) for cat, w in
                ((cat, w) for cat, words in x.XHS_BANNED.items()
                 for w in words if w in blob)]
        cats = {cat for cat, _w in hits}
        self.assertIn("通信资源", cats,
                      "预检对「虚拟号码/OTP/短信」这类词必须能拦下，"
                      "否则 voklit 覆盖后的 0 命中说明不了问题")

    def test_引流词也被拦(self):
        note = {"title": "月收$1.6K", "body": "加微信详聊", "_cards_text": ""}
        hits = x.xhs_compliance(note)
        self.assertTrue(any(cat == "引流导流" for cat, _w, _f in hits))

    def test_正常内容不误报(self):
        """别把正常案例也拦了 —— 误报会让预检被忽略。"""
        c = self._case("quran-unlock")
        note = x.build_note(c)
        note["_cards_text"] = x.cards_text(c)
        self.assertEqual(x.xhs_compliance(note), [])


class TestMarkLedger(unittest.TestCase):
    """`_mark()`/`_unmark()` 台账写入回归（2026-10-15 毁历史事故）。

    ⚠ 旧实现只取 id 列表、丢`at`，追加一条就把整本台账的发布时间
      洗成当前时间 ⇒ `data/xhs_drafts.json` 的 `at` 完全不可信。
      这几条测试钉死：旧条目时间戳不许动、幂等、行尾不变。
    ⚠ 绝不能用 `data/*.json` 当靶子（曾覆盖 41 条台账）—— 全部走 tmp。
    """

    def _tmp(self, name, text):
        p = os.path.join(tempfile.mkdtemp(prefix="xhs_mark_"), name)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def test_existing_timestamps_preserved(self):
        p = self._tmp("drafts.json",
                      '[{"id": "a", "at": "2026-09-28 20:40"},\n'
                      ' {"id": "b", "at": "2026-09-28 20:40"}]')
        x._mark(p, "podawaa")
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual([y["id"] for y in d], ["a", "b", "podawaa"])
        self.assertEqual(d[0]["at"], "2026-09-28 20:40",
                         "旧条目 at 被重写 = 台账历史被毁")
        self.assertEqual(d[1]["at"], "2026-09-28 20:40")
        self.assertTrue(d[2]["at"].startswith("20"), "新条目要打时间戳")

    def test_idempotent_no_rewrite(self):
        raw = '[{"id": "a", "at": "2026-09-28 20:40"}]'
        p = self._tmp("drafts.json", raw)
        before = open(p, "rb").read()
        x._mark(p, "a")                # 已存在 → 应原样返回
        self.assertEqual(open(p, "rb").read(), before,
                         "幂等：已存在的 id 不该重写文件")

    def test_crlf_preserved(self):
        """真实台账 `data/xhs_drafts.json` 是 CRLF（实测 233 个 CRLF）。"""
        p = self._tmp("drafts.json",
                      '[\r\n  {"id": "a", "at": "2026-09-28 20:40"}\r\n]')
        x._mark(p, "b")
        raw = open(p, "rb").read()
        self.assertIn(b"\r\n", raw, "CRLF 文件必须保持 CRLF")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""),
                         "不允许混入裸 LF")

    def test_lf_preserved(self):
        p = self._tmp("pub.json",
                      '[\n  {"id": "a", "at": "2026-09-28 20:40"}\n]')
        x._mark(p, "b")
        raw = open(p, "rb").read()
        self.assertNotIn(b"\r\n", raw, "LF 文件必须保持 LF")

    def test_legacy_plain_id_list(self):
        """老格式（纯 id 列表）也要能追加，且补上时间戳。"""
        p = self._tmp("pub.json", '["a", "b"]')
        x._mark(p, "c")
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual([y["id"] for y in d], ["a", "b", "c"])
        for y in d:
            self.assertTrue(y.get("at"), "纯 id 老条目要补 at")

    def test_legacy_dict_format(self):
        """老格式（{"id": {...}}）也要能追加，且**保持 dict 形态**。

        ⚠ 不许借修 bug 之手把老台账归一成 list —— 那等于擅自改用户
          数据文件形态，老测试 `test_unmark支持dict形态` 就是这么钉的。
        """
        p = self._tmp("pub.json", '{"a": {"at": "2026-09-28 20:40"}}')
        x._mark(p, "c")
        d = json.load(open(p, encoding="utf-8"))
        self.assertIsInstance(d, dict, "老 dict 台账不该被归一成 list")
        self.assertEqual(sorted(d), ["a", "c"])
        self.assertEqual(d["a"], {"at": "2026-09-28 20:40"},
                         "dict 老格式的条目也要原样保住")

    def test_unmark_keeps_others_and_newlines(self):
        p = self._tmp("drafts.json",
                      '[\r\n  {"id": "a", "at": "2026-09-28 20:40"},\r\n'
                      '  {"id": "b", "at": "2026-10-01 09:00"}\r\n]')
        self.assertTrue(x._unmark(p, "a"), "确实存在的 id 应返回 True")
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual([y["id"] for y in d], ["b"])
        self.assertEqual(d[0]["at"], "2026-10-01 09:00",
                         "_unmark 也不该动别人的时间戳")
        self.assertNotIn(b"\n", open(p, "rb").read().replace(b"\r\n", b""),
                         "_unmark 必须保持 CRLF")
        self.assertFalse(x._unmark(p, "不存在"), "不存在的 id 返回 False")

    def test_unmark_missing_file(self):
        p = os.path.join(tempfile.mkdtemp(prefix="xhs_mark_"), "none.json")
        self.assertFalse(x._unmark(p, "a"), "文件不存在时不该建文件/报错")


class TestGenericFallbackTitle(unittest.TestCase):
    """「产品名：海外小生意」兜底回归（2026-10-15，12/60 条中招）。

    ⚠ 根因：`_raw_make_xhs_title` 的「产品名：描述」那条候选塞的是**裸
      desc**，没过 `_title_desc()` 压缩（金额前缀那条过了、这条没过，纯
      bug）⇒ desc 超 20 字预算就整条被拒，直落 generic「海外小生意」。
      实测 12 条：`quotedeck` 的 desc 只 11 字、拼起来 21 字、超预算
      1 字也被拒。

    这几条钉死两件事：
      ① desc 略超预算时，仍必须走压缩而不是退到「海外小生意」；
      ② 覆盖表里那 12 条的成品标题不许含通用词。
    """

    GEN = ("海外小生意", "一人公司生意", "海外小项目")

    def _desc_over_budget_case(self):
        """desc 只比预算多几个字 —— 旧实现在这里退到「海外小生意」。

        ⚠ 名字要用**真实长度**（`QuoteDeck` 9 字），`_mkc` 默认名只有
          4 字，拼起来不超预算，测不到 bug（我第一版就踩了这个）。
        """
        c = _mkc(name="QuoteDeck", one_liner="为保险代理提供专业报价")
        total = len(c["name"]) + 1 + len(c["one_liner"])
        self.assertGreater(total, x.TITLE_MAX,
                           "样例必须真的超预算（现 %d），否则测不到" % total)
        self.assertLessEqual(total - x.TITLE_MAX, 3,
                             "样例应是「略超」，超太多测的是另一条路径")
        return c

    def test_略超预算不落通用兜底(self):
        t = x.make_xhs_title(self._desc_over_budget_case())
        for g in self.GEN:
            self.assertNotIn(g, t, "desc 略超就退到「%s」= 压缩没生效" % g)
        self.assertTrue(4 <= len(t) <= x.TITLE_MAX, "长度仍须在预算内：%r" % t)
        self.assertNotIn("…", t, "不许出现省略号")

    def test_压缩后仍保留描述主干(self):
        """压缩应取主干，不是砍成空壳。"""
        c = _mkc(one_liner="为 Mac 用户提供 4K 电影感动态壁纸的订阅服务")
        t = x.make_xhs_title(c)
        self.assertTrue(len(t) <= x.TITLE_MAX)
        self.assertNotIn("…", t)
        # 至少留下描述里的一部分实词
        self.assertTrue(any(w in t for w in ("壁纸", "Mac", "订阅", "4K")),
                        "压缩把描述全砍没了：%r" % t)

    def test_真实十二条覆盖无通用词(self):
        """data/xhs_title_overrides.json 里那批不许再出现通用词。"""
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "data", "xhs_title_overrides.json")
        if not os.path.isfile(p):
            self.skipTest("覆盖表不存在")
        ov = json.load(open(p, encoding="utf-8"))
        cases = {c["id"]: c for c in x.load_cases()}
        checked = 0
        for cid, v in ov.items():
            if cid not in cases:
                continue
            t = x.make_xhs_title(cases[cid])
            for g in self.GEN:
                self.assertNotIn(g, t, "%s 退到通用词「%s」：%r" % (cid, g, t))
            self.assertLessEqual(len(t), x.TITLE_MAX,
                                 "%s 超预算：%r" % (cid, t))
            checked += 1
        self.assertGreater(checked, 20, "只检查到 %d 条，覆盖表没读对" % checked)


class TestLedgerExtras(unittest.TestCase):
    """台账扩展字段必须活下来（2026-10-18 对账回填时踩到）。

    对账脚本 `xhs_sync_ledger.py` 会往 `xhs_published.json` 写
    `note_id` / `title`，而 `_load_ledger()` 原来只挑 `id`/`at`，
    于是**下一次 `mark_published` 就把对账信息全抹了**。
    """

    def _tmp(self, text):
        p = os.path.join(tempfile.mkdtemp(prefix="xhs_extra_"), "pub.json")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def test_extra_fields_survive_mark(self):
        p = self._tmp('[{"id": "a", "at": "2026-09-29 12:09",'
                      ' "note_id": "6abb39e8", "title": "月收$128K：x",'
                      ' "note": "接口对账回填"}]')
        x._mark(p, "b")
        d = json.load(open(p, encoding="utf-8"))
        a = [e for e in d if e["id"] == "a"][0]
        self.assertEqual(a["note_id"], "6abb39e8", "扩展字段被归一化抹掉了")
        self.assertEqual(a["title"], "月收$128K：x")
        self.assertEqual(a["note"], "接口对账回填")
        self.assertEqual(a["at"], "2026-09-29 12:09", "老条目时间戳不许动")


class TestLedgerMoneyTokens(unittest.TestCase):
    """`$128,000 MRR` 的金额解析（2026-10-18 对账时误判成 1280 亿）。

    坑：单位字母后紧跟 `\\s*` 时，"$128,000 MRR" 里的 "M" 会被当成
    「百万」后缀 ⇒ 128000 × 1e6，金额口径彻底错位，标题→case 全部匹配不上。
    """

    def test_mrr_word_not_read_as_million(self):
        import xhs_sync_ledger as S
        self.assertEqual(S.money_tokens("$128,000 MRR"), {128000.0})
        self.assertEqual(S.money_tokens("约为 $20,000 MRR（自报）"), {20000.0})

    def test_k_and_m_suffix(self):
        import xhs_sync_ledger as S
        self.assertEqual(S.money_tokens("月收$1.6K：x"), {1600.0})
        self.assertEqual(S.money_tokens("$202K MRR"), {202000.0})

    def test_decimal_and_commas(self):
        import xhs_sync_ledger as S
        self.assertEqual(S.money_tokens("MRR $42.72"), {42.72})
        self.assertEqual(S.money_tokens("$1,600"), {1600.0})


class TestWebApiBodyCompact(unittest.TestCase):
    """`xhs_webapi._compact()` 必须和 JS `JSON.stringify` 逐字节一致。

    坑（2026-10-18）：POST 的签名是对**对象再序列化**的结果算的，
    发出去的 body 只要多一个空格（`json.dumps` 默认 `", "`）就对不上，
    服务端一律 406 —— 而且 body 里的报错只有 `{"code":-1}`，极难定位。
    """

    def test_no_spaces(self):
        import xhs_webapi as W
        self.assertEqual(W._compact({"a": 1, "b": 2}), '{"a":1,"b":2}')

    def test_non_ascii_not_escaped(self):
        import xhs_webapi as W
        self.assertEqual(W._compact({"t": "中文标题"}),
                         '{"t":"中文标题"}')

    def test_nested_compact(self):
        import xhs_webapi as W
        self.assertEqual(W._compact({"common": {"note_id": ""}}),
                         '{"common":{"note_id":""}}')

    def test_str_passthrough_and_none(self):
        import xhs_webapi as W
        self.assertEqual(W._compact('{"a":1}'), '{"a":1}')
        self.assertEqual(W._compact(None), "")


if __name__ == "__main__":
    unittest.main()
