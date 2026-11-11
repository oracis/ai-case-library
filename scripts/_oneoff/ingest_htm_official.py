#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 howtomakemi.com「官方披露」里**收入口径明确**的 8 条写入 data/cases.json。

口径纪律（对应 MEMORY 红线）：
1. 只收页面把金额明确标成 ARR / 年营收 / 月收入 的条目；标成融资额的（如 abstract 的
   ¥1.68亿 Series A）一律不收 —— 那是 68 条里的「仅融资」档。
2. 页面以人民币计价（实测汇率常数：cursor ¥67.2亿 = $1B、lovable ¥13.44亿 = $200M
   ⇒ 6.72 CNY/USD），本库统一按 USD 存数值，**换算只做除 6.72 这一个动作**，
   原始 ¥ 文案逐字写进 metric_note，任何时候都能回查。
3. verification 一律 partial：howtomakemi 是聚合站，页面给了具名来源（访谈/评测/官网）
   但没有可点的一手链接，够不到 official（与 audit_evidence 的判据一致）。
4. needs_review=True，human_read 不代勾。

用法： python scripts/_oneoff/ingest_htm_official.py [--dry]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")
RATE = 6.72                      # howtomakemi 的 CNY/USD 换算常数（站点实测）
HOME = "https://howtomakemi.com/cases/%s/"
TODAY = "2026-10-26"


def usd(cny):
    """¥ → $，只做这一个除法，四舍五入到整数。"""
    return int(round(cny / RATE))


def src(slug, name, site, kind="aggregator", label=None):
    out = [{"label": label or ("howtomakemi 案例页（聚合，含具名来源）"),
            "url": HOME % slug, "kind": kind}]
    if site:
        out.append({"label": "%s 官网" % name, "url": site, "kind": "official"})
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

# ── 1. Arcads ────────────────────────────────────────────────────────────────
NEW.append(case(
    id="arcads", name="Arcads", name_en="Arcads", origin="法国 · 巴黎",
    one_liner="用 AI 演员批量生成可直接投放的视频广告，10 人团队做到 2026 年营收约 ¥1.01亿",
    category="营销工具", industry="AI 视频广告素材",
    caliber="annual",
    metrics={
        "revenue": usd(101_000_000),
        "headline": "2026 年营收约 ¥1.01亿（≈$15.0M），6000 付费客户，10 人团队",
        "metric_note": "页面原文：月收入栏 ¥1.01亿；「2026 年营收约 ¥1.01亿」「6000 付费客户，"
                       "10 人团队，人均营收约 ¥672万」。ARR 台阶：2024-06 $1M → 2025-05 $6M → "
                       "2025-11 ¥6,720万（$10M）→ 2026 年营收 $15M。¥→$ 按站点汇率 6.72 折算"
                       "（本库统一 USD），原值保留在库外，可回页核对。⚠ 页面自标「融资口径矛盾」："
                       "创始人称完全 bootstrapped 且盈利，GetLatka 汇总页另列 raised ¥1.68亿。",
        "paying_customers": 6000,
        "team": "10 人",
        "growth": "2024-06 ARR $1M → 2025-05 $6M → 2025-11 $10M → 2026 年营收 $15M",
        "price_point": "按生成量计费的用量订阅；企业年单可达 ¥67.2万（≈$10万）",
    },
    models=["订阅制", "按量计费"],
    replicability={"tech": 3, "distribution": 3, "capital": 2, "timing": 3},
    verdict="它卖的从来不是「AI 生成视频」，而是「能直接投出去、且投得起的广告素材」——"
            "把 UGC 拍摄的排期、版权、演员三笔成本一起删掉，付费理由才不需要解释。",
    what_it_does="输入脚本，平台用 AI 演员生成带真人感的口播视频广告，直接省掉 UGC 创作者拍摄、"
                 "排期、版权那一整套流程。创始人上一个项目是七位数营收的减肥 App，"
                 "深知付费广告素材是增长的核心瓶颈：要持续产出、要快、要便宜。",
    how_it_makes_money="用量订阅制（按生成量计费），企业年单可达 ¥67.2万。2026 年营收约 ¥1.01亿，"
                       "6000 付费客户，10 人团队，人均营收约 ¥672万。",
    why_it_works=[
        "痛点是「素材产能」而不是「创意」：投放团队缺的是一周几十条能跑的素材，AI 演员直接把这个产能做成商品。",
        "客户是拿着广告预算的投放方，素材成本对比投放预算只占几个点，价格敏感度极低。",
        "10 人团队靠约 100 个搭在 Gumloop 上的内部 agent 做竞品广告监控、红人 sourcing 与内容生成，把人均营收推到 ¥672万。",
    ],
    playbook=[
        "先确认你的客户手上已经有一笔「必须花掉」的预算（这里是广告投放费），再卖给他降本工具——预算在，成交不用教育。",
        "把生成能力打包成「按量计费」，让客户按产能扩张付费，而不是按席位——月费封顶会直接压制你的收入上限。",
        "用自己产品做自己的投放素材：既是获客，也是最好的产品演示，还顺手攒下「什么素材能跑」的数据。",
    ],
    signals=[
        "10 人团队、6000 付费客户、2026 年营收约 ¥1.01亿，人均营收约 ¥672万",
        "ARR 从 2024-06 的 $1M 到 2025-11 的 $10M，约 17 个月 10 倍",
        "内部约 100 个 AI agent 承担竞品监控 / 红人 sourcing / 内容生成",
    ],
    founder="Romain Torres（CEO，负责营销与销售，前减肥 App 创业者）与 Dylan Fournier（联合创始人，负责产品）；2024 年 1 月创立。",
    traffic_sources=["自家产品生成广告直接投放", "约 100 个内部 AI agent 做增长动作", "企业直销"],
    tags=["AI视频", "广告素材", "小团队高营收", "bootstrapped"],
    sources=src("arcads", "Arcads", "https://www.arcads.ai"),
))

