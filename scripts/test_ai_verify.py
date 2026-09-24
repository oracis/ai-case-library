"""ai_verify.py 的单元测试（全程离线，不联网、不碰 LLM）。

要守住的不变量：
1. AI 的布尔回答必须配上结构性证据才能变成勾 —— 不能裸信 LLM。
2. 门槛是三态：AI 明确说 no 才记进 gates_denied；拿不准必须落到「未确认」，
   绝不能被当成 false 而判死候选。反证只在一道都没成立时才构成否决（见第 5 条）。
3. human_read 在非 --publish 模式下永远不自动勾。
4. JSON 提取要容忍 ```json 围栏与前后废话。
5. 门槛是「一道成立就进库」：一道 yes 就放行，AI 的反证降级成提醒；
   只有「一道都没成立 + 有明确反证」才判不进库。

放宽的一条（2026-09-16）：没有一手来源不再一票否决，只进「待人工复核」提醒。
AI 没找到官网不代表这条数字是假的 —— 那是人该判的事，不是替人判死。
但提醒必须留着，本库所有数字错误都出在中转述这一层。

放宽的另一条（2026-09-17）：human_read 从「拦发布」降级成「标记 + 提醒」。
原来它一拦，每条候选都卡在「还差 1 项」，而勾完之后案例上什么都不留，
等于每次提升都要人点一下、换来的却是一份阅后即焚的承诺。现在不勾也能进库，
但会在 warnings 里留 pending_human_read，并把 human_read=False 存到案例上。
第 3 条不变量（AI 不得代勾）没变 —— 放宽的是它拦不拦，不是谁能勾。
"""

import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A                                              # noqa: E402
import verify_rules as R                                           # noqa: E402


CAND = {
    "id": "gojiberry-ai",
    "name": "GojiberryAI",
    "name_en": "GojiberryAI",
    "one_liner": "面向 B2B 的 AI CRM 平台",
    "metrics": {"headline": "$424,368（TrustMRR 榜单快照）"},
    "blocking": "口径待核：需打开 TrustMRR 公司页确认是 MRR 还是 all-time",
    "playbook": [],
}


def good_ai(**over):
    """一份「证据齐全」的 AI 回答，测试里只覆盖关心的那项。"""
    ai = {
        "still_alive": True, "still_alive_evidence": "官网 2026 年仍在更新",
        "is_business": True, "is_business_evidence": "订阅制 CRM",
        "solo_possible": True, "solo_possible_evidence": "SaaS 自助注册",
        "verification": "stripe",
        "caliber": "mrr", "caliber_reason": "TrustMRR 连 Stripe 实时同步",
        "caliber_consistent": True,
        "number": "$424,368 MRR",
        "sources": [
            {"label": "TrustMRR 公司页", "url": "https://trustmrr.com/company/gojiberryai",
             "kind": "stripe"},
            {"label": "官网定价页", "url": "https://gojiberry.ai/pricing", "kind": "official"},
        ],
        "corrections": [],
        "bonus": {"founder_disclosure": True, "pricing_confirmed": True,
                  "replicable_low": True, "secondary_corroborated": True},
        "summary": "B2B AI CRM",
        "confidence": 0.8,
    }
    ai.update(over)
    return ai


class PickTest(unittest.TestCase):
    def test_占位条目_未获取_体量太小_自动跳过(self):
        cands = [
            dict(CAND, id="a"),
            dict(CAND, id="b", name="（待发现）竞品组"),
            dict(CAND, id="c", metrics={"headline": "未获取"}),
            dict(CAND, id="d", blocking="体量太小，暂不精写"),
        ]
        got = [c["id"] for c in A.pick_candidates(cands)]
        self.assertEqual(got, ["a"])

    def test_高优先级排前面(self):
        cands = [
            dict(CAND, id="normal", blocking="口径待核"),
            dict(CAND, id="top", blocking="高优先级复核：榜单第一名"),
        ]
        got = [c["id"] for c in A.pick_candidates(cands)]
        self.assertEqual(got, ["top", "normal"])

    def test_include_small_可放宽(self):
        cands = [dict(CAND, id="d", blocking="体量太小，暂不精写")]
        got = [c["id"] for c in A.pick_candidates(cands, include_small=True)]
        self.assertEqual(got, ["d"])

    def test_limit_生效(self):
        cands = [dict(CAND, id="c%d" % i) for i in range(10)]
        self.assertEqual(len(A.pick_candidates(cands, limit=3)), 3)


