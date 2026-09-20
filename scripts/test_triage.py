"""triage.py 的单元测试。

这一层要守住的核心不变量：

1. **纯函数**：同样的输入永远同样的输出。初筛的结论要能被复现、被引用，
   否则「为什么这条排前面」就说不清。测试直接调用两次比结果。
2. **量级与关注度不能冒充证据**：`疑似金额 $99`（采集器从标题正则抽的）
   不是数字；300 分的 Show HN 帖也不等于有人在付钱。这两类误判一旦漏进
   深核队列，AI 的钱就花在「核一个没有生意的帖子」上。
3. **候选池不由分数判死**。候选池的条目是人工一条条挑进来的，
   机器只能排前后 —— 判 drop 的唯一理由是硬标记（已发布重复）。
4. **材料已齐的条目不该再花 AI**。草稿判定可发布 = 缺的是人点发布，
   不是缺核实。这条是真实数据教出来的：6 份草稿全部已可发布。
5. **零成本补数只认现成脚本**。`backfill` 不是形容词，它给出的必须是一条
   真能跑的命令，否则就是把「人去点开官网看定价」伪装成「自动化」。

历史教训（形成这份测试的直接原因）：第一版把这些判错，于是
「疑似金额」条目冒充有数据（队列深核 7 条里 6 条是假的）、
候选池 27 条人工挑的条目被分数判归档、288 条被判「零成本可补」。
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import triage as T                                              # noqa: E402
import verify_rules as R                                        # noqa: E402
import ai_verify as A                                           # noqa: E402


def rec(**over):
    """一条队列条目的最小样本，测试里只覆盖关心的字段。"""
    base = {
        "id": "x",
        "name": "X",
        "source_url": "https://x.dev/",
        "harvest_source": "hn",
        "source_kind": "self_reported",
        "metrics": {"headline": "未获取"},
    }
    base.update(over)
    return base


def cand(**over):
    """一条候选池条目的最小样本。"""
    base = {
        "id": "c",
        "name": "C",
        "category": "AI 工具",
        "models": ["订阅制"],
        "verification": "partial",
        "metrics": {"headline": "收入 $5K / 售价 $20K"},
        "sources": [],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
class Basics(unittest.TestCase):
    def test_host_of(self):
        self.assertEqual(T.host_of("https://www.Foo.com/a/b"), "foo.com")
        self.assertEqual(T.host_of("http://sub.bar.io:8080/x"), "sub.bar.io")
        self.assertEqual(T.host_of("not-a-url"), "")
        self.assertEqual(T.host_of(None), "")

    def test_norm_name_去掉论坛前缀与标点(self):
        self.assertEqual(T.norm_name("Show HN: Foo Bar"), "foobar")
        self.assertEqual(T.norm_name("Foo-Bar!"), "foobar")
        # 中文必须保留，否则纯中文名会全被归一化成空串互相撞上
        self.assertEqual(T.norm_name("宜兴麻将"), "宜兴麻将")

    def test_name_prefix(self):
        self.assertEqual(T.name_prefix("（待发现）AI 视频竞品"), "待发现")
        self.assertEqual(T.name_prefix("（市场均价观察）TrustMRR 统计"), "市场均价观察")
        self.assertEqual(T.name_prefix("普通名字"), "")
        self.assertEqual(T.name_prefix(""), "")

    def test_norm_url(self):
        self.assertEqual(T.norm_url("HTTPS://WWW.Foo.com/a/"), "foo.com/a")
        self.assertEqual(T.norm_url("https://x.dev/p#frag"), "x.dev/p")

    def test_看起来像原始标题(self):
        self.assertTrue(T.looks_like_raw_title("Show HN: My new thing"))
        self.assertTrue(T.looks_like_raw_title("I built a tool for X"))
        self.assertTrue(T.looks_like_raw_title("a" * 60))
        self.assertFalse(T.looks_like_raw_title("GojiberryAI"))
        self.assertFalse(T.looks_like_raw_title(""))

    def test_疑似金额不算数字(self):
        """采集器自己写着「不是收入」的东西，不能当数字用。"""
        self.assertFalse(T.has_numbers(rec(metrics={"headline": "疑似金额 $99"})))
        self.assertFalse(T.has_numbers(rec(metrics={"headline": "未获取"})))
        self.assertFalse(T.has_numbers(rec(metrics={"headline": "具体数字待补"})))
        self.assertTrue(T.has_numbers(rec(metrics={"headline": "收入 $5K / 月"})))
        self.assertTrue(T.has_numbers(rec(metrics={"mrr": 1200})))
        self.assertTrue(T.has_numbers(rec(trade={"price": "$10K"})))

    def test_文本旁证不算数字(self):
        """**「关于数据的事」不是数据本身。**

        2026-09-20 真实数据里踩到的：hn_discussion 装的是 HN 评论原文，
        评论里写 "v1 API"、"3 years" 都会被当成数字，于是 5 条 headline
        全是「未获取」的帖子凭空多了 16 分（WEIGHTS["has_numbers"]），
        还可能被送进花钱的深核 —— 真正该走的 backfill 反而没人管。

        这条一旦放宽，损失不是分数虚高，是**把钱花在错的地方**。
        """
        # 评论原文里的数字不算
        self.assertFalse(T.has_numbers(rec(metrics={
            "headline": "未获取",
            "hn_discussion": "Java client for TypeSafe AI's System One API (POST /v1/systemone)."})),
            "评论里的 v1 被当成了项目的数字")
        self.assertFalse(T.has_numbers(rec(metrics={
            "headline": "未获取",
            "hn_discussion": "I ran it for 3 years on Wine 8."})),
            "评论里的 3 years 被当成了项目的数字")

        # 来源说明 / 口径说明里的数字不算（日期最容易误命中）
        self.assertFalse(T.has_numbers(rec(metrics={
            "headline": "未获取",
            "provenance": "verified through connected external providers and APIs"})))
        self.assertFalse(T.has_numbers(rec(metrics={
            "headline": "未获取",
            "metric_note": "口径：MRR = 当前月经常性收入；截至 2026-09。"})),
            "口径说明里的日期被当成了数字")

        # 帖子摘录也不算 —— 数字该由数值字段承载，只在摘录里出现说明
        # 采集器没提取到，那正是 backfill 的活
        self.assertFalse(T.has_numbers(rec(metrics={
            "headline": "未获取",
            "story_excerpt": "Post 1 Revenue $200 / mo and $150 MRR"})),
            "摘录里的收入被当成了已提取的数字")

        # 但它们不影响真数字字段 —— 跳过的是旁证，不是整条记录
        self.assertTrue(T.has_numbers(rec(metrics={
            "headline": "未获取", "hn_discussion": "no digits here", "mrr": 1200})))

    def test_文本旁证字段清单别被误删(self):
        """清单本身是规则的一部分，删一个就等于放宽一类误判。"""
        self.assertEqual(set(T.TEXT_ONLY_KEYS),
                         {"hn_discussion", "story_excerpt", "provenance", "metric_note"})

    def test_收入抽取(self):
        self.assertEqual(T.revenue_of(rec(metrics={"headline": "收入 $6.2K / 售价 $15K"})), 6200)
        self.assertEqual(T.revenue_of(rec(metrics={"headline": "$70 MRR / 7 个订阅"})), 70)
        self.assertEqual(T.revenue_of(rec(metrics={"headline": "$1.2M ARR"})), 100000)
        self.assertEqual(T.revenue_of(rec(metrics={"mrr": 8600})), 8600)
        # 抽不到就是 None —— 绝不能因此判「量级太小」
        self.assertIsNone(T.revenue_of(rec(metrics={"headline": "未获取"})))
        self.assertIsNone(T.revenue_of(rec(metrics={"headline": "6 周 $3,400 收入"})))

    def test_近30天收入按契约名读取(self):
        """TrustMRR 的 Current MRR 对非订阅制项目恒为 0，只有近 30 天收入有值。

        读的必须是 data/sources.json 契约里的 last_30d_revenue。这个键一旦
        改名（历史上就叫错过，见 backfill_trustmrr 的 last30 死变量），
        「月流水几万」的条目会静默降级成「没有数字」。
        """
        self.assertEqual(T.revenue_of(rec(metrics={"last_30d_revenue": 79596.73})),
                         79596.73)
        self.assertTrue(T.has_numbers(rec(metrics={"last_30d_revenue": 79596.73})))
        # mrr 存在时优先用 mrr（经常性收入比单月流水更可比）
        self.assertEqual(
            T.revenue_of(rec(metrics={"mrr": 3423, "last_30d_revenue": 2900})), 3423)
        # 只认契约名：内部暂存名不该被当成数据来源
        self.assertIsNone(T.revenue_of(rec(metrics={"revenue_last30d": 79596.73})))

    def test_抽取收入不会抓到售价(self):
        """headline 里同时有收入和售价，抓错那个就把量级判反了。"""
        got = T.revenue_of(rec(metrics={"headline": "收入 $84 / 售价 $6.4K / 倍数 6.4x"}))
        self.assertEqual(got, 84)

    def test_关注度(self):
        self.assertEqual(T.attention_of(rec(note="自动采集（Hacker News，发布于 2026-09-11，3531 分 / 42 条讨论）")),
                         (3531, 42))
        self.assertEqual(T.attention_of(rec(note="无此项")), (None, None))

    def test_来源推断(self):
        self.assertEqual(T.source_of(rec(harvest_source="", source_url="https://trustmrr.com/startup/x")),
                         "trustmrr")
        self.assertEqual(T.source_kind_of(rec(harvest_source="trustmrr", source_kind="")), "verified")
        self.assertEqual(T.source_kind_of(rec(harvest_source="hn", source_kind="")), "self_reported")
        # 认不出来的按最低档，宁可保守
        self.assertEqual(T.source_kind_of(rec(harvest_source="unknown", source_kind="")), "discovery")


# ---------------------------------------------------------------------------
class IndexTest(unittest.TestCase):
    CASES = [{
        "id": "gojiberryai",
        "name": "GojiberryAI",
        "sources": [{"url": "https://trustmrr.com/startup/superfruits", "kind": "stripe"},
                    {"url": "https://gojiberry.ai/", "kind": "official"}],
    }]

    def test_靠官网域名认出重复(self):
        """案例的官网在 sources[].url 里，source_url 字段是空的 ——
        只读 source_url 的话这个索引等于没建（第一版就是这么错的）。"""
        idx = T.Index(cases=self.CASES)
        self.assertEqual(idx.duplicate_of(rec(id="y", name="Y",
                                              source_url="https://gojiberry.ai/pricing"), "inbox"),
                         "官网与已发布案例相同")

    def test_采集站不同页面不算重复(self):
        """TrustMRR 的 /marketplace 和 /startup/<slug> 同域名不同页，不是同一条。"""
        idx = T.Index(cases=self.CASES)
        self.assertIsNone(idx.duplicate_of(
            rec(id="z", name="Z", source_url="https://trustmrr.com/marketplace"), "inbox"))

    def test_同来源页算重复(self):
        idx = T.Index(cases=self.CASES)
        self.assertEqual(idx.duplicate_of(
            rec(id="w", name="W", source_url="https://trustmrr.com/startup/superfruits"), "inbox"),
            "来源页与已发布案例是同一个")

    def test_按名字与id认重复(self):
        idx = T.Index(cases=self.CASES)
        self.assertEqual(idx.duplicate_of(rec(id="gojiberryai", name="别的"), "inbox"),
                         "已发布成案例")
        self.assertEqual(idx.duplicate_of(rec(id="zz", name="Show HN: GojiberryAI"), "inbox"),
                         "与已发布案例同名")

    def test_已在候选池(self):
        idx = T.Index(candidates=[{"id": "cometly", "name": "Cometly"}])
        self.assertEqual(idx.duplicate_of(rec(id="cometly", name="Cometly"), "inbox"),
                         "已在候选池，不必再进一次队列")

    def test_草稿判定(self):
        draft = {"gates": [g["key"] for g in R.GATES],
                 "musts": [m["key"] for m in R.MUSTS],
                 "verification": "official", "source_kinds": ["official"], "caliber": "arr"}
        idx = T.Index(drafts={"c": draft})
        _, verdict = idx.draft_of({"id": "c"})
        self.assertTrue(verdict["publishable"])
        _, none_verdict = T.Index().draft_of({"id": "c"})
        self.assertIsNone(none_verdict)

    def test_同名分组(self):
        rows = [{"id": "a", "name": "Show HN: Foo"}, {"id": "b", "name": "Foo"},
                {"id": "c", "name": "Bar"}]
        self.assertEqual(T.name_collisions(rows), [("foo", ["a", "b"])])


# ---------------------------------------------------------------------------
class Grading(unittest.TestCase):
    def test_有数字的候选_值得深核(self):
        r = T.score_record(cand(), "candidate")
        self.assertEqual(r["grade"], "deep")

    def test_量级太小_不深核(self):
        r = T.score_record(cand(metrics={"headline": "收入 $84 / 售价 $6.4K"}), "candidate")
        self.assertEqual(r["grade"], "later")
        self.assertIn("tiny_revenue", {m["key"] for m in r["misses"]})

    def test_量级刚好过线_深核(self):
        r = T.score_record(cand(metrics={"headline": "收入 $1.1K / 售价 $30K"}), "candidate")
        self.assertEqual(r["grade"], "deep")

    def test_人标注体量太小_不深核(self):
        r = T.score_record(cand(blocking="体量太小，暂不精写"), "candidate")
        self.assertNotEqual(r["grade"], "deep")

    def test_候选池不由分数判死(self):
        """人工挑进来的条目：没数字也只是「先放着」，不能建议归档。"""
        r = T.score_record(cand(metrics={"headline": "未获取"}, verification="unverified",
                                sources=[], models=[], category="未分类"), "candidate")
        self.assertEqual(r["grade"], "later")

    def test_候选池唯一的drop理由是重复(self):
        idx = T.Index(cases=[{"id": "c", "name": "C"}])
        r = T.score_record(cand(), "candidate", idx)
        self.assertEqual(r["grade"], "drop")
        self.assertEqual(r["flags"][0]["key"], "duplicate")

    def test_材料已齐_压过深核(self):
        """草稿判定可发布 → ready，不再花 AI。"""
        draft = {"gates": [g["key"] for g in R.GATES],
                 "musts": [m["key"] for m in R.MUSTS],
                 "verification": "official", "source_kinds": ["official"], "caliber": "arr"}
        r = T.score_record(cand(), "candidate", T.Index(drafts={"c": draft}))
        self.assertEqual(r["grade"], "ready")
        self.assertTrue(r["ready"])
        self.assertIn("别再花 AI", r["action"])

    def test_占位与参考条目_进later不进drop(self):
        """作者故意留在池子里的占位/参考条目，不该被建议归档。"""
        ph = T.score_record(cand(name="（待发现）AI 视频竞品"), "candidate")
        self.assertEqual(ph["grade"], "later")
        self.assertIn("placeholder", {f["key"] for f in ph["flags"]})

        ref = T.score_record(cand(name="（基准线）2 人团队收入分布"), "candidate")
        self.assertEqual(ref["grade"], "later")
        self.assertIn("reference", {f["key"] for f in ref["flags"]})

    def test_trustmrr缺数字_判backfill并给出可执行命令(self):
        r = T.score_record(rec(id="t", name="T", harvest_source="trustmrr",
                               source_kind="verified",
                               metrics={"headline": "TrustMRR 验证收入榜条目（具体数字待补）"}),
                           "inbox")
        self.assertEqual(r["grade"], "backfill")
        self.assertIn("backfill_trustmrr.py", r["action"])
        self.assertIn("--only inbox", r["action"])

    def test_有官网但缺数字_不冒充零成本可补(self):
        """打开官网看定价是人去点的活，没有脚本可跑 —— 只能算 later + 线索。

        用一条高关注度的条目来测：没有关注度的「有官网但没数字」条目该判归档
        （队列里这种有 245 条，它们对「收入案例库」就是噪音），
        所以只有先被关注度救进 later 的条目，才轮得到这条 hint。
        """
        r = T.score_record(rec(id="h", name="H", metrics={"headline": "未获取"},
                               source_url="https://somewhere.dev/",
                               note="自动采集（Hacker News，发布于 2026-09-11，321 分 / 0 条讨论）"),
                           "inbox")
        self.assertEqual(r["grade"], "later")
        self.assertNotIn("backfill_kind", r)
        self.assertIn("官网", r["hint"])

    def test_没数字没关注度_判归档(self):
        """有官网不等于有生意 —— 没有数字也没有人讨论的落地页，归档。"""
        r = T.score_record(rec(id="h2", name="H2", metrics={"headline": "未获取"},
                               source_url="https://somewhere.dev/",
                               note="自动采集（Hacker News，发布于 2026-09-11，1 分 / 0 条讨论）"),
                           "inbox")
        self.assertEqual(r["grade"], "drop")

    def test_有真实数字就不归档(self):
        """数字是这数据集里最稀缺的东西，多小都不该被当噪音扫掉。"""
        r = T.score_record(rec(id="t", name="T", harvest_source="indiehackers",
                               source_kind="self_reported",
                               metrics={"headline": "自报收入 $25 / mo"}),
                           "inbox")
        self.assertEqual(r["grade"], "later")
        self.assertIn("tiny_revenue", {m["key"] for m in r["misses"]})

    def test_只指向代码仓库_扣分(self):
        r = T.score_record(rec(id="g", name="G", source_url="https://github.com/a/b"),
                           "inbox")
        self.assertIn("code_only", {m["key"] for m in r["misses"]})

    def test_队列噪音判归档(self):
        r = T.score_record(rec(id="n", name="Show HN: a toy", source_url="https://github.com/a/b",
                               note="自动采集（Hacker News，发布于 2026-09-11，1 分 / 0 条讨论）"),
                           "inbox")
        self.assertEqual(r["grade"], "drop")

    def test_高关注度免于被判噪音(self):
        """没数字仍然没数字，但 300 分的帖子至少值得人扫一眼。"""
        r = T.score_record(rec(id="hi", name="Show HN: a toy", source_url="https://github.com/a/b",
                               note="自动采集（Hacker News，发布于 2026-09-11，321 分 / 0 条讨论）"),
                           "inbox")
        self.assertEqual(r["grade"], "later")

    def test_疑似金额不给深核(self):
        r = T.score_record(rec(id="s", name="S", metrics={"headline": "疑似金额 $10k"},
                               source_url="https://s.dev/"), "inbox")
        self.assertNotEqual(r["grade"], "deep")
        self.assertIn("suspect_amount", {f["key"] for f in r["flags"]})

    def test_声称一手但没来源链接_要标记(self):
        r = T.score_record(cand(verification="stripe", sources=[]), "candidate")
        self.assertIn("missing_source_url", {f["key"] for f in r["flags"]})
        self.assertIn("first_hand_claimed", {h["key"] for h in r["hits"]})


# ---------------------------------------------------------------------------
class TierAdvice(unittest.TestCase):
    def test_候选池走规则引擎同一套政策(self):
        r = T.score_record(cand(sources=[{"url": "https://trustmrr.com/startup/x",
                                          "kind": "stripe"}]), "candidate")
        self.assertEqual(r["tier"], R.TIER_PREMIUM)
        self.assertEqual(r["tier"], R.default_case_tier(
            cand(sources=[{"url": "https://trustmrr.com/startup/x", "kind": "stripe"}]))[0])

    def test_队列验证源补完数字才谈档位(self):
        withnum = T.score_record(rec(id="a", name="A", harvest_source="trustmrr",
                                     source_kind="verified",
                                     metrics={"headline": "月收入 $58k"}), "inbox")
        self.assertEqual(withnum["tier"], R.TIER_PREMIUM)
        without = T.score_record(rec(id="b", name="B", harvest_source="trustmrr",
                                     source_kind="verified",
                                     metrics={"headline": "待补"}), "inbox")
        self.assertIsNone(without["tier"])
        self.assertIn("补完才谈得上档位", without["tier_reason"])

    def test_队列自报条目不定档(self):
        r = T.score_record(rec(id="c", name="C", metrics={"headline": "收入 $5K"}), "inbox")
        self.assertIsNone(r["tier"])
        self.assertIn("深核之后", r["tier_reason"])


# ---------------------------------------------------------------------------
class Purity(unittest.TestCase):
    def test_同输入同输出(self):
        """纯函数：结论要能被复现，否则「为什么排前面」说不清。"""
        rows = [cand(), rec(id="r", name="R")]
        first = [T.score_record(r, "candidate" if "verification" in r else "inbox")
                 for r in rows]
        second = [T.score_record(r, "candidate" if "verification" in r else "inbox")
                  for r in rows]
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True),
                         json.dumps(second, ensure_ascii=False, sort_keys=True))

    def test_空记录不炸(self):
        r = T.score_record({}, "inbox")
        self.assertIn(r["grade"], T.GRADE_ORDER)
        self.assertEqual(r["score"], 0)
        r2 = T.score_record({}, "candidate")
        self.assertIn(r2["grade"], T.GRADE_ORDER)

    def test_分数被夹在0到100(self):
        r = T.score_record(cand(sources=[{"url": "https://x.dev/", "kind": "stripe"},
                                         {"url": "https://y.dev/", "kind": "official"}]),
                           "candidate")
        self.assertLessEqual(r["score"], 100)
        self.assertGreaterEqual(r["score"], 0)


# ---------------------------------------------------------------------------
class Aggregate(unittest.TestCase):
    def test_汇总与逐条一致(self):
        cands = [cand(), cand(id="c2", metrics={"headline": "收入 $84"})]
        inbox = [rec(id="i1", name="I1")]
        out = T.triage_all(cands, inbox, T.Index())
        self.assertEqual(out["summary"]["total"], 3)
        self.assertEqual(sum(out["summary"]["by_grade"].values()), 3)
        for scope in ("candidate", "inbox"):
            sub = [i for i in out["items"] if i["scope"] == scope]
            self.assertEqual(out["summary"]["by_scope"][scope]["total"], len(sub))
            self.assertEqual(sum(out["summary"]["by_scope"][scope]["by_grade"].values()),
                             len(sub))

    def test_每条都带grade与action(self):
        out = T.triage_all([cand()], [rec()], T.Index())
        for i in out["items"]:
            self.assertIn(i["grade"], T.GRADE_ORDER)
            self.assertTrue(i["action"])
            self.assertEqual(i["grade_label"], T.GRADE_LABEL[i["grade"]])


class RealData(unittest.TestCase):
    """跑真实数据的不变量。数据会变，但下面这几条不该变。"""

    @classmethod
    def setUpClass(cls):
        cls.data = T.load_all()
        cls.out = T.triage_all(cls.data["candidates"], cls.data["inbox"],
                               T.build_index(cls.data))

    def test_每条都有档位与动作(self):
        for i in self.out["items"]:
            self.assertTrue(i["grade"], i["id"])
            self.assertTrue(i["action"], i["id"])

    def test_候选池一条都不判归档(self):
        bad = [i["id"] for i in self.out["items"]
               if i["scope"] == "candidate" and i["grade"] == "drop"]
        self.assertEqual(bad, [], "候选池被判归档：%s" % bad)

    def test_判归档的都没数字(self):
        """噪音的定义是「没有可核的东西」。有数字还判归档，说明规则写歪了。"""
        bad = [i["id"] for i in self.out["items"]
               if i["grade"] == "drop" and T.has_numbers(
                   next(x for x in (self.data["inbox"] + self.data["candidates"])
                        if x.get("id") == i["id"]))]
        self.assertEqual(bad, [], "有数字却被判归档：%s" % bad)

    def test_深核的都有数字(self):
        bad = []
        for i in self.out["items"]:
            if i["grade"] != "deep":
                continue
            src = self.data["inbox"] if i["scope"] == "inbox" else self.data["candidates"]
            if not T.has_numbers(next(x for x in src if x.get("id") == i["id"])):
                bad.append(i["id"])
        self.assertEqual(bad, [], "没有数字却进了深核：%s" % bad)

    def test_backfill给出的都是跑得起来的命令(self):
        for i in self.out["items"]:
            if i["grade"] == "backfill":
                self.assertIn("scripts/backfill_trustmrr.py", i["action"], i["id"])

    def test_来源性质口径与采集器一致(self):
        """triage 的四个来源性质必须和 harvest 的图例同集合 ——
        两边各写一份，早晚会飘。"""
        import harvest as H
        self.assertEqual(sorted(T.SOURCE_KINDS), sorted(H.KIND_LABEL.keys()))


# ---------------------------------------------------------------------------
class AiVerifyIntegration(unittest.TestCase):
    """ai_verify 消费初筛排序 —— 这几条钉住「选取顺序」的语义。"""

    def test_按初筛分排序(self):
        """有数字、量级够的排在没有数字的前面。"""
        good = cand(id="good", sources=[{"url": "https://good.dev/", "kind": "stripe"}])
        weak = cand(id="weak", verification="unverified", sources=[],
                    metrics={"headline": "未获取"}, models=[], category="未分类")
        got = [c["id"] for c in A.pick_candidates([weak, good])]
        self.assertEqual(got[0], "good")

    def test_材料已齐的跳过(self):
        draft = {"gates": [g["key"] for g in R.GATES],
                 "musts": [m["key"] for m in R.MUSTS],
                 "verification": "official", "source_kinds": ["official"], "caliber": "arr"}
        can = cand(id="done")
        idx = T.Index(drafts={"done": draft})
        self.assertEqual(A.pick_candidates([can], index=idx), [])
        # 显式点名仍要处理它（人说了算）
        self.assertEqual([c["id"] for c in A.pick_candidates([can], ids=["done"], index=idx)],
                         ["done"])

    def test_人工标的优先级压过机器分(self):
        """人写的「高优先级复核」必须置顶 —— 机器分再高也不能盖过人的判断。"""
        top = cand(id="top", blocking="高优先级复核：榜单第一名",
                   metrics={"headline": "收入 $1.2K / 售价 $50K"})
        normal = cand(id="normal", sources=[{"url": "https://n.dev/", "kind": "stripe"}])
        self.assertEqual([c["id"] for c in A.pick_candidates([normal, top])],
                         ["top", "normal"])

    def test_plan带初筛结论(self):
        plan = A.plan_data()
        self.assertTrue(plan["items"])
        for it in plan["items"]:
            self.assertIn("grade", it)
            self.assertIn("score", it)
            self.assertIn("action", it)


if __name__ == "__main__":
    unittest.main()