# ── 2. Instantly ────────────────────────────────────────────────────────────
NEW.append(case(
    id="instantly", name="Instantly", name_en="Instantly", origin="爱沙尼亚 · 全远程",
    one_liner="把「邮件能不能送进收件箱」做成产品的冷外联平台，零融资做到 ARR 约 ¥2.57亿",
    category="营销工具", industry="B2B 冷外联 / 邮件可交付性",
    caliber="arr",
    metrics={
        "arr": usd(257_000_000),
        "mrr": usd(21_504_000),
        "headline": "ARR 约 ¥2.57亿（≈$38.2M），MRR 约 ¥2,150.4万，24,500 付费客户",
        "metric_note": "页面原文：「约 ¥2.57亿 ARR（约 ¥2,150.4万 MRR），24,500 付费客户，"
                       "ARPU 约 ¥874」「2024 年底约 2000 万 ARR，2026 年约 4000 万 ARR，几乎每年翻倍」"
                       "「bootstrapped，没有拿过任何外部融资」。¥→$ 按站点汇率 6.72 折算。",
        "paying_customers": 24500,
        "arpu": usd(874),
        "growth": "2024 年底 ARR 约 $20M → 2026 年约 $40M，几乎每年翻倍",
        "price_point": "Growth ¥249/月（≈$37）、Hypergrowth ¥652/月（≈$97）；"
                       "含 Leads / CRM / 可交付性组件的实际方案多在 ¥1,008–¥2,688/月（≈$150–$400）",
    },
    models=["订阅制", "自助增长"],
    replicability={"tech": 3, "distribution": 3, "capital": 2, "timing": 4},
    verdict="冷邮件这个品类里，所有人都在卖「更聪明的文案」，它卖的是「邮件真的能送达」——"
            "把客户最怕的那件事（进垃圾箱）当成产品本身，付费理由就变成了基础设施而不是工具。",
    what_it_does="面向 B2B 冷外联的 AI 销售参与平台：无限邮箱连接 + 无限暖箱（150 万+ 账户的暖箱网络）、"
                 "AI Copilot 搭活动、Unibox 统一收件箱、4.5 亿+ 联系人的内置线索库，"
                 "以及 2025 年 3 月发布的 AI 销售 Agent。",
    how_it_makes_money="纯 SaaS 订阅，零外部融资。Growth 起价 ¥249/月，Hypergrowth ¥652/月，"
                       "Leads/CRM/可交付性组件单独计费，实际方案多在 ¥1,008–¥2,688/月。"
                       "ARR 约 ¥2.57亿、MRR 约 ¥2,150.4万、24,500 付费客户、ARPU 约 ¥874。",
    why_it_works=[
        "它卖的是「可交付性」这个基础设施级痛点：邮箱被封、域名被拉黑，是冷邮件真正的成本，文案好坏只是第二层。",
        "1.5M+ 账户的暖箱网络是规模效应资产——客户越多，暖箱越可信，新客户越难被替代。",
        "渠道自洽：早期靠自己给自己发冷邮件获客，产品本身就是销售工具，获客成本结构性地低。",
    ],
    playbook=[
        "把客户最恐惧的失败模式（邮件进垃圾箱）直接做成产品名与卖点，比强调「AI 更聪明」有效得多。",
        "AppSumo 首发拿第一批种子用户与口碑，再用社区（Cold Email Masterclass，5 万+ 成员）+ 联盟分佣做复利渠道。",
        "定价按「发信账号数」而不是「功能档位」：客户的业务规模增长会自动带动客单价。",
    ],
    signals=[
        "零外部融资做到 ARR 约 $38M、24,500 付费客户",
        "AppSumo 首发带来 ¥33.6万初始收入，Facebook 社区 5 万+ 成员",
        "联合创始人是前 lead gen 代理商，痛点来自自己每天被工具折磨",
    ],
    founder="Nils Schneider（CEO）与 Raul Kaevand（联合创始人），此前各自经营 lead gen 代理商，"
            "通过 Reddit 的 r/cofounder 认识；团队全远程，分布欧洲与迪拜。",
    traffic_sources=["AppSumo 首发", "Facebook 社区 Cold Email Masterclass（5 万+）", "联盟分佣", "自家产品发冷邮件获客"],
    tags=["冷邮件", "B2B获客", "bootstrapped", "高ARR"],
    sources=src("instantly", "Instantly", "https://instantly.ai"),
))