class MapTest(unittest.TestCase):
    def test_齐全回答_可发布但挂着未核读提醒(self):
        draft = A.map_ai_to_draft(CAND, good_ai(), publish_mode=False)
        r = R.evaluate(draft)
        # AI 把研究类的活全干完 → 没有拦发布的必填缺口了。
        # 「亲自看过原文」仍然留给人工勾，但 2026-09-17 起它是**标记不是闸门**：
        # 不勾也能进库，代价是挂一条 pending_human_read 提醒、且案例上
        # human_read 落成 False，让「这条没人核过」看得见。
        self.assertEqual([m["key"] for m in r["missing"]], [])
        self.assertTrue(r["publishable"])
        self.assertFalse(r["human_read"])
        self.assertIn("pending_human_read", [w["key"] for w in r["warnings"]])
        self.assertEqual(r["tier"], R.TIER_PREMIUM)   # 质量分 70，够精品

    def test_没有一手来源_只提醒不否决(self):
        # 放开之后：没有一手来源不再进 missing 拦发布，只在 warnings 里标出来
        ai = good_ai(sources=[
            {"label": "公众号转述", "url": "https://mp.weixin.qq.com/s/x", "kind": "secondary"}])
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        r = R.evaluate(draft)
        self.assertNotIn("primary_source", draft["musts"])
        keys = [w["key"] for w in r["warnings"]]
        self.assertIn("primary_source_kind", keys)
        self.assertIn("secondary_only", keys)
        # 没有拦发布的缺口的；只剩「没人核读过」这类提醒
        self.assertEqual([m["key"] for m in r["missing"]], [])
        self.assertTrue(r["publishable"])

    def test_没有一手来源_发布模式也能发(self):
        # 用户的原话：确实没有一手来源，也让它通过核查（由人复核后发布）
        ai = good_ai(sources=[
            {"label": "媒体报道", "url": "https://techcrunch.com/2026/a", "kind": "press"}],
            confidence=0.8)
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=True)
        r = R.evaluate(draft)
        self.assertTrue(r["publishable"])
        self.assertIn("primary_source_kind", [w["key"] for w in r["warnings"]])
        self.assertIn("待人工复核", r["verdict"])

    def test_门槛三态_yes_no_unknown(self):
        ai = good_ai(still_alive="yes", is_business="no", solo_possible="unknown")
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertIn("still_alive", draft["gates"])
        self.assertEqual(draft["gates_denied"], ["is_business"])
        # unknown 两边都不进 —— 它是「没核到」，不是「被否」
        self.assertNotIn("solo_possible", draft["gates"])
        self.assertNotIn("solo_possible", draft["gates_denied"])

    def test_一道成立_AI反证不判死只提醒(self):
        # 本轮核心改动：still_alive=no，但另两道成立 → 放行，反证转成提醒
        draft = A.map_ai_to_draft(CAND, good_ai(still_alive="no"), publish_mode=True)
        r = R.evaluate(draft)
        self.assertTrue(r["publishable"])
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertIn("denied_still_alive", [w["key"] for w in r["warnings"]])
        self.assertIn("含 1 项 AI 反证", r["verdict"])

    def test_三道全否_才判不进库(self):
        # 一道都没成立，AI 还拿到了三份明确反证 —— 这种进库是污染，必须拦
        draft = A.map_ai_to_draft(
            CAND, good_ai(still_alive="no", is_business="no", solo_possible="no"),
            publish_mode=True)
        r = R.evaluate(draft)
        self.assertFalse(r["publishable"])
        self.assertIn("不进库", r["verdict"])
        self.assertEqual([g["key"] for g in r["gates_failed"]],
                         ["still_alive", "is_business", "solo_possible"])

    def test_门槛unknown_不判死(self):
        ai = good_ai(still_alive="unknown", is_business="unknown", solo_possible="unknown")
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        r = R.evaluate(draft)
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertEqual(len(r["unverified_gates"]), len(R.GATES))

    def test_布尔False_按拿不准处理(self):
        # 老提示词教过模型「不确定就 false」。若把 False 当反证，
        # 这次修好的批量误杀会原样复发 —— 它必须落到「未确认」。
        draft = A.map_ai_to_draft(CAND, good_ai(is_business=False), publish_mode=False)
        r = R.evaluate(draft)
        self.assertEqual(draft["gates_denied"], [])
        self.assertEqual(len(r["gates_failed"]), 0)
        self.assertIn("is_business", [g["key"] for g in r["unverified_gates"]])

    def test_no_大小写都算否决(self):
        draft = A.map_ai_to_draft(CAND, good_ai(still_alive="NO"), publish_mode=False)
        self.assertEqual(draft["gates_denied"], ["still_alive"])

    def test_编造的url_进不了草稿(self):
        ai = good_ai(sources=[
            {"label": "我编的", "url": "不是链接", "kind": "official"},
            {"label": "官网", "url": "https://gojiberry.ai", "kind": "official"},
        ])
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertEqual(len(draft["sources"]), 1)

    def test_human_read_默认不自动勾(self):
        draft = A.map_ai_to_draft(CAND, good_ai(), publish_mode=False)
        self.assertNotIn("human_read", draft["musts"])

    def test_publish模式也不代勾human_read(self):
        """2026-09-17 改：--publish 不再代勾，任何模式都不勾。

        之前 publish_mode 会代勾，那是因为人工核读拦发布、不勾就发不出去。
        现在它不拦了，案例上还会把它和核读时间戳一起存下来当凭据 ——
        AI 再代勾就只剩「伪造一条人工核读记录」这一个效果。
        """
        for mode in (False, True):
            draft = A.map_ai_to_draft(CAND, good_ai(), publish_mode=mode)
            self.assertNotIn("human_read", draft["musts"],
                             "publish_mode=%s 时代勾了" % mode)
            self.assertNotIn("AI 代勾", draft["note"])

    def test_人以前勾过的human_read不丢(self):
        draft = A.map_ai_to_draft(
            dict(CAND, _old_musts=["human_read"]), good_ai(), publish_mode=False)
        self.assertIn("human_read", draft["musts"])

    def test_bonus要和来源对得上(self):
        # AI 说 secondary_corroborated，但来源只有一个域名 → 不给分
        ai = good_ai(sources=[
            {"label": "A", "url": "https://trustmrr.com/company/x", "kind": "stripe"},
            {"label": "B", "url": "https://trustmrr.com/company/y", "kind": "press"},
        ])
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertNotIn("secondary_corroborated", draft["bonus"])

    def test_stripe来源_等级必须是stripe(self):
        ai = good_ai(verification="official")
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertEqual(draft["verification"], "stripe")

    def test_有修正表_corrections_found自动给分(self):
        ai = good_ai(corrections=[
            {"claim": "累计收入 $424K", "truth": "是 MRR", "source": "https://trustmrr.com/company/gojiberryai"}])
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertIn("corrections_found", draft["bonus"])

    def test_未知枚举一律回落(self):
        ai = good_ai(caliber="周收入", bonus={"nope": True})
        draft = A.map_ai_to_draft(CAND, ai, publish_mode=False)
        self.assertEqual(draft["caliber"], "")
        self.assertEqual([b for b in draft["bonus"] if b not in R.BONUS_KEYS], [])

    def test_等级由来源构成推导(self):
        # 来源里有 stripe → 等级就是 stripe，AI 说什么不算
        self.assertEqual(A.map_ai_to_draft(CAND, good_ai(verification="official"),
                                           publish_mode=False)["verification"], "stripe")
        # AI 嘴上说 stripe、来源里没有 → 降回 partial
        ai = good_ai(verification="stripe", sources=[
            {"label": "创始人推文", "url": "https://x.com/a", "kind": "founder"}])
        self.assertEqual(A.map_ai_to_draft(CAND, ai, publish_mode=False)["verification"],
                         "partial")
        # 没有一手来源、AI 枚举也非法 → partial
        ai = good_ai(verification="随便", sources=[
            {"label": "公众号", "url": "https://mp.weixin.qq.com/s/x", "kind": "secondary"}])
        self.assertEqual(A.map_ai_to_draft(CAND, ai, publish_mode=False)["verification"],
                         "partial")


