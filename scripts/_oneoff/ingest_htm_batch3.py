#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「有条件」6 条入库（serviceform / salesrobot / jobright / sandra-ai / outward / workhero）。

来源：`_howtomakemi_backlog小团队筛选.md` 的「6 条有条件」档 —— 产品形态成立，
但缺的是渠道、平台、创始人 IP、行业履历或真人兜底，不是产品本身。

口径纪律（与前两批一致，见 `_howtomakemi入库评估.md`）：
1. 金额只收页面明确标成 ARR / MRR / 年营收 的；¥→$ 只做「除 6.72」一个动作，
   原始 ¥ 文案逐字留在 metric_note。
2. verification 一律 partial；sources 只用 `aggregator`（聚合站）+ `homepage`（官网首页）。
3. 🚨 本批有两条「页面收入栏 ≠ 正文数字」，必须在 note 里点明，不许取大值：
   - serviceform：栏里 ¥1.01亿 是 Seedtable **年营收估测**的下界，不是月收入。
   - outward：栏里 ¥672万 是「商业化后 8 个月累计收入」，正文另称 ARR 已过 8 位数
     （>¥6,720万）。本库取「8 位数」说法的下界，并在 note 里写明这是下界。
4. needs_review=True，human_read 不代勾。

用法
----
    python scripts/_oneoff/ingest_htm_batch3.py --dry
    python scripts/_oneoff/ingest_htm_batch3.py
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

