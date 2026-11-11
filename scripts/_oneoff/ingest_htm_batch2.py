#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「小团队/SaaS 可复制」筛出的第一梯队 3 条入库。

来源：`_howtomakemi_backlog小团队筛选.md` 的「6 条形态可抄」里的前三条
（autoshorts / rosie / boredhumans）。

口径纪律（与 `_howtomakemi入库评估.md` 一致）：
1. 金额只收页面明确标成 ARR / MRR / 年营收 的；¥→$ 只做「除 6.72」一个动作，
   原始 ¥ 文案逐字留在 metric_note。
2. verification 一律 partial：howtomakemi 是聚合站，页面给了具名来源但没有可点的
   一手链接。boredhumans 连具名来源都没有（只写「公开报道」），同样只能 partial，
   并在 note 里写明证据最薄。
3. 来源 kind：官网首页用 `homepage`（只证产品存在，不证明数字），聚合站用
   `aggregator` —— 这两个 kind 是 2026-10-26 才登记进 `verify_rules.SOURCE_TIERS`
   的；用 `official` 会把 A 级证据借给只有 D 级事实的条目，也会让它们被档位策略
   错误地推进精品池。
4. needs_review=True，human_read 不代勾。

用法
----
    python scripts/_oneoff/ingest_htm_batch2.py --dry
    python scripts/_oneoff/ingest_htm_batch2.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")
RATE = 6.72                      # howtomakemi 的 CNY/USD 换算常数（cursor ¥67.2亿=$1B 反推）
HOME = "https://howtomakemi.com/cases/%s/"
TODAY = "2026-10-26"


def usd(cny):
    return int(round(cny / RATE))


def src(slug, name, site):
    out = [{"label": "howtomakemi 案例页（聚合，含具名来源）",
            "url": HOME % slug, "kind": "aggregator"}]
    if site:
        out.append({"label": "%s 官网首页" % name, "url": site, "kind": "homepage"})
    return out


def case(**kw):
    base = {
        "status": "curated",
        "verification": "partial",
        "tier": "standard",
        "tier_reason": "二手聚合站转述（具名访谈/评测/官网，无可点一手链接），"
                       "按证据只到「口径待核」；数字已逐条回抄 metric_note 备查",
        "quality_score": 0,
        "needs_review": True,
        "human_read": False,
        "human_read_at": "",
        "corrections": [],
        "published_from": kw["id"],
        "verified_at": TODAY,
        "updated_at": TODAY,
        "china_fit": None,
        "solo_fit": None,
    }
    base.update(kw)
    return base


NEW = []