# ── 3. ahron.ai ─────────────────────────────────────────────────────────────
NEW.append(case(
    id="ahron", name="ahron.ai", name_en="ahron.ai", origin="德国 · 慕尼黑",
    one_liner="给 AI agent 装上企业人事流程地图的 Agentic HR 平台，4 人团队、零销售做到 ARR 约 ¥753万",
    category="垂直行业 SaaS", industry="HR / Agentic 流程自动化",
    caliber="arr",
    metrics={
        "arr": usd(7_530_000),
        "headline": "ARR 约 ¥753万（≈$1.12M），4 人团队，无销售人员",
        "metric_note": "页面原文：「据第三方 ARR 数据库记录，公司在 2026 年 8 月 ARR 突破 ¥753万，"
                       "而且是在没有雇任何销售人员的情况下做到的。需要说明的是，这个数字来自第三方数据库估算，"
                       "未经审计，只能作为量级参考。」——页面自己就标了未经审计，入库按「口径待核」处理。"
                       "¥→$ 按站点汇率 6.72 折算。",
        "team": "4 人左右",
        "growth": "2025 年成立，2026-08 ARR 突破 ¥753万，零销售人员",
    },
    models=["订阅制", "企业销售"],
    replicability={"tech": 4, "distribution": 4, "capital": 2, "timing": 3},
    verdict="同样是「AI agent」，它多做的第一步是流程挖掘——没有「这家公司人事流程长什么样」的地图，"
            "agent 只能回答问题，不能动手干活。这一层地图才是它能卖给中大型企业的原因。",
    what_it_does="Agentic HR 平台：先做流程挖掘，把一家公司从「招人」到「离职」的全链路数据扒出来，"
                 "建员工生命周期的数字孪生，再让 agent 在这个底座上自动优化招聘、入职与人才管理。",
    how_it_makes_money="企业 SaaS 订阅。客户是有十几套互不相通 HR 工具（ATS/入职/绩效）的中大型企业，"
                       "卖的是「用一层 agent 把它们替掉或串起来」。2026-08 ARR 突破 ¥753万，零销售人员。",
    why_it_works=[
        "先把流程数据变成资产（process mining + 数字孪生），再让 agent 在上面执行——顺序反过来的产品都停在问答层。",
        "目标客户是已经堆了十几套 HR 系统的中大型企业，集成与替代的价值可以用「删掉几个系统」直接算出来。",
        "创始人三十年 HR 科技积累与上一家公司（Talentry）的退出信用，让 4 人团队也能进大企业采购名单。",
    ],
    playbook=[
        "做垂直 agent 之前，先问「我有没有这家公司流程的地图」；没有地图的 agent 只能当客服机器人卖。",
        "零销售团队不是省成本，是筛客户：每一单都来自既有关系与转介绍，成交周期反而更短。",
        "行业奖项在欧洲企业采购里是真渠道（德国 HR Start-up Award），不是 PR 装饰。",
    ],
    signals=[
        "4 人团队、零销售人员、2026-08 ARR 突破 ¥753万",
        "创始人 Dr. Carl Hoffmann 2013 年创办 Talentry（德语区头部），2022 年与 CleverConnect 合并",
        "2026 年获德国 HR Start-up Award（BPM 等机构评选）",
    ],
    founder="Dr. Carl Hoffmann，HR 科技三十年；2013 年创办 Talentry，2022 年与法国 CleverConnect 合并"
            "（合并后 270+ 人）任联合创始人；2025 年在慕尼黑从零创办 ahron.ai。",
    traffic_sources=["创始人既有客户关系与转介绍", "行业奖项", "企业直销（无销售团队）"],
    tags=["AgenticHR", "流程挖掘", "行业老兵", "小团队"],
    sources=src("ahron", "ahron.ai", "https://ahron.ai"),
))