# ── 1. Serviceform ──────────────────────────────────────────────────────────
NEW.append(case(
    id="serviceform", name="Serviceform", name_en="Serviceform", origin="芬兰",
    one_liner="把「官网上那张没人填的联系表单」升级成 AI 销售/客服 Agent，"
              "1500+ 网站、20+ 国家，年营收估测约 ¥1.01亿",
    category="垂直行业 SaaS", industry="官网获客 / 对话式 Agent",
    caliber="annual",
    metrics={
        "annual_revenue": usd(101_000_000),
        "headline": "第三方估测年营收 ¥1.01亿–¥2.69亿（≈$15.0M–$40.0M），1500+ 网站在跑，70+ 人",
        "metric_note": "页面原文：「第三方数据库（Seedtable）对它年营收的估测在 ¥1.01亿–¥2.69亿区间"
                       "——这类估测偏乐观，但至少说明它不是小作坊了」「官网自述 1500+ 网站 在跑 Serviceform，"
                       "覆盖 20+ 国家」「70+ 员工，分布在图尔库、斯德哥尔摩、巴塞罗那、伦敦、科伦坡五个办公室」"
                       "「平均给客户带来 +71% 营收提升（官网口径）」。"
                       "⚠ 页面「💰 月收入」栏里的 ¥1.01亿 是**年营收估测区间的下界**，不是月收入、也不是官方披露值 —— "
                       "本条 caliber 记 annual，headline 写成区间，不取大值。¥→$ 只除 6.72。",
        "team": "70+ 人（五个办公室：图尔库 / 斯德哥尔摩 / 巴塞罗那 / 伦敦 / 科伦坡）",
        "customers": "1500+ 网站在跑，覆盖 20+ 国家；案例客户含 RE/MAX、Peugeot、Hyundai、Sotheby's",
        "funding": "累计约 ¥3,292.8万（≈$4.9M）：芬兰国家技术创新局领投种子轮，"
                   "BackingMinds、Gorilla Capital 跟投，2024-11 完成约 ¥1,747.2万一轮",
        "price_point": "SaaS 订阅，按站点与功能模块分级（价格页未公开明细）",
    },
    models=["订阅制", "代理商分销"],
    replicability={"tech": 3, "distribution": 2, "capital": 4, "timing": 3},
    verdict="它起手的东西小到不像生意——官网上那张没人填的表单；九年后长成 AI 销售 Agent。"
            "可抄的是这个起点（从「客户已经在填的表单」切进去），抄不动的是 145+ 家代理商和五个办公室的地推。",
    what_it_does="面向中小企业的 AI 对话式获客平台，核心仍是「帮企业在官网上多抓一个客户」："
                 "AI 销售/客服 Agent 7×24 应答并把对话往成交推（不是把客户打发走）、"
                 "带条件逻辑的智能表单、对话里直接约看房/约试驾/约面试、"
                 "WhatsApp 营销与线索跟进工作流。主打房地产、汽车经销、电商三个垂直。",
    how_it_makes_money="SaaS 订阅，按站点数与功能模块分级。年营收为第三方估测（¥1.01亿–¥2.69亿），"
                       "非官方披露。公司 70+ 人、五个办公室，2024-11 还在拿融资 —— "
                       "说明它是「用销售和代理商推着走的 SaaS」，不是靠自助增长。",
    why_it_works=[
        "切入点极小：不是造新需求，是把「官网那张表单」这个既有转化口子做到极致。",
        "KPI 站在营收一侧：多数 AI 客服卖「减少人工」（deflection），它卖「多成交」，"
        "预算从销售口出而不是客服口出，天花板完全不同。",
        "145+ 全球代理商把交付外包出去，自己只留产品与品牌——这是它覆盖 20+ 国家的原因。",
        "垂直行业标杆客户（RE/MAX / Peugeot / Sotheby's）让同行业复制时自带信任。",
    ],
    playbook=[
        "从「客户已经在填的表单」切进去，而不是从「客户还没做的事」切进去——前者的预算已经存在。",
        "把成效数字当广告语：「线索到成交 +460%」这类客户案例数字，比任何功能介绍都管用。",
        "自助增长撑不住 B2B 大单时，用代理商网络换覆盖：把交付能力开放出去，自己收订阅。",
    ],
    signals=[
        "1500+ 网站在跑、20+ 国家、70+ 员工（官网自述）",
        "累计融资约 ¥3,292.8万，2024-11 仍在新一轮",
        "官网口径：客户平均营收提升 +71%",
    ],
    founder="Iranthi Gomes（CEO）与 Jarkko Oksanen（CTO），2018 年在芬兰图尔库创立。"
            "About 页强调「still founder-led, still shipping every week」——"
            "70+ 人、五个办公室、跑了九年，把「还在一线、还每周发版」当卖点。",
    traffic_sources=["145+ 全球代理商网络（代理商 / 咨询公司 / 集成商贴牌带客户）",
                     "垂直行业标杆客户背书（房地产 / 汽车 / 电商）", "五国本地团队做地推",
                     "官网成效数据当广告语（+71% 营收、线索翻倍）"],
    tags=["对话式获客", "表单", "代理商分销", "垂直 SaaS"],
    sources=src("serviceform", "Serviceform", "https://serviceform.com"),
))

