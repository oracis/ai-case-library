#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归锁：LLM 标题改写的硬校验、生成器接入、零改动写盘（2026-11-04）。

背景：用户问「彻底去模板化为什么一定要人工？我们已经接了 AI」。
结论是**改写动作交给模型，事实裁定交给代码**。本文件锁的就是后者：
  · 模型试跑时把 `$835` 写成「八三一美元」、`$4,666` 约成「四千多美元」
    —— 中文数字绕过了「阿拉伯数字必须来自原文」的子集校验；
  · 模型把产品名 `3D-AGENT` 按正文写成 `3D-Agent`，台账按标题认亲会错位；
  · 175 条全量跑出 17 次「口径粘连」误报，判据必须只认糊块形状。
所有夹具都用 tmp，不碰真 `data/` 与 `out/articles/`。
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import make_article as M          # noqa: E402
import wechat_ai_titles as W      # noqa: E402
import clean_title_caliber as C   # noqa: E402

RAW = ("[20-0] 产品名：20-0\n业务：球迷从历史球队里选球员组阵容\n"
       "公开数字原文：MRR $835\n形态：梦幻球队构建游戏\n一句判断：卖的是参与感")
NAME = "20-0"
ALLOWED = W.numset(RAW)


def why(t, seen=None, raw=RAW, name=NAME, no_money=False):
    """测其它判据时关掉金额闸。

    下面这些用例（中文数字换算 / 糊块 / 禁词 / 撞车）的夹具刻意带
    `MRR $835` —— 金额闸默认开启（2026-11-04 用户口径），不显式关掉会
    抢先拦下、被测规则根本跑不到。金额闸本身见 `MoneyGate`。
    """
    return W.validate("20-0", t, name, W.numset(raw), seen or {}, raw,
                      no_money=no_money)


class MoneyGate(unittest.TestCase):
    """2026-11-04 用户口径：**所有标题**不出现金额（产品说明替代收入播报）。"""

    def test_money_rejected_by_default(self):
        for t in ("旋转历史球队选球员，20-0 赚走球迷 MRR $835",
                  "20-0 近 30 天收入 $835",
                  "20-0 一年做到几百万美金",
                  "20-0 的订阅每月 12 美元"):
            self.assertIn("标题含金额", why(t, no_money=True), t)

    def test_scale_is_not_money(self):
        # 「100 万用户」「576 人付费」是可核验规模，不是金额（曾误判 26 条）
        for t in ("100 万用户里只有 576 人付费，20-0 卖的是参与感",
                  "20-0 用 5 万个招聘页做聚合"):
            self.assertNotIn("标题含金额", why(t, no_money=True), t)

    def test_can_be_disabled_for_legacy_tests(self):
        self.assertNotIn("标题含金额",
                         why("旋转历史球队选球员，20-0 赚走球迷 MRR $835"))


class Numset(unittest.TestCase):
    def test_thousand_separator(self):
        self.assertEqual(W.numset("$244,029 和 $835"), {"244029", "835"})

    def test_no_numbers(self):
        self.assertEqual(W.numset("没有数字"), set())


