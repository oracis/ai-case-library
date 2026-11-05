#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""draft_case_text 的机器守卫回归测试。

这些守卫是「AI 起草 → 入库」这条链路唯一的安全网，而它们**每一个都是
被实测打脸之后才加上/改对的**，所以每条断言都对应一次真实事故：

    中文守卫          3 条里 2 条吐英文
    客单价不准        12454÷407 模型给 29.04（正确 30.60）
    派生放行集合      组合爆炸，「MRR 增长 88%」照样通过
    增速断言          .md 原文里什么数字都有，通用数字守卫拦不住编的增速
    毛利率误杀        守卫把合法的「毛利率 80%」当增速拦了
    空 dict 跳过      `if summary:` 把空素材条目的守卫整个绕过
    slug 对不上       fireply 的 slug 对不上 fireply-ai → 拿到空素材
    MRR 贴错标签      素材 MRR=1279 / 30天=1155，模型写「MRR $1,155」
    货币写错「元」     28 条把 USD 写成「元」，trustmrr 全是美元应写 $
    裸 .0 浮点        37 条整数被写成 31677.0 这类浮点尾

改守卫前先跑这个：python scripts/test_draft_case_text.py
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import draft_case_text as D  # noqa: E402


def _draft(**kw):
    base = {
        "one_liner": "", "what_it_does": "", "how_it_makes_money": "",
        "verdict": "", "why_it_works": [], "playbook": [],
    }
    base.update(kw)
    return base


class Numbers(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(D._norm("1,155"), 1155.0)
        self.assertEqual(D._norm("12454.0"), 12454.0)

    def test_extract_numbers(self):
        self.assertEqual(D.extract_numbers("MRR $1,155 与 30 天 $12,454"),
                         {1155.0, 12454.0, 30.0})

    def test_ghost_number_flagged(self):
        """素材里没有的数字必须被拦下。"""
        p, ghosts = D.check_numbers(_draft(one_liner="MRR $99,999"),
                                    {12454.0, 407.0})
        self.assertTrue(p)
        self.assertIn(99999.0, ghosts)

    def test_source_number_allowed(self):
        p, _g = D.check_numbers(_draft(one_liner="MRR $12,454"), {12454.0})
        self.assertEqual(p, [])

    def test_no_derived_allowance(self):
        """派生放行集合已砍掉：407-30=377 不该因为「能算出来」就放行。

        原实现把日期、粉丝数也拿去做两两四则运算，组合爆炸后几乎任何数
        都能凑出来 —— 守卫形同虚设。
        """
        p, _g = D.check_numbers(_draft(one_liner="增长到 377 个"), {407.0, 30.0})
        self.assertTrue(p)


class GrowthClaims(unittest.TestCase):
    def test_fabricated_growth_flagged(self):
        """「MRR 增长 88%」：素材没给增速，必须拦。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["近 30 天 MRR 增长了 88%"]),
            {"growth_30d": 62.0})
        self.assertTrue(p)

    def test_source_growth_allowed(self):
        p = D.check_growth_claims(
            _draft(why_it_works=["近 30 天增速 12.1%"]),
            {"growth_30d": 12.1})
        self.assertEqual(p, [])

    def test_profit_margin_not_mistreated(self):
        """毛利率是素材真字段，不能被当成编造的增速误杀。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["80% 的毛利率说明盈利能力强"]),
            {"profit_margin": 80.0})
        self.assertEqual(p, [])

    def test_empty_summary(self):
        """素材为空时任何增速断言都不该放行。"""
        p = D.check_growth_claims(
            _draft(why_it_works=["增长 30%"]), {})
        self.assertTrue(p)

    def test_non_growth_pct_allowed(self):
        """「15% 市场费用」「100% 收入」是费率/分成，不是增速，不误拦。"""
        p = D.check_growth_claims(
            _draft(how_it_makes_money="收取 15% 市场费用，保留 100% 收入分成"),
            {"growth_30d": 681.1})
        self.assertEqual(p, [])

    def test_organic_growth_pct_allowed(self):
        """「100% 有机增长」= 获客结构描述（全自然流量），不是增速率，不误拦。"""
        p = D.check_growth_claims(
            _draft(what_it_does="实现 100% 的有机增长，零广告投放"),
            {"growth_30d": 10.5})
        self.assertEqual(p, [])