# ── 2. SalesRobot ───────────────────────────────────────────────────────────
NEW.append(case(
    id="salesrobot", name="SalesRobot", name_en="SalesRobot", origin="印度",
    one_liner="用 LinkedIn 官方移动端 API 做外联自动化、号称封号率 0.00001%，"
              "bootstrapped 五年做到 4100+ 用户、MRR 约 ¥33.6万",
    category="营销工具", industry="LinkedIn 冷启动 / 销售自动化",
    caliber="mrr",
    metrics={
        "mrr": usd(336_000),
        "headline": "MRR 约 ¥33.6万（≈$50K），4100+ 用户、110+ 家白标代理，bootstrapped 五年",
        "metric_note": "页面原文：「Basic：每 LinkedIn 账号 ¥262/月（年付）；Advanced：¥396/月；"
                       "Professional：¥531/月」「白标代理计划：代理商月利润 ¥6.7万+」"
                       "「创始人用自家产品，27 个月从 0 做到 ¥33.6万 MRR」「bootstrapped 五年做到 4100+ 用户」。"
                       "⚠ MRR 为创始人自述、经聚合站转述，无 Stripe 直读页面。"
                       "「封号率 0.00001%」是产品自述，无法独立验证。¥→$ 只除 6.72。",
        "paying_customers": 4100,
        "team": "两位创始人 + 小团队（公开页面未给确切人数）",
        "price_point": "¥262 / ¥396 / ¥531 每月（年付，≈$39 / $59 / $79），按 LinkedIn 账号数分档；"
                       "AI Appointment Setter 与邮件自动化另计",
        "funding": "无外部融资（bootstrapped）",
        "growth": "27 个月从 0 到 MRR ¥33.6万（创始人自述）",
    },
    models=["订阅制", "白标分销"],
    replicability={"tech": 3, "distribution": 3, "capital": 2, "timing": 3},
    verdict="它卖的不是「自动化」，是「账号不会死」——把最吓人的那个风险（封号）做成唯一差异点，"
            "价格和功能都退到第二位。这个定位法可抄，LinkedIn 本身不可抄。",
    what_it_does="LinkedIn + 邮件双通道外联自动化平台。与早期浏览器插件派的区别在底层："
                 "走 LinkedIn 官方移动端 API 而非模拟浏览器、每账号配专用住宅 IP、"
                 "收到警告自动降速；上层提供 AI 个性化语音/视频消息（克隆声音与形象）、"
                 "AI Appointment Setter（5 分钟内回消息、自主约会议）与白标代理计划。",
    how_it_makes_money="SaaS 订阅，按 LinkedIn 账号数三档（≈$39 / $59 / $79 每月，年付），"
                       "AI 邀约与邮件自动化作附加组件另计。MRR 约 ¥33.6万（≈$50K），"
                       "4100+ 用户，110+ 家代理商贴牌销售，零融资、bootstrapped 五年。",
    why_it_works=[
        "把行业最大的恐惧量化成一个数字：封号率 0.00001%。在对比测评里，「最安全」这个标签自己会传播。",
        "底层换成官方移动端 API + 住宅 IP，绕开了插件派被平台识别的结构性缺陷——"
        "这是工程选择，不是话术。",
        "创始人先当自己产品的头号用户：SalesRobot 的第一批客户是它自己冷外联谈下来的。",
        "白标代理把客户变成分销网络：110+ 家代理商各自带客户进来，边际获客成本趋近于零。",
    ],
    playbook=[
        "在一个「有硬恐惧」的品类里，把恐惧本身做成卖点，而不是回避它。",
        "差异化落在底层工程（API vs 插件、住宅 IP、动态节流），别落在功能列表长度上。",
        "用自家产品卖自家产品：第一批客户必须靠产品自己获取，否则你不知道它到底能不能用。",
    ],
    signals=[
        "MRR 约 ¥33.6万（≈$50K），4100+ 用户，零融资五年（创始人自述）",
        "110+ 家白标代理商，代理商月利润 ¥6.7万+",
        "定价 ¥262 / ¥396 / ¥531 每月（年付）",
    ],
    founder="Saurav Gupta 与 Arun Singh，印度德里，2020 年 5 月创立。"
            "Saurav 在此前一个 SaaS 上闷头做了 18 个月功能、发 LinkedIn DM 没回音，"
            "最后账上只剩 ¥860 关门——「做 maker 不做 seller 必死」是 SalesRobot 的由来。",
    traffic_sources=["创始人亲自做冷外联（用自家产品）", "对比测评里的「最安全」定位",
                     "白标代理渠道 110+ 家", "销售圈口碑"],
    tags=["LinkedIn", "冷外联", "白标分销", "bootstrapped"],
    sources=src("salesrobot", "SalesRobot", "https://salesrobot.co"),
))

