# -*- coding: utf-8 -*-
"""反向样本表（data/anti_cases.json）的结构锁（2026-10-26）。

这张表是新加的第三方口径（cases=推荐位 / graveyard=已停运 / anti=可读不建议抄），
最容易出的错不是写错某一条，而是**悄悄混进另一张表**：id 撞了、role 写漏了、
「那个数其实不是收入」却没写口径说明。所以这批测试守的是结构，不是文风。
"""
import io
import json
import os
import sys
import unittest

# Windows 控制台默认 GBK，中文 traceback 会 UnicodeEncodeError。
# ⚠ 就地 reconfigure，不要新包一层 TextIOWrapper（会连带关掉底层 buffer）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

ANTI = os.path.join(ROOT, "data", "anti_cases.json")
CASES = os.path.join(ROOT, "data", "cases.json")

CALIBERS = {"mrr", "arr", "annual", "funding", "valuation",
            "gmv", "price", "lifetime", "unknown", "invalid"}
ROLES = {"structural", "license", "caliber"}


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class TestAntiCasesTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load(ANTI)
        cls.cases = cls.raw["cases"]
        cls.by_id = {c["id"]: c for c in cls.cases}
        cls.pub = load(CASES)
        cls.pub_cases = cls.pub["cases"] if isinstance(cls.pub, dict) else cls.pub

    # ---- 表本身 ----
    def test_表能读出且条数对得上_summary(self):
        # /2：加了分篇（volume + volumes），字段契约变了，主版本跟着升
        self.assertEqual(self.raw.get("_schema"), "anti_cases/2")
        self.assertEqual(len(self.cases), self.raw["_summary"]["total"])

    def test_id不重复(self):
        self.assertEqual(len(self.by_id), len(self.cases))

    def test_id不与推荐位相撞(self):
        # 撞了会让 find_article / build --case 抽错稿，比「少一条」严重得多
        pub_ids = {c["id"] for c in self.pub_cases}
        hit = sorted(set(self.by_id) & pub_ids)
        self.assertEqual(hit, [], "反向样本与 cases.json 有同 id：%s" % hit)

    # ---- 三类标签 ----
    def test_role合法且出现在roles里(self):
        for c in self.cases:
            self.assertIn(c["role"], ROLES, c["id"])
            self.assertTrue(c["roles"], c["id"])
            self.assertIn(c["role"], c["roles"], c["id"])
            for r in c["roles"]:
                self.assertIn(r, ROLES, c["id"])

    def test_三类都非空(self):
        # 只有两类 = 有一类是被硬塞进别的桶的，分类等于失效
        got = {c["role"] for c in self.cases}
        self.assertEqual(got, ROLES)

    def test_每条都归篇且篇是已声明的(self):
        # 2026-10-26：原报告的 ①②③④ 换成「按门槛类型合并成 7 篇」（volume）。
        # 一条没归篇 = 这篇永远写不到它；归到不存在的篇 = 站上分组会凭空少一条。
        vols = {v["key"] for v in self.raw.get("volumes") or []}
        self.assertTrue(vols, "缺 volumes 元信息")
        for c in self.cases:
            self.assertIn(c.get("volume"), vols, c["id"])

    def test_每篇条数与声明一致(self):
        vols = self.raw.get("volumes") or []
        self.assertEqual(len(vols), 7, "按门槛类型分 7 篇")
        total = 0
        for v in vols:
            ids = [c["id"] for c in self.cases if c["volume"] == v["key"]]
            self.assertEqual(len(ids), v["count"], "篇「%s」条数与 count 不符" % v["key"])
            self.assertEqual(sorted(ids), sorted(v["ids"]), v["key"])
            self.assertTrue(v.get("title") and v.get("judge"), v["key"])
            # 短名只给 chips 用，太长会把筛选条挤成两行
            self.assertLessEqual(len(v.get("short") or ""), 4, v["key"])
            total += v["count"]
        self.assertEqual(total, len(self.cases))

    def test_分篇不改变role归属(self):
        # 篇是按「门槛类型」再切一刀，不许趁机改 role（改了等于分类标准漂移）
        for v in self.raw.get("volumes") or []:
            if v["key"] in ("license", "caliber"):
                for c in self.cases:
                    if c["volume"] == v["key"]:
                        self.assertEqual(c["role"], v["key"], c["id"])

    def test_已单独成文的标记只给已存在的稿(self):
        # 3 条试点（各 role 一条）标了 standalone；标记与真实文件必须对得上，
        # 否则站点会挂一个「已单独成文」但点开没有的徽章。
        marked = [c for c in self.cases if c.get("standalone")]
        self.assertEqual(len(marked), 3)
        self.assertEqual({c["role"] for c in marked}, ROLES)
        for c in marked:
            art = os.path.join(ROOT, "out", "articles", "%s.html" % c["id"])
            self.assertTrue(os.path.isfile(art), "缺成稿 %s（standalone 标记无据）" % art)

    # ---- 口径 ----
    def test_caliber在枚举内(self):
        for c in self.cases:
            self.assertIn(c["caliber"], CALIBERS, c["id"])

    def test_不是月收入就必须写口径说明(self):
        # 这一条是这个库最贵的教训：那个数看起来像收入，实际是融资/GMV/定价。
        # 不写清「它其实是什么」，下一个人还会照标签读。
        for c in self.cases:
            if c["caliber"] != "mrr":
                self.assertTrue(c.get("caliber_note"), "%s 缺 caliber_note" % c["id"])

    def test_口径待定必须给原因(self):
        for c in self.cases:
            if c.get("needs_verify"):
                self.assertIn(c.get("needs_verify_reason"),
                              ("caliber_undefined", "caliber_conflict"), c["id"])

    def test_口径分歧必须留痕两边(self):
        for c in self.cases:
            cf = c.get("caliber_conflict")
            if not cf:
                continue
            self.assertIn("old", cf, c["id"])
            self.assertIn("now", cf, c["id"])
            self.assertTrue(cf.get("why"), c["id"])
            self.assertNotEqual(cf["old"], cf["now"], c["id"])

    # ---- 对外字段 ----
    def test_摘要不超公众号上限(self):
        for c in self.cases:
            s = c.get("summary")
            if s:
                self.assertLessEqual(len(s), 120, "%s 摘要 %d 字" % (c["id"], len(s)))

    def test_每条都有why_not_copy与栏目名(self):
        for c in self.cases:
            self.assertTrue(c.get("why_not_copy"), c["id"])
            self.assertIn("别照着做", c.get("series_tag", ""), c["id"])

    def test_sources至少有聚合页且带url(self):
        for c in self.cases:
            urls = [s.get("url", "") for s in c.get("sources") or []]
            self.assertTrue(urls, c["id"])
            self.assertTrue(any("howtomakemi.com/cases/" in u for u in urls), c["id"])

    def test_核实等级不许冒充一手(self):
        # 原始媒体没逐条打开，就不许标 official / stripe
        for c in self.cases:
            self.assertNotIn(c.get("verification"), ("official", "stripe"), c["id"])