class ExtractTest(unittest.TestCase):
    def test_裸json(self):
        self.assertEqual(A.extract_json('{"a":1}'), {"a": 1})

    def test_围栏加废话(self):
        text = '好的，以下是结果：\n```json\n{"a": {"b": 2}}\n```\n希望有帮助'
        self.assertEqual(A.extract_json(text), {"a": {"b": 2}})

    def test_没有json要报错(self):
        with self.assertRaises(ValueError):
            A.extract_json("我不知道")


class QueryTest(unittest.TestCase):
    def test_TrustMRR来源_会加一条定向搜索(self):
        qs = A.build_queries(CAND)
        self.assertTrue(any("TrustMRR" in q for q in qs))
        self.assertTrue(len(qs) <= 4)


class KnownUrlTest(unittest.TestCase):
    """抓取顺序：**先抓候选自带的已知地址，不够再用搜索补**。

    起因（2026-09-17，gojiberryai 实测）：原实现只走搜索，而 Bing 对这个
    产品**根本没收录** —— 查 GojiberryAI 返回的是「无结果」兜底模块（一堆
    高校页面）。AI 拿到 5 页无关原文，只能诚实地说「全部指标均无法核实」，
    于是 0 来源 / 置信度 3% / 质量分 0。而候选自己就带着 TrustMRR 详情页
    （3.4k 字正文，含 MRR 与 Stripe 验证），那才是最有价值的原文。

    所以顺序很关键：已知地址在前，否则搜索返回的垃圾会把 5 个名额占满。
    """

    def setUp(self):
        """本类所有用例都靠 stub 网络栈，浏览器兜底层也一并 stub 掉。

        否则 collect_pages 里新增的「常规抓取失败 → 无头 Chrome 再试」会真的
        拉起本机 Chrome 去访问 bad.example / spa.example，测试就不再封闭了。
        """
        self._render = A.render_page
        A.render_page = lambda *a, **kw: None

    def tearDown(self):
        A.render_page = self._render

    def test_已知地址_来源详情页在前官网在后(self):
        cand = {"source_url": "https://trustmrr.com/startup/superfruits",
                "website": "https://gojiberry.ai/"}
        # 2026-09-24：TrustMRR 挂牌页一律换成官方 .md（干净且含挂牌价/倍数），
        # 原 HTML 留在后面当 .md 拿不到时的兜底。
        self.assertEqual(A.known_urls(cand),
                         ["https://trustmrr.com/startup/superfruits.md",
                          "https://trustmrr.com/startup/superfruits",
                          "https://gojiberry.ai/"])

    def test_已知地址_带上sources里的url(self):
        cand = {"website": "https://a.example/",
                "sources": [{"url": "https://b.example/x"},
                            {"url": "https://a.example/"},        # 与 website 重复
                            {"label": "没 url 的"}],
                "source_url": ""}
        self.assertEqual(A.known_urls(cand),
                         ["https://a.example/", "https://b.example/x"])

    def test_已知地址_去重且只收http(self):
        cand = {"website": "gojiberry.ai",              # 没协议头，不收
                "source_url": "https://x.example/",
                "sources": [{"url": "https://x.example/"}, {"url": ""}]}
        self.assertEqual(A.known_urls(cand), ["https://x.example/"])

    def test_已知地址_空候选不炸(self):
        for c in ({}, {"website": None, "sources": None},
                  {"sources": ["不是 dict"]}):
            self.assertEqual(A.known_urls(c), [])

    def test_抓取顺序_已知地址先于搜索(self):
        """核心断言：已知地址排前面，搜索排在后面补。"""
        order = []
        cand = {"source_url": "https://trustmrr.com/startup/superfruits",
                "metrics": {"headline": "x"}}
        orig = (A.fetch_text, A.search, A.build_queries)
        try:
            def fake_fetch(u, cap=6000):
                order.append(("fetch", u))
                return "x" * 500
            A.fetch_text = fake_fetch
            A.search = lambda q, per_query=6: (order.append(("search", q)),
                                               ["https://junk.example/1"])[1]
            A.build_queries = lambda c: ["q1"]
            pages, used = A.collect_pages(cand, max_pages=5)
            kinds = [k for k, _ in order]
            self.assertEqual(kinds[0], "fetch", order)
            self.assertEqual(order[0][1],
                             "https://trustmrr.com/startup/superfruits.md")
            self.assertIn("search", kinds, "已知地址不够时应当回落搜索")
        finally:
            (A.fetch_text, A.search, A.build_queries) = orig

    def test_已知地址够数_就不再搜索(self):
        cand = {"website": "https://a.example/", "source_url": "https://b.example/"}
        orig = (A.fetch_text, A.search)
        searched = []
        try:
            A.fetch_text = lambda u, cap=6000: "x" * 500
            A.search = lambda q, per_query=6: searched.append(q) or []
            A.collect_pages(cand, max_pages=2)
            self.assertEqual(searched, [], "已知地址已够，不该再打搜索")
        finally:
            (A.fetch_text, A.search) = orig

    def test_抓不到的那个_不拦住后面的来源(self):
        """fetch_text 的契约是「失败返回 None」，不是抛异常。"""
        cand = {"website": "https://bad.example/", "source_url": "https://ok.example/"}
        orig = (A.fetch_text, A.search)
        try:
            A.fetch_text = lambda u, cap=6000: None if "bad" in u else "y" * 500
            A.search = lambda q, per_query=6: []          # 别让测试真去联网
            pages, used = A.collect_pages(cand, max_pages=5)
            self.assertEqual(used, ["https://ok.example/"])
            self.assertEqual(len(pages), 1)
        finally:
            (A.fetch_text, A.search) = orig

    def test_抓不到正文的不要(self):
        """fetch_text 的契约：真没东西可给时返回 None。

        「太短就不要」这道判定已经收进 fetch_text 里了（它要区分「纯空壳」和
        「空壳但有 meta 摘要」），collect_pages 只认 None / 非 None。
        """
        cand = {"website": "https://spa.example/", "source_url": "https://ok.example/"}
        orig = (A.fetch_text, A.search)
        try:
            A.fetch_text = lambda u, cap=6000: None if "spa" in u else "y" * 500
            A.search = lambda q, per_query=6: []
            pages, used = A.collect_pages(cand, max_pages=5)
            self.assertEqual(used, ["https://ok.example/"])
            self.assertEqual(len(pages), 1)
        finally:
            (A.fetch_text, A.search) = orig

    def test_中文二手转载站一律拦掉(self):
        """2026-09-24 第二轮：mort / stealth-company 那轮混进来的这几个。

        项目原则是「一手证据 > 二手转述」，这类站的出现基本等于 cn.bing 没搜到
        真来源时的兜底噪声。宁可让 AI 说「无法核实」，也不能拿中文转载冒充证据。
        """
        junk = ("https://www.zhihu.com/explore",
                "https://blog.gitcode.com/24fb56a9f104535e87f55ef2f3f89699.html",
                "https://uuyc.163.com/features/work/",
                "https://www.ai-bot.cn/union-alpha/",
                "https://www.mcpworld.com/zh/detail/7dffd9dccaa084a06a66cdec269be03d",
                "https://blog.csdn.net/x", "https://www.jianshu.com/p/x",
                "https://juejin.cn/post/x", "https://www.cnblogs.com/a/p/x.html")
        for u in junk:
            self.assertTrue(A.is_irrelevant_source(u), "%s 没被拦住" % u)
        # 别误伤英文名串糖葫芦的正常域名
        for u in ("https://www.stripe.com/", "https://trustmrr.com/startup/x",
                  "https://example.com/"):
            self.assertFalse(A.is_irrelevant_source(u), "%s 被误伤" % u)

    def test_搜索命中里的垃圾不占名额(self):
        """黑名单必须在这里就生效。

        以前它只在 map_ai_to_draft 阶段过滤，那时名额早被占掉了 ——
        实测 stan 那轮 5 个名额里 4 个给了音乐站与 baidu 问答，
        真正该读的来源反而没位置。
        """
        cand = {"website": "https://a.example/", "metrics": {"headline": "x"}}
        orig = (A.fetch_text, A.search, A.build_queries)
        fetched = []
        try:
            A.fetch_text = lambda u, cap=6000: (fetched.append(u), "x" * 500)[1]
            A.build_queries = lambda c: ["q1"]
            A.search = lambda q, per_query=6: [
                "https://music.163.com/song?id=1",
                "https://y.qq.com/n/ryqq/mv/x",
                "https://real.example/report",
            ]
            pages, used = A.collect_pages(cand, max_pages=5)
            self.assertNotIn("https://music.163.com/song?id=1", used)
            self.assertNotIn("https://y.qq.com/n/ryqq/mv/x", used)
            self.assertIn("https://real.example/report", used)
            # 关键：垃圾连抓都不该抓 —— 既不浪费请求，也不占那 5 个名额
            self.assertNotIn("https://music.163.com/song?id=1", fetched)
        finally:
            (A.fetch_text, A.search, A.build_queries) = orig


