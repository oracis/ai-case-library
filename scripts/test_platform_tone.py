#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-04「按平台调性编写文稿」回归锁。

锁三件事：
  1. 五平台不能共用骨架 / 逐字同稿（`platform_tone.body_fingerprint`）；
  2. 生成器标题必须过 `publish_guard.scan_title` 和 `tone_issues`；
  3. 两个标题根因（公众号 headline 旁注、头条 `t[:30]` 硬切）不许复发；
     罐头来源句判据不许把带时点的合法出处误判成模板。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import gen_platform_md as GM            # noqa: E402
import make_article as MA               # noqa: E402
import platform_tone as TONE            # noqa: E402
import publish_guard as G               # noqa: E402
import retone_drafts as RT              # noqa: E402
import toutiao_publish as TP            # noqa: E402

CASE = {
    "id": "demo", "name": "Demo",
    "one_liner": "「免费版 Chess.com」拿下 100 万+ 用户，MRR $7,306、576 个订阅。",
    "what_it_does": "做免费国际象棋对战平台，2025 年 3 月上线。",
    "how_it_makes_money": "核心对战免费，增值功能订阅收费。",
    "verdict": "免费产品的变现是人数乘以极低的转化率。",
    "metrics": {"headline": "近 30 天收入 $16,120，TrustMRR 实时核对（非订阅制，MRR 恒 0）",
                "subscriptions": 576, "growth": "上线 5 天做到 18,380 美元 MRR"},
    "playbook": ["打在价格上", "成本以服务器为主"],
    "why_it_works": ["对手池越深留存越好", "踩上品类大盘"],
}


class TestToneProfile(unittest.TestCase):
    def test_nine_platforms_declared(self):
        self.assertEqual(len(TONE.TONE), 9)
        for p in ("wechat", "toutiao", "xhs", "bilibili", "zhihu",
                  "cnblogs", "csdn", "tencentcloud", "aliyun"):
            self.assertIn(p, TONE.TONE)
            for k in ("voice", "title_style", "opening", "sections",
                      "closing", "forbid", "limit"):
                self.assertIn(k, TONE.TONE[p], "%s 缺 %s" % (p, k))

    def test_five_text_skeletons_are_distinct(self):
        sk = TONE.skeletons()
        vals = [sk[p] for p in TONE.TEXT_PLATFORMS]
        self.assertEqual(len(set(vals)), len(vals),
                         "五文本平台不能共用同一串栏目")

    def test_openings_are_distinct(self):
        ops = [TONE.profile(p)["opening"] for p in TONE.TEXT_PLATFORMS]
        self.assertEqual(len(set(ops)), len(ops), "五平台开场白不能共用")

    def test_fingerprint_ignores_watermark_but_catches_same_body(self):
        a = "# 标题 A\n\n> 平台：tencentcloud ｜ 状态：本地稿\n\n同一个正文。\n"
        b = "# 标题 B\n\n> 平台：aliyun ｜ 状态：本地稿\n\n同一个正文。\n"
        c = "# 标题 C\n\n另一个正文。\n"
        self.assertEqual(TONE.body_fingerprint(a), TONE.body_fingerprint(b))
        self.assertNotEqual(TONE.body_fingerprint(a), TONE.body_fingerprint(c))


class TestGeneratedCopy(unittest.TestCase):
    def test_generated_bodies_all_distinct(self):
        fps = [TONE.body_fingerprint(GM.render(p, CASE))
               for p in TONE.TEXT_PLATFORMS]
        self.assertEqual(len(set(fps)), len(fps),
                         "生成稿跨平台逐字相同（11-04 两朵云 cod-dex 事故）")

    def test_generated_titles_pass_guard_and_tone(self):
        for p in TONE.TEXT_PLATFORMS:
            txt = GM.render(p, CASE)
            t = G.title_of(txt) if False else txt.split("\n")[0][2:]
            blocks = [f for f in G.scan_title(p, t) if f["level"] == "block"]
            self.assertEqual(blocks, [], "%s 标题被闸拦：%s" % (p, blocks))
            self.assertEqual(TONE.tone_issues(p, t, txt), [],
                             "%s 生成稿违反本平台调性禁忌" % p)

    def test_no_pipeline_marks_in_generated_copy(self):
        for p in TONE.TEXT_PLATFORMS:
            txt = GM.render(p, CASE)
            self.assertFalse(G.WATERMARK.search(txt), "%s 带管线水印" % p)
            self.assertFalse(G.CANNED_SOURCE.search(txt), "%s 带罐头来源句" % p)

    def test_tech_platform_titles_have_no_money(self):
        for p in TONE.TEXT_PLATFORMS:
            t = GM.make_title(CASE, platform=p)
            self.assertNotRegex(t, r"[$¥]\s*\d|\d[\d,.]*\s*(?:万美元|美元|MRR|ARR)",
                                "%s 标题带金额：%s" % (p, t))