# ── 4. Fyxer ────────────────────────────────────────────────────────────────
NEW.append(case(
    id="fyxer", name="Fyxer", name_en="Fyxer", origin="英国 · 伦敦",
    one_liner="长在 Gmail/Outlook 里的 AI 执行助理，8 个月 ARR 从 ¥672万冲到 ¥1.14亿",
    category="AI 工具", industry="AI 执行助理 / 邮箱与会议",
    caliber="arr",
    metrics={
        "arr": usd(114_000_000),
        "headline": "ARR ¥1.14亿（≈$17.0M，2025-08），8 个月涨约 17 倍",
        "metric_note": "页面原文：「2025 年初 ARR 还是 ¥672万，到 2025 年 8 月已经超过 ¥1.14亿，"
                       "八个月涨了十几倍」「47.5 万名以上的个人职场用户」「三个月后留存率 90%」"
                       "「2025 年 3 月拿了 ¥6,720万 A 轮（20VC 领投，Marc Benioff 参投，估值约 ¥4.03亿），"
                       "半年后 9 月又拿了 ¥2.02亿 B 轮（Madrona 领投、Lakestar 参投）」。"
                       "¥→$ 按站点汇率 6.72 折算；ARR 取页面最近一个明确时点（2025-08）。",
        "users": "47.5 万+ 个人职场用户",
        "retention": "三个月留存率 90%",
        "growth": "2025 年初 ARR ¥672万 → 2025-08 ARR ¥1.14亿",
        "funding": "A 轮 ¥6,720万（2025-03，20VC 领投，Benioff 参投，估值 ¥4.03亿）+ B 轮 ¥2.02亿（2025-09）",
    },
    models=["订阅制", "企业销售"],
    replicability={"tech": 3, "distribution": 3, "capital": 3, "timing": 3},
    verdict="在一个「AI 会不会乱说话」是最大心理障碍的品类里，它主动交出控制权——"
            "草稿必须人点一下才发。这一刀砍掉了信任成本，比任何准确率数字都管用。",
    what_it_does="AI 执行助理：直接集成 Gmail 与 Outlook，把收件箱分成可执行类别、用你自己的语气起草回复、"
                 "跟踪该跟进的事，自动加入 Zoom/Meet/Teams 静默记录并生成带重点与任务分发的纪要，"
                 "另有一个可追问「上次那个客户答应什么时候回款」的聊天入口。",
    how_it_makes_money="SaaS 订阅，个人与团队分档，按席位/邮箱账户收费，另设企业版。"
                       "2025 年初 ARR ¥672万，2025 年 8 月超过 ¥1.14亿；47.5 万+ 个人用户，"
                       "另有 AT&T、Starbucks、TikTok、Siemens 等企业客户。",
    why_it_works=[
        "零迁移成本：不要求你搬工具，直接嵌进 Gmail/Outlook，绕开了同类产品最大的死因。",
        "训练数据是「干」出来的：创业前做真人执行助理服务，累计交付 50 万小时，AI 学的是真实助理怎么排序、怎么措辞。",
        "合规证书（SOC 2 / HIPAA / ISO 27001 / GDPR / CASA Tier 2）直接当销售武器，缩短大企业采购流程。",
    ],
    playbook=[
        "在信任敏感的品类里，主动限制 AI 的权限（不代发、只起草）反而是最强的卖点。",
        "先做人工服务攒交付经验与训练集，再把经验蒸馏成产品——比直接做 AI 产品少走两年弯路。",
        "选一个「按真实支出排名」的第三方榜单切进去（a16z 的 AI 应用支出榜），比投广告更能拿到企业信任。",
    ],
    signals=[
        "8 个月 ARR 从 ¥672万 涨到 ¥1.14亿；a16z/Mercury 的 AI 应用支出榜第 7，压过 Perplexity 与 Lovable",
        "三个月留存率 90%，47.5 万+ 个人职场用户",
        "创立前团队做真人执行助理服务，累计交付 50 万小时",
    ],
    founder="Richard Hollingsworth（CEO）与 Rami Karabibar 联合创立；公司 2023 年 10 月成立（英国注册号 15189973），"
            "最初 4 人，AI 转型后 20 多人。",
    traffic_sources=["零迁移成本的邮箱内嵌", "企业合规证书", "a16z 支出榜曝光", "高留存带来的口碑"],
    tags=["AI助理", "邮箱", "高留存", "企业合规"],
    sources=src("fyxer", "Fyxer", "https://www.fyxer.com"),
))