class MetaFallbackTest(unittest.TestCase):
    """纯 SPA 官网的兜底：正文抓不到时用 meta 摘要，而不是当成「官网不存在」。

    实测 stan.store：HTTP 200 但只有 2913 字节的 Vue 壳（<div id="app"> + JS），
    strip_html 得 0 字 —— 一个真实存在的官网就此消失。meta 与 <title> 是
    服务端渲染的，不含收入数字但能说明「这家是做什么的」，比空手强。
    """

    STAN = ('<!doctype html><html><head>'
            '<meta hid="og:title" property="og:title" content="Stan - Your Creator Store"/>'
            '<meta hid="description" ame="description" content="Mak Money Creating Stan profile"/>'
            '<title>Stan - Your Creator Store</title>'
            '</head><body><div id="app"></div>'
            '<noscript>enable JavaScript</noscript></body></html>')

    def test_从meta抠摘要_含拼错的name(self):
        vals = A.page_meta(self.STAN)
        self.assertIn("Stan - Your Creator Store", vals)
        # stan.store 把 name 写成了 ame，兜底要能接住
        self.assertIn("Mak Money Creating Stan profile", vals)

    def test_属性顺序反过来也能抠(self):
        html = '<meta content="倒过来的" property="og:description">'
        self.assertIn("倒过来的", A.page_meta(html))

    def test_没有meta就是空(self):
        self.assertEqual(A.page_meta("<html><body>  </body></html>"), [])

    def test_纯SPA_回落到meta并标明(self):
        orig = A.http_get
        try:
            A.http_get = lambda u, **kw: self.STAN
            t = A.fetch_text("https://stan.store/")
            self.assertIsNotNone(t, "有 meta 就不该返回 None")
            self.assertIn("meta 摘要", t)
            self.assertIn("Stan - Your Creator Store", t)
        finally:
            A.http_get = orig

    def test_有正文仍追加meta与JSONLD_但不走SPA兜底(self):
        # 2026-09-24 改行为：正文长时不再丢弃 meta——TrustMRR 挂牌价只写在
        # meta/JSON-LD 里（见 FetchTextStructuredTest）。这里守住的是：
        # SPA 兜底那段「JS 渲染」话术不能出现（正文明明抓到了）。
        orig = A.http_get
        try:
            body = ('<html><head>'
                    '<meta property="og:title" content="结构化标题"/>'
                    '</head><body>' + ("真正的正文。" * 40) + "</body></html>")
            A.http_get = lambda u, **kw: body
            t = A.fetch_text("https://x.example/")
            self.assertIn("真正的正文", t)
            self.assertNotIn("JS 渲染", t)
            self.assertIn("结构化标题", t, "meta 内容要追加进正文")
        finally:
            A.http_get = orig

    def test_空壳且无meta_返回None(self):
        orig = A.http_get
        try:
            A.http_get = lambda u, **kw: "<html><body><div id=app></div></body></html>"
            self.assertIsNone(A.fetch_text("https://empty.example/"))
        finally:
            A.http_get = orig