# ── 1. AutoShorts.ai ────────────────────────────────────────────────────────
NEW.append(case(
    id="autoshorts", name="AutoShorts.ai", name_en="AutoShorts.ai",
    origin="美国",
    one_liner="一个人做的「不露脸短视频」生成 + 自动发布订阅，28000+ 付费创作者，MRR 约 ¥69.2万",
    category="AI 工具", industry="AI 工具 / 短视频",
    caliber="mrr",
    metrics={
        "mrr": usd(692_000),
        "headline": "MRR 约 ¥69.2万（≈$103K，2026-08），28000+ 付费创作者，一个人、零融资",
        "metric_note": "页面原文：「三档订阅：19 / 39 / ¥464 每月（对应不同的每日发布条数与生成额度）」"
                       "「2024 年 2 月 1 日第一个付费用户，2 月 28 日 MRR ¥6,720；3 月 16 日 2000 → "
                       "4 月 27 日 2 万 → 5 月 13 日 4 万」「2026 年 8 月：MRR 约 ¥69.2万，"
                       "28000+ 付费创作者，累计生成 250 万+ 条视频，为创作者带来 8 亿+ 次播放」"
                       "「利润率约 75-80%，全程零融资，一个人」。"
                       "⚠ 页面早期台阶的币种混写（¥6,720 / 2000 / 2万 / 4万，很可能是 $6,720 → $20K → $40K），"
                       "只能当量级参考，不能逐点回算增速。¥→$ 只除 6.72。",
        "paying_customers": 28000,
        "team": "1 人（创始人每天 3–4 小时）",
        "growth": "2024-02 首个付费用户 → 2026-08 MRR 约 ¥69.2万（≈$103K）",
        "price_point": "三档 19 / 39 / 69 美元每月（页面写「19 / 39 / ¥464 每月」，¥464≈$69），"
                       "按每日发布条数与生成额度分档",
        "margin": "利润率约 75–80%",
        "usage": "累计生成 250 万+ 条视频，为创作者带来 8 亿+ 次播放",
    },
    models=["订阅制", "自助增长"],
    replicability={"tech": 3, "distribution": 4, "capital": 2, "timing": 2},
    verdict="它卖的从来不是「生成视频」，是「不用手动发」——内置直连 TikTok / YouTube / Instagram 的"
            "自动发布管道才是月费的理由；手动发和自动发是两个物种，而这条管道恰是最难搬的部分。",
    what_it_does="让不露脸的人批量生产短视频并自动发布：选一个方向（历史冷知识、圣经故事、"
                 "都市传说这类完播率高的题材）、选一种画风（漫画 / 迪士尼 / 乐高 / UGC 真人等几十种）、"
                 "设定发布频率，剩下的全自动——大模型写脚本、ElevenLabs 配音、图像与视频模型出画面、"
                 "自动加字幕套排版、定时推送到 TikTok / YouTube Shorts / Instagram。"
                 "用户隔几天看一眼数据就行。",
    how_it_makes_money="三档月订阅（19 / 39 / 69 美元），按每日发布条数与生成额度分档。"
                       "2026 年 8 月 MRR 约 ¥69.2万（≈$103K），28000+ 付费创作者，"
                       "利润率约 75–80%，零融资、一人运营。",
    why_it_works=[
        "它站在「别人赚钱」的卖方位置：用户靠广告分成与联盟佣金赚钱，它只收订阅费——"
        "客户的成功故事就是它的广告，而它自己不承担内容成败的风险。",
        "分发自动化是不可替代的那一环：平台开放接口、账号轮换、发布节流都要长期维护，"
        "自建成本远高于每月 19 美元。",
        "获客成本结构性低：核心用户本身就是会拍「我用这个工具做了个不露脸频道」测评视频的创作者，"
        "联盟分佣让产品自己生产自己的演示素材。",
    ],
    playbook=[
        "卖给「想赚钱的人」而不是「想省时间的人」：前者会算 ROI，也愿意为分销管道付月费。",
        "把最难维护的部分（平台直发、接口变更、账号风控）做成产品护城河，生成能力本身并不值钱。",
        "冷启动用 Meta 广告把 0→1 万 MRR 打穿（演示极直观：一句话进、几分钟出成片），"
        "稳定后停投，转联盟与口碑。",
    ],
    signals=[
        "2026-08 MRR 约 ¥69.2万（≈$103K），28000+ 付费创作者，零融资、一个人",
        "累计生成 250 万+ 条视频、带来 8 亿+ 次播放；利润率约 75–80%",
        "增长曲线全靠公开数据：2024-02 首个付费用户 → 2024-05 已到 4 万级"
    ],
    founder="Eric Smith，前 Instagram 工程师，2023 年底辞职单干，一个人、零融资、每天只干 3–4 小时。"
            "2023 年 12 月为了自动化自己的短视频，没做市场分析就写了个「又快又脏」的 MVP 自用，"
            "核心循环跑通后丢上线，才发现有同样问题的人多到超出预期。",
    traffic_sources=["Meta 广告冷启动（打出 0→1 万 MRR 后停投）", "联盟分佣 10–30%",
                     "在 X 上公开晒收入（核心用户不在 X，但潜在合作方在）"],
    tags=["短视频", "不露脸", "自动发布", "单人公司"],
    sources=src("autoshorts", "AutoShorts.ai", "https://autoshorts.ai"),
))