class Caliber(unittest.TestCase):
    def test_non_subscription_cannot_say_mrr(self):
        """MRR 恒 0 的非订阅制，写「MRR $X」= 口径混用（红线 1）。"""
        p = D.check_caliber(_draft(one_liner="MRR $1,155"),
                            {"cur_mrr": 0, "rev_30d": 1155.0})
        self.assertTrue(p)
        self.assertIn("非订阅制", p[0])

    def test_mrr_label_must_match_source(self):
        """把近 30 天收入贴上 MRR 标签：数字有出处但标签错，最隐蔽。"""
        p = D.check_caliber(_draft(one_liner="MRR $1,155"),
                            {"cur_mrr": 1279.0, "rev_30d": 1155.0})
        self.assertTrue(p)
        self.assertIn("贴错标签", p[0])

    def test_correct_mrr_allowed(self):
        p = D.check_caliber(_draft(one_liner="MRR $1,279"),
                            {"cur_mrr": 1279.0, "rev_30d": 1155.0})
        self.assertEqual(p, [])

    def test_zero_mrr_claim_allowed(self):
        """「MRR 恒 0」本身是正确说法，不该被拦。"""
        p = D.check_caliber(_draft(one_liner="MRR 0，非订阅制"),
                            {"cur_mrr": 0, "rev_30d": 1155.0})
        self.assertEqual(p, [])


class CurrencyFormat(unittest.TestCase):
    """货币单位（元）与裸 .0 浮点守卫：实测 28 条写成「元」、37 条带 .0。"""

    def test_cny_yuan_flagged(self):
        p = D.check_currency_format(_draft(
            one_liner="近30天已验证收入31677.0元"))
        self.assertTrue(p)
        self.assertIn("元", p[0])

    def test_eur_yuan_allowed(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="每月 1990 欧元，按欧元计价"))
        self.assertEqual(p, [])

    def test_element_yuan_allowed(self):
        p = D.check_currency_format(_draft(
            playbook=["利用游戏化元素提升留存"]))
        self.assertEqual(p, [])

    def test_bare_float_flagged(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="毛利率为 85.0%"))
        self.assertTrue(p)
        self.assertIn(".0", p[0])

    def test_normal_decimal_allowed(self):
        p = D.check_currency_format(_draft(
            how_it_makes_money="客单价为 $7.76"))
        self.assertEqual(p, [])

    def test_dollar_ok(self):
        p = D.check_currency_format(_draft(
            one_liner="近30天已验证收入 $31,677"))
        self.assertEqual(p, [])


class Validate(unittest.TestCase):
    def test_empty_summary_still_runs_guards(self):
        """`if summary:` 会因空 dict 为 falsy 而跳过守卫（踩过）。

        slug 对不上 summary 时正是素材最空、模型最容易编的时候，
        守卫反而被关掉了 —— 必须写 `is not None`。
        """
        problems = D.validate(
            _draft(one_liner="MRR $1,155", why_it_works=["增长 88%"]),
            {1155.0}, {})
        self.assertTrue(any("非订阅制" in p or "贴错标签" in p for p in problems))
        self.assertTrue(any("增速" in p for p in problems))

    def test_non_chinese_flagged(self):
        problems = D.validate(_draft(one_liner="MRR $12,454 for autoclip"),
                              {12454.0}, {"cur_mrr": 12454.0})
        self.assertTrue(any("不是中文" in p for p in problems))

    def test_banned_phrase_flagged(self):
        problems = D.validate(
            _draft(one_liner="MRR $1,234 的工具",
                   why_it_works=["前景广阔，值得关注"]),
            {1234.0}, {"cur_mrr": 1234.0})
        self.assertTrue(any("空话" in p for p in problems))

    def test_replicability_keys(self):
        problems = D.validate(
            _draft(one_liner="MRR $1,234 的工具",
                   replicability={"tech": 2, "data": 2,
                                  "sales": 3, "domain": 4}),
            {1234.0}, {"cur_mrr": 1234.0})
        self.assertTrue(any("键名不对" in p for p in problems))