# ── 3. Jobright ─────────────────────────────────────────────────────────────
NEW.append(case(
    id="jobright", name="Jobright", name_en="Jobright", origin="美国",
    one_liner="港大博士婉拒 Meta 百万年薪，两年把 AI 求职平台做到月访问 500 万、ARR 约 ¥3,360万且已盈利",
    category="AI 工具", industry="求职 / 人才匹配",
    caliber="arr",
    metrics={
        "arr": usd(33_600_000),
        "headline": "ARR 突破 ¥3,360万（≈$5.0M）且已盈利，月访问 500 万+，累计融资约 ¥5,376万–¥6,048万",
        "metric_note": "页面原文：「ARR 突破 ¥3,360万，并且已经实现盈利」「Jobright 在美国的月访问量超过 500 万」"
                       "「早期 ¥3,024万（Lanchi Capital 领投，UpHonest Ventures、源码资本跟投），"
                       "2025 年 6 月 25 日在用户规模 52 万、Jobright Agent 正式发布时完成新一轮天使轮"
                       "（HR Tech Investments 与 Translink Capital 联合投资），累计约 ¥5,376万至¥6,048万」。"
                       "⚠ ARR 与盈利性来自公司对外口径经聚合站转述，无财报或支付平台直读。¥→$ 只除 6.72。",
        "monthly_visits": "美国月访问 500 万+",
        "paying_customers": "用户规模 52 万（2025-06，公司口径）",
        "funding": "累计约 ¥5,376万–¥6,048万（≈$8.0M–$9.0M），Lanchi Capital 领投早期轮",
        "price_point": "C 端免费 + 增值订阅，同时对 B 端招聘需求收费",
    },
    models=["Freemium", "订阅制", "双端收费"],
    replicability={"tech": 3, "distribution": 2, "capital": 3, "timing": 2},
    verdict="它靠一个「不是核心功能」的小功能引爆冷启动：H-1B 赞助历史查询。"
            "产品力之外，它真正稀缺的是创始人把自己的移民经历做成了内容 IP——这条国内抄不动。",
    what_it_does="面向北美求职者的 AI 原生求职平台，做 LinkedIn 没做好的部分："
                 "岗位与人的真实匹配度判断、投递进度追踪、目标公司内部人脉、"
                 "以及让它出圈的 H-1B 签证担保历史查询（这家公司愿不愿意为你办签证）。"
                 "2025 年推出 Jobright Agent，把「帮你找」升级成「替你跑」。",
    how_it_makes_money="C 端免费 + 增值订阅，同时向 B 端招聘需求收费。"
                       "ARR 突破 ¥3,360万（≈$5.0M）且已盈利——对靠融资买流量的同类 C 端 AI 产品来说罕见。"
                       "美国月访问 500 万+，累计融资约 ¥5,376万–¥6,048万。",
    why_it_works=[
        "用一个信息鸿沟当钩子：H-1B 担保历史在别处几乎查不到，却是国际求职者的生死信息，"
        "获客效率远高于「智能匹配」这类抽象卖点。",
        "创始人自己做 IP：从 4000 粉丝起，在 LinkedIn 写刚到美国不会用 Google Maps 的真实经历，"
        "一年涨到 5 万粉、单篇最高 150 万浏览——在高获客成本的硅谷，这是被低估的渠道。",
        "口碑爆发是用户带来的：一篇「海投 5000 份简历终获特斯拉 offer」的帖子几天带来近 3 万注册。",
        "双端收费：C 端订阅 + B 端招聘需求，不必把全部压力压在消费者付费上。",
    ],
    playbook=[
        "先找「查得到 vs 查不到」的信息鸿沟，再谈 AI——鸿沟本身就是产品。",
        "创始人 IP 是冷启动的杠杆，前提是你愿意把私人经历写出来，而不是发产品更新。",
        "在求职这类天然聚集的圈层里，一个被验证的社群会自己扩散，别急着投放。",
    ],
    signals=[
        "ARR 突破 ¥3,360万（≈$5.0M）且已盈利（公司口径）",
        "美国月访问 500 万+，用户 52 万（2025-06）",
        "累计融资约 ¥5,376万–¥6,048万，Lanchi Capital 领投",
    ],
    founder="郑玉典（Ethan Zheng），联合创始人兼 CTO。南京大学软件本科、香港大学博士（数据库与数据挖掘），"
            "在 Twitter 三年做到带 20 多人广告机器学习团队的技术主管，后加入 NewsBreak 从零搭推荐系统。"
            "2023-03-14（他生日，也是 GPT-4 发布日）与合伙人 Eric 带着一份概念 PPT 开始融资，"
            "几乎同时婉拒 Meta 的百万年薪 offer。",
    traffic_sources=["H-1B 担保查询的社群口碑（印度裔求职者圈层）",
                     "创始人 LinkedIn IP（1 年 4 千→5 万粉，单篇 150 万浏览）",
                     "用户自发的千万级浏览帖子", "TikTok 与校园社群自发推荐"],
    tags=["求职", "H-1B", "创始人 IP", "双端收费"],
    sources=src("jobright", "Jobright", "https://jobright.ai"),
))