# ── 5. HeyReach ─────────────────────────────────────────────────────────────
NEW.append(case(
    id="heyreach", name="HeyReach", name_en="HeyReach", origin="北马其顿 · 斯科普里",
    one_liner="LinkedIn 多账号自动化触达平台，被 YC 拒两次、零融资做到 ARR 约 ¥1.21亿",
    category="营销工具", industry="LinkedIn 冷启动 / 销售自动化",
    caliber="arr",
    metrics={
        "arr": usd(121_000_000),
        "headline": "ARR 约 ¥1.21亿（≈$18.0M），6500+ 客户，全程零外部融资",
        "metric_note": "页面原文：「2023 年上线，不到一年 ARR 破 ¥672万，两年内约 ¥4,032万，"
                       "2026 年约 ¥1.21亿；全程零外部融资；员工 51 至 100 人」"
                       "「客户超 6500 家，G2 评分 4.7，获评 G2 2026 最快增长产品」。"
                       "月收入栏的 ¥672万 是上线首年的 ARR 台阶，不是当期值——本条按 2026 年 ARR 计。"
                       "¥→$ 按站点汇率 6.72 折算。",
        "paying_customers": 6500,
        "team": "51–100 人",
        "growth": "上线首年 ARR ¥672万 → 两年 ¥4,032万 → 2026 年 ¥1.21亿，全程零融资",
        "price_point": "单账号起价 ¥531/月（≈$79），卖点是「无限发送者、一个固定价」",
    },
    models=["订阅制", "自助增长"],
    replicability={"tech": 3, "distribution": 3, "capital": 2, "timing": 3},
    verdict="它的产品定义来自一次平台政策变化：LinkedIn 把每日好友请求压到 20–40 个之后，"
            "「多账号轮换」从技巧变成了刚需，它就把这个刚需直接做成品类。",
    what_it_does="LinkedIn 自动化触达平台：无限发送者轮换（连接任意数量账号、固定费用、系统自动轮换）、"
                 "统一收件箱、从 Sales Navigator/Clay/RB2B/HubSpot 导入线索、组合式触达步骤（加好友/发消息/看主页，"
                 "带 If Connected 逻辑），并可接 Claude/ChatGPT 让 agent 端到端跑触达。",
    how_it_makes_money="订阅制按账号数定价，单账号起价 ¥531/月，核心卖点是「无限发送者、一个固定价」，"
                       "直接对准 agency 手上有几十个客户账号的痛点。2026 年 ARR 约 ¥1.21亿，6500+ 客户，零融资。",
    why_it_works=[
        "需求由平台规则创造：20–40 个/天的限制让多账号轮换成为规模化获客的唯一合法路径，产品站在需求正中央。",
        "定价按「账号数 + 无限发送者」而非席位，agency 的客户越多、越离不开它。",
        "成为别人工具链里的一环：与 Clay、Instantly、Smartlead 原生集成，被集成就是被分发。",
    ],
    playbook=[
        "平台政策收紧不是坏事：限制一落地，原本的「技巧」立刻变成所有同行的共同刚需，做工具的人吃到最大红利。",
        "能力可迁移、平台不可迁移——Reddit 关了门，同一套自动化搬到 LinkedIn 就活了。别把公司绑在某个平台的政策上。",
        "在功能高度同质化的赛道里，把客户支持当北极星指标（他们重建支持部门后止住了下滑）。",
    ],
    signals=[
        "零外部融资、6500+ 客户、G2 4.7、G2 2026 最快增长产品",
        "平台单季数据：659 万+ 好友请求被接受、164 万+ 对话开启、感兴趣线索占比 37%",
        "被 YC 拒两次，第二次是在 ARR 破 ¥672万后的回拨面试",
    ],
    founder="Nick Velkovski（CEO）与 Stefan Kotevski（CTO），北马其顿起步；此前做 Reddit 私信工具 Howitzer，"
            "因 Reddit 政策与 API 收紧转型 LinkedIn。",
    traffic_sources=["与 Clay/Instantly/Smartlead 的原生集成", "G2 榜单", "agency 口碑", "客户支持口碑"],
    tags=["LinkedIn", "冷启动", "零融资", "被集成"],
    sources=src("heyreach", "HeyReach", "https://heyreach.io"),
))