# ── 2. Rosie ────────────────────────────────────────────────────────────────
NEW.append(case(
    id="rosie", name="Rosie", name_en="Rosie", origin="美国 · 芝加哥郊区",
    one_liner="给本地小生意接电话的 AI 语音前台，自助 ¥329/月起，转型 8 个月做到 ARR 约 ¥672万",
    category="垂直行业 SaaS", industry="本地服务 / AI 语音接待",
    caliber="arr",
    metrics={
        "arr": usd(6_720_000),
        "headline": "ARR 突破 ¥672万（≈$1.0M，2025-08），转型后 8 个月，团队 8–10 人",
        "metric_note": "页面原文：「自助订阅，¥329/月起」「据第三方记录，Rosie 在 2025 年 8 月突破 "
                       "¥672万 ARR，而它从上一个产品转型过来只用了 8 个月」「团队约 8 到 10 人，"
                       "官网月访问约 5.3 万，机构投资人包括 Afore Capital」。"
                       "⚠ 「据第三方记录」= 聚合站转述，页面没有公司公告、财报或一手帖子的可点链接，"
                       "因此只到「口径待核」。¥→$ 只除 6.72。",
        "team": "8–10 人（转型初期砍到 6 人）",
        "growth": "2024 年前后转型，8 个月 ARR 突破 ¥672万（2025-08）",
        "price_point": "自助订阅 ¥329/月起（≈$49/月）；多门店连锁可从小档位自己长上来",
        "monthly_visits": "官网月访问约 5.3 万",
        "funding": "Afore Capital 等机构投资（金额与轮次未披露）",
    },
    models=["订阅制", "自助增长"],
    replicability={"tech": 3, "distribution": 3, "capital": 3, "timing": 3},
    verdict="它的定价就是它的获客：¥329/月起低到不需要开会讨论，注册几分钟上线——"
            "把「要不要试试」的决策成本压到零，比任何销售话术都有效。",
    what_it_does="给本地小生意（园艺、除虫、小型律所、宠物美容等仍靠电话接单的行业）做 AI 电话接听："
                 "24 小时接电话、回答常见问题、预约、过滤骚扰电话，再把通话摘要用短信或邮件发给老板。"
                 "它会读商家的 Google 商家资料与网站内容来「学会」这门生意，几分钟就能配置好。"
                 "2025 年是纯语音产品，2026 年起扩成语音 + 网页聊天 + 短信 + 邮件的多渠道客户沟通平台。",
    how_it_makes_money="自助 SaaS 订阅，¥329/月起，按门店/通话量分档。2025 年 8 月 ARR 突破 ¥672万"
                       "（≈$1.0M），团队 8–10 人，官网月访问约 5.3 万。",
    why_it_works=[
        "漏接一通电话就是丢一单，ROI 不用教育：客户本来就在 Google 上找号码打过来，接不住就是损失。",
        "价格定在「不用开会」的档位，注册到上线只要几分钟，销售环节被产品本身替掉。",
        "团队带着上一个产品的经验过来：CartHook/Rally 时期积累的支付、SMB 自助增长与结账漏斗认知，"
        "直接复用在「小生意愿意为什么付月费」这件事上。",
    ],
    playbook=[
        "先确认客户的钱是「已经在流失的」而不是「还没赚到的」——漏接电话属于前者，成交阻力最小。",
        "冷邮件用最笨的办法打穿：Mailreef + Clay，每天 1000 封、连发两个月，拿到的第一批用户同时给了产品反馈。",
        "盯真实注册转化，不要盯线索数：投 Google PMax 时线索很漂亮但注册转化极差，白烧了一个月。",
    ],
    signals=[
        "转型后 8 个月 ARR 突破 ¥672万（≈$1.0M），团队 8–10 人",
        "官网月访问约 5.3 万，机构投资人含 Afore Capital",
        "2026 年从纯语音扩到语音+网页聊天+短信+邮件的多渠道",
    ],
    founder="Jordan Gal，芝加哥郊区连续创业者：早年做日用品电商，一年做到 ¥336万后以 ¥134.4万卖出；"
            "2015 年起做 CartHook（Shopify 结账优化），做大后与 Shopify 的结账体系正面冲突；"
            "之后做 Rally（headless 结账），拿了种子轮与 A 轮但 ARR 卡在几十万美元；"
            "2024 年前后带原班人马转型 Rosie，团队砍到 6 人，90 天做出 MVP。",
    traffic_sources=["冷邮件（Mailreef + Clay，1000 封/天 × 2 个月）", "Meta 与 Google 付费广告",
                     "SEO 外包（目标付费与自然各占一半）", "低门槛自助转化"],
    tags=["语音接待", "本地服务", "自助订阅", "连续创业者"],
    sources=src("rosie", "Rosie", "https://heyrosie.com"),
))