class TestTitleRootCauses(unittest.TestCase):
    def test_wechat_title_drops_caliber_note_and_fits_64(self):
        c = dict(CASE, name="Altitude Cleaning Crew")
        titles, _ = MA.build_titles(c)
        self.assertNotIn("TrustMRR 实时核对", titles[0])
        self.assertNotIn("非订阅制", titles[0])
        self.assertLessEqual(len(titles[0]), 64)

    def test_wechat_title_still_keeps_the_number(self):
        titles, _ = MA.build_titles(dict(CASE, name="Altitude Cleaning Crew"))
        self.assertIn("$16,120", titles[0], "清旁注不该把可核验数字也清掉")

    def test_toutiao_no_dangling_number_tail(self):
        out = TP._truncate_title("Chessigma：「免费版 Chess.com」拿下 100 万+ 用户，MRR $7,306", 30)
        self.assertFalse(out.endswith("10"), "又切出半个数字：%s" % out)
        self.assertLessEqual(len(out), 30)

    def test_toutiao_no_dangling_particle_tail(self):
        out = TP._truncate_title("Shopify 商家的 Meta 追踪本来就是错的，于是有了这门生意", 30)
        self.assertFalse(out.endswith("了"), "又切出半个词：%s" % out)
        self.assertEqual(out, "Shopify 商家的 Meta 追踪本来就是错的")


class TestCannedSourcePrecision(unittest.TestCase):
    CANNED = [
        "数据来源：官网、TrustMRR 支付验证页、公开访谈。",
        "数据来源：产品官网公开信息、公开支付验证页。",
        "数据来源：产品官网公开定位、应用商店公开信息、公开支付验证页。",
        "来源：官网 + TrustMRR 支付验证页 + 公开访谈。",
        "数据是实时核对过的（TrustMRR 直连支付网关），口径带修正。",
    ]
    LEGIT = [
        "数据来源：产品公开定价页与应用商店公开信息（2026-10 快照）。",
        "数据来源：官方公开口径（产品官网、公开访谈、支付验证页快照，2026-10-14 同步）。",
        "公开数据显示它累计收入 $1.27M（数据来源：TrustMRR、Steal What Works）。",
    ]

    def test_four_variants_caught(self):
        for s in self.CANNED:
            self.assertTrue(G.CANNED_SOURCE.search(s), "漏判罐头句：%s" % s)

    def test_dated_specific_sources_pass(self):
        for s in self.LEGIT:
            self.assertFalse(G.CANNED_SOURCE.search(s), "误伤带时点出处：%s" % s)

    def test_retone_uses_guard_regex_not_its_own(self):
        self.assertIs(RT.CANNED_LINE, G.CANNED_SOURCE)
        self.assertIs(RT.WM_LINE, G.WATERMARK)


class TestRepoState(unittest.TestCase):
    """存量稿验收锁：跑 retone 之前若有人重新引入水印/坏标题，这里会红。"""

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "drafts", "newbatch")),
                         "没有产物目录")
    def test_retone_plan_is_idempotent(self):
        changed = [(r, t) for r, t, _ in RT.plan() if t != RT._read(r)]
        self.assertEqual(changed, [], "存量稿又被改回旧形状了：%s" %
                         [r for r, _ in changed][:5])

    def test_no_cross_platform_duplicate_copy(self):
        import glob
        fps = {}
        for p in TONE.TEXT_PLATFORMS:
            for f in glob.glob(os.path.join(ROOT, "drafts", "newbatch", p, "*.md")):
                with open(f, encoding="utf-8") as fh:
                    fp = TONE.body_fingerprint(fh.read())
                fps.setdefault(fp, []).append(os.path.relpath(f, ROOT))
        dup = [v for v in fps.values() if len(v) > 1]
        self.assertEqual(dup, [], "跨平台逐字同稿：%s" % dup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