# ── 6. Campfire ─────────────────────────────────────────────────────────────
NEW.append(case(
    id="campfire", name="Campfire", name_en="Campfire", origin="美国",
    one_liner="AI 原生 ERP，用自研会计大模型替掉 NetSuite，把月结从 15 天压到 3 天",
    category="垂直行业 SaaS", industry="财务 / ERP",
    caliber="mrr",
    metrics={
        "mrr": usd(3_360_000),
        "headline": "MRR 约 ¥336万（≈$500K，页面月收入栏），十二周连融两轮共一亿美金",
        "metric_note": "页面原文：月收入栏 ¥336万；「2024-02 种子轮 ¥2,352万（Foundation Capital 领投）、"
                       "2025-08 A 轮 ¥2.35亿（Accel 领投）、2025-10 B 轮 ¥4.37亿（Accel 与 Ribbit 联合领投），"
                       "十二周内一共募了一亿美金，累计约 1.08 亿」「年内营收十倍增长」"
                       "「有客户月结从 15 天压到 3 天」。¥→$ 按站点汇率 6.72 折算。"
                       "⚠ 页面未标该月收入的时点，按「口径待核」入库。",
        "growth": "页面口径「年内营收十倍增长」（未给基期绝对值）",
        "funding": "种子 ¥2,352万（2024-02）+ A 轮 ¥2.35亿（2025-08）+ B 轮 ¥4.37亿（2025-10），十二周内共募约 $100M",
        "customers": "从 50 人到 5000 人的公司；已披露客户含 Replit、Decagon、PostHog",
    },
    models=["订阅制", "企业销售"],
    replicability={"tech": 5, "distribution": 4, "capital": 5, "timing": 3},
    verdict="ERP 替换这门生意从来不靠「功能更多」，靠的是一个 CFO 不用算就能信的 ROI 数字——"
            "它选的是「月结天数」，15 天压到 3 天。",
    what_it_does="AI 原生 ERP：自动分类交易、银行对账、付款匹配发票；Ember AI 用自然语言提问 30 秒给出带来源的答案；"
                 "收入自动化处理订阅/用量计费/分层定价与合同变更的收入确认；核心总账支持多实体合并与持续关账。"
                 "底层是只吃会计数据训练的专有模型 LAM（Large Accounting Model）。",
    how_it_makes_money="订阅制，按客户规模与模块收年费，客户从 50 人到 5000 人。"
                       "定价叙事压在可量化 ROI 上（月结天数），资本侧十二周连融两轮共约 $100M。",
    why_it_works=[
        "「替换 NetSuite」是 CFO 主动在搜的词，需求是搜索型而非创造型，获客靠意图而非教育。",
        "把 ROI 收敛成一个会计圈人人都懂的指标（月结天数 15→3），销售话术不需要解释。",
        "客户名单自带背书：Replit、Decagon、PostHog 从 NetSuite 迁过来，本身就是下一个客户的安全感来源。",
    ],
    playbook=[
        "卖替代型产品时，先找到客户内部已经在用的、可量化的痛苦指标（天数、工时、差错率），把你的价值绑上去。",
        "创始人身份即信任状：一个干了十五年财务的人来卖财务系统，比任何 demo 都省销售成本。",
        "专有模型只在「窄而深」的领域做（只吃会计数据），比通用大模型更容易说出准确率数字。",
    ],
    signals=[
        "十二周内连融两轮、共约 $100M；2024-02 起累计融资约 $108M",
        "客户含 Replit、Decagon、PostHog；覆盖 SaaS/专业服务/医疗科技/航天",
        "LAM 在对账与差异分析上自称准确率超 95%",
    ],
    founder="John Glasgow，创始人兼 CEO 兼 CFO；履历含 Fidelity、Union Square Advisors、Adobe 财务与战略总监、"
            "Invoice2go 业务拓展与合作副总裁（参与把公司卖给 Bill.com，约 ¥42亿）。",
    traffic_sources=["CFO 主动搜索「替换 NetSuite」", "YC S23 与 Accel 连续背书", "客户名单背书", "创始人身份"],
    tags=["AI ERP", "财务", "替换型产品", "高融资"],
    sources=src("campfire", "Campfire", ""),
))