class ResultShapeTest(unittest.TestCase):
    """verify_one 的返回必须够界面渲染「这一轮审了哪些」那张结果表。

    起因：AI 跑完只在日志留一句「处理 1 条｜已发布 0」，页面上看不出
    审的是哪条、结论如何、该点哪儿。所以返回里必须带 name / verdict /
    remain / unverified 这些字段 —— 它们是人和结果之间唯一的接口。
    """

    def _fake_admin(self, result):
        """搭一个只回结果的假后台，绕开联网与 LLM。"""
        class FakeAdmin:
            def get_draft(self, cid):
                return None

            def put_draft(self, cid, draft):
                return result

            def promote(self, cid):
                return {}, 200
        return FakeAdmin()

    def _run(self, ai_result, rule_result, pages=None, publish=False):
        """把 verify_one 的四个外部依赖全部替换掉，只看它怎么拼返回值。"""
        calls = []
        orig = (A.search, A.fetch_text, A.llm, A.extract_json,
                A.map_ai_to_draft, A.current_blockers, A.build_queries,
                A.render_page)
        A.search = lambda q: ["https://example.com/a"]
        A.fetch_text = lambda u: "x" * 300 if pages is None else pages
        A.llm = lambda msgs: "{}"
        A.extract_json = lambda t: {"confidence": 0.9}
        A.build_queries = lambda cand: ["q"]
        A.current_blockers = lambda old: ([], [])
        A.map_ai_to_draft = lambda cand, ai, pub: {
            "caliber": "mrr", "verification": "official",
            "sources": [{"label": "s", "url": "https://example.com/a", "kind": "official"}],
        }
        # 2026-09-24：抓取失败后还有一层无头浏览器兜底，这条用例要模拟
        # 「一处都抓不到」，必须连它也 stub 掉，否则会真的拉起本机 Chrome。
        A.render_page = lambda u, **kw: None
        try:
            return A.verify_one(dict(CAND), self._fake_admin(rule_result),
                                publish=publish)
        finally:
            (A.search, A.fetch_text, A.llm, A.extract_json,
             A.map_ai_to_draft, A.current_blockers, A.build_queries,
             A.render_page) = orig

    def test_结果里带案例名和id(self):
        out = self._run({}, {"verdict": "可发布", "bonus_score": 60,
                             "publishable": True})
        self.assertEqual(out["id"], CAND["id"])
        self.assertEqual(out["name"], CAND["name"])

    def test_结果里带判定与质量分(self):
        out = self._run({}, {"verdict": "还差 1 项必填", "bonus_score": 35,
                             "publishable": False,
                             "missing": [{"label": "口径选定"}]})
        self.assertEqual(out["verdict"], "还差 1 项必填")
        self.assertEqual(out["score"], 35)
        self.assertEqual(out["remain"], ["口径选定"])
        self.assertEqual(out["caliber"], "mrr")
        self.assertEqual(out["source_count"], 1)

    def test_门槛未确认与提醒都列出来(self):
        out = self._run({}, {
            "verdict": "可发布", "bonus_score": 50, "publishable": True,
            "unverified_gates": [{"label": "还在运营"}],
            "warnings": [{"label": "仅有二手来源"}],
            "denied_gates": [{"label": "是个生意"}],
        })
        self.assertEqual(out["unverified"], ["还在运营"])
        self.assertEqual(out["warnings"], ["仅有二手来源"])
        self.assertEqual(out["denied"], ["是个生意"])

    def test_没抓到原文时也带回名字(self):
        out = self._run({}, {}, pages="")
        self.assertFalse(out["ok"])
        self.assertEqual(out["name"], CAND["name"])
        self.assertIn("没抓到任何原文", out["why"])

    def test_发布成功时标出案例id(self):
        out = self._run({}, {"verdict": "可发布", "bonus_score": 70,
                             "publishable": True, "tier_label": "精品"},
                        publish=True)
        self.assertTrue(out["published"])

    def test_质量分低于阈值时只存草稿(self):
        """发布阈值是靠 --min-score 卡在 verify_one 里的（CLI 与 GUI 共用）。"""
        def run_with_min_score(rule_result, min_score):
            calls = []
            orig = (A.search, A.fetch_text, A.llm, A.extract_json,
                    A.map_ai_to_draft, A.current_blockers, A.build_queries,
                    A.Admin)
            A.search = lambda q: ["https://example.com/a"]
            A.fetch_text = lambda u: "x" * 300
            A.llm = lambda msgs: "{}"
            A.extract_json = lambda t: {"confidence": 0.9}
            A.build_queries = lambda cand: ["q"]
            A.current_blockers = lambda old: ([], [])
            A.map_ai_to_draft = lambda cand, ai, pub: {
                "caliber": "mrr", "verification": "official", "sources": []}

            class FakeAdmin:
                def get_draft(self, cid):
                    return None

                def put_draft(self, cid, draft):
                    return rule_result

                def promote(self, cid):
                    calls.append(cid)
                    return {}, 200
            try:
                return A.verify_one(dict(CAND), FakeAdmin(), publish=True,
                                    min_score=min_score), calls
            finally:
                (A.search, A.fetch_text, A.llm, A.extract_json,
                 A.map_ai_to_draft, A.current_blockers, A.build_queries,
                 A.Admin) = orig

        out, promoted = run_with_min_score(
            {"verdict": "可发布", "bonus_score": 55, "publishable": True}, 60)
        self.assertTrue(out["held"])
        self.assertFalse(out["published"])
        self.assertEqual(promoted, [], "分不够就不该真的去发布")

        out2, promoted2 = run_with_min_score(
            {"verdict": "可发布", "bonus_score": 75, "publishable": True}, 60)
        self.assertTrue(out2["published"])
        self.assertEqual(promoted2, [CAND["id"]])