# ── 4. Sandra AI ────────────────────────────────────────────────────────────
NEW.append(case(
    id="sandra-ai", name="Sandra AI", name_en="Sandra AI", origin="法国",
    one_liner="三个巴黎综合理工同学（都做过麦肯锡）做的车行 AI 语音前台，24/7 多语种直接约试驾，"
              "2025 年营收约 ¥295.7万、YC 背书",
    category="垂直行业 SaaS", industry="汽车经销 / AI 语音前台",
    caliber="annual",
    metrics={
        "annual_revenue": usd(2_957_000),
        "headline": "2025 年营收约 ¥295.7万（≈$440K，月 run rate 约 ¥24.9万），4 人团队、YC F24",
        "metric_note": "页面原文：「营收：2025 年约 ¥295.7万（约 ¥24.9万/月 run rate）」"
                       "「融资：种子轮约 ¥336万（2024-12，YC F24 + Kima Ventures、Rethink Capital 等）」"
                       "「三个创始人、4 人团队」「行业里大约 30% 的来电没人接，而 80% 的保养预约还是靠电话约」。"
                       "⚠ 页面「💰 月收入」栏写的是年营收，本条 caliber 记 annual；"
                       "「¥24.9万/月 run rate」是页面自己按 12 个月摊的折算值，不是独立观测的 MRR，故不写进 metrics。"
                       "¥→$ 只除 6.72。",
        "team": "4 人（三位创始人 + 1 人）",
        "price_point": "B2B 订阅：单店买 AI 前台席位；大体量经销商集团按门店数打包（价格未公开）",
        "funding": "种子轮约 ¥336万（≈$500K，2024-12，YC F24 + Kima Ventures、Rethink Capital 等）",
        "customers": "汽车经销商与修理厂（门店数未披露）",
    },
    models=["订阅制", "B2B 直销"],
    replicability={"tech": 3, "distribution": 3, "capital": 3, "timing": 3},
    verdict="它不卖「AI」，卖「你每店每年因为漏接电话亏掉的钱」——把痛点换算成金额摆在老板面前，"
            "比讲多语种模型好卖十倍。可抄的是这个换算方式，抄不动的是 YC 网络给的第一批车行客户。",
    what_it_does="给汽车经销商和修理厂做 AI 语音前台：24/7 用多语种、真人感的声音接电话，"
                 "回答车型与库存、报服务时间、直接在通话里排试驾和保养预约，"
                 "并能深层接入经销商的 DMS（经销商管理系统）去查真实库存与排期。",
    how_it_makes_money="B2B SaaS 订阅，按门店数与通话/消息量收费：单店买 AI 前台席位，"
                       "经销商集团按门店打包。2025 年营收约 ¥295.7万（≈$440K），"
                       "种子轮约 ¥336万，团队 4 人。",
    why_it_works=[
        "痛点可量化到钱：行业里约 30% 来电没人接、80% 保养预约仍靠电话——漏一通就是丢一单。",
        "落地在 DMS 里而不是浮在表面：能查真实库存与排期，才叫「接待」，否则只是语音菜单。",
        "YC 身份在保守的 B2B 采购里是背书，尤其对车行老板这种看人不看 PPT 的客群。",
        "demo 自带传播力：多语种真人感接电话，老板听一遍就懂，不需要解释模型。",
    ],
    playbook=[
        "把漏掉的钱算成具体金额，再谈产品——B2B 老板只对「我损失了多少」有反应。",
        "接进对方已有的系统（DMS / CRM / 排期），价值才从「省人工」升级成「不漏单」。",
        "选一个仍然重度依赖电话的行业，越是「还没被 SaaS 改造」的地方，AI 语音的边际价值越高。",
    ],
    signals=[
        "2025 年营收约 ¥295.7万（≈$440K），4 人团队",
        "YC 2024 秋季 batch，种子轮约 ¥336万（2024-12）",
        "接入经销商 DMS，能查真实库存与排期",
    ],
    founder="Badr El Idrissi Mokdad（CEO）、Ismail Majjad（CPO）、Skandere Sahli（CTO），"
            "三人十年前在 École Polytechnique 相识。Badr 与 Ismail 都做过麦肯锡（Badr 还待过 Bain，"
            "深耕 BPO/呼叫中心），Skandere 在麦肯锡 AI 部门 QuantumBlack 和 MIT 待过。",
    traffic_sources=["「漏接电话=丢单」的量化痛点话术", "YC 网络带来首批经销商客户与信任",
                     "demo 在汽车零售圈口碑传导"],
    tags=["AI 语音", "汽车经销", "YC", "B2B 订阅"],
    sources=src("sandra-ai", "Sandra AI", "https://www.sandra-ai.com"),
))