class Lookup(unittest.TestCase):
    def test_slug_fallback_to_id(self):
        """fireply 的 slug 对不上 fireply-ai，必须按 id 兜底。"""
        summ = {"fireply-ai": {"cur_mrr": 1279.0}}
        self.assertIs(D.lookup_summary(summ, "fireply", "fireply-ai"),
                      summ["fireply-ai"])

    def test_slug_direct_hit(self):
        summ = {"autoclip": {"cur_mrr": 1.0}}
        self.assertIs(D.lookup_summary(summ, "autoclip", "autoclip"),
                      summ["autoclip"])

    def test_missing_returns_none(self):
        self.assertIsNone(D.lookup_summary({}, "x", "y"))


class FactsCard(unittest.TestCase):
    def test_arpu_precomputed(self):
        """客单价必须由脚本算好 —— 模型做除法会算错（踩过两次）。"""
        card = D.facts_card("x", {"cur_mrr": 12454.0, "active_subs": 407}, "")
        self.assertIn("$30.6", card)

    def test_arpu_absent_for_non_subscription(self):
        card = D.facts_card("x", {"cur_mrr": 0, "rev_30d": 1155.0}, "")
        self.assertIn("无法推算", card)

    def test_missing_fields_marked(self):
        card = D.facts_card("x", {}, "")
        self.assertIn("创始人：未公开", card)