# ── 3. BoredHumans ──────────────────────────────────────────────────────────
NEW.append(case(
    id="boredhumans", name="BoredHumans", name_en="BoredHumans", origin="美国",
    one_liner="一个人用 100 多个免费 AI 小工具拼成的 SEO 流量矩阵，ARR 约 ¥5,913.6万、零融资",
    category="AI 工具", industry="AI 小工具矩阵 / SEO 流量",
    caliber="arr",
    metrics={
        "arr": usd(59_136_000),
        "mrr": usd(4_926_000),
        "headline": "ARR 约 ¥5,913.6万（≈$8.8M），折月约 ¥492.6万（≈$733K），100+ 个工具页，一人无融资",
        "metric_note": "页面原文：「公开报道给出的数字是：年经常性收入约 ¥5,913.6万，折合每月约 ¥492.6万」。"
                       "⚠ 这是本批里证据最薄的一条：全页只有「公开报道」四个字，没有报道名、没有链接、"
                       "没有统计时点，连聚合站自己都没给出具名来源 —— 因此只到「口径待核」，"
                       "headline 里的数字当量级参考用，不可引用为已核实值。"
                       "变现是「展示广告 + 高级订阅」两条线，页面对两者拆分未披露。¥→$ 只除 6.72。",
        "tools": "100+ 个免费 AI 小工具",
        "team": "1 人",
        "price_point": "工具免费（引流），变现靠展示广告 + 高级订阅（去广告、更强算力、更高频次）",
    },
    models=["Freemium", "广告变现"],
    replicability={"tech": 2, "distribution": 3, "capital": 1, "timing": 3},
    verdict="单个小工具几乎不可能爆，但 100 个长尾工具页同时吃自然搜索，总有几个天天在接单——"
            "它赌的不是产品力，是数量对单点失败的摊薄。",
    what_it_does="不是一个产品，是一百多个产品：在同一个域名下塞满各种免费 AI 小工具——"
                 "歌词生成器、国际象棋陪练、图片处理、聊天机器人、文字游戏……每个只解决一个极小的问题。"
                 "用户打开挑一个顺手的用，工具免费，看广告或付高级费解锁更强能力。",
    how_it_makes_money="展示广告 + 高级订阅（premium）两条线，工具本身免费当引流钩子。"
                       "公开报道口径：年经常性收入约 ¥5,913.6万（≈$8.8M），折合每月约 ¥492.6万。"
                       "成本端极简：一个人 + 廉价云资源（Vercel / Stripe 一类），无销售、无增长团队、无融资。",
    why_it_works=[
        "SEO 长尾矩阵：每个小工具都是一个独立可索引的页面，100 多个页面同时跑，总有几个在长尾词上稳定接流量。",
        "持续发布摊薄风险：不打磨一个完美产品，而是高频上线新工具，单个失败不致命。",
        "边际成本极低：几乎纯广告 + 订阅收入、无人力成本，少量流量也能正向循环。",
    ],
    playbook=[
        "找大厂看不上的「无聊」细分需求，第一天就收费——这是这套模型里唯一可跨市场搬运的部分。",
        "用数量对抗单点失败：把「重复劳动产品化」当核心能力，而不是追求某个爆款。",
        "国内照搬不了它的流量前提：它的天花板建立在 Google 自然流量 + 全球分发 + Stripe/Vercel 廉价基建上，"
        "换成百度/微信生态，页面权重与变现路径都要重做。",
    ],
    signals=[
        "公开报道口径 ARR 约 ¥5,913.6万（≈$8.8M），一人、无融资、无销售团队",
        "100+ 个免费 AI 工具页构成的自然流量矩阵",
        "被列入 2026 年「Solo-Corn」（单人独角兽）代表案例，与 Base44（单人 6 个月做到 ¥127万月利润、"
        "Wix 以约 ¥5.38亿收购）、Daymaker 并列",
    ],
    founder="Nick Dobos，美国人，自学成才，没有计算机科学学位、没有外部投资人。"
            "核心能力不是某个算法，而是把重复劳动产品化然后堆量。",
    traffic_sources=["SEO 工具页矩阵", "持续发布新工具（relentless shipping）", "极简基础设施带来的低成本"],
    tags=["工具矩阵", "SEO", "一人公司", "广告变现"],
    sources=src("boredhumans", "BoredHumans", "https://boredhumans.com"),
))


def main():
    dry = "--dry" in sys.argv
    with open(CASES, encoding="utf-8") as f:
        data = json.load(f)
    have = {c["id"] for c in data}
    add = [c for c in NEW if c["id"] not in have]
    print("库内现有 %d 条；本次待写 %d 条（已存在跳过 %d）"
          % (len(data), len(add), len(NEW) - len(add)))
    for c in add:
        m = c["metrics"]
        print("  + %-12s %-7s %-14s %s"
              % (c["id"], c["caliber"], "arr" if "arr" in m else "",
                 m.get("headline", "")[:56]))
    if dry:
        print("[dry] 未写盘")
        return 0
    if not add:
        return 0
    data.extend(add)
    atomicio.write_json(CASES, data, indent=2)
    print("已写入 %s（%d 条）" % (CASES, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