# ── 5. Outward Intelligence ─────────────────────────────────────────────────
NEW.append(case(
    id="outward", name="Outward Intelligence", name_en="Outward Intelligence", origin="美国",
    one_liner="三个拒绝 VC 的中年人（Google / Palantir / Harris Poll 出身），"
              "用 AI 把又慢又重的民调公司做到 8 位数 ARR",
    category="企业服务", industry="市场调研 / 民意调查",
    caliber="arr",
    metrics={
        "arr": usd(67_200_000),
        "headline": "ARR 已过 8 位数（页面称超 ¥6,720万，≈$10.0M，取其下界），零 VC、8 个月做到 ¥672万收入",
        "metric_note": "页面原文：「三位创始人 2023 年 3 月创立，不拿任何 VC」「商业化后 8 个月收入突破 ¥672万」"
                       "「公开报道（Business Insider 2026-06-26）称查验过其 2025 年税表，验证 7 位数收入」"
                       "「后续报道称其已达 8 位数 ARR（年经常性收入超 ¥6,720万）」。"
                       "⚠ 页面「💰 月收入」栏里的 ¥672万 是**商业化后 8 个月的累计收入**，"
                       "既不是月收入也不是 ARR；ARR 只有「8 位数、超 ¥6,720万」这个模糊说法，"
                       "本库取下界 $10.0M 并在 headline 标明是下界，不据此推算月收入。¥→$ 只除 6.72。",
        "team": "三位创始人起家（2025-11 引入前 Harris Poll CEO Will Johnson 任 CEO）",
        "customers": "Hims & Hers、Warby Parker、T-Mobile 等品牌",
        "funding": "不接受 VC（自筹）",
        "price_point": "B2B 调研服务：按项目交付 + 订阅（价格未公开）",
    },
    models=["项目制", "订阅制"],
    replicability={"tech": 3, "distribution": 2, "capital": 3, "timing": 3},
    verdict="它把「一串人才能交付」的调研流程压缩成 AI 流水线，但真正难抄的不是 AI，"
            "是三位创始人在 Harris Poll / Palantir / Google 攒下的方法论与品牌客户名单。",
    what_it_does="AI 驱动的定量市场调研与民意调查平台。传统调研靠研究员、分析师、项目经理、"
                 "质控、运营一整串人交付一份报告；它把问卷生成、抽样、响应质量校验、"
                 "程序化分析、报告交付压成流水线，同时保留行业专家（前 Harris Poll 的人）把关数据质量。",
    how_it_makes_money="B2B 调研服务，按项目交付 + 订阅。用 AI 自动化把人工成本压到极低、维持高毛利。"
                       "商业化后 8 个月收入突破 ¥672万；8 位数 ARR（页面称超 ¥6,720万）。"
                       "不收 VC，用收入养公司。",
    why_it_works=[
        "创始人的行业履历可以直接替代早期招聘：产品、研究方法、技术战略三块各有一个做过的人。",
        "把「交付周期」当核心指标：调研行业的痛点是慢和重，AI 压掉的是流程而不是结论。",
        "品牌客户背书（Hims & Hers、Warby Parker、T-Mobile）对同类 B2B 客户是强信号。",
        "反 VC 叙事自带传播力：Business Insider 愿意写「三个中年人拒绝 VC 做到 8 位数 ARR」。",
    ],
    playbook=[
        "找一个「人力密度极高、结论标准化」的行业，用 AI 压流程而不是压质量。",
        "方法论必须由行业里的人来背书，否则客户不信你的数据——这是 to B 调研的准入条件。",
        "不拿 VC 也能做：现金流从第一天就是正的，前提是客单价足够覆盖交付成本。",
    ],
    signals=[
        "商业化 8 个月收入突破 ¥672万；后续报道称 8 位数 ARR（超 ¥6,720万）",
        "Business Insider 2026-06-26 称查验过其 2025 年税表，验证 7 位数收入",
        "零 VC，2025-11 引入前 Harris Poll CEO 出任 CEO",
    ],
    founder="三位联合创始人：Amir Kanpurwala（前 Harris Poll 首席产品官）、"
            "Abhish Raghavan（前 Harris Poll 定制研究负责人）、"
            "Brian Tatum（前 Palantir 部署负责人，七年 Palantir，曾任 Maersk、HSBC 首席数据官）。"
            "2025 年 11 月引入前 Harris Poll CEO Will Johnson 出任 CEO。",
    traffic_sources=["创始人行业履历背书", "品牌客户名单（Hims & Hers / Warby Parker / T-Mobile）",
                     "Business Insider 等媒体叙事"],
    tags=["市场调研", "民调", "零 VC", "流程自动化"],
    sources=src("outward", "Outward Intelligence", "https://www.outwardintelligence.com"),
))

