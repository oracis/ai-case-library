#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人可做性评分 + 双轴综合诊断。

一、solo_fit（个人可做性）
    把 cases.json 里已有的 replicability 四维（1=最容易，5=最难）翻成
    「越高越好做」的方向，归一化到 0-100，再补一个原数据没有、但对
    「一个人能不能做」最致命的维度：

        delivery  单人交付   1.4   不用团队 / 资质 / 7×24 值守

    五个维度和权重（权重按「一个人做」的杀手排序）：
        delivery   1.4   要团队要资质就是死刑
        reach      1.3   一个人卖不出去等于白做
        capital    1.1   能不能不烧钱熬到盈亏平衡
        build      1.0   技术栈在不在射程内（对全栈开发者不是瓶颈，故权重低）
        window     1.0   现在进场还有没有位置

    其中 build / reach / capital / window 直接由 replicability 反推
    （build = 6 - tech，以此类推），保证两套数据永不打架；
    只有 delivery 需要人工判断，写在 DELIVERY 表里。
    满分 = 5 × 5.8 = 29，归一化到 100。

二、composite（双轴综合分）
    solo_fit 和 china_fit 是**乘性关系**，不是加性关系：
    「一个人能做但国内没人买」= 0，「国内有需求但我做不了」= 0。
    所以不做算术平均，而用木桶式聚合并把短板权重放大：

        composite = 0.6 × min(solo, china) + 0.4 × mean(solo, china)

    这样 90/40 → 50，60/60 → 60：**双及格 > 单点突出**。

三、quadrant（四象限）
    以 70 分为「及格线」，把 24 条案例分进四个格子：
        go       双高          可以开干
        export   能做但没市场   做海外，别在国内卷
        partner  有市场但啃不动 值得找人 / 拿资质
        skip     双低          别碰

用法：
    python scripts/score_solo_fit.py              # 打分并写回 data/cases.json
    python scripts/score_solo_fit.py --dry-run    # 只看结果
    python scripts/score_solo_fit.py --top 10     # 只看前 10
    python scripts/score_solo_fit.py --threshold 75   # 换及格线
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# ------------------------------------------------------------------ 权重与维度

SOLO_WEIGHTS = {
    "delivery": 1.4,
    "reach": 1.3,
    "capital": 1.1,
    "build": 1.0,
    "window": 1.0,
}

SOLO_DIMS = [
    ("build", "造得出来", "技术栈在不在一个人射程内"),
    ("delivery", "单人交付", "不用团队 / 资质 / 7×24 值守"),
    ("reach", "够得着客户", "不靠销售团队就能触达"),
    ("capital", "启动轻", "不需要先烧钱就能开张"),
    ("window", "窗口还开着", "现在进场还有没有位置"),
]

SOLO_MAX = 5 * sum(SOLO_WEIGHTS.values())          # 29.0

# replicability(1=最容易) → solo_fit(5=最好做) 的反转映射
INVERT = {
    "build": "tech",
    "reach": "distribution",
    "capital": "capital",
    "window": "timing",
}

QUADRANTS = {
    "go": ("可以开干", "两边都过线：一个人能做，国内也有市场。"),
    "export": ("能做，但别在国内卖", "技术完全在手，卡在国内的需求或支付土壤上。出口做更顺。"),
    "partner": ("有市场，但一个人啃不动", "需求是真的，门槛在资质、大客户销售或团队交付上。"),
    "skip": ("别碰", "两个方向都不过线。"),
}

# ------------------------------------------------------------------ 人工判断表
# 只需填 delivery（单人交付，1-5）和理由。其余四维从 replicability 自动反推。
def _plain(s):
    """判断理由一律按**纯文本**存盘（站点上 `esc()` 直出，markdown 不生效）。

    2026-10-29：`test_report_notes_have_no_bold_marker` 抓到 6 条理由里写了
    `**强调**`，读者看到的是字面星号。在写入口统一剥掉，比逐条改字面量稳：
    以后新加条目忘了也不会再漏。⚠ 只影响这两个纯文本字段，正文的 markdown
    强调走的是另一条链路（make_article 自己转 `<strong>`），别在这里动。
    """
    return (s or "").replace("**", "")