class TestAntiCasesPipeline(unittest.TestCase):
    """build --anti 这条链路的两个前置约束。"""

    def test_load_anti_cases能读(self):
        import wechat_publish as W
        rows = W.load_anti_cases()
        self.assertEqual(len(rows), 33)
        self.assertIn("id", rows[0])

    def test_anti不占篇号(self):
        # 反面案例走 series_tag，不许在 data/issue_numbers.json 里留下条目
        reg = load(os.path.join(ROOT, "data", "issue_numbers.json"))
        anti_ids = {c["id"] for c in load(ANTI)["cases"]}
        hit = sorted(anti_ids & set(reg))
        self.assertEqual(hit, [], "反向样本被写进篇号台账：%s" % hit)

    def test_摘要优先于one_liner(self):
        # build_one 若回退到 one_liner，会把聚合站的宣传语当摘要（内含金额与「值得抄」语气）
        with io.open(os.path.join(HERE, "wechat_publish.py"), encoding="utf-8") as f:
            src = f.read()
        # 用 assertTrue：assertIn 失败会把整个 5400 行源码打进 traceback（实测 260KB）
        self.assertTrue('c.get("summary") or c.get("one_liner"' in src,
                        "build_one 的摘要回退链被改了：应当 summary 优先，否则反面案例的"
                        "摘要会变成聚合站宣传语（内含金额、语气是「值得抄」）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