# ── 7. Wispr Flow ───────────────────────────────────────────────────────────
NEW.append(case(
    id="wispr-flow", name="Wispr Flow", name_en="Wispr Flow", origin="美国 · 旧金山",
    one_liner="按一下说话、松开就得到排版好文字的语音输入工具，5 个月 ARR 涨 10 倍到 ¥6,720万",
    category="AI 工具", industry="语音输入 / 生产力",
    caliber="arr",
    metrics={
        "arr": usd(67_200_000),
        "headline": "ARR ¥6,720万（≈$10.0M，2025-10），5 个月涨 10 倍，付费转化约 19%",
        "metric_note": "页面原文：「付费转化率约 19%」「2025 年 10 月 ARR 做到 ¥6,720万，"
                       "而且是在 5 个月内涨了 10 倍」「到 2025 年 11 月，已经有 270 家财富 500 强的团队在用，"
                       "每周还净增约 125 家新企业客户」「官方口径下准确率 97.2%」"
                       "「2025-06 ¥2.02亿 A 轮（Menlo Ventures 领投）、2025-11 又拿 2500 万 A 轮扩展，"
                       "累计 ¥5.44亿，估值约 7 亿」。¥→$ 按站点汇率 6.72 折算。",
        "conversion": "付费转化率约 19%",
        "growth": "5 个月 ARR 涨 10 倍（至 2025-10 的 ¥6,720万），月环比约 40%",
        "enterprise": "270 家财富 500 强团队在用，每周净增约 125 家",
        "funding": "A 轮 ¥2.02亿（2025-06，Menlo 领投）+ A 轮扩展 2500 万美元（2025-11），累计 ¥5.44亿，估值约 $700M",
    },
    models=["订阅制", "Freemium", "企业销售"],
    replicability={"tech": 5, "distribution": 2, "capital": 4, "timing": 3},
    verdict="它赢在「不是把每个字记下来，而是理解你想表达什么」——口误、改主意、中英混说都能收敛，"
            "这才是语音输入从玩具变成生产工具的分界线。",
    what_it_does="按快捷键说话，把自然口语变成已排版、可直接发出的文字，速度是打字 3–4 倍。"
                 "自动清掉「呃」「啊」等赘词，你说「算了，改成 5 点半」它会直接改写前面的内容。"
                 "全平台（macOS/Windows/iOS/Android）、104+ 语言，可在 Cursor、VS Code 里直接语音写代码。",
    how_it_makes_money="Freemium 订阅 + 企业席位双轮：个人免费试用后转订阅（转化率约 19%），企业按席位卖，"
                       "团队版可共享词汇表。2025-10 ARR ¥6,720万，5 个月涨 10 倍；270 家 F500 团队在用。",
    why_it_works=[
        "产品体验的「惊艳感」自带传播：2024-10 macOS 版在 Product Hunt 首发炸开，90% 增长来自口碑。",
        "跨平台铺量是留存前提：语音输入场景碎片化，缺一个平台就断一次，补齐四端后切换成本才真正归零。",
        "企业渠道反哺个人：270 家 F500 里每个员工都是潜在个人用户，B 端签下来 C 端自然滚起来。",
    ],
    playbook=[
        "语音/输入类产品要在「理解意图」而不是「转写准确率」上做差异——前者是体验分水岭，后者很快会同质化。",
        "先在一个平台做到惊艳（macOS + PH 首发），再补端；一次性铺四端会摊薄打磨质量。",
        "把等待名单当发布会：Android 版上线前攒了 37.5 万等待名单，冷启动成本直接归零。",
    ],
    signals=[
        "付费转化率约 19%（消费级软件里属极高水平）",
        "5 个月 ARR 涨 10 倍、月环比约 40%；270 家财富 500 强在用，每周净增约 125 家",
        "累计融资 ¥5.44亿（≈$81M），估值约 $700M",
    ],
    founder="Tanay Kothari（CEO）与 CTO Sahaj Garg，斯坦福室友；团队 2019 年起在斯坦福 AI Lab 做无声穿戴输入，"
            "2024 年 pivot 到纯软件，硬件积累的 ASR 技术成为壁垒。",
    traffic_sources=["Product Hunt 首发（90% 增长来自口碑）", "跨平台铺量", "企业渠道反哺", "创始人 build in public"],
    tags=["语音输入", "Freemium", "高转化", "跨平台"],
    sources=src("wispr-flow", "Wispr Flow", ""),
))