class FetchTextStructuredTest(unittest.TestCase):
    """fetch_text 必须把 meta 摘要 / JSON-LD 一并喂给 AI。

    2026-09-24 search1api 实测：TrustMRR 的挂牌横幅由客户端渲染，
    "listed for sale at $50,000" 只存在于 meta description 与 JSON-LD Offer，
    可见正文里没有。只看可见文本会让 AI 把真挂牌价当成「误读」纠错掉。
    """

    PAGE = (
        "<html><head>"
        '<meta name="description" content="listed for sale at $50,000. $1,408 MRR.">'
        "</head><body><p>" + "正文内容。" * 300 + "</p>"
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Search1API","offers":'
        '{"@type":"Offer","price":50000,"priceCurrency":"USD"}}</script>'
        "</body></html>"
    )

    def setUp(self):
        self.orig = A.http_get
        A.http_get = lambda url, **kw: self.PAGE

    def tearDown(self):
        A.http_get = self.orig

    def test_长正文也带meta与JSONLD(self):
        txt = A.fetch_text("https://trustmrr.com/startup/x")
        self.assertIn("listed for sale at $50,000", txt, "meta 摘要得带上")
        self.assertIn('"price":50000', txt.replace(" ", ""), "JSON-LD 报价得带上")
        self.assertIn("JSON-LD", txt, "结构化段要有标记，让 AI 知道证据性质")

    def test_正文短时回落meta_原行为不变(self):
        A.http_get = lambda url, **kw: (
            '<html><head><meta name="description" content="SPA 页摘要"></head>'
            "<body><p>短</p></body></html>")
        txt = A.fetch_text("https://x.example/")
        self.assertIn("SPA 页摘要", txt)
        self.assertIn("JS 渲染", txt)