class Validate(unittest.TestCase):
    def test_good_title_passes(self):
        self.assertEqual(why("旋转历史球队选球员，20-0 赚走球迷 MRR $835"), "")

    def test_chinese_numeral_is_rejected(self):
        # 3 条真实试跑逼出来的洞：中文数字串绕过 numset 子集校验
        self.assertIn("中文数字串", why("20-0 把 MRR $835 说成八三一美元"))
        self.assertIn("中文数字串", why("20-0 月收入四千多美元"))

    def test_chinese_money_unit_not_in_source(self):
        self.assertIn("中文金额单位", why("20-0 每月进账 835 美元"))

    def test_number_must_come_from_material(self):
        self.assertIn("数字不在素材原文", why("20-0 一天卖出 9999 份阵容"))

    def test_product_name_required(self):
        self.assertIn("缺产品名", why("球迷选球员组阵容的梦幻游戏"))

    def test_product_name_case_insensitive(self):
        # 台账按标题认亲，但大小写变体不该判「缺产品名」；
        # 由 normalize_name() 统一回 cases.json 的原拼写。
        # 夹具要带上标题里出现的英文词（Blender），否则会被英文词白名单闸拦下
        raw = "[x] 产品名：3D-AGENT\n公开数字原文：MRR $835\n形态：接管 Blender 建模流程"
        r = W.validate("x", "3d-agent 接管 Blender，MRR $835", "3D-AGENT",
                       W.numset(raw), {}, raw, no_money=False)
        self.assertEqual(r, "")

    def test_length_bounds(self):
        self.assertIn("太短", why("20-0 赚 $835"))
        self.assertIn("超长", why("20-0 " + "很" * 70))

    def test_banned_words(self):
        for w in ("深度拆解", "保姆级", "TrustMRR 实时核对", "扫码"):
            self.assertIn("禁词", why("20-0 的 MRR $835，" + w))

    def test_glued_money_blob_blocked(self):
        # 「近30天$31,677」「近30天收入$924」= 口径与金额零分隔
        # （夹具必须带金额才测得到，故 no_money=False）
        r = "20-0 已跑出近30天$31,677"
        self.assertIn("糊块", W.validate("x", r, NAME,
                                         W.numset(r), {}, r, no_money=False))

    def test_normal_income_phrase_allowed(self):
        # 全量实测曾误拒 17 次：「拿到近 30 天收入 $924」是合法中文
        r = "car-docs.ro 靠自动合同生成拿到近 30 天收入 $924"
        self.assertNotIn("糊块", W.validate("x", r, "car-docs.ro",
                                            W.numset(r), {}, r, no_money=False))

    def test_duplicate_title_rejected(self):
        t = "旋转历史球队选球员，20-0 赚走球迷 MRR $835"
        self.assertIn("撞车", why(t, seen={t: "another-case"}))


class NameHelpers(unittest.TestCase):
    def test_normalize_name_keeps_ledger_spelling(self):
        self.assertEqual(
            W.normalize_name("3D-Agent 接管 Blender", "3D-AGENT"),
            "3D-AGENT 接管 Blender")

    def test_inject_name_uses_natural_forms(self):
        """2026-11-04：不再用「｜」拼接（会被判机械断面）。

        两种形态：普通句 ⇒「名字：说明」；场景/条件句 ⇒ 名字后置。
        """
        plain = W.inject_name("卖的是参与感", NAME, "a")
        self.assertTrue(plain.startswith(NAME + "："))
        self.assertNotIn("｜", plain)
        scene = W.inject_name("一旦把照片拍给 AI", NAME, "a")
        self.assertTrue(scene.endswith(NAME))
        self.assertNotIn("｜", scene)
        self.assertEqual(W.inject_name("卖的是参与感", NAME, "a"),
                         W.inject_name("卖的是参与感", NAME, "b"))  # 与 cid 无关

    def test_inject_name_noop_when_present(self):
        self.assertEqual(W.inject_name("20-0 卖的是参与感", NAME, "a"),
                         "20-0 卖的是参与感")


class BodyDelta(unittest.TestCase):
    """`--verify` 的正文判据：只认黑话清洗这一种差异，别的一律判红。

    2026-11-05：清洗存量产物（「入库时→收录时」）之后基线过期，192 条里
    33 条被判「除 h1 外还有改动」。实测那 33 条 100% 等于清洗结果。
    修法是把这**唯一**一种被授权的变换写进判据，而不是重取基线 ——
    重取基线等于给以后所有正文改动白送通行证。
    """

    def test_identical_passes(self):
        self.assertTrue(W.body_delta_allowed("abc", "abc"))

    def test_jargon_scrub_delta_passes(self):
        old = "<p>早期入库时收入未公开</p>"
        new = "<p>早期收录时收入未公开</p>"
        self.assertTrue(W.body_delta_allowed(old, new))

    def test_jargon_scrub_plus_anything_else_fails(self):
        """洗完之后又多改一个字 ⇒ 不是「已知差异」，必须判红。"""
        old = "<p>早期入库时收入未公开</p>"
        new = "<p>早期收录时收入未公开！</p>"
        self.assertFalse(W.body_delta_allowed(old, new))

    def test_unrelated_change_fails(self):
        self.assertFalse(W.body_delta_allowed("<p>收入 $100</p>", "<p>收入 $200</p>"))