class Wording(unittest.TestCase):
    """2026-11-05：4 条新草稿抽检发现的措辞事故。

    根因不是模型自由发挥，而是素材卡抬头把「支付网关 API 直读」「已验证收入」
    「（已算好）」当固定标签印给了模型，模型照抄后变成：
    ① 缓存快照说成「截至 2026-11-05 实时直读」；
    ② partial 证据等级写成「经 Stripe API 直读验证」；
    ③ 给模型看的批注「（已算好）」印给读者；
    ④ 客单价（推算值）写成「将订阅定价压在 $10 以下」。
    改源头治未来、scrub_wording() 治已生成的稿，两者都要锁住。
    """

    REAL_BAD = {
        "what_it_does": (
            "截至 2026-11-05，支付网关直读显示当前 MRR 为 $3,929，活跃订阅数为 7。"
            "收入数据来自 Polar 支付网关 API 直读。当前 MRR 为 $441，"
            "近 30 天已验证收入 $368。"),
        "how_it_makes_money": (
            "按支付网关直读数据，当前 MRR $3,929 除以活跃订阅数 7，"
            "得出客单价 $561.29。当前 MRR 为 $397，活跃订阅数 6，"
            "客单价 $66.17（已算好）。用 MRR $441 除以活跃订阅数 9，"
            "推算客单价约 $49。"),
        "why_it_works": [
            "活跃订阅 159 个，MRR $1,561，客单价 $9.82（支付网关直读）——低价订阅降低门槛。",
            "支付网关直读：近 30 天已验证收入 $366，其中 2026-10-19 各 $66.01。",
            "收入数据经 Stripe API 直读验证。",
        ],
        "playbook": ["将订阅定价压在 $10 以下，降低首次尝试的决策成本。"],
        "replicability": {"tech": 2, "distribution": 3, "capital": 1, "timing": 3},
    }

    @staticmethod
    def _money(data):
        import re
        return set(re.findall(r"\$[\d,]+(?:\.\d+)?",
                              json.dumps(data, ensure_ascii=False)))

    def test_facts_card_no_strong_claim_labels(self):
        card = D.facts_card("x", {"cur_mrr": 12454.0, "active_subs": 407,
                                  "rev_30d": 11550.0}, "")
        for bad in ("支付网关 API 直读", "已算好", "已验证收入"):
            self.assertNotIn(bad, card)
        self.assertIn("缓存快照", card)
        self.assertIn("不是本库实时直读", card)
        self.assertIn("$30.6", card)  # 去掉批注不能把数值一起删掉

    def test_system_bans_realtime_fabricated_date_and_arpu_as_price(self):
        for rule in ("缓存快照", "不许自己编日期", "客单价不是定价",
                     "素材卡里的批注", "不要重算"):
            self.assertIn(rule, D.SYSTEM)
        self.assertNotIn("客单价（已算好）", D.SYSTEM)
        self.assertNotIn("订阅数 ÷ MRR", D.SYSTEM)  # 原式写反了，已改掉

    def test_wording_bans_detects_every_real_case(self):
        hits = D.wording_bans(self.REAL_BAD)
        labels = {h.split(" @ ")[0] for h in hits}
        self.assertEqual(labels, {"自编时点+实时直读", "实时直读强断言",
                                  "已验证收入", "素材批注",
                                  "客单价当定价", "复述除法过程"})

    def test_scrub_cleans_every_real_case(self):
        out, n = D.scrub_wording(self.REAL_BAD)
        self.assertGreater(n, 0)
        self.assertEqual(D.wording_bans(out), [])
        self.assertIn("TrustMRR 缓存快照", out["what_it_does"])
        self.assertIn("支付源为 Polar", out["what_it_does"])
        self.assertIn("支付源为 Stripe", out["why_it_works"][2])
        self.assertIn("把客单价控制在", out["playbook"][0])
        self.assertNotIn("已算好", out["how_it_makes_money"])

    def test_scrub_never_changes_money(self):
        out, _n = D.scrub_wording(self.REAL_BAD)
        self.assertEqual(self._money(out), self._money(self.REAL_BAD))

    def test_scrub_drops_model_invented_date(self):
        out, _n = D.scrub_wording(self.REAL_BAD)
        self.assertNotIn("截至 2026-11-05", out["what_it_does"])

    def test_scrub_is_idempotent(self):
        once, _n = D.scrub_wording(self.REAL_BAD)
        twice, n2 = D.scrub_wording(once)
        self.assertEqual(n2, 0)
        self.assertEqual(once, twice)

    def test_scrub_keeps_other_fields_intact(self):
        out, _n = D.scrub_wording(self.REAL_BAD)
        self.assertEqual(out["replicability"], self.REAL_BAD["replicability"])
        self.assertNotIn("replicability", dict(D._visible_texts(out)))
        clean = _draft(one_liner="MRR $1,155 的订阅制工具", verdict="它卖的是节奏")
        out2, n = D.scrub_wording(clean)
        self.assertEqual(n, 0)
        self.assertEqual(out2, clean)