class FetchLayerTest(unittest.TestCase):
    """2026-09-24 新增的抓取层：TrustMRR 官方 .md / discovery + 无头浏览器兜底。

    背景：抓取层以前只有 cn.bing 一条路（小众外文站基本搜不到），本轮改用
    TrustMRR 官方给 AI 的通道，并为 Cloudflare 站补一条本机渲染的退路。
    """

    def setUp(self):
        self._render = A.render_page
        # 本类默认不开浏览器；只有专门测渲染兜底的用例才替换成 want=True 的版本
        A.render_page = lambda *a, **kw: None

    def tearDown(self):
        A.render_page = self._render

    def test_官方md优先于原html(self):
        """llms.txt 把 /startup/{slug}.md 定为 AI 入口，必须排在 HTML 前面。"""
        cand = {"id": "superfruits",
                "source_url": "https://trustmrr.com/startup/superfruits",
                "website": "https://gojiberry.ai/"}
        urls = A.known_urls(cand)
        self.assertEqual(urls[0], "https://trustmrr.com/startup/superfruits.md")
        self.assertIn("https://trustmrr.com/startup/superfruits", urls,
                      "原 HTML 要留着当 .md 拿不到时的兜底")

    def test_已经是md就不再加后缀(self):
        cand = {"source_url": "https://trustmrr.com/startup/x.md"}
        urls = A.known_urls(cand)
        self.assertEqual([u for u in urls if u.endswith(".md")],
                         ["https://trustmrr.com/startup/x.md"], "%r 重复了" % urls)

    def test_discovery挂了不影响取URL(self):
        """discovery 是锦上添花，挂了绝不能让流程中断。"""
        orig, A.http_get = A.http_get, lambda url, **kw: (_ for _ in ()).throw(
            OSError("boom"))
        try:
            self.assertEqual(A.trustmrr_discovery(force=True), {})
        finally:
            A.http_get = orig
            A._DISCOVERY_CACHE["data"] = None
        self.assertEqual(A.known_urls({"source_url": "https://a.example/"}),
                         ["https://a.example/"])

    def test_discovery给候选补官网(self):
        """候选池本来 0/28 带 URL；discovery 命中时该把官网补进去。"""
        A._DISCOVERY_CACHE["data"] = {
            "superfruits": {"slug": "superfruits", "website": "https://sf.example/"}}
        try:
            self.assertIn("https://sf.example/", A.known_urls({"id": "superfruits"}))
        finally:
            A._DISCOVERY_CACHE["data"] = None

    def test_常规抓取失败才动用无头浏览器(self):
        calls = []
        orig = (A.known_urls, A.build_queries, A.search, A.fetch_text)
        try:
            A.known_urls = lambda c: ["https://cf.example/"]
            A.build_queries = lambda c: []
            A.search = lambda q, per_query=6: []
            A.fetch_text = lambda u, cap=6000: calls.append(("fetch", u)) or None
            A.render_page = lambda u, **kw: calls.append(("render", u)) or "渲染出来的正文。" * 60
            pages, used = A.collect_pages({}, max_pages=3)
        finally:
            (A.known_urls, A.build_queries, A.search, A.fetch_text) = orig

        self.assertEqual(used, ["https://cf.example/"], "渲染兜底成功也要算这一页")
        self.assertEqual(calls, [("fetch", "https://cf.example/"),
                                 ("render", "https://cf.example/")],
                         "顺序必须是先常规抓取、失败才渲染：%r" % (calls,))
        self.assertIn("渲染出来的正文", pages[0])

    def test_常规抓取成功就不开浏览器(self):
        calls = []
        orig = (A.known_urls, A.build_queries, A.search, A.fetch_text)
        try:
            A.known_urls = lambda c: ["https://ok.example/"]
            A.build_queries = lambda c: []
            A.search = lambda q, per_query=6: []
            A.fetch_text = lambda u, cap=6000: "正经正文。" * 80
            A.render_page = lambda u, **kw: calls.append(u)
            A.collect_pages({}, max_pages=3)
        finally:
            (A.known_urls, A.build_queries, A.search, A.fetch_text) = orig
        self.assertEqual(calls, [], "有正文就别付 5–8 秒/页的渲染代价")

    def test_render_page对非法输入返回None(self):
        """不是 http 开头的（比如空串）别拿去喂浏览器。"""
        self.assertIsNone(A.render_page(""))
        self.assertIsNone(A.render_page("not-a-url"))