class SetH1(unittest.TestCase):
    def test_only_h1_inner_changes(self):
        src = ("<html>\n<h1>旧 &amp; 标题</h1>\n"
               "<p>TrustMRR 实时核对（非订阅制）</p>\n</html>\n")
        new, ok = W.set_h1(src, "新标题 & <b>")
        self.assertTrue(ok)
        self.assertIn("<h1>新标题 &amp; &lt;b&gt;</h1>", new)
        self.assertEqual(C.plain(C.h1_inner(new)), "新标题 & <b>")
        self.assertIn("TrustMRR 实时核对（非订阅制）", new)   # 正文数据卡不动
        # 除 h1 inner 外逐字一致
        m1, m2 = C.H1.search(src), C.H1.search(new)
        self.assertEqual(src[:m1.start(2)] + src[m1.end(2):],
                         new[:m2.start(2)] + new[m2.end(2):])

    def test_no_h1_returns_unchanged(self):
        src = "<p>没有标题</p>"
        self.assertEqual(W.set_h1(src, "x"), (src, False))


class MakeArticleIntegration(unittest.TestCase):
    """覆盖表缺失/损坏不能让生成器崩；存在时优先级低于人工表。"""

    def setUp(self):
        self._root = M.ROOT
        self._cache = dict(M._AI_TITLE_CACHE)
        self.tmp = tempfile.TemporaryDirectory()
        M.ROOT = self.tmp.name
        M._AI_TITLE_CACHE.update(mtime=None, titles={})
        os.makedirs(os.path.join(self.tmp.name, "data"))

    def tearDown(self):
        M.ROOT = self._root
        M._AI_TITLE_CACHE.clear()
        M._AI_TITLE_CACHE.update(self._cache)
        self.tmp.cleanup()

    @staticmethod
    def _case(cid):
        return {"id": cid, "name": cid, "one_liner": "做点什么",
                "metrics": {"headline": "MRR $835"}}

    def _write(self, titles):
        p = os.path.join(self.tmp.name, "data", "wechat_title_overrides.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"titles": titles}, f, ensure_ascii=False)

    def test_missing_file_is_empty_not_crash(self):
        self.assertEqual(M.ai_title_overrides(), {})
        t, manual = M.build_titles(self._case("no-such-case"))
        self.assertFalse(manual)
        self.assertIn("no-such-case", t[0])

    def test_broken_json_is_empty_not_crash(self):
        p = os.path.join(self.tmp.name, "data", "wechat_title_overrides.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ broken")
        self.assertEqual(M.ai_title_overrides(), {})

    def test_override_becomes_first_title(self):
        self._write({"demo": "一句具体的 AI 标题"})
        t, manual = M.build_titles(self._case("demo"))
        self.assertTrue(manual)
        self.assertEqual(t[0], "一句具体的 AI 标题")
        self.assertEqual(len(t), 3)              # 备选仍保留供人挑

    def test_human_table_wins_over_ai(self):
        cid = sorted(M.TITLE_OVERRIDES)[0]
        self._write({cid: "AI 写的标题不该赢"})
        t, _ = M.build_titles(self._case(cid))
        self.assertEqual(t[0], M.TITLE_OVERRIDES[cid][0])

    def test_mtime_cache_refreshes(self):
        self._write({"demo": "第一版"})
        self.assertEqual(M.ai_title_overrides()["demo"], "第一版")
        M._AI_TITLE_CACHE["mtime"] += 1         # 伪造 mtime 变化
        self._write({"demo": "第二版"})
        self.assertEqual(M.ai_title_overrides()["demo"], "第二版")


class ScopeSafety(unittest.TestCase):
    """只读真库核对范围，绝不写盘。"""

    def test_no_published_in_default_scope(self):
        tg = {c for c, _ in W.targets("175")}
        self.assertEqual(len(tg & W.published_ids()), 0)

    def test_protected_not_in_scope(self):
        tg = {c for c, _ in W.targets("175")}
        self.assertEqual(len(tg & W.protected_ids()), 0)

    def test_all_scope_superset(self):
        self.assertGreaterEqual(
            {c for c, _ in W.targets("all")},
            {c for c, _ in W.targets("175")})


if __name__ == "__main__":
    unittest.main()
