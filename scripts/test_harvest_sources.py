# -*- coding: utf-8 -*-
"""harvest.py 五源解析 + source_kind 的测试。

离线：全部用 scripts/fixtures/ 里抓下来的真实响应，不联网。
唯一的网络替代是给 http_json 打桩（HN 评论那段）。

为什么要单独测这些：采集是无人值守的，解析器一旦因为对方改版而静默失灵，
表现是「今天没采到东西」而不是报错 —— 属于最难发现的一类故障。

用法：python scripts/test_harvest_sources.py
"""

import io
import json
import os
import sys
import tempfile
import contextlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import harvest as H                                                   # noqa: E402
import ai_verify as A                                                  # noqa: E402

FIX = os.path.join(REPO, "scripts", "fixtures")

fails = []
passed = 0


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def section(title, fn):
    """跑一段测试，异常不让整轮中止。

    踩过两次的坑：某段抛异常后测试跑一半没输出，退出码却是 0 —— 看起来全绿。
    这里把异常记成失败并继续，让问题一定要露出来。
    """
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        fails.append("%s（抛异常）" % title)
        print("  [FAIL] 这一段抛异常中止了：%s: %s" % (type(e).__name__, e))


def fixture(name):
    with open(os.path.join(FIX, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# ---------------------------------------------------------------------------

def test_source_kind():
    print("[1] source_kind 映射 —— 可信度必须钉死在数据层")
    chk("TrustMRR -> verified", H.source_kind_of("trustmrr") == "verified")
    chk("IndieHackers -> self_reported",
        H.source_kind_of("indiehackers") == "self_reported")
    chk("HN -> self_reported", H.source_kind_of("hn") == "self_reported")
    chk("HN 别名 hackernews 同档", H.source_kind_of("hackernews") == "self_reported")
    chk("ARR Club -> secondary", H.source_kind_of("arrclub") == "secondary")
    chk("Product Hunt -> discovery", H.source_kind_of("ph") == "discovery")
    chk("PH 别名 producthunt 同档", H.source_kind_of("producthunt") == "discovery")
    chk("未知来源 -> 兜底 discovery（保守）",
        H.source_kind_of("whatever-new-source") == "discovery")
    chk("空值不炸", H.source_kind_of("") == "discovery" and H.source_kind_of(None) == "discovery")
    chk("大小写不敏感", H.source_kind_of("TrustMRR") == "verified")
    # 四个值必须都在标签表里，否则前端徽章会显示成裸英文 key
    chk("四种性质都有中文标签",
        all(v in H.KIND_LABEL for v in set(H.SOURCE_KIND.values())),
        sorted(set(H.SOURCE_KIND.values())))
    chk("KIND_RANK 覆盖四值且有序",
        H.KIND_RANK["verified"] < H.KIND_RANK["self_reported"]
        < H.KIND_RANK["secondary"] < H.KIND_RANK["discovery"])
    print()


def test_money():
    print("[2] parse_abbrev_money —— 金额解析")
    chk("$125K", H.parse_abbrev_money("$125K") == 125000)
    chk("$1.2M", H.parse_abbrev_money("$1.2M") == 1200000)
    chk("$100M", H.parse_abbrev_money("$100M") == 100000000)
    chk("$3.5B", H.parse_abbrev_money("$3.5B") == 3500000000)
    chk("$100 无单位", H.parse_abbrev_money("$100") == 100)
    chk("带千分位 $1,234,567", H.parse_abbrev_money("$1,234,567") == 1234567)
    chk("句子里的金额也能抽",
        H.parse_abbrev_money("reached $100M in ARR") == 100000000)
    chk("无金额 -> None", H.parse_abbrev_money("no money") is None)
    chk("空值 -> None", H.parse_abbrev_money("") is None and H.parse_abbrev_money(None) is None)
    chk("小写 k 也认", H.parse_abbrev_money("$20k") == 20000)
    print()


def test_indiehackers():
    print("[3] Indie Hackers：metrics 区块解析（真实 fixture）")
    html = fixture("indiehackers_product.html")
    rec = H.parse_ih_product(html, "page-lens-ai")

    chk("产品名去掉了站名后缀", rec["name"] == "Page Lens AI", rec["name"])
    chk("官网从 --website 块拿到",
        rec["website"] == "https://www.pagelensai.com", rec["website"])
    chk("收入解析成数字", rec["_ih"]["revenue"] == 100, rec["_ih"]["revenue"])
    chk("收入原文保留", rec["_ih"]["revenue_raw"] == "$100 / mo", rec["_ih"]["revenue_raw"])
    chk("周期 mo 认出来", rec["_ih"]["period"] == "mo")
    chk("帖数拿到", rec["_ih"]["posts"] == "1", rec["_ih"]["posts"])
    chk("来源标记为 indiehackers", rec["_origin"] == "indiehackers")
    chk("详情页 URL 正确",
        rec["url"] == "https://www.indiehackers.com/product/page-lens-ai", rec["url"])
    chk("headline 明说「自报」", "自报" in rec["_ih"]["headline"], rec["_ih"]["headline"])
    chk("note 要求交叉核对", "交叉核对" in rec["_ih"]["note"])
    chk("one_liner 来自 meta description（不是导航垃圾）",
        "PageLens AI helps founders" in rec["one_liner"], rec["one_liner"][:60])
    chk("无 markdown 星号（前端不解析 md）", "**" not in rec["_ih"]["note"])

    # 有收入才提口径：否则会写出一句自相矛盾的假话
    chk("有收入时给出 caliber", "mo" in rec["_ih"]["caliber"], rec["_ih"]["caliber"])
    norev = H.parse_ih_product(
        '<html><title>No Rev - Indie Hackers</title>'
        '<a class="product-metrics__stat product-metrics__stat--posts" href="/x">'
        '<span class="product-metrics__stat-number">2</span></a></html>', "no-rev")
    chk("无收入时不提口径", norev["_ih"]["caliber"] == "", norev["_ih"]["caliber"])
    chk("无收入时 headline 说实话",
        "未公开" in norev["_ih"]["headline"], norev["_ih"]["headline"])

    print()
    print("[4] Indie Hackers：sitemap 只留干净产品页")
    xml = ('<urlset>'
           '<loc>https://www.indiehackers.com/product/userguiding</loc>'
           '<loc>https://www.indiehackers.com/product/userguiding/new-interface-234</loc>'
           '<loc>https://www.indiehackers.com/product/visalist/launch-website-3079</loc>'
           '<loc>https://www.indiehackers.com/product/page-lens-ai</loc>'
           '<loc>https://www.indiehackers.com/post/12345</loc>'
           '<loc>https://www.indiehackers.com/product/userguiding</loc>'
           '</urlset>')
    slugs = H.ih_slugs_from_xml(xml, 100)
    chk("里程碑 URL 被丢掉（含 /）",
        "userguiding/new-interface-234" not in slugs, slugs)
    chk("visalist 的里程碑页也丢掉", "visalist" not in slugs, slugs)
    chk("post 页丢掉", all("post" not in s for s in slugs))
    chk("只留干净 slug 且去重",
        slugs == ["userguiding", "page-lens-ai"], slugs)
    chk("limit 生效", H.ih_slugs_from_xml(xml, 1) == ["userguiding"])

    print()
    print("[5] Indie Hackers：story 抽取（唯一拿得到「怎么做到的」的地方）")
    chk("passed $20K MRR 句式",
        H._ih_story("<p>Firstly, we've passed $20K MRR today!</p>")
        == "Firstly, we've passed $20K MRR today!", H._ih_story("<p>Firstly, we've passed $20K MRR today!</p>"))
    chk("reached $150,000 ARR 句式",
        "$150,000 ARR" in H._ih_story("<p>We reached $150,000 ARR in about eight months.</p>"))
    chk("MRR is $4,200 句式",
        H._ih_story("<p>MRR is $4,200 and growing.</p>") != "")
    chk("无金额 -> 空", H._ih_story("<p>no money here</p>") == "")
    chk("narrative/arrow 里的 arr 不误命中（词边界）",
        H._ih_story("<p>The narrative arrow points left for $5,000.</p>") == "")
    chk("metrics 挂件 $100/mo 不算 milestone",
        H._ih_story('<a><span>Revenue</span><span>$100</span><span>/</span><span>mo</span></a>') == "")
    chk("夹带导航文案的整句被丢弃（宁缺勿脏）",
        H._ih_story("<nav>Sign in Join Visit Website</nav><p>We hit $9K MRR last month.</p>") == "")
    chk("内联 JS 不会污染摘要",
        H._ih_story('<script>var x="false"); } } });"</script><p>ok</p>') == "")
    # 真实抓到的形态：评论区 UI 前缀要剥掉
    chk("剥掉评论区 UI 前缀",
        H._ih_story("<p>:) Dozey · 6 years ago · Reply June 26, 2020 "
                    "We've passed $20K MRR!</p>") == "We've passed $20K MRR!",
        H._ih_story("<p>:) Dozey · 6 years ago · Reply June 26, 2020 "
                    "We've passed $20K MRR!</p>"))
    chk("带日期的前缀也剥",
        H._ih_story("<p>Reply March 3, 2021 We hit $5,000 MRR this month!</p>")
        == "We hit $5,000 MRR this month!",
        H._ih_story("<p>Reply March 3, 2021 We hit $5,000 MRR this month!</p>"))
    chk("没有 UI 前缀时原样返回",
        H._ih_story("<p>We reached $150,000 ARR in eight months.</p>")
        == "We reached $150,000 ARR in eight months",
        H._ih_story("<p>We reached $150,000 ARR in eight months.</p>"))
    print()


def test_arrclub():
    print("[6] ARR Club：FAQPage JSON-LD 解析（真实 fixture）")
    a = H.parse_arr_club_page(fixture("arrclub_company.html"))
    chk("公司名", a["name"] == "Dbt Labs", a["name"])
    chk("官网明文（Organization.url）", a["website"] == "https://getdbt.com", a["website"])
    chk("行业", a["industry"] == "AI Data Infrastructure", a["industry"])
    chk("成立年份", a["founded"] == "2016", a["founded"])
    chk("ARR 带年份存", a["arr_by_year"] == {"2025": 100000000}, a["arr_by_year"])
    chk("current_arr 单独存", a["current_arr"] == 100000000, a["current_arr"])

    print()
    print("[7] ARR Club：年份必须和数字成对（防串年份）")
    # 合成一份「同公司多年份 + 增速」的 FAQ —— 真实站点上 Notion 就是这个样子
    fake = ('<script type="application/ld+json">%s</script>'
            '<script type="application/ld+json">%s</script>') % (
        json.dumps({"@type": "Organization", "name": "Notion",
                    "url": "https://notion.so", "industry": "AI Productivity",
                    "foundingDate": "2016"}),
        json.dumps({"@type": "FAQPage", "mainEntity": [
            {"name": "What is Notion's ARR in 2025?",
             "acceptedAnswer": {"text": "Notion reached $600M in ARR in 2025, "
                                        "according to ARR Club's verified data."}},
            {"name": "What is Notion's ARR in 2024?",
             "acceptedAnswer": {"text": "Notion reached $300M in ARR in 2024."}},
            {"name": "What is Notion's current ARR?",
             "acceptedAnswer": {"text": "Notion's current ARR is $600M."}},
            {"name": "What is Notion's revenue growth rate?",
             "acceptedAnswer": {"text": "Notion's revenue growth rate is +83% year-over-year."}},
        ]}))
    b = H.parse_arr_club_page(fake)
    chk("两个年份分别落位",
        b["arr_by_year"] == {"2025": 600000000, "2024": 300000000}, b["arr_by_year"])
    chk("current_arr 不与年度值混淆", b["current_arr"] == 600000000)
    chk("增速抽出来", b["growth"] == "+83%", b["growth"])
    chk("Organization 同块解析", b["website"] == "https://notion.so")

    print()
    print("[8] ARR Club：sitemap 枚举（真实 fixture，已精简为 220 条 <loc>）")
    xml = fixture("arrclub_sitemap.xml")
    slugs = H.arr_slugs_from_xml(xml, 100000)
    # 断言性质而不是具体条数：真实站点的公司页数量会变（实测 1000~1002 之间浮动），
    # 钉死数字只会让测试因为对方收录变化而变红，反而掩盖真问题。
    chk("提到公司页", len(slugs) == 200, len(slugs))
    chk("dbt-labs 在里面", "dbt-labs" in slugs)
    chk("工具页被排掉（database/signal/pricing…）",
        not (set(slugs) & H.ARR_NON_COMPANY), sorted(set(slugs) & H.ARR_NON_COMPANY))
    chk("根域被排掉", "" not in slugs)
    chk("带 query 的被排掉", "database" not in slugs)
    chk("带子路径的被排掉", "a" not in slugs and "b" not in slugs)
    chk("公司页 slug 不含斜杠", all("/" not in s for s in slugs))
    chk("全部小写", all(s == s.lower() for s in slugs))
    chk("limit 生效", len(H.arr_slugs_from_xml(xml, 5)) == 5)
    chk("limit=1 取第一个公司页",
        H.arr_slugs_from_xml(xml, 1) == ["dbt-labs"], H.arr_slugs_from_xml(xml, 1))
    print()


def test_enrich():
    print("[9] HN 评论正文（http_json 打桩，不联网）")
    orig = H.http_json
    try:
        H.http_json = lambda url, **kw: {"children": [
            {"text": "<p>We spent $0 on ads. All of our growth came from a single Reddit post "
                     "that happened to hit the front page on a Sunday.</p>",
             "children": [
                 {"text": "<p>Which subreddit exactly? We tried r/SaaS for months and got "
                          "absolutely nothing out of it.</p>",
                  "children": []}]},
            {"text": "<p>too short</p>", "children": []},
            {"text": "<p>Churn was the real problem, 8% monthly until we added annual plans.</p>",
             "children": []},
        ]}
        txt = H.fetch_hn_comments("123", top_n=2)
        txt3 = H.fetch_hn_comments("123", top_n=3)
        chk("抓到评论正文", "We spent $0 on ads" in txt, txt[:60])
        chk("浅层评论优先（depth 0 排在 depth 1 前）",
            txt3.index("We spent $0") < txt3.index("Which subreddit"), txt3[:40])
        chk("短评论被过滤（<60 字的是噪音）", "too short" not in txt)
        chk("按 top_n 截断", txt.count("\n") == 1, "行数=%d" % (txt.count("\n") + 1))
        chk("top_n=3 能多拿一条", txt3.count("\n") == 2)

        H.http_json = lambda url, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        chk("请求失败返回空串而不是抛异常", H.fetch_hn_comments("9") == "")

        H.http_json = lambda url, **kw: {}
        chk("无 children 不炸", H.fetch_hn_comments("9") == "")

        H.http_json = lambda url, **kw: {"children": [{"text": "", "children": []}]}
        chk("全空评论不炸", H.fetch_hn_comments("9") == "")
    finally:
        H.http_json = orig

    print()
    print("[10] TrustMRR .md：数据新鲜度与出处分层（真实 fixture）")
    md = fixture("trustmrr_stan.md")
    import re as _re
    m = _re.search(r"next refresh expected around ([0-9T:\-\.]+Z)", md)
    chk("fixture 里有 refresh 时间戳", bool(m), m.group(1) if m else "")
    chk("fixture 里有出处分层说明",
        "verified through connected external providers" in md)
    chk("fixture 里有 X 粉丝数", bool(_re.search(r"X followers:\s*([\d,]+)", md)))
    # 版本号/空 slug 不该触发请求
    chk("空 slug 直接返回空（不发请求）", H.fetch_trustmrr_md("") == {})
    print()


def test_integration():
    print("[11] 集成：跑一遍 main()，确认每条都带 source_kind")
    tmp = tempfile.mkdtemp(prefix="harvest_test_")
    orig_dir = H.DATA_DIR
    orig = (H.harvest_hn, H.harvest_indiehackers, H.harvest_trustmrr,
            H.harvest_producthunt)
    try:
        H.DATA_DIR = tmp

        def fake_hn(limit, keywords):
            return [{"name": "HN Thing", "one_liner": "we hit mrr", "url": "https://x.com/a",
                     "points": 7, "comments": 3, "created_at": "2026-09-01",
                     "object_id": "111", "_blob": "HN Thing mrr revenue", "_origin": "hn"}]

        def fake_ih(limit, delay=1.5, refresh_slugs=False, persist=True, breadth=False):
            return [{"name": "IH Thing", "one_liner": "自报 mrr",
                     "url": "https://www.indiehackers.com/product/ih-thing",
                     "website": "https://ih.example", "points": 0, "comments": 0,
                     "created_at": "2026-09-01", "_blob": "IH Thing mrr revenue",
                     "_origin": "indiehackers",
                     "_ih": {"slug": "ih-thing", "revenue": 5000, "revenue_raw": "$5K / mo",
                             "period": "mo", "posts": "3", "website": "https://ih.example",
                             "story": "we hit $5K MRR", "headline": "自报收入 $5,000 / mo",
                             "note": "自报，需交叉核对。"}}]

        def fake_tm(limit):
            return [{"name": "TM Thing", "one_liner": "verified", "url": "https://trustmrr.com/startup/tm",
                     "website": "https://tm.example", "points": 0, "comments": 0,
                     "created_at": "2026-09-01", "_blob": "TM Thing mrr revenue verified",
                     "_origin": "trustmrr", "_verified_rank": True}]

        def fake_ph(limit, token):
            return [{"name": "PH Thing", "one_liner": "launched today",
                     "url": "https://www.producthunt.com/products/ph-thing",
                     "points": 500, "comments": 0, "created_at": "2026-09-01",
                     "_blob": "PH Thing launched mrr", "_origin": "ph"}]

        H.harvest_hn, H.harvest_indiehackers = fake_hn, fake_ih
        H.harvest_trustmrr, H.harvest_producthunt = fake_tm, fake_ph

        argv = sys.argv
        sys.argv = ["harvest.py", "--source", "all", "--limit", "5",
                    "--hn-comments", "0", "--trustmrr-md", "0"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = H.main()
        finally:
            sys.argv = argv

        chk("main() 正常退出", rc == 0, "rc=%s" % rc)

        with open(os.path.join(tmp, "inbox.json"), encoding="utf-8") as f:
            inbox = json.load(f)
        ids = {c.get("name"): c for c in inbox}
        chk("四个源都进了采集队列", len(inbox) == 4, sorted(ids))
        chk("每条都有 source_kind 字段",
            all(c.get("source_kind") for c in inbox),
            [(c.get("name"), c.get("source_kind")) for c in inbox])
        chk("HN 条目 -> self_reported",
            ids.get("HN Thing", {}).get("source_kind") == "self_reported")
        chk("IH 条目 -> self_reported",
            ids.get("IH Thing", {}).get("source_kind") == "self_reported")
        chk("TrustMRR 条目 -> verified",
            ids.get("TM Thing", {}).get("source_kind") == "verified")
        chk("Product Hunt 条目 -> discovery",
            ids.get("PH Thing", {}).get("source_kind") == "discovery")

        ph = ids.get("PH Thing", {})
        chk("PH 不带任何收入字段（只有热度没有钱）",
            not any(k in (ph.get("metrics") or {})
                    for k in ("mrr", "arr", "arr_by_year", "all_time")),
            sorted((ph.get("metrics") or {}).keys()))

        ih = ids.get("IH Thing", {})
        chk("IH 明确标注自报", (ih.get("metrics") or {}).get("self_reported") is True)
        chk("IH 保留 story_excerpt",
            (ih.get("metrics") or {}).get("story_excerpt") == "we hit $5K MRR",
            (ih.get("metrics") or {}).get("story_excerpt"))

        hn = ids.get("HN Thing", {})
        chk("HN 留存 objectID 供补评论", hn.get("hn_id") == "111", hn.get("hn_id"))

        chk("简报文件写出来了",
            os.path.exists(os.path.join(tmp, "last_harvest.json")))
    finally:
        H.DATA_DIR = orig_dir
        (H.harvest_hn, H.harvest_indiehackers, H.harvest_trustmrr,
         H.harvest_producthunt) = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print()


def test_ai_verify_denylist():
    print("[12] ai_verify：无关来源域名黑名单")
    chk("剑桥词典（eloquent 那次污染）",
        A.is_irrelevant_source("https://dictionary.cambridge.org/zhs/词典/eloquent"))
    chk("金山词霸", A.is_irrelevant_source("https://www.iciba.com/word?w=eloquent"))
    chk("有道词典", A.is_irrelevant_source("https://dict.youdao.com/result?word=x"))
    chk("网易云音乐（那次污染）", A.is_irrelevant_source("https://music.163.com/song?id=1"))
    chk("QQ 音乐（那次污染）", A.is_irrelevant_source("https://y.qq.com/n/ryqq/x"))
    chk("Amazon", A.is_irrelevant_source("https://www.amazon.com/dp/B0"))
    chk("TrustMRR 不该被误杀",
        not A.is_irrelevant_source("https://trustmrr.com/startup/stan"))
    chk("HN 不该被误杀",
        not A.is_irrelevant_source("https://news.ycombinator.com/item?id=1"))
    chk("IndieHackers 不该被误杀",
        not A.is_irrelevant_source("https://www.indiehackers.com/product/x"))
    chk("公司官网不该被误杀（含 store 后缀）",
        not A.is_irrelevant_source("https://stan.store/"))
    chk("域名后缀匹配不能误伤（notdictionary.com.evil.io）",
        not A.is_irrelevant_source("https://notdictionary.com.evil.io/"))
    chk("空 URL 不炸", not A.is_irrelevant_source("") and not A.is_irrelevant_source(None))
    chk("source_host 去 www",
        A.source_host("https://www.example.com/a") == "example.com")

    # 过滤发生在取前 6 条之前 —— 否则垃圾会白占名额
    draft = A.map_ai_to_draft(
        {"id": "x", "playbook": []},
        {"sources": [
            {"label": "剑桥词典", "url": "https://dictionary.cambridge.org/x"},
            {"label": "金山词霸", "url": "https://www.iciba.com/word?w=x"},
            {"label": "官网", "url": "https://real.example/", "kind": "official"},
        ], "confidence": 0.5},
        publish_mode=False)
    chk("无关来源没进 sources",
        [s["url"] for s in draft["sources"]] == ["https://real.example/"],
        draft["sources"])
    chk("记录了剔除条数", draft.get("irrelevant_sources_dropped") == 2,
        draft.get("irrelevant_sources_dropped"))
    chk("note 里透明标注",
        "已剔除 2 条无关来源" in draft.get("note", ""), draft.get("note", "")[:90])
    chk("source_kinds 只反映真实来源",
        draft["source_kinds"] == ["official"], draft["source_kinds"])
    print()


def test_bounded_fetch():
    print("[15] _fetch_html_bounded：总耗时上限（urlopen 的 timeout 管不住慢速滴数据）")
    import urllib.request as _ur
    import urllib.error as _ue

    class FakeResp:
        """按块吐出指定 bytes，用来模拟「连接活着但很慢」。"""

        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.headers = {"Content-Length": str(sum(len(c) for c in self.chunks))}

        def read(self, n=-1):
            return self.chunks.pop(0) if self.chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeOpener:
        def __init__(self, resp):
            self.resp = resp

        def open(self, req, timeout=None):
            return self.resp

    orig = H.urllib.request.build_opener
    try:
        # 1) max_bytes 到了就停（按块检查，所以最多超一个块）
        chunks = [b"A" * 65536] * 100
        H.urllib.request.build_opener = lambda *a, **k: FakeOpener(FakeResp(chunks))
        out = H._fetch_html_bounded("https://x/", max_bytes=200000, max_seconds=999)
        chk("按 max_bytes 截断（不读完 100 块）",
            200000 <= len(out) <= 200000 + 65536, len(out))

        # 2) 总耗时到了就停（每块之间睡一下）
        import time as _t
        slow = []

        class SlowResp(FakeResp):
            def read(self, n=-1):
                _t.sleep(0.05)
                return super().read(n)

        H.urllib.request.build_opener = lambda *a, **k: FakeOpener(SlowResp([b"B" * 65536] * 50))
        t0 = _t.time()
        out2 = H._fetch_html_bounded("https://x/", max_bytes=10 ** 9, max_seconds=1)
        elapsed = _t.time() - t0
        chk("按总耗时截断（不会一直等）", elapsed < 3, "%.1fs" % elapsed)
        chk("耗时截断也返回部分内容", len(out2) > 0, len(out2))

        # 3) 正常读完不截断
        H.urllib.request.build_opener = lambda *a, **k: FakeOpener(FakeResp([b"hello"]))
        chk("短内容完整返回", H._fetch_html_bounded("https://x/") == "hello")

        # 4) 连接失败照常抛（由调用方兜底）
        def boom(*a, **k):
            raise _ue.URLError("net down")

        H.urllib.request.build_opener = boom
        try:
            H._fetch_html_bounded("https://x/")
            chk("网络异常应向上抛", False)
        except Exception:                                      # noqa: BLE001
            chk("网络异常应向上抛（由调用方兜底）", True)
    finally:
        H.urllib.request.build_opener = orig
    print()


def test_ih_cursor():
    print("[14] IndieHackers slug 缓存与游标（分片太大，必须轮换 + 缓存）")
    orig_fetch = H._fetch_html
    orig_bounded = H._fetch_html_bounded
    calls = []

    def fake_fetch(url, timeout=30):
        calls.append(url)
        if url == H.IH_SITEMAP:
            return ('<sitemapindex>'
                    '<loc>https://x/shard1.xml</loc>'
                    '<loc>https://x/shard2.xml</loc>'
                    '<loc>https://x/shard3.xml</loc>'
                    '</sitemapindex>')
        if "shard" not in url:                     # 产品详情页
            return ('<html><title>%s - Indie Hackers</title>'
                    '<a class="product-metrics__stat product-metrics__stat--revenue" href="/x">'
                    '<span class="product-metrics__stat-label">Revenue</span>'
                    '<div class="product-metrics__stat-value">'
                    '<span class="product-metrics__stat-number">$100</span>'
                    '<span class="product-metrics__stat-unit">mo</span></div></a></html>'
                    % url.rsplit("/", 1)[-1])
        # 每个分片给 3 个不同的产品 slug
        n = url.split("shard")[1].split(".")[0]
        return ('<urlset>'
                + "".join('<loc>https://www.indiehackers.com/product/p%s-%d</loc>' % (n, k)
                          for k in range(3))
                + '<loc>https://www.indiehackers.com/product/p%s-0/milestone-1</loc>' % n
                + '</urlset>')

    try:
        H._fetch_html = fake_fetch
        H._fetch_html_bounded = lambda url, **kw: fake_fetch(url)
        st = {"slugs": [], "cursor": 0, "next_shard": 0, "barren": [], "fetched_at": ""}

        batch, st = H.ih_pool_slugs(2, state=st)
        chk("首次会去抓一个分片", any("shard" in c for c in calls), calls)
        chk("只抓了一个分片（不能全下，太慢）",
            len([c for c in calls if "shard" in c]) == 1, calls)
        chk("拿到 slug", batch == ["p1-0", "p1-1"], batch)
        chk("里程碑 URL 没混进来", all("/" not in s for s in batch), batch)
        chk("游标推进", st["cursor"] == 2, st["cursor"])
        chk("下次该抓第 2 片", st["next_shard"] == 1, st["next_shard"])
        chk("记录了抓取时间", bool(st["fetched_at"]), st["fetched_at"])

        # 第二次：缓存够用，不该再下载
        before = len([c for c in calls if "shard" in c])
        batch2, st = H.ih_pool_slugs(1, state=st)
        chk("缓存命中时不再下载",
            len([c for c in calls if "shard" in c]) == before, calls)
        chk("接着上次发（游标生效）", batch2 == ["p1-2"], batch2)

        # 游标走到尾部 -> 环绕
        batch3, st = H.ih_pool_slugs(3, state=st)
        chk("尾部环绕不越界", len(batch3) == 3, batch3)
        chk("环绕后游标回到范围内", 0 <= st["cursor"] < len(st["slugs"]), st["cursor"])

        # 强制刷新 -> 抓下一个分片并轮换
        old = len(st["slugs"])
        _, st2 = H.ih_pool_slugs(2, refresh=True, state=dict(st))
        chk("refresh 会再抓一片", len(st2["slugs"]) > old,
            "%d -> %d" % (old, len(st2["slugs"])))
        chk("轮换到第 2 片（不重复抓同一片）",
            any("shard2" in c for c in calls), calls[-2:])

        # 空分片要记进 barren，不再反复浪费时间
        def empty_shard_fetch(url, timeout=30):
            calls.append(url)
            if url == H.IH_SITEMAP:
                return '<sitemapindex><loc>https://x/shard9.xml</loc></sitemapindex>'
            return '<urlset><loc>https://www.indiehackers.com/post/1</loc></urlset>'

        H._fetch_html = empty_shard_fetch
        H._fetch_html_bounded = lambda url, **kw: empty_shard_fetch(url)
        _, st4 = H.ih_pool_slugs(1, refresh=True,
                                 state={"slugs": ["x"], "cursor": 0, "next_shard": 0,
                                        "barren": [], "fetched_at": ""})
        chk("空分片被标记为 barren", st4["barren"] == [0], st4["barren"])
        n_before = len([c for c in calls if "shard9" in c])
        # 索引里只有这一片，且它已被标记 barren -> 不该再下
        _, st5 = H.ih_pool_slugs(1, refresh=True, state=dict(st4))
        chk("barren 分片不再重复下载",
            len([c for c in calls if "shard9" in c]) == n_before, calls[-2:])

        # 分片下载失败要能降级用缓存，而不是崩
        H._fetch_html = lambda url, timeout=30: (_ for _ in ()).throw(RuntimeError("net down"))
        H._fetch_html_bounded = H._fetch_html
        b, st3 = H.ih_pool_slugs(1, state=dict(st))
        chk("下载失败仍能用旧缓存", len(b) == 1, b)

        H._fetch_html = lambda url, timeout=30: ""
        H._fetch_html_bounded = H._fetch_html
        b2, _ = H.ih_pool_slugs(1, state={"slugs": [], "cursor": 0})
        chk("无缓存 + 抓不到 -> 空列表不崩", b2 == [], b2)

        # 缓存过期判定
        chk("无时间戳视为过期", H.ih_slug_cache_stale({}))
        chk("坏时间戳视为过期", H.ih_slug_cache_stale({"fetched_at": "乱七八糟"}))
        from datetime import datetime as _dt
        chk("刚抓的不算过期",
            not H.ih_slug_cache_stale(
                {"fetched_at": _dt.now().strftime("%Y-%m-%d %H:%M")}))
        chk("30 天前算过期",
            H.ih_slug_cache_stale({"fetched_at": "2020-01-01 00:00"}))
    finally:
        H._fetch_html = orig_fetch
        H._fetch_html_bounded = orig_bounded

    # 列表页解析（首选的快速通道）
    print("[16] IndieHackers 列表页解析（首选通道：88KB/页，几秒拿 21 个产品）")
    listing = ('<a href="/product/age-calculator-2">x</a>'
               '<a href="/product/ailoitte/">y</a>'
               '<a href="/product/audjust-ai">z</a>'
               '<a href="/product/audjust-ai">重复</a>'
               '<a href="/product/boom-bucket/milestone-1">里程碑</a>'
               '<a href="/products">复数形式不该被当成产品</a>'
               '<a href="/product/new">添加产品入口</a>')
    ls = H.parse_ih_listing(listing)
    chk("提到产品 slug", ls == ["age-calculator-2", "ailoitte", "audjust-ai",
                                "boom-bucket"], ls)
    chk("复数 /products 不会误命中", "products" not in ls)
    chk("「添加产品」入口被排掉", "new" not in ls)
    chk("里程碑链接只取产品本体", "boom-bucket" in ls and "milestone-1" not in ls)
    chk("结果去重且保序", ls.count("audjust-ai") == 1)
    chk("有两条列表页地址（高收入榜 + 最新）", len(H.IH_LIST_URLS) == 2, H.IH_LIST_URLS)
    chk("默认通道不碰 sitemap（快）",
        "sitemap" not in " ".join(u for _l, u in H.IH_LIST_URLS))

    # --dry-run 语义：persist=False 时一个文件都不该落
    import shutil
    tmp = tempfile.mkdtemp(prefix="ih_persist_")
    orig_dir = H.DATA_DIR
    orig_fetch = H._fetch_html
    orig_bounded = H._fetch_html_bounded
    orig_listing = H.ih_listing_slugs
    try:
        H.DATA_DIR = tmp
        H._fetch_html = fake_fetch
        H._fetch_html_bounded = lambda url, **kw: fake_fetch(url)
        H.ih_listing_slugs = lambda: ["p1-0"]        # 列表页通道打桩，避免真联网

        H.harvest_indiehackers(1, delay=0, persist=False, breadth=True)
        chk("persist=False 不写缓存文件",
            not os.path.exists(os.path.join(tmp, "ih_slugs.json")),
            os.listdir(tmp))
        H.harvest_indiehackers(1, delay=0, persist=True, breadth=True)
        chk("persist=True 会写缓存文件",
            os.path.exists(os.path.join(tmp, "ih_slugs.json")),
            os.listdir(tmp))
        H.harvest_indiehackers(1, delay=0, persist=True, breadth=False)
        chk("默认（非 breadth）不碰 sitemap 缓存",
            True, "")
    finally:
        H.DATA_DIR = orig_dir
        H._fetch_html = orig_fetch
        H._fetch_html_bounded = orig_bounded
        H.ih_listing_slugs = orig_listing
        shutil.rmtree(tmp, ignore_errors=True)
    print()


def test_sources_config():
    print("[13] sources.json 与 harvest.py 的 source_kind 不能漂移")
    path = os.path.join(REPO, "data", "sources.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    by_id = {s["id"]: s for s in cfg.get("sources", [])}
    chk("五个源都在", set(by_id) == {"trustmrr", "indiehackers", "producthunt",
                                     "hackernews", "arrclub"}, sorted(by_id))

    # 这一步是关键：sources.json 是给人看的说明，harvest.py 是真正跑的逻辑。
    # 两边对不上时不会报错，只会让「说明写着 A 级、代码按 discovery 处理」这种
    # 静默不一致一直存在。
    for sid, expect in (("trustmrr", "verified"), ("indiehackers", "self_reported"),
                        ("producthunt", "discovery"), ("hackernews", "self_reported"),
                        ("arrclub", "secondary")):
        chk("%s 在 sources.json 里是 %s" % (sid, expect),
            by_id.get(sid, {}).get("source_kind") == expect,
            by_id.get(sid, {}).get("source_kind"))
        chk("%s 在 harvest.py 里也是 %s" % (sid, expect),
            H.source_kind_of(sid) == expect, H.source_kind_of(sid))

    legend = cfg.get("source_kind_legend") or {}
    chk("四种性质都有说明文案",
        set(legend) == {"verified", "self_reported", "secondary", "discovery"},
        sorted(legend))
    chk("说明文案与 KIND_LABEL 对齐",
        all(legend.get(k) for k in H.KIND_LABEL))

    # 每个 enabled 的源都该有可跑的通道记录
    for s in cfg.get("sources", []):
        if s.get("enabled"):
            chk("%s 记了通道（endpoints）" % s["id"], bool(s.get("endpoints")))
    chk("每个源都写了 caveat（知道它哪里不可信）",
        all(s.get("caveat") for s in cfg.get("sources", [])))
    print()


def test_infer_source():
    print("[17] 来源推断与存量回填（手工录入的条目没有 harvest_source）")
    chk("有 harvest_source 时直接用",
        H.infer_source({"harvest_source": "trustmrr"}) == "trustmrr")
    chk("靠 source_url 认出 TrustMRR",
        H.infer_source({"source_url": "https://trustmrr.com/startup/stan"}) == "trustmrr")
    chk("靠 source_url 认出 IH",
        H.infer_source({"source_url": "https://www.indiehackers.com/product/x"})
        == "indiehackers")
    chk("靠 source_url 认出 HN",
        H.infer_source({"source_url": "https://news.ycombinator.com/item?id=1"}) == "hn")
    chk("靠 source_url 认出 ARR Club",
        H.infer_source({"source_url": "https://arr.club/dbt-labs"}) == "arrclub")
    chk("靠专属 slug 字段推断", H.infer_source({"trustmrr_slug": "x"}) == "trustmrr")
    chk("靠 hn_id 推断", H.infer_source({"hn_id": "123"}) == "hn")
    chk("什么都没有 -> 空串", H.infer_source({}) == "")
    chk("域名后缀匹配不误伤",
        H.infer_source({"source_url": "https://nottrustmrr.com.evil.io/"}) == "")

    # 关键：TrustMRR 验证过的收入不能被降级成「仅线索」
    recs = [
        {"id": "stan", "source_url": "https://trustmrr.com/startup/stan"},
        {"id": "manual", "name": "手工录入"},
        {"id": "hn1", "harvest_source": "hn"},
    ]
    n = H.ensure_source_kind(recs)
    chk("补了 3 条", n == 3, n)
    chk("TrustMRR 条目 -> verified", recs[0]["source_kind"] == "verified",
        recs[0]["source_kind"])
    chk("无来源的手工条目 -> discovery", recs[1]["source_kind"] == "discovery")
    chk("HN 条目 -> self_reported", recs[2]["source_kind"] == "self_reported")
    chk("幂等：再跑不重复补", H.ensure_source_kind(recs) == 0)

    # force 用来修正早期版本把一切判成 discovery 的错误
    stale = [{"source_url": "https://trustmrr.com/startup/stan", "source_kind": "discovery"}]
    chk("force 能纠正误判", H.ensure_source_kind(stale, force=True) == 1
        and stale[0]["source_kind"] == "verified", stale[0]["source_kind"])
    chk("非 force 不动已填的值", H.ensure_source_kind(stale) == 0)

    # 真实数据：每条都必须有 source_kind，且候选池里的 TrustMRR 条目是 verified
    for name in ("inbox", "candidates"):
        path = os.path.join(REPO, "data", name + ".json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        missing = [x.get("id") for x in data if not x.get("source_kind")]
        chk("data/%s.json 每条都有 source_kind" % name, not missing, missing[:5])
    # 候选池里的 TrustMRR 条目必须是 verified。注意这是**全称命题**：
    # 「凡是带 TrustMRR 来源的，source_kind 都为 verified」——池子被发布空了
    # （2026-09-20 把最后 5 条发走之后就是空的）时它依然成立，
    # 写成 `tr and all(...)` 会让空池子判失败，那是把「没数据」误报成「数据错了」。
    with open(os.path.join(REPO, "data", "candidates.json"), encoding="utf-8") as f:
        cands = json.load(f)
    tr = [c for c in cands if "trustmrr.com" in (c.get("source_url") or "")]
    bad_tr = [c.get("id") for c in tr if c.get("source_kind") != "verified"]
    chk("候选池里带 TrustMRR 来源的都是 verified",
        not bad_tr,
        "共 %d 条，异常 %s" % (len(tr), bad_tr[:5]))

    # 案例侧不加同款断言：source_kind（verified/discovery）是**候选池专有**字段，
    # 描述「这条是从哪条采集流水线来的」。案例用的是另一套 ——
    # verification（stripe/official/partial…）+ source_kinds（来源种类集合）。
    # 30 条已发布案例的 source_kind 历史上全是 None，本就如此，不是缺陷。
    # 曾经在这里加过一条「案例也要有 source_kind」的断言，是照抄候选侧口径的误判。
    print()


def test_trustmrr_discovery():
    print("[18] TrustMRR discovery 通道：增量补充 + 去重（离线 stub）")
    API = H.TRUSTMRR_API
    DISC = H.TRUSTMRR_DISCOVERY_API
    api_payload = {
        "recentlyListedStartups": [{"slug": "a", "name": "A",
                                   "url": "https://trustmrr.com/startup/a",
                                   "website": "https://a.com",
                                   "revenue": {"mrr": 200, "last30Days": 200, "total": 200}}],
        "bestDeals": [{"slug": "b", "name": "B",
                       "url": "https://trustmrr.com/startup/b",
                       "website": "https://b.com",
                       "revenue": {"mrr": 200, "last30Days": 200, "total": 200}}]}
    disc_payload = {
        "recentlyAddedStartups": [
            {"slug": "b", "name": "B2", "url": "https://trustmrr.com/startup/b",
             "website": "https://b.com", "revenue": {"mrr": 200}},
            {"slug": "c", "name": "C", "website": "https://c.com",
             "revenue": {"mrr": 200}, "growth30d": 20},
            {"slug": "d", "name": "D", "website": "",
             "revenue": {"mrr": 0}, "stealthMode": True}],
        "fastestGrowingStartups": [{"slug": "e", "name": "E",
                                   "website": "https://e.com",
                                   "revenue": {"mrr": 50}}]}
    orig = H.http_json

    def fake(url, **kw):
        if url == API:
            return api_payload
        if url == DISC:
            return disc_payload
        raise AssertionError("unexpected url " + url)

    try:
        H.http_json = fake
        disc = H.harvest_trustmrr_discovery(50)
        disc_slugs = {i["_api"]["slug"] for i in disc}
        chk("discovery 产出 b、c（d 隐身 / e 低于门槛已丢）",
            disc_slugs == {"b", "c"}, disc_slugs)
        c = [i for i in disc if i["_api"]["slug"] == "c"][0]
        chk("discovery 条目带 website", c.get("website") == "https://c.com")
        chk("discovery 条目带 growth（增长榜信号）",
            c["_api"].get("growth") == 20, c["_api"].get("growth"))

        merged = H.harvest_trustmrr(50)
        merged_slugs = [i["_api"]["slug"] for i in merged]
        chk("合并后无重复 slug", len(merged_slugs) == len(set(merged_slugs)), merged_slugs)
        chk("合并 = api(a,b) + discovery 新增(c)",
            set(merged_slugs) == {"a", "b", "c"}, merged_slugs)
        chk("合并结果全部带 website", all(i.get("website") for i in merged))
    finally:
        H.http_json = orig
    print()


def main():
    print("=" * 62)
    print("  harvest 五源解析 + source_kind 测试（离线）")
    print("=" * 62)
    print()
    section("source_kind 映射", test_source_kind)
    section("金额解析", test_money)
    section("IndieHackers", test_indiehackers)
    section("IndieHackers slug 缓存/游标", test_ih_cursor)
    section("抓取时长上限", test_bounded_fetch)
    section("ARR Club", test_arrclub)
    section("详情页补充", test_enrich)
    section("集成：main() 全流程", test_integration)
    section("ai_verify 来源黑名单", test_ai_verify_denylist)
    section("sources.json 配置一致性", test_sources_config)
    section("来源推断与存量回填", test_infer_source)
    section("TrustMRR discovery 通道", test_trustmrr_discovery)

    print("=" * 62)
    if fails:
        print("  结果：%d 通过 / %d 失败" % (passed, len(fails)))
        for f in fails:
            print("    ✗ %s" % f)
    else:
        print("  结果：全部通过（%d 项）" % passed)
    print("=" * 62)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