DELIVERY = {
    # ── 2026-10-26 backlog「有条件」档入库的 6 条 ──
    "serviceform": (2, "卖出后要接官网、接 CRM、接企业微信/WhatsApp，还要按客户所在行业配"
                       "预约与线索流转规则——这是有实施环节的 B2B，一个人做不了。它自己用"
                       "145+ 家代理商承担交付，正是因为这个活不能靠产品自助完成。"),
    "salesrobot": (3, "开通是自助的，负担在**天天处理封号与限速**：平台风控一变，用户发不出去"
                      "就来找你，申诉、换 IP、调节奏都得做，没法外包也不能排期。"),
    "jobright": (4, "C 端自助，没有实施环节；长期负担在数据与匹配质量——职位数据要持续清洗、"
                    "匹配规则要跟着招聘季调，属「长期小修」而不是「一次性上线」。"),
    "sandra-ai": (3, "每家车行的 DMS、排班、话术都不一样，上线要陪客户核对一遍，"
                     "但接听是 AI 做，不需要值守——比纯人肉的语音前台轻，比纯 SaaS 重。"),
    "outward": (2, "项目制交付：问卷设计、抽样、质控、出报告，每个项目都有人工判断环节，"
                   "AI 压的是流程成本不是交付责任；一个人只能做到很窄的品类。"),
    "workhero": (1, "本质是**服务外包**：AI 执行 + 真人办公室经理复核，每个客户都要一个"
                    "懂 HVAC 的人盯着。它的 11 人团队就是交付能力本身，不是可选项。"),
    # ── 2026-10-26 backlog「形态可抄」第一梯队入库的 3 条 ──
    "autoshorts": (3, "自助订阅，开通零实施；负担在分发管道——TikTok/YouTube/IG 的接口、"
                      "限额与风控三天两头变，用户发不出去第一个找的是你，这部分没法外包。"),
    "rosie": (4, "自助配置为主：读商家资料、设预约规则，几分钟上线；"
                 "偶尔要帮客户做号码转移与日历对接，但不需要实施团队，也没有 7×24 值守"
                 "（电话是 AI 接，不是人接）。"),
    "boredhumans": (3, "零交付：工具即开即用，没有 onboarding。真正的负担在**持续上新与 SEO 维护**——"
                       "100+ 个页面要跟着搜索规则改，停更就开始掉流量，这是慢性的活。"),
    # ── 2026-10-26 TrustMRR 档案已下架的两条 ──
    "gistly": (2, "卖法是 Book a demo 的企业单：要接通话系统与 CRM、过安全评审、按客户定 QA 规则，"
                  "客户流失时还要有人复盘为什么评分不准——一个人扛不住交付与售后，"
                  "这也是它 155 个客户就得配直销的原因。"),
    "research-match": (4, "纯自助订阅，没有实施环节，一个人能交付；真正的负担在维护——"
                          "2.5 亿篇论文索引要持续更新、20+ 位教授沉淀的检查规则要跟着修订，"
                          "是「长期小修」而不是「一次性上线」。"),
    # ── 2026-10-26 从 howtomakemi 官方披露线入库的 8 条 ──
    "arcads": (4, "自助订阅为主；客户是投放团队，素材跑不动会来问怎么调，"
                  "有一点顾问成分，但不是实施。"),
    "instantly": (4, "自助开通；只有暖箱与可交付性出问题时才需要人介入排障。"),
    "ahron": (2, "卖的是流程挖掘 + 集成，必须有人做实施、对接 ATS/绩效系统，"
                 "4 人团队已经到极限——这也解释了它为什么零销售。"),
    "fyxer": (3, "个人版自助，企业版要过 IT 审批与邮箱域授权，得有人配合客户 IT。"),
    "heyreach": (3, "客户是 agency，账号被封、轮换策略失效都要人处理申诉与重配。"),
    "campfire": (1, "替换 ERP 涉及总账迁移、历史数据核对、审计口径确认，"
                    "没有实施团队根本交付不了。"),
    "wispr-flow": (5, "装完即用，零交付、零运维。"),
    "build-concierge": (2, "要接客户的电话系统与工单系统，还要按行业调流程，"
                           "实施与调优省不掉。"),
    "gojiberryai": (3, "自动触达要天天盯：发送限频、封号申诉、话术迭代，客户还会追着问"
                       "线索质量——售后省不掉。它自己就经历过「支持过载、几乎不睡」那一阵。"),
    "sierra": (1, "交付对象是美国财富 500 强：要 SLA、要合规审计、要 7×24 待命。"
                  "这不是「忙一点」，是要养一个交付团队。"),
    "genius-ai": (3, "多租户 SaaS 单人能维护，但门店客户会打电话、要上门教，"
                     "客服人力省不掉。"),
    "nitra": (1, "医疗软件要过资质、数据不出院、HIS 对接，"
                 "合规和技术支持都必须有团队和法人主体。"),
    "viktor": (2, "产品本身一个人写得出来，但企业客户要采购流程、要约演示、"
                  "要有人在群里响应。"),
    "chatbase": (4, "自助式 SaaS，用户自己注册自己配。人工交付几乎为零。"),
    "rezi": (5, "纯自助工具，付钱就能用，没有交付环节。"),
    "postiz": (4, "开源自部署，社区和文档替你承担了大量售后。"),
    "shipfast": (5, "数字商品，买完就走。零交付、零运维。"),
    "trustmrr": (5, "纯信息展示站，没有交付。"),
    "comp-ai": (2, "合规审计结论要有资质背书，客户买的是「谁来签字」，不是工具。"),
    "aeo-engine": (5, "纯 SaaS 自助，客户自己看报表。"),
    "lancer-app": (4, "数据订阅，只需定期维护采集管道。"),
    "speel-co": (4, "SaaS 自助，但生成质量偶尔要人工兜底，留一点人力。"),
    "visualizee-ai": (4, "渲染服务自助化，出图不满意客户会来找，但不算重人力。"),
    "checkvibe": (5, "工具型产品，跑完出报告，没有交付环节。"),
    "storyshort-ai": (4, "自助生成，人工介入很少。"),
    "meerkats-ai": (2, "自动外呼要呼叫基础设施 + 合规审核 + 话术迭代，"
                      "而且出问题要有人兜。"),
    "coral": (4, "开源可自部署，售后被社区分担。"),
    "startclaw": (4, "托管平台，自助开通。"),
    "outrank": (5, "SaaS 自助。"),
    "marc-lou-portfolio": (5, "全是数字产品和轻 SaaS，天然无交付。"),
    "bustem": (4, "投诉流程可以自动化，但平台申诉要按规则人工调整策略。"),
    "kibu": (1, "客户是机构，采购要走关系、走评审，还要有人驻场培训。"),
    "pieter-levels": (5, "Photo AI 这类纯自助，付钱即用。"),

    # ---- 2026-09-20 发布的一批 ----
    "1lookup": (4, "纯按量调用的 API，客户接进去就不再来找你了。"
                   "唯一的人工地带是数据准确率被投诉时要去查样本 —— 那是偶尔的，不是常态。"),
    "stan": (2, "创作者平台的交付重在地推和客服：要拉创作者入驻、帮他们配好收银页、"
                "处理提现与纠纷。支付类产品出问题就是钱的纠纷，必须有人接。"),
    "magicslides-app": (5, "自助 SaaS，输入主题出 PPT，没有交付环节。"
                           "导出坏图这类偶发问题不足以养人。"),
    "autoreels-ai": (4, "批量生成基本自助，但成片质量不达标时客户会要求重生成或调参，"
                        "留一点人工兜底。"),
    "insect-bite-id": (5, "拍照出结果的单点工具，用完即走，零交付。"
                          "唯一的售后是识别错误的反馈，量小且异步。"),

    # ---- 2026-09-25 补的一批（TrustMRR 挂牌标的为主）----
    "voklit": (2, "通信服务要 7×24 值守：号码被封、通道波动、客户收不到验证码"
                  "都是当天的紧急工单，还要持续对接上游运营商与各国合规。"
                  "它的终局（停机挂牌）本身就是这条最有力的证据。"),
    "prosp": (3, "SaaS 部分基本自助，但自动外呼要天天盯发送限频、封号申诉和"
                 "话术迭代，客户还会追着问线索质量 —— 和 gojiberryai、meerkats-ai"
                 "是同一类负担，只是它的数据是 AI 生成的，人工稍轻。"),
    "divine-widgets": (5, "内容是现成经文与配图，推送全自动，1 人已跑起来。"
                          "没有交付环节，也没有客服人力。"),
    "promptmonitor-io": (4, "定时跑批出报告，自助订阅为主；"
                            "偶尔要向客户解释「这个曝光数据是怎么采的」，"
                            "属于支持成本，不是交付人力。"),
    "uplinked-b-v": (2, "它已经不是一个人的项目：团队 2–5 人、web + iOS + Android "
                        "三端并行，而顾问型客户买的是「有人帮我搞定」，"
                        " onboarding 与陪跑省不掉。"),
    "le19emetrou": (5, "履约全自动——供应商下单、物流跟踪、客户邮件全部自动触发，"
                       "1 人已跑通 26 单。唯一的活是调广告，那属于获客不是交付。"),
    "search1api": (4, "无状态 API，客户接进去就不再来找你；"
                      "人工只在故障与上游成本波动时出现，非常态。"),

    # ---- 2026-09-30 补的两条 ----
    "mort": (4, "求职工具本身是自助订阅，但自动投递是「代用户操作」："
                "投错公司、投到已关闭的岗位、或被 ATS 判为异常投递，都要有人兜。"
                "交付量不大，但因为是替人做一件有后果的事，不能做到零人力。"),
    "quran-unlock": (5, "纯自助 App：免费下载、进度全自动、订阅走 RevenueCat，"
                        "用户不会来问任何问题。1 人团队（TrustMRR 记 1 person）已跑起来。"),

    # ---- 2026-10-05 补的两条（premium 晋升时漏了，卡住 fit_ready 闸门）----
    "harperai": (2, "产品是数据对齐加AI 检索，但它的客户是贷款经纪与再融资顾问："
                    " £300/月 买的是「别错过这次再融资窗口」，出了漏提醒就是真金白银的损失，"
                    "客户会追着问某笔 charge 到底解没解押。这类生意要有人接需求、"
                    "做培训、扛数据源变更，一个人能跑，但跑不满。"),
    "easymix": (2, "上传音轨就能出成品，自助程度高；可音乐人会在意「这版到底干不干净」，"
                   "要求重调EQ / 换母带是常态。人工带宽压得住，不等于零 —— "
                   "定价对着录音棚的 120–250 € 打，交付体验就是产品的一部分。"),

    # ---- 2026-10-11 补：Stripe 深核通过的 7 条 ----
    # delivery 判「单人交付难度」，1 = 最难（要养团队），5 = 最轻（零交付）。
    # ⚠️ 这 7 条 MRR 跨度 $1,521~$202,060，但**delivery 与体量无关**：
    # 体量大往往正因为它需要团队（cometly 9 人）。别按收入高低打分。

    # cometly判 1：它是这批里最重的。客户是 B2B SaaS 的 CMO/增长负责人，
    # 买的是「别把广告费打错」—— 归因结论错了是真金白银的损失，
    # 客户会要约演示、要建群、要有人跟。官网 About 页列了2 CSM + AE + 实施专员，
    # Enterprise 还配专属 solutions engineer 与 onboarding。
    # 🚨 这条同时被标了 segment="赛道样本" / exclude_from_solo_cases=true，
    # 就是因为它的交付结构一个人根本接不住 —— delivery 判 1 与那个标记同源。
    "cometly": (1, "企业销售 + 实施 + 客户成功的完整交付：约演示、建群、"
                   "盯数据接入、季度复盘会。官网挂着 2 位创始人 + CTO + 2 CSM + AE + "
                   "实施专员共 9 人，Enterprise 还有专属 solutions engineer 与 onboarding。"
                   "一个人能卖第一单，接不了第二单。"),

    # vid-ai 判 3：产品侧是自助的，但内容质量要人兜。
    # AI 出的片子客户总要人看一遍、要改，批量客户（一天要10 条）会持续提要求。
    # 团队 2–5 人 —— 一个人能跑，但要有人接需求、改流程。
    "vid-ai": (3, "生成是自助的，但「能不能发」要人看：脚本、画幅、口播节奏都得过一遍。"
                  "做批量内容的客户一天要十几条，会持续提改稿要求。"
                  "公开资料称团队 2–5 人 —— 一个人能跑量，接不住规模化客户的定制诉求。"),

    # podawaa 判 4：LinkedIn 工具本身是自助订阅，分析报告由平台出。
    # 交付轻，唯一重的地方是账号封禁申诉—— 那要人跟。
    "podawaa": (4, "订阅制自助 SaaS：排期、发布、分析都由产品完成，人工交付很少。"
                   "唯一吃人力的地带是 LinkedIn 账号被限制后的申诉 —— "
                   "那要有人拿着身份证明和申诉材料反复跟，且官方不保证通道畅通。"),

    # publbee 判 4：纯自助工具，作者自己配关键词与生成。
    # 交付几乎为零，只有出版合规类问题（会不会被 KDP 拒）偶尔要答。
    "publbee": (4, "月/年订阅自助：调研、关键词、写作、配图都在后台，"
                   "作者自己动手。唯一可能要回话的是KDP 拒稿原因这类问题，"
                   "属偶发答疑，不是常态交付。"),

    # vectosolve 判 5：纯转换工具，跑完出文件，没有交付环节。
    # 反推单价约 $7，这个价位不可能也不需要有交付。
    "vectosolve": (5, "纯自助转换：传图、出SVG/DXF/DST/G-code，付钱就用。"
                      "MRR $1,808 配 260 个订阅反推单价约 $7 —— "
                      "这个价位天然不该有人工交付，也不该有。"),

    # augora-ai 判 4：追踪配置可在后台自助完成。
    # 但 Shopify 商家接不好追踪会来问「为什么我的数字对不上」，
    # 这类问题一半是配置一半是 Meta 侧的事，答起来琐碎但不需要驻场。
    "augora-ai": (4, "追踪配置与自动化都在后台自助完成。吃人力的地方是"
                     "「我的数字为什么对不上」这类问题 —— 一半答配置、"
                     "一半其实是 Meta 侧的事，琐碎但不需要驻场，带宽压得住。"),

    # wpconvert 判 4：这批里最接近自助的一条，也是唯一拿到 solo_possible 的。
    # 转换本身是纯工程交付（站点转好就完事）；偶尔要答「某个页面没转对」。
    "wpconvert-ai-convert-ai-sites-to-wordpress": (
        4, "交付是纯工程：站点转好、部署上线、给一份说明，完事。"
           "偶尔要处理「这个页面没转对」这类返工，量不大。"
           "页面标注团队规模 1 人 —— 这批里唯一拿到 solo_possible 的一条。"),

    # ---- 2026-10-15 补：门槛放宽后新晋升的 29 条 ----
    # 判据一律按**交付形态**，不按收入高低：体量大往往正因为它需要团队。
    "alchie": (5, "纯自助：输入想法、出 LinkedIn 帖子、付费用额度，没有交付环节。"
                  "一人 Bootstrapped，成本只有模型调用。"),
    "ae-intelligence": (5, "改写工具纯自助，免费档不限字数也不产生服务义务。"
                           "100 万用户配 1,065 个付费订阅，靠产品自己接住流量。"),
    "abmaxx": (5, "App 内订阅，拍照出计划全自动，零人工。唯一售后是识别/"
                  "计划不准的反馈，量小且异步。"),
    "autocontent-api": (5, "无状态 API，客户接进去就跑，故障与上游波动才需要人，"
                           "非常态交付。"),
    "apiframe": (5, "视频生成 API，按调用计费，自助接入。算力是成本不是交付人力。"),
    "ai-toolbox": (5, "工具导航/聚合站，信息组织即产品，没有交付环节，"
                      "SEO 是唯一渠道。"),
    "chessigma": (5, "教学 App 自助订阅，对弈与课程全自动。"),
    "storyshort": (4, "批量生成自助为主，但成片不达标时客户会要求重生成或调参数，"
                      "留一点人工兜底 —— 与 autoreels-ai 同型。"),
    "based-labs-ai": (4, "生成工具自助，且社区替它承担了一部分答疑。"
                         "吃算力成本，不吃交付人力。"),
    "draftly": (4, "一句话生成站点是自助的，但导出/部署/接域名这类最后一公里"
                   "会让非技术客户回来问，有一点支持成本。"),
    "audn-ai": (4, "音频生成自助，偶尔要解释生成结果与音色参数。"),
    "hypit-ai": (4, "自助内容工具，出图出文全自动，人工只在异常时出现。"),
    "chatseo": (4, "SEO 工具自助订阅，报表自动跑；客户会问「排名怎么采的」，"
                   "属答疑不是交付。"),
    "brawler": (4, "自助内容生成，人工介入很少。"),
    "amicited": (4, "AI 引用监控 SaaS，定时跑批出报告，自助为主。"),
    "angel-match": (4, "投资人数据库订阅，数据维护有一点持续人力，"
                       "但不需要按客户交付。"),
    "calendesk": (4, "预约 SaaS 自助开通，客户支持有一点，不需要驻场。"),
    "capgo": (4, "开发者工具 + 开源：文档与社区替它承担了大量售后，"
                 "与 postiz / coral 同型。"),
    # ⚠ 这里原来误写成「OTA 热更新」——那是 capgo 的判据，串条了。
    # cod-dex 是互动编程学习平台（课程 + 练习 + 勋章 + Club 订阅），
    # 交付形态是「内容一次性做好、后续自助学」，不是按客户交付。
    "cod-dex": (4, "课程、练习与勋章体系做好之后是全自动的，学员自助学；"
                   "持续投入在内容更新与社区，不是按客户交付。"),
    "backlinker-ai": (4, "外链建设自助化程度高，但外链资源本身要持续维护，"
                         "留一点人力。"),
    "cartboss": (4, "接入即用，弃单短信全自动触发；人工只在通道/送达异常时出现。"),
    "adspirer": (3, "投放由 AI 自动跑，但客户会追着问 ROAS 与策略调整，"
                    "这类「结果解释」省不掉 —— 与 prosp 同型，只是 AI 让它轻一些。"),
    "avenue-ticketing-inc": (3, "出票与对账可自动化，但主办方接入、结算周期、"
                                "退改纠纷都要有人接，平台型生意的中量交付。"),
    "corsidia": (3, "垂直行业服务 + 软件：案子要人对接，客户买的不是纯工具。"),
    "applyall": (2, "代投服务是**替用户做一件有后果的事**：投错公司、投到已关闭"
                    "岗位都要有人兜，与 mort 同型。$249–299 一次性买断对应的是"
                    "人工交付，不是软件。"),
    "analytics-mates": (2, "MRR 只占月流水一小部分，主体是 $999/站点起的"
                           "一次性审计项目 —— 那是人工交付，规模受限于人的时间。"),
    "adelante-ltd": (2, "卖的是「我们替你跑」：配置、培训、持续优化都由它做，"
                        "$900/月起的价格里含的就是这份人力。客户越多越要人。"),
    "brevilabs": (1, "$45k 四周冲刺 + $45k/月顾问费，是标准的项目制咨询，"
                     "必须团队交付。95% 毛利只是因为人力成本没挂进这条口径。"),
    "backpedal-ltd": (1, "线下 GPS 找回团队 + 硬件设备，是重履约生意："
                         "偷车发生后才能交付，且受本地能力限制，一个人做不来。"),

    # ---- 同日补：上一批里只有 DELIVERY 没有 replicability 的 12 条 ----
    "boothie-ai-photo-editor": (4, "选风格、传自拍、出图全自动，零人工交付。"
                                   "持续成本在风格模型的迭代与上新，是产品投入不是交付。"),
    "nutria-9e3eaa": (5, "拍照/搜索记一餐，出热量与建议全自动，纯自助 App。"
                         "食物库维护是产品工作，不占交付人力。"),
    "fastwrite-io": (5, "输入草稿、出改写结果，纯自助，没有交付环节。"),
    "downconvert-sketchup-files": (5, "上传文件、转换、下载，纯工具零交付；"
                                      "吃力的是把各版本格式吃透，那是研发不是交付。"),
    "macwall": (5, "数字商品，付费用下载，零交付。"),
    "growingupvideo": (4, "上传照片、出成片是自助的，但用户对「像不像」"
                          "主观性强，不满意要重生成或调参，留一点兜底人力。"),
    "homy": (5, "装了就用，纯工具，零交付。"),
    "stealth-rdp": (2, "VPS 是托管生意：机器宕机、网络抖动、客户连不上"
                       "都要有人响应，7×24 值守跑不掉 —— 这与「产品交付」是两回事。"),
    "white-glove-content": (2, "名字里的 White Glove 就是答案：客户会审稿、要改、"
                               "要按自己的品牌调性调整，人工交付省不掉。"
                               "MRR $70K 对应的是一支内容团队，不是一个人。"),
    "pet-plus-us": (4, "上传照片、选风格、出图全自动；"
                       "但肖像类商品的主观满意率低，退款与重生成要有人处理。"),
    "wagiff-app": (3, "打卡与社区本身自助，但 UGC 社区要持续审核内容、"
                      "要有人运营氛围 —— 这是它唯一的人力支出。"),
    "quotedeck": (3, "只有 4 个客户，但每个客户都要把自家保险产品的"
                     "条款与费率配进系统，实施是一次性的、省不掉的。"),
    "private-venture-1": (5, '偏自助的交付形态，人工介入少。'),
    "adawcag": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "aeo-checker": (3, '偏自助的交付形态，人工介入少。'),
    "ad-machine": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "advanced-gsc-mcp": (5, '偏自助的交付形态，人工介入少。'),
    "3d-agent": (5, '偏自助的交付形态，人工介入少。'),
    "adtruffle": (4, '偏自助的交付形态，人工介入少。'),
    "ai-designer": (3, '偏自助的交付形态，人工介入少。'),
    "acquire-community": (4, '偏自助的交付形态，人工介入少。'),
    "atelier-nuage": (5, '偏自助的交付形态，人工介入少。'),
    "fireply-ai": (3, '偏自助的交付形态，人工介入少。'),
    "ismalicious": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "uizze": (5, '偏自助的交付形态，人工介入少。'),
    "barron-tech-llc": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "autoclip": (3, '偏自助的交付形态，人工介入少。'),
    "chariot-ai": (5, '偏自助的交付形态，人工介入少。'),
    "openalternative": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "breadsync": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 finance_trading 同型。'),
    "cbt-artificial-intelligence-technology-inc-dba-zuuz": (4, '偏自助的交付形态，人工介入少。'),
    "firecert": (3, '偏自助的交付形态，人工介入少。'),
    "arcton": (4, '偏自助的交付形态，人工介入少。'),
    "captureflow": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "corey-chiu": (5, '偏自助的交付形态，人工介入少。'),
    "opentweet": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "apogee": (3, '偏自助的交付形态，人工介入少。'),
    "blawgy-com": (5, '偏自助的交付形态，人工介入少。'),
    "autosubtitles": (4, '偏自助的交付形态，人工介入少。'),
    "axis-labc2026": (3, '偏自助的交付形态，人工介入少。'),
    "astonish-email-limited": (3, '偏自助的交付形态，人工介入少。'),
    "bluff-ai": (4, '偏自助的交付形态，人工介入少。'),
    "baller": (4, '偏自助的交付形态，人工介入少。'),
    "athenify": (4, '偏自助的交付形态，人工介入少。'),
    "christian-tech-jobs": (3, '偏自助的交付形态，人工介入少。'),
    "bouncer": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "blitzcut": (4, '偏自助的交付形态，人工介入少。'),
    "bidva-buy-linkedin-accounts": (5, '偏自助的交付形态，人工介入少。'),
    "coprep-ai": (4, '偏自助的交付形态，人工介入少。'),
    "canopy-api": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "billionverify": (5, '偏自助的交付形态，人工介入少。'),
    "algofuse-ai": (4, '偏自助的交付形态，人工介入少。'),
    "botric": (5, '偏自助的交付形态，人工介入少。'),
    "checkform-gymnastics": (4, '偏自助的交付形态，人工介入少。'),
    "claws-host": (5, '偏自助的交付形态，人工介入少。'),
    "coco": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "blogr-ai": (5, '偏自助的交付形态，人工介入少。'),
    "client-one": (5, '偏自助的交付形态，人工介入少。'),
    "coralflavor": (5, '偏自助的交付形态，人工介入少。'),
    "apexvol": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 finance_trading 同型。'),
    "kitchendesign": (5, '偏自助的交付形态，人工介入少。'),
    "blacktwist": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "cloud-backup-for-podio": (5, '偏自助的交付形态，人工介入少。'),
    "spark-playground": (4, '偏自助的交付形态，人工介入少。'),
    "clawlink": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "ai-text-humanizer-netherlands-belgium": (4, '偏自助的交付形态，人工介入少。'),
    "agentskit": (3, '偏自助的交付形态，人工介入少。'),
    "altitude-cleaning-crew": (1, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 local_service 同型。'),
    "anyapi": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "aprs-conseil": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "athletiks": (3, '偏自助的交付形态，人工介入少。'),
    "bakering-app-sl": (1, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 local_service 同型。'),
    "bettrbot": (5, '偏自助的交付形态，人工介入少。'),
    "brandmylaptop": (3, '偏自助的交付形态，人工介入少。'),
    "brandmymac": (3, '偏自助的交付形态，人工介入少。'),
    "brokenstudios": (5, '偏自助的交付形态，人工介入少。'),
    "chronoid": (5, '偏自助的交付形态，人工介入少。'),
    "firstrep": (4, '偏自助的交付形态，人工介入少。'),
    "orbio": (5, '偏自助的交付形态，人工介入少。'),
    "ai-prank-call": (5, '偏自助的交付形态，人工介入少。'),
    "betting-tips": (5, '偏自助的交付形态，人工介入少。'),
    "ai-voice-calls": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "demopolish": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "chatsell-co": (4, '偏自助的交付形态，人工介入少。'),
    "roster-for-slack": (4, '偏自助的交付形态，人工介入少。'),
    "static-run": (5, '偏自助的交付形态，人工介入少。'),
    "ai-math-solver": (5, '偏自助的交付形态，人工介入少。'),
    "ai-smart-decor": (5, '偏自助的交付形态，人工介入少。'),
    "verse": (4, '偏自助的交付形态，人工介入少。'),
    "clawbox": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "bankstatemently": (1, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 local_service 同型。'),
    "fit-it-on": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "ai-influencer-generator": (3, '偏自助的交付形态，人工介入少。'),
    "flowy": (3, '偏自助的交付形态，人工介入少。'),
    "check-id": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "chat-whisperer": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "quickvoc": (3, '偏自助的交付形态，人工介入少。'),
    "blue-esim": (5, '偏自助的交付形态，人工介入少。'),
    "captime": (4, '偏自助的交付形态，人工介入少。'),
    "alodata": (5, '偏自助的交付形态，人工介入少。'),
    "chiptune": (5, '偏自助的交付形态，人工介入少。'),
    "calsy": (5, '偏自助的交付形态，人工介入少。'),
    "firsto": (5, '偏自助的交付形态，人工介入少。'),
    "atslebenslauf-com": (4, '偏自助的交付形态，人工介入少。'),
    "anymelo": (4, '偏自助的交付形态，人工介入少。'),
    "reddit-grow": (3, '偏自助的交付形态，人工介入少。'),
    "cleaner": (4, '偏自助的交付形态，人工介入少。'),
    "arbichat-s-l": (5, '偏自助的交付形态，人工介入少。'),
    "bromeai": (5, '偏自助的交付形态，人工介入少。'),
    "fire-tech-apps-llc": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "bolsai": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 finance_trading 同型。'),
    "sportsjobs-online": (3, '偏自助的交付形态，人工介入少。'),
    "chartvio": (5, '偏自助的交付形态，人工介入少。'),
    "brikk": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "elispeak": (4, '偏自助的交付形态，人工介入少。'),
    "myterrace-net": (5, '偏自助的交付形态，人工介入少。'),
    "cat-games": (5, '偏自助的交付形态，人工介入少。'),
    "c12s-ai": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "forage": (5, '偏自助的交付形态，人工介入少。'),
    "aiwith-me": (3, '偏自助的交付形态，人工介入少。'),
    "june": (4, '偏自助的交付形态，人工介入少。'),
    "blockit": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "orbit-ai": (5, '偏自助的交付形态，人工介入少。'),
    "aidelly": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "car-docs-ro": (3, '偏自助的交付形态，人工介入少。'),
    "ai-swap-facetik": (4, '偏自助的交付形态，人工介入少。'),
    "climbx": (3, '偏自助的交付形态，人工介入少。'),
    "blainy": (4, '偏自助的交付形态，人工介入少。'),
    "aistudios": (4, '偏自助的交付形态，人工介入少。'),
    "car-dealer-tracker": (4, '偏自助的交付形态，人工介入少。'),
    "careerkit": (4, '偏自助的交付形态，人工介入少。'),
    "chunks-microlearning": (4, '偏自助的交付形态，人工介入少。'),
    "alienart-io": (3, '偏自助的交付形态，人工介入少。'),
    "chartsgpt": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 finance_trading 同型。'),
    "brandiseer": (5, '偏自助的交付形态，人工介入少。'),
    "clear-food": (5, '偏自助的交付形态，人工介入少。'),
    "bolty": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "chefgpt": (4, '偏自助的交付形态，人工介入少。'),
    "basincheck": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "alphascope": (4, '偏自助的交付形态，人工介入少。'),
    "business-freelance": (4, '偏自助的交付形态，人工介入少。'),
    "aura-ai-ai-video-image-generator": (4, '偏自助的交付形态，人工介入少。'),
    "coaching-marketing-kit": (4, '偏自助的交付形态，人工介入少。'),
    "fishingai": (4, '偏自助的交付形态，人工介入少。'),
    "cooldock": (5, '偏自助的交付形态，人工介入少。'),
    "auraclaw": (5, '偏自助的交付形态，人工介入少。'),
    "bloxg-roblox-marketing-agency": (5, '偏自助的交付形态，人工介入少。'),
    "archifruits-ai": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "cheatmate": (4, '偏自助的交付形态，人工介入少。'),
    "chatedu-inc": (4, '偏自助的交付形态，人工介入少。'),
    "agentsguild": (5, '偏自助的交付形态，人工介入少。'),
    "ai-baby-generator": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "adstudio": (4, '偏自助的交付形态，人工介入少。'),
    "adriselab": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "ai-cover-music-video": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "affililist": (3, '偏自助的交付形态，人工介入少。'),
    "ai-detector-image-checker": (4, '偏自助的交付形态，人工介入少。'),
    "admirror": (3, '偏自助的交付形态，人工介入少。'),
    "account-expense-manager": (4, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "anon-2": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "7running": (4, '偏自助的交付形态，人工介入少。'),
    "private-venture": (5, '偏自助的交付形态，人工介入少。'),
    "aftersora": (4, '偏自助的交付形态，人工介入少。'),
    "3dplotter": (5, '偏自助的交付形态，人工介入少。'),
    "agence-web-abstract27": (5, '偏自助的交付形态，人工介入少。'),
    "20-0": (5, '偏自助的交付形态，人工介入少。'),
    "ai-directories": (3, '偏自助的交付形态，人工介入少。'),
    "adstellar": (2, '交付偏重：要人接需求、做实施或驻场，一个人能吃下但规模受限于时间，与 agency_service 同型。'),
    "aesty": (5, '偏自助的交付形态，人工介入少。'),
    "adle": (4, '偏自助的交付形态，人工介入少。'),
    "acsperf": (5, '偏自助的交付形态，人工介入少。'),
    "the-stealthiest-of-ventures": (5, '偏自助的交付形态，人工介入少。'),
    "gtm-help": (3, '偏自助的交付形态，人工介入少。'),
    "mac-duo": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "dope-data": (5, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "orior-ai": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    "agenkit": (5, '偏自助的交付形态，人工介入少。'),
    "kreapro": (4, '偏自助的交付形态，人工介入少。'),
    "toonthis": (3, '自助式产品（App/API/工具），用户自注册自配置，人工交付极少，与同型自助工具一致。'),
    # ── 2026-10-27 批次漏补的两条（2026-10-29 补）──
    # ⚠ 537b443 把 upvoty / flodesk 写进 cases.json 时只带了 replicability，
    # 没跟 SOLO / CHINA 两张判断表 ⇒ test_fit_ready 的「不许缺判断」从 10-27
    # 起一直红（本地与 CI 同红，不是环境差异）。补的时候没套下面的通用文案：
    # 这两条都是自助订阅，但交付负担不同 —— 一条在集成维护，一条在送达率。
    "upvoty": (4, '自助订阅，开通零实施：用户在页面里自己贴脚本、装集成，投票板与'
                  '路线图开箱即用。负担在**集成与 AI 辅助的长期小修** —— '
                  'GitHub/Slack/Jira 的授权与接口会变，推荐规则要跟着用户反馈调，'
                  '属「长期小修」而非「一次性上线」；不需要交付团队，也不需要值守。'),
    "flodesk": (3, '自助上手，设计器与模板库本来就是让用户自己动手的；但它有一块躲不掉'
                   '的交付负担 —— **送达率**。发信 IP 信誉、退信与投诉率、各大邮箱'
                   '服务商的过滤规则都要长期盯着，掉进垃圾箱客户当天就来，而它没有'
                   '销售团队可以分担这层售后。比纯工具重，比项目制轻。'),
    # ── 2026-10-29 补：fit_ready --check 报的 26 条 ──
    # 18 条是本轮晋升进来的，8 条是更早发布时漏掉的。判据一律按**交付形态**
    # 而不是收入高低：有些案子体量很大（headshotpro、conductor），但交付仍然是
    # 自助的；有些体量很小（freelance-writing-club、agent-media），交付却是
    # 每天要有人真干活 —— 后者才是「一个人做不了」的那类。
    "flowmance-templates": (4, "卖的是模板与区块文件，付款即下载，没有实施环节。负担在"
                               "持续出新品：模板库一停更，长尾关键词带来的订单立刻变少，"
                               "属于长期小修而不是一次性上线。"),
    "neoscene": (4, "iOS 自助订阅，开通零人工。负担在推理成本与模型迭代——图生视频按次"
                    "烧算力，模型换代后效果落后就掉订，而档案自述毛利率只有 10%，"
                    "这块成本没有缓冲空间。"),
    "boxer-ai": (4, "下载即用，没有交付团队。负担在机型适配：靠 iPhone 摄像头做动作识别，"
                    "每代新机型的摄像头与算力都要重测，安卓碎片化会更重，所以它只做 iOS"
                    "不是保守而是必然。"),
    "kori": (4, "自助订阅，零交付。负担在内容：口语课要按场景一节节写，停更就是产品贬值，"
                "但一个人一天写一节是可持续的节奏，不需要团队。"),
    "admitodds-ai-college-admissions-platform": (3, "自助上传、自助出结果，但有两块躲不掉的"
                                                "人工负担：一是录取数据与判断规则要按年更新，"
                                                "二是判断错了家长会来追问，这是要背后果的售后，"
                                                "不能当成纯工具。"),
    "clamshell": (5, "卖的是一个二进制文件，$9.99 一次买断，交付成本为零。唯一负担是"
                     "macOS 大版本升级后重测一次，一年几次，一个人完全扛得住。"),
    "colorcraft": (4, "一句话进、PDF 出，全自助。负担在模板与规则：KDP 的排版校验规则"
                      "会变，PDF 内嵌字体与出血也要跟着调，属长期小修。"),
    "kodo": (4, "浏览器里自助使用，没有实施环节。负担在算力与生成质量——AI 生成要"
                "持续调模型与提示词，成本按 credits 走，量一上来毛利就被压。"),
    "freelance-writing-club": (2, "本质是人工编辑：每个工作日都要有人真读一遍稿、筛出"
                                  "值得写的约稿线索，再写成一封邮件。这个「人肉筛选」"
                                  "就是产品本身，自动化掉之后订阅理由也就没了，一个人"
                                  "最多撑到几千订。"),
    "agent-media": (2, "订阅卖的是**连续更新的剧集**，不是工具。固定演员阵容的短剧要"
                       "一集集写、一集集生成，停更就掉订；这是内容生产的活，不是软件"
                       "交付，一个人做不成一门订阅生意。"),
    "lapsula": (3, "诊所自助开户，但两处要人：一是各家诊所的排班与流程差异要陪客户配一遍，"
                   "二是涉及病患数据，出事要有人负责合规。比纯 SaaS 重，比定制项目轻。"),
    "lyro": (4, "自助订阅，交付零人工。负担在算力与版权边界：歌曲生成按次烧算力，"
                "生成内容的版权归属要盯平台规则，但都不需要团队。"),
    "flint": (3, "订阅自助，走 iMessage 分发省掉了 App 发行那一整套。负担在内容与通道："
                 "灵修内容要持续写，iMessage 通道的投递与 Apple 的政策变动不归自己管。"),
    "pdf-vault": (3, "交付是授权文件 + 托管销售页，自助完成。负担在授权纠纷与被转售滥用："
                     "卖「可转售授权」意味着要处理买家之间转手带来的客服与追责，"
                     "这块没法完全自动化。"),
    "coldcaller-pro": (3, "订阅自助，但产品就是线索库——数据要持续抓、持续清洗，"
                          "过期数据等于产品失效。这是长期的运营负担，不需要团队但需要"
                          "天天干。"),
    "coldsire": (3, "技术上是自动开通（配 DNS、接 Instantly），但邮箱商与反垃圾规则一变，"
                    "客户当天就收不到信、就来找你。运维式交付，比纯 SaaS 重。"),
    "knightvision": (4, "自助上传需求出素材，没有实施环节。负担在算力成本与素材合规："
                        "广告素材要过平台审核，违规词与虚假宣传的风险由你兜。"),
    "conductor": (3, "面向公司客户，接入要文档与技术支持，但不需要实施团队——API 产品"
                     "的交付是「文档写好 + 出问题能定位」。比 C 端工具重，比项目制轻。"),
    "headshotpro": (4, "全自助：上传照片、出图、下载，没有人工环节。负担在模型效果与退款——"
                       "证件照对「像不像」极敏感，不满意就退，这是长期的效果调优工作。"),
    "klar": (3, "学生自助订阅，没有交付团队。负担在题库：要按本地考试大纲持续补题、"
                "跟着考纲改版重做，内容量比代码重。"),
    "refactoring-ui": (5, "一本电子书，卖文件，交付成本为零，也不需要维护。极端地说："
                          "写完之后可以十年不管。"),
    "pdf-ai": (4, "自助上传、自助问答。负担在解析质量与成本：文档格式千奇百怪，"
                  "解析出错用户立刻感知，模型调用又按量计费，两头都要长期盯。"),
    "david-ai": (2, "有线下人工环节：AI 批量造站只是前半段，后半段是寄明信片、接来电、"
                    "真跟进。这条路本质是本地 BD，一个人只能覆盖极窄的一片区域，"
                    "扩不了量。"),
    "sleek": (4, "自助使用，没有实施。负担在 credits 成本与生成质量，与 kodo 同类，"
                 "一个人可以维持，但量大了算力账单要先算清楚。"),
    "3ak-track": (4, "学生自助订阅。负担在训练计划内容：田径项目多、周期长，计划要按"
                     "赛季写，一个人能写但会占掉大部分时间。"),
    "plausible-analytics": (3, "云托管是自助订阅，但开源这一层带来躲不掉的负担：issue、"
                              "PR、自托管用户的提问都来找你，还要跟浏览器与 CMS 的"
                              "兼容性变化。长期小修，且没有上限。"),
}


# ------------------------------------------------------------------ 工具

def load_json(name, default=None):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name, payload):
    path = os.path.join(DATA_DIR, name + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def to_score(raw, max_raw):
    return round(raw / max_raw * 100, 1)


def medal_for(rank):
    return {1: "gold", 2: "silver", 3: "bronze"}.get(rank)


def composite(solo, china):
    """木桶式聚合：短板占 6 成权重。两者缺一，总分就塌。"""
    lo, hi = min(solo, china), max(solo, china)
    return round(0.6 * lo + 0.4 * (solo + china) / 2, 1)


def quadrant_of(solo, china, threshold):
    if solo >= threshold and china >= threshold:
        return "go"
    if solo >= threshold:
        return "export"
    if china >= threshold:
        return "partner"
    return "skip"


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser(description="个人可做性评分 + 双轴综合诊断")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--top", type=int, default=0, help="只打印前 N 名（0 = 全部）")
    ap.add_argument("--threshold", type=float, default=70.0, help="四象限及格线，默认 70")
    args = ap.parse_args()

    cases = load_json("cases", [])
    if not cases:
        print("[!] data/cases.json 为空")
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    no_rep, no_delivery, no_china = [], [], []
    rows = []

    for c in cases:
        cid = c.get("id")
        rep = c.get("replicability") or {}

        # 四维反推
        dims = {}
        ok = True
        for key, src in INVERT.items():
            v = rep.get(src)
            if not isinstance(v, int):
                ok = False
                break
            dims[key] = 6 - v
        if not ok:
            no_rep.append(cid)
            continue

        # 单人交付：人工判断
        d = DELIVERY.get(cid)
        if not d:
            no_delivery.append(cid)
            continue
        dims["delivery"] = d[0]
        delivery_note = _plain(d[1])

        raw = round(sum(dims[k] * SOLO_WEIGHTS[k] for k in SOLO_WEIGHTS), 1)
        solo = to_score(raw, SOLO_MAX)

        china = (c.get("china_fit") or {}).get("score")
        if china is None:
            no_china.append(cid)

        rows.append({
            "case": c, "dims": dims, "raw": raw, "solo": solo,
            "delivery_note": delivery_note, "china": china,
        })

    # --- solo_fit 排名 ---
    rows.sort(key=lambda x: (-x["solo"], x["case"].get("name") or ""))
    for i, r in enumerate(rows, 1):
        r["solo_rank"] = i
        r["solo_medal"] = medal_for(i)

    # --- composite 排名（只算有 china_fit 的）---
    both = [r for r in rows if r["china"] is not None]
    for r in both:
        r["composite"] = composite(r["solo"], r["china"])
        r["quadrant"] = quadrant_of(r["solo"], r["china"], args.threshold)
    both.sort(key=lambda x: (-x["composite"], x["case"].get("name") or ""))
    for i, r in enumerate(both, 1):
        r["composite_rank"] = i
        r["composite_medal"] = medal_for(i)

    # --- 写入 ---
    for r in rows:
        c = r["case"]
        c["solo_fit"] = {
            "score": r["solo"],
            "raw": r["raw"],
            "max_raw": SOLO_MAX,
            "rank": r["solo_rank"],
            "medal": r["solo_medal"],
            "dims": r["dims"],
            "weights": SOLO_WEIGHTS,
            "delivery_note": r["delivery_note"],
            "derived_from": "replicability (%s)" % ", ".join(
                "%s=6-%s" % (k, v) for k, v in INVERT.items()),
            "scored_at": today,
        }
        if r["china"] is None:
            c.pop("composite", None)
            continue
        c["composite"] = {
            "score": r["composite"],
            "solo": r["solo"],
            "china": r["china"],
            "rank": r["composite_rank"],
            "medal": r["composite_medal"],
            "quadrant": r["quadrant"],
            "quadrant_label": QUADRANTS[r["quadrant"]][0],
            "formula": "0.6×min(solo,china) + 0.4×mean(solo,china)",
            "threshold": args.threshold,
            "scored_at": today,
        }

    # --- 打印 ---
    W = 90
    print("=" * W)
    print("  一、个人可做性排行（solo_fit）· 满分 %.1f → 归一化 100" % SOLO_MAX)
    print("  权重：单人交付 1.4｜够得着客户 1.3｜启动轻 1.1｜造得出来 1.0｜窗口 1.0")
    print("  ※ build/reach/capital/window 由 replicability 反推，只有 delivery 是新判断")
    print("=" * W)
    lbl = {1: "🥇 金牌", 2: "🥈 银牌", 3: "🥉 铜牌"}
    shown = rows[:args.top] if args.top > 0 else rows
    for r in shown:
        c = r["case"]
        print("\n%s  %2d. %-24s %5.1f / 100" % (
            lbl.get(r["solo_rank"], "   "), r["solo_rank"], c.get("name", "")[:24], r["solo"]))
        print("      " + "  ".join("%s %d" % (lab, r["dims"][k]) for k, lab, _ in SOLO_DIMS))
        print("      交付：%s" % r["delivery_note"])
    if len(shown) < len(rows):
        print("\n  … 其余 %d 条略（--top 0 看全部）" % (len(rows) - len(shown)))

    print("\n\n" + "=" * W)
    print("  二、双轴综合排行（composite）· 0.6×短板 + 0.4×均值")
    print("  及格线 %.0f 分：双高＝可以开干，单高＝要么做海外、要么找人合伙" % args.threshold)
    print("=" * W)
    shown2 = both[:args.top] if args.top > 0 else both
    for r in shown2:
        c = r["case"]
        qlabel, _ = QUADRANTS[r["quadrant"]]
        print("\n%s  %2d. %-24s %5.1f" % (
            lbl.get(r["composite_rank"], "   "), r["composite_rank"],
            c.get("name", "")[:24], r["composite"]))
        print("      个人可做性 %5.1f  ×  国内移植性 %5.1f   → [%s] %s" % (
            r["solo"], r["china"], r["quadrant"], qlabel))
    if len(shown2) < len(both):
        print("\n  … 其余 %d 条略" % (len(both) - len(shown2)))

    print("\n\n" + "=" * W)
    print("  三、四象限分布")
    print("=" * W)
    for key in ("go", "partner", "export", "skip"):
        grp = [r for r in both if r["quadrant"] == key]
        label, desc = QUADRANTS[key]
        print("\n  [%s] %s（%d 条）— %s" % (key, label, len(grp), desc))
        for r in grp:
            print("      · %-24s solo %5.1f  china %5.1f" % (
                r["case"].get("name", "")[:24], r["solo"], r["china"]))

    print("\n" + "=" * W)
    if both:
        t3 = both[:3]
        print("  综合金银铜：%s" % " / ".join(
            "%s %s" % (n, r["case"].get("name"))
            for n, r in zip(["金", "银", "铜"], t3)))
    print("=" * W)

    # --- 问题提示 ---
    if no_rep:
        print("\n[!] 缺 replicability，无法算 solo_fit：%s" % ", ".join(no_rep))
    if no_delivery:
        print("\n[!] DELIVERY 表里没填（将跳过）：%s" % ", ".join(no_delivery))
    if no_china:
        print("\n[i] 以下案例还没跑 score_china_fit.py，本次不算综合分：%s"
              % ", ".join(no_china))

    if args.dry_run:
        print("\n--dry-run：未写入 data/cases.json")
        return 0

    save_json("cases", cases)
    print("\n已写入 data/cases.json（solo_fit %d 条，composite %d 条）" % (len(rows), len(both)))
    print("页面刷新即可看到「综合排行」视图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