class FragmentWarning(unittest.TestCase):
    """档案残片要**点名**，不许静默当素材（2026-10-21 补）。

    fetch_tm_md 抓到一半被打断会留下半截 .md；draft_one 照样读得进来，
    素材少一半而模型不自知。fetch 侧已改成重抓 + 原子覆盖，这里守消费侧：
    残片要打印告警（告诉用户先重抓），不许闷声写正文。
    """

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self._old = D.TM_DIR, D.ai_verify.llm
        D.TM_DIR = self.d
        # 模型侧打桩：只要被调到就返回空 JSON（不联网、不花钱）
        D.ai_verify.llm = lambda *a, **kw: "{}"

    def tearDown(self):
        import shutil
        D.TM_DIR, D.ai_verify.llm = self._old
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, name, text, out):
        import contextlib
        import io
        with open(os.path.join(self.d, name + ".md"), "w",
                  encoding="utf-8") as f:
            f.write(text)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            D.draft_one(name, name, {}, retries=0)
        out.append(buf.getvalue())

    def test_残片会告警(self):
        out = []
        self._run("demo", "# Demo\n\n只有开头的一小段，页脚没有。\n" * 20, out)
        self.assertIn("残片", out[0])
        self.assertIn("fetch_tm_md", out[0])

    def test_完整档案不告警(self):
        out = []
        body = "# Demo\n\n" + ("- Insight: 正常内容一行。\n" * 40)
        self._run("demo2", body + "\n## Related TrustMRR Pages\n"
                  "- [llms.txt](https://trustmrr.com/llms.txt)\n\n", out)
        self.assertNotIn("残片", out[0])