class FetchLayerTest(unittest.TestCase):
    """2026-09-24 加的抓取层：官方 .md 优先 + discovery 补 URL + 无头浏览器兜底。

    起因是 le19emetrou 那轮：候选池一条 URL 都没有 → 退化成 cn.bing 搜索 →
    抓回百度知道/作业帮。这三条是新通道各自的回归断言，**全部 stub 掉网络**，
    只验接线不验外网。
    """

    def test_TrustMRR详情页_官方md排在最前(self):
        """llms.txt 把 /startup/{slug}.md 定为 AI 入口，且 .md 比渲染 HTML 更全。

        原 HTML 保留在后面，作为 .md 拿不到时的兜底。
        """
        urls = A.known_urls({"id": "abc",
                             "source_url": "https://trustmrr.com/startup/abc"})
        self.assertEqual(urls[0], "https://trustmrr.com/startup/abc.md")
        self.assertIn("https://trustmrr.com/startup/abc", urls)

    def test_md去重_不会写成md_dot_md(self):
        urls = A.known_urls({"id": "abc",
                             "source_url": "https://trustmrr.com/startup/abc.md"})
        self.assertEqual(len([u for u in urls if u.endswith(".md")]), 1)

    def test_非TrustMRR网址不动(self):
        urls = A.known_urls({"id": "abc", "website": "https://example.com/"})
        self.assertEqual(urls, ["https://example.com/"])

    def test_discovery能补上候选缺的官网(self):
        """候选池 0/28 带 URL；discovery 命中时应当自动补上。"""
        A._DISCOVERY_CACHE["data"] = {
            "abc": {"slug": "abc", "website": "https://abc.example/"}}
        try:
            urls = A.known_urls({"id": "abc"})
        finally:
            A._DISCOVERY_CACHE["data"] = None
        self.assertIn("https://abc.example/", urls)

    def test_discovery挂了不影响后续流程(self):
        """discovery 是锦上添花，绝不能因为它挂了就让整个核实失败。"""
        orig = A.http_get
        A.http_get = lambda *a, **kw: (_ for _ in ()).throw(OSError("boom"))
        try:
            A._DISCOVERY_CACHE["data"] = None
            self.assertEqual(A.trustmrr_discovery(), {})
            urls = A.known_urls({"id": "abc"})
        finally:
            A.http_get = orig
            A._DISCOVERY_CACHE["data"] = None
        self.assertEqual(urls, ["%s/startup/abc.md" % A.TRUSTMRR_SITE,
                                "%s/startup/abc" % A.TRUSTMRR_SITE])

    def test_常规抓取失败才用无头浏览器(self):
        """Cloudflare 挡住的站（实测 Indie Hackers 403）走这条退路。"""
        seen = []
        orig_f, orig_r = A.fetch_text, A.render_page
        A.fetch_text = lambda u, cap=6000: seen.append(("fetch", u)) or None
        A.render_page = lambda u, timeout=40, budget_ms=8000: (
            seen.append(("render", u)) or "x" * 400)
        try:
            pages, used = A.collect_pages({"id": "abc"}, max_pages=5)
        finally:
            A.fetch_text, A.render_page = orig_f, orig_r
        self.assertEqual(seen[0], ("fetch", "%s/startup/abc.md" % A.TRUSTMRR_SITE))
        self.assertEqual(seen[1], ("render", "%s/startup/abc.md" % A.TRUSTMRR_SITE))
        self.assertEqual(used, ["%s/startup/abc.md" % A.TRUSTMRR_SITE])

    def test_常规抓取成功就不开浏览器(self):
        """每页 5–8 秒，能省就省。"""
        orig_f, orig_r = A.fetch_text, A.render_page
        A.fetch_text = lambda u, cap=6000: "x" * 400
        A.render_page = lambda u, timeout=40, budget_ms=8000: self.fail("不该调用")
        try:
            A.collect_pages({"id": "abc"}, max_pages=5)
        finally:
            A.fetch_text, A.render_page = orig_f, orig_r

    def test_没装浏览器时render_page返回None(self):
        orig = A._find_browser
        A._find_browser = lambda: None
        try:
            self.assertIsNone(A.render_page("https://example.com/"))
        finally:
            A._find_browser = orig


class HNAlgoliaSearchTest(unittest.TestCase):
    """2026-09-24 加的 HN Algolia 检索：免 key 的 HN 讨论证据源，排在所有通用搜索前。

    边界：Algolia 只索引 HN 内容，查不到官网，所以替代不了 cn.bing，只能当补充
    证据源（讨论页 = 评论里的真话）。全部 stub 掉网络，只验接线。
    """

    HN_JSON = json.dumps({
        "hits": [
            {"objectID": "111", "url": "https://real-product.example/"},
            {"objectID": "222", "url": "https://real-product.example/"},   # 重复外链
            {"objectID": "333", "url": ""},                                # 无外链，只给讨论页
        ],
    })

    def test_返回讨论页与外链且去重(self):
        orig = A.http_get
        A.http_get = lambda u, **kw: self.HN_JSON
        try:
            out = A.search_hn_algolia("some product")
        finally:
            A.http_get = orig
        # 两条指向同一外链的只留一条；三个 objectID 各一条讨论页
        self.assertIn("https://news.ycombinator.com/item?id=111", out)
        self.assertIn("https://news.ycombinator.com/item?id=222", out)
        self.assertIn("https://news.ycombinator.com/item?id=333", out)
        self.assertIn("https://real-product.example/", out)
        self.assertEqual(out.count("https://real-product.example/"), 1,
                         "重复外链要收敛成一条")

    def test_解析失败返回空不抛(self):
        orig = A.http_get
        A.http_get = lambda u, **kw: (_ for _ in ()).throw(RuntimeError("net down"))
        try:
            self.assertEqual(A.search_hn_algolia("x"), [])
        finally:
            A.http_get = orig

    def test_search把HN排最前且不挤掉bing(self):
        """HN 最多贡献 3 条，给 cn.bing 留名额（HN 查不到官网）。"""
        orig = (A.search_hn_algolia, A.search_bing, A.search_ddg)
        A.search_hn_algolia = lambda q, n=5: [
            "https://news.ycombinator.com/item?id=%d" % i for i in range(1, 6)]
        A.search_bing = lambda q: ["https://bing-hit.example/report"]
        A.search_ddg = lambda q: []
        try:
            out = A.search("some product", per_query=6)
        finally:
            (A.search_hn_algolia, A.search_bing, A.search_ddg) = orig
        # HN 在前且不超过 3 条
        self.assertEqual(out[:3],
                         ["https://news.ycombinator.com/item?id=1",
                          "https://news.ycombinator.com/item?id=2",
                          "https://news.ycombinator.com/item?id=3"])
        # bing 仍被纳入 —— HN 没有把通用搜索挤掉
        self.assertIn("https://bing-hit.example/report", out)
        self.assertLessEqual(len(out), 6)