# ── 6. WorkHero ─────────────────────────────────────────────────────────────
NEW.append(case(
    id="workhero", name="WorkHero", name_en="WorkHero", origin="美国",
    one_liner="盯住 20 人以下的 HVAC 小店，用 AI Agent + 真人办公室经理接管开票报批退税保修，"
              "帮老板每周省 15–20 小时，ARR 约 ¥336万",
    category="垂直行业 SaaS", industry="HVAC 承包商 / AI 后台外包",
    caliber="arr",
    metrics={
        "arr": usd(3_360_000),
        "headline": "ARR 约 ¥336万（≈$500K，数月内做到），11 人远程团队，"
                    "种子轮 ¥3,360万（≈$5.0M，2025-10）",
        "metric_note": "页面原文：「营收方面，其 Founding Growth Marketer 招聘启事里写得很直白："
                       "数月内做到 ¥336万 ARR」「2025 年 10 月，WorkHero 宣布 ¥3,360万种子轮，Navitas Capital 领投」"
                       "「团队只有 11 人，且高度远程」「美国 HVAC 是个 ¥6,720亿的行业，但 85% 的公司员工少于 20 人」"
                       "「小 HVAC 老板每周花 20+ 小时在行政事务上」。"
                       "⚠ ARR 来源是招聘启事而非公司公告，属公司自述经聚合站转述；"
                       "页面另引第三方数据库（Datanyze）估测年营收约 ¥1,344万，与本库取值不同源，仅备查。"
                       "¥→$ 只除 6.72。",
        "team": "11 人，高度远程（剑桥 / 布鲁克林 / 办公室经理散布全美）",
        "funding": "种子轮 ¥3,360万（≈$5.0M，2025-10，Navitas Capital 领投，"
                   "Workshop Ventures、York IE 及前 ServiceTitan 高管等天使参投）",
        "price_point": "按月订阅的「AI 后台外包」，定价未公开；对标雇一个全职办公室经理（年 ¥33.6万起）的零头",
        "customers": "20 人以下的 HVAC 承包商",
    },
    models=["订阅制", "服务外包"],
    replicability={"tech": 2, "distribution": 2, "capital": 3, "timing": 3},
    verdict="它的定价锚不是软件，是「雇一个人的零头」——AI 干活、真人行业老手复核。"
            "这类生意的门槛不在模型，在你有没有 HVAC 行业的人来做复核。",
    what_it_does="面向小 HVAC（暖通空调）承包商的 AI 后台：AI 自动开票、拉取施工许可、申报热泵退税、"
                 "注册设备保修、维护价格表；每个客户再配一位 HVAC 行业出身的办公室经理做主动复核与财务建议。"
                 "老板不需要打开电脑，活就干完了。",
    how_it_makes_money="按月订阅的「AI 后台外包」：客户买到的是 AI 执行 + 真人复核的组合，"
                       "成本约为雇一个全职办公室经理（年 ¥33.6万+）的零头。"
                       "ARR 约 ¥336万（数月内做到，出自招聘启事），种子轮 ¥3,360万，11 人。",
    why_it_works=[
        "价格锚定人力而不是软件：对标「雇一个人一年 ¥33.6万起」，订阅费显得微不足道。",
        "AI + 真人复核的分工解决了纯 AI 不敢碰的活：退税申报、施工许可这类错一次就赔钱的流程。",
        "政策复杂度即护城河：各州热泵退税规则越繁琐，代理申报的价值越高，通过率接近 100% 就是卖点。",
        "用行业联合调研发声：和 HVAC School 一起调研出「老板每周 20+ 小时耗在行政上」，权威性远高于自吹。",
    ],
    playbook=[
        "选「85% 都是 20 人以下」的分散行业：大厂看不上，SaaS 卖不动，正好是 AI 后台的空位。",
        "把 AI 放在执行位、把行业老手放在复核位——纯 AI 交付不了「错一次就赔钱」的流程。",
        "定价挂在客户最熟悉的人力成本上，别挂在软件订阅的行业均价上。",
    ],
    signals=[
        "ARR 约 ¥336万，数月内做到（出自招聘启事）",
        "种子轮 ¥3,360万（2025-10，Navitas Capital 领投），11 人远程团队",
        "退税申报处理时间缩短 50%、通过率接近 100%（公司口径）",
    ],
    founder="Furman Haynes 与 Kyler Evitt，两位哈佛商学院毕业生，2022 年夏在 Year Up 实习时"
            "结识 Gerald Chertavian（Year Up 创始人，2023 年卸任 CEO 后在哈佛商学院教书，现任 WorkHero 董事长）。"
            "公司由 Workshop Ventures 孵化。",
    traffic_sources=["与 HVAC School 联合调研的内容营销", "创始人叙事（Boston Globe 报道）",
                     "「让小老板对抗 PE 并购」的社会影响力定位", "把州级退税政策变成刚需"],
    tags=["HVAC", "AI 后台", "真人复核", "蓝领行业"],
    sources=src("workhero", "WorkHero", "https://www.workhero.pro"),
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
        print("  + %-12s %-7s %s" % (c["id"], c["caliber"], c["metrics"].get("headline", "")[:60]))
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