class Revalidate(unittest.TestCase):
    """全量 --revalidate 的素材缺失保护（2026-11-05）。

    事故：summary.json 只剩 16 键时跑全量，177 条旧草稿拿空摘要回套，
    171 条由净转脏；其中连 .md 都没有的 5 条把真实引用过的数字全报成
    「素材里没有的数字」，apply 会因此把它们挡掉。
    """

    @staticmethod
    def _entry(**kw):
        base = _draft(
            one_liner="这是一个中文定位句",
            what_it_does="这个产品做一件具体的事。",
            how_it_makes_money="靠订阅收费。",
            verdict="值得继续观察。",
            why_it_works=["中文理由一条"], playbook=["中文步骤一条"],
            replicability={"tech": "", "distribution": "",
                           "capital": "", "timing": ""},
        )
        base.update(kw)
        return base

    def test_无素材条目跳过且原状态不被改写(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            stage = {"ghost": self._entry(
                _slug="ghost", _problems=["哨兵：保持原样"], _needs_human=True)}
            bad, skipped = D.revalidate_stage(
                stage, {}, tm_dir=td, reporter=lambda *a: None)
            self.assertEqual((bad, skipped), (0, 1))
            self.assertEqual(stage["ghost"]["_problems"], ["哨兵：保持原样"])
            self.assertTrue(stage["ghost"]["_needs_human"])

    def test_有md的条目仍会被回套(self):
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with io.open(os.path.join(td, "ok.md"), "w", encoding="utf-8") as f:
                f.write("# Ok\n\n| Last 30 days | $1,000 |\n"
                        "\n## Related TrustMRR Pages\n- [llms.txt](x)\n")
            stage = {"ok": self._entry(
                _slug="ok", what_it_does="这个产品做一件事，收入 1000 元。")}
            bad, skipped = D.revalidate_stage(
                stage, {}, tm_dir=td, reporter=lambda *a: None)
            self.assertEqual(skipped, 0)
            self.assertEqual(bad, 1)
            self.assertTrue(any("人民币" in p for p in stage["ok"]["_problems"]))
            self.assertTrue(stage["ok"]["_needs_human"])

    def test_只有摘要没有md也会回套(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            summ = {"ok": {"slug": "ok", "cur_mrr": 1000,
                           "active_subs": 0, "provider": "Stripe"}}
            stage = {"ok": self._entry(_slug="ok")}
            bad, skipped = D.revalidate_stage(
                stage, summ, tm_dir=td, reporter=lambda *a: None)
            self.assertEqual((bad, skipped), (0, 0))
            self.assertEqual(stage["ok"]["_problems"], [])
            self.assertFalse(stage["ok"]["_needs_human"])


class StockWording(unittest.TestCase):
    """2026-11-05 二轮：181 条存量草稿里 129 条带旧措辞，首版 SCRUB_RULES
    只覆盖 4 条新稿的句式，全量预演有 38 条洗完仍违规。这里用**真实存量
    原句**锁住补齐后的形态，防止规则再退回「只认一种写法」。
    """

    STOCK_BAD = {
        "what_it_does": (
            "收款走 Stripe，收入数字由支付网关 API 直读。当前 MRR $402，活跃订阅 22 个。"
            "收入由 Whop 与 Stripe 两个支付网关 API 直读。"
            "收入数据经 Stripe API 直读验证，当前 MRR 为 $28,579。"
            "收入由支付网关 API 直读，非档案自述。"
            "收入通过 RevenueCat 支付网关 API 直读，非订阅制。"
            "付费订阅 217 份，由 Stripe 支付网关 API 直读。"),
        "how_it_makes_money": (
            "当前 MRR $618 除以活跃订阅数 148，得出客单价为 $4.18。"
            "用 MRR $4,945 除以活跃订阅数 1068，得出客单价 $4.63。"
            "客单价 $83.09（支付网关直读，已算好）。"
            "另有客单价 $66.17（已算好），直接引用素材。"
            "收入来自 Stripe 支付网关 API 直读，2026-11-03 同步。"
            "MRR $531（支付网关 API 直读），活跃订阅数 13 个。"),
        "why_it_works": [
            "支付网关 API 直读：近 30 天已验证收入 $2,675，数据来自 Stripe。",
            "近 30 天已验证收入 $2,786（支付网关直读），机制：买断制收入随时间衰减。",
            "支付网关直读显示近 30 天收入 $6,680。",
        ],
        "playbook": [
            "用支付网关 API 直读收入并公开：把实数摆出来。",
            "使用 Paddle 等支付网关 API 直读收入，避免手动录入或截图。",
            "将订阅定价压在 $10 以下，降低首次尝试的决策成本。",
        ],
        "replicability": {"tech": 2, "distribution": 3, "capital": 1, "timing": 3},
    }

    @staticmethod
    def _money(data):
        import re
        return set(re.findall(r"\$[\d,]+(?:\.\d+)?",
                              json.dumps(data, ensure_ascii=False)))

    @staticmethod
    def _text(data):
        return "\n".join(t for _k, t in D._visible_texts(data))

    def test_bans_catches_all_stock_shapes(self):
        labels = {h.split(" @ ")[0] for h in D.wording_bans(self.STOCK_BAD)}
        self.assertEqual(labels, {"实时直读强断言", "已验证收入", "素材批注",
                                  "客单价当定价", "复述除法过程"})

    def test_scrub_cleans_all_stock_shapes(self):
        out, n = D.scrub_wording(self.STOCK_BAD)
        self.assertGreater(n, 10)
        self.assertEqual(D.wording_bans(out), [])

    def test_scrub_never_changes_money(self):
        out, _n = D.scrub_wording(self.STOCK_BAD)
        self.assertEqual(self._money(out), self._money(self.STOCK_BAD))

    def test_scrub_keeps_payment_sources(self):
        out, _n = D.scrub_wording(self.STOCK_BAD)
        text = self._text(out)
        for src in ("支付源为 Stripe", "支付源为 Whop 与 Stripe",
                    "支付源为 RevenueCat"):
            self.assertIn(src, text)

    def test_no_strong_claim_label_left(self):
        out, _n = D.scrub_wording(self.STOCK_BAD)
        text = self._text(out)
        for bad in ("支付网关 API 直读", "已验证收入", "已算好",
                    "除以活跃订阅数", "将订阅定价压在", "支付网关直读"):
            self.assertNotIn(bad, text, bad)

    def test_scrub_is_idempotent_on_stock(self):
        once, _n = D.scrub_wording(self.STOCK_BAD)
        twice, n2 = D.scrub_wording(once)
        self.assertEqual(n2, 0)
        self.assertEqual(once, twice)

    def test_scrub_keeps_non_visible_fields(self):
        out, _n = D.scrub_wording(self.STOCK_BAD)
        self.assertEqual(out["replicability"], self.STOCK_BAD["replicability"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