# ── 8. Build Concierge ──────────────────────────────────────────────────────
NEW.append(case(
    id="build-concierge", name="Build Concierge", name_en="Build Concierge", origin="英国 · 利兹",
    one_liner="把工地服务公司的来电、工单、收款全自动的 AI 运营层，半年做到 ARR 接近 ¥1,344万",
    category="垂直行业 SaaS", industry="家装/工地服务 运营自动化",
    caliber="arr",
    metrics={
        "arr": usd(13_440_000),
        "mrr": usd(1_122_000),
        "headline": "ARR 接近 ¥1,344万（≈$2.0M，2025 年底），12 家付费客户在产",
        "metric_note": "页面原文：「2025 年 6 月完成约 ¥3,427.2万种子轮，估值约 ¥3.11亿」"
                       "「2025 年底 12 家付费客户在产，年化营业额接近 ¥266.4万、逼近 ¥1,344万 ARR」"
                       "「另一篇创始人帖称正式交易约 6 个月即达 ¥1,344万 ARR，并定下 2026 年底 ¥4,440万+ ARR 目标」；"
                       "月收入栏 ¥112.2万（×12 ≈ ¥1,344万，与 ARR 自洽）。"
                       "⚠ 页面内部对「接近 ¥266.4万」与「接近 ¥1,344万」并存，本条按后者的 ARR 口径入库并保留原话。",
        "paying_customers": 12,
        "growth": "正式交易约 6 个月达 ¥1,344万 ARR；目标 2026 年底 ¥4,440万+ ARR",
        "funding": "种子轮约 ¥3,427.2万（2025-06），估值约 ¥3.11亿",
        "usage": "单个真实客户四周内：自动创建 3863 个工单、25992 次实时 AI 决策、3201 条结构化记录写入客户系统",
    },
    models=["订阅制", "垂直深挖"],
    replicability={"tech": 3, "distribution": 4, "capital": 3, "timing": 3},
    verdict="它没给工地老板再加一个 dashboard，而是直接替他把电话接起来、把工单开出来——"
            "卖「活被干了」而不是「数据被看见了」，垂直行业才肯付钱。",
    what_it_does="面向灭虫、电气、水暖、排水、屋顶、清洁、建筑维修等工地服务公司的 AI 客服与运营平台。"
                 "三层：理解（电话/邮件/短信/PDF/表单转结构化数据）、决策（优先级/路由/系统更新）、"
                 "执行（跨系统自动执行）。模块含 AI 电话接待、网站助手、全渠道客服、端到端工作流引擎。",
    how_it_makes_money="SaaS 订阅，按坐席与使用量收费。2025 年底 12 家付费客户在产，ARR 接近 ¥1,344万；"
                       "正式交易约 6 个月即达该 ARR，目标 2026 年底 ¥4,440万+。",
    why_it_works=[
        "痛点极其具体：电话漏接、工单靠手工录、调度靠人脑——每一项都能折算成丢掉的订单金额。",
        "卖的是「替你把活干了」的运营层，不是又一个 dashboard：垂直行业的老板没有时间学新软件。",
        "创始人的退出信誉是冷启动资产：上一家公司 BigChange 卖给 Simpro Group，估值超 ¥26.64亿。",
    ],
    playbook=[
        "垂直行业要卖「执行」而不是「可见性」：客户不缺报表，缺的是把活干完的人。",
        "用可计数的自动化动作做销售证据（四周 3863 个工单 / 25992 次 AI 决策）——比任何 demo 都好使。",
        "透明创始人打法：在 LinkedIn 持续公开客户数、ARR、AI 处理量，专业服务圈里比广告更有说服力。",
    ],
    signals=[
        "12 家付费客户、ARR 接近 ¥1,344万、种子轮估值 ¥3.11亿",
        "单客户四周内 3863 个工单、25992 次 AI 决策、3201 条结构化记录",
        "创始人上一家公司 BigChange 以超 ¥26.64亿估值卖给 Simpro Group",
    ],
    founder="Martin Port（CEO），英国利兹连续创业者，第四家公司；联合创始人含 Simon Field（COO）、"
            "Matt Fisher（CTO）、Leah Port。董事会含 Travis Perkins 前高管、Jet Aire 创始人。",
    traffic_sources=["创始人退出信誉", "行业董事会资源", "LinkedIn 公开运营数据（透明创始人）", "企业直销"],
    tags=["垂直AI", "现场服务", "运营自动化", "连续创业者"],
    sources=src("build-concierge", "Build Concierge", ""),
))


def main():
    dry = "--dry" in sys.argv
    data = json.load(open(CASES, encoding="utf-8"))
    have = {c["id"] for c in data}
    add = [c for c in NEW if c["id"] not in have]
    print("库内现有 %d 条；本次待写 %d 条（已存在跳过 %d）"
          % (len(data), len(add), len(NEW) - len(add)))
    for c in add:
        m = c["metrics"]
        print("  + %-16s %-14s %s" % (c["id"], c["caliber"],
                                      m.get("headline", "")[:52]))
    if dry:
        print("[dry] 未写盘")
        return
    if not add:
        return
    data.extend(add)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomicio.write_text(CASES, text)
    print("已写入 %s（%d 条）" % (CASES, len(data)))


if __name__ == "__main__":
    main()
