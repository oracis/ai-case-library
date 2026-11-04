#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国内移植可行性评分。

给每条精写案例打 6 个维度的分（各 1-5 分，5 = 最容易搬回国内做），
按权重算总分、排名，前三名给金 / 银 / 铜牌。

维度和权重（权重向「生死线」倾斜）：
    demand       1.5   国内目标客户会不会真掏钱
    payment      1.2   国内能不能顺畅收到钱（Stripe 不可用是最大的坑）
    compliance   1.2   是否触碰监管红线
    acquisition  1.0   海外获客渠道在国内有没有等价物
    localization 1.0   本地化改造量，越小分越高
    competition  1.0   国内是否已有强势免费替代

满分 = 5 × 6.9 = 34.5，归一化到 0-100。

这不是「个人能不能做」的评分——那是 cases.json 里已有的 replicability 四维。
这里是「能不能搬回中国做」，两件事经常结论相反：
比如 CheckVibe 一个人完全能做，但在国内卖不动；Nitra 产品很好做，但医疗牌照拿不到。

用法：
    python scripts/score_china_fit.py             # 打分并写回 data/cases.json
    python scripts/score_china_fit.py --dry-run   # 只看结果，不写入
    python scripts/score_china_fit.py --top 8     # 只打印前 8
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

WEIGHTS = {
    "demand": 1.5,
    "payment": 1.2,
    "compliance": 1.2,
    "acquisition": 1.0,
    "localization": 1.0,
    "competition": 1.0,
}

DIMS = [
    ("demand", "付费意愿", "国内目标客户会不会真掏钱"),
    ("payment", "支付可达", "国内能不能顺畅收款"),
    ("compliance", "合规空间", "越不碰红线分越高"),
    ("acquisition", "获客迁移", "海外渠道在国内有没有等价物"),
    ("localization", "改造成本", "要改多少才算能用"),
    ("competition", "竞争空位", "国内是否已有强势免费替代"),
]

MAX_RAW = 5 * sum(WEIGHTS.values())          # 34.5

# ------------------------------------------------------------------ 评分表
# 分数是主观判断，但每一条都写明了依据。要调整就改这里，然后重跑。
def _plain(s):
    """note 是**纯文本**字段（站点上 `esc()` 直出，markdown 不生效）。

    2026-10-29：`test_report_notes_have_no_bold_marker` 抓到 9 条 note 里写了
    `**强调**`，读者看到的是字面星号。在写入口统一剥掉，比逐条改字面量稳。
    """
    return (s or "").replace("**", "")


SCORES = {
    # ── 2026-10-26 backlog「有条件」档入库的 6 条 ──
    "serviceform": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="渠道与本地生态对接",
        note="需求与预算都是现成的：「官网多抓一个客户」在国内是成熟采购项（在线客服、SCRM、"
             "表单工具都在卖），而且预算从销售口出，天花板比客服口高，付费意愿给 4。"
             "真正难搬的是它的**渠道结构**——145+ 家代理商把交付外包出去才覆盖 20+ 国家，"
             "国内这套要么自建地推、要么重找集成商，获客迁移只给 2。本地化 3：要接企业微信、"
             "400 电话、国内 CRM，表单与预约的链路都得重做。竞争 2：智齿、网易七鱼、纷享销客"
             "已经把「官网对话获客」这个位置占住，空位只在「对话直接推进到成交」这一层。",
    ),
    "salesrobot": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="平台生态与封号治理",
        note="这条基本只能借方法、借不了产品。它的全部价值绑在 LinkedIn 上——底层走官方移动端"
             "API、配住宅 IP、按警告动态节流，而 LinkedIn 在国内不可用；换成微信/企业微信后，"
             "「自动化外挂」是平台零容忍项，封号不是风险而是必然，所以合规与本地化都给 1。"
             "可借的是那套定位法：把品类里最吓人的风险（封号）量化成一个数字当唯一卖点。"
             "需求给 3——国内 B2B 冷启动同样痛，但合规路径完全不同（企业微信官方接口只能做"
             "「客户联系」不能做「批量添加」）。",
    ),
    "jobright": dict(
        demand=5, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="招聘平台数据不开放",
        note="需求端国内比美国更凶（求职内卷、海投更普遍），5 分站得住。但两个硬伤：一是"
             "**数据源**——它的钩子是 H-1B 担保查询，国内对应的是「这家公司落不落户、社保基数、"
             "有没有裁员史」，而这些数据散在 BOSS 直聘/脉脉/企查查手里且不开放 API，"
             "职位数据也拿不到；二是合规，简历属个人信息，招聘类平台在国内实际需要相应资质，"
             "且抓取第三方职位数据有明确法律风险。C 端付费意愿只给 3（国内求职者为工具付费"
             "的习惯弱于美国）。可借的是「创始人 IP + 一个信息鸿沟当钩子」这套冷启动方式。",
    ),
    "sandra-ai": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="DMS 封闭与电话营销治理",
        note="它的价值锚是「不漏接电话」，而国内车行的线索入口已经大幅迁到微信与抖音私信，"
             "纯电话接听的份额比欧美低，需求给 3。付款方是车行老板、按门店订阅，B 端付费"
             "意愿给 4。两处卡点：一是 DMS——它的深度价值来自接进经销商管理系统查真实库存与"
             "排期，国内 DMS 由几家厂商把持（CDK、经销商集团自建），第三方接不进去；"
             "二是合规，接听侧相对安全，但一旦做外呼回拨就落进电话营销治理与实名制，"
             "录音还涉及个人信息单独同意。竞争 3：国内有 AI 客服，但「按门店卖、直接约试驾」"
             "这个位置还没被占死。",
    ),
    "outward": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="民调资质与数据合规",
        note="**民意调查在国内不是普通生意**：涉外民调、公开发布民调结果都有明确管理要求，"
             "这一条决定了合规只能给 1，也是它最不可能整体搬过来的原因。能搬的是纯商业侧——"
             "品牌方的新品测试、广告效果调研、用户满意度，这块国内有问卷网、腾讯问卷等成熟玩家，"
             "需求给 3、竞争 2。它的方法论护城河（前 Harris Poll 的人把关抽样与质控）在国内"
             "对应的是「有调研公司背景的合伙人」，不是一个人能补的。替代路径是把 AI 压在"
             "「交付周期」上做一家调研工作室，而不是做一个平台。",
    ),
    "workhero": dict(
        demand=3, payment=2, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="小 B 付费能力与地推成本",
        note="形态很清楚：AI 干活 + 行业老手复核，定价锚在「雇一个人的零头」。国内对应行业是"
             "暖通、家电维修、装修队，同为 20 人以下极度分散，但两处不同：一是小 B 付费能力——"
             "这类老板已经在用便宜的代账与开票服务（年费千元级），「按月订阅后台」的价格带"
             "被压得很低，付费意愿只给 2；二是政策红利不存在——它的高价值环节是代报州级热泵"
             "退税（政策越复杂越刚需），国内没有等价物，剩下的开票、报批、保修都能被现成"
             "财税/售后 SaaS 覆盖，竞争给 3。要抄只能抄「AI 执行 + 真人复核」这个交付结构。",
    ),
    # ── 2026-10-26 backlog「形态可抄」第一梯队入库的 3 条 ──
    "autoshorts": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="平台发布接口",
        note="「不露脸批量做短视频」这个需求国内比海外更凶（抖音/视频号的日更压力更大），"
             "付费意愿 4 分站得住。真正卡的是那两条最值钱的东西：一是分发管道——它的核心资产是"
             "直连 TikTok/YouTube/IG 的自动发布接口，国内没有等价物（抖音开放平台的发布权限对企业"
             "资质有门槛，视频号/小红书根本没有开放发布接口），整条管道要重做；二是合规，AI 生成"
             "真人形象与合成内容在国内要过标识义务与肖像授权。竞争给 2：剪映、度加这类免费工具"
             "已经吃掉「生成」这一层，空位只剩「跨平台自动发」。",
    ),
    "rosie": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="电信实名与外呼治理",
        note="本地小生意漏接电话 = 直接丢单，这个痛点在国内一模一样（美团/抖音本地生活的商家"
             "同样靠电话接单），付费意愿给 4。合规要小心：**接听侧**相对安全，但产品一旦从"
             "「接电话」扩到「回拨/外呼」，就落进骚扰电话治理与电信实名那一套，这是国内与海外"
             "最大的分岔。本地化量不小——要接 400/固话、要对接微信/美团预约、要给老板发微信"
             "而不是短信。竞争 3：国内有 AI 客服，但「按门店卖 ¥300 级自助语音前台」这个位置"
             "还没被占死。",
    ),
    "boredhumans": dict(
        demand=2, payment=2, compliance=3, acquisition=1, localization=3,
        competition=2,
        note="工具本身最通用（改语言就能跑），但它的天花板建在**Google 自然流量 + 全球分发 + "
             "Stripe 廉价收款**这三件事上，国内三件全不同：百度与微信生态 SEO 权重逻辑完全不同、"
             "小工具站变现主要靠广告联盟且单价低、海外订阅收款渠道也没有。所以获客迁移只给 1、"
             "付费意愿 2——这套模型在国内能跑通的是「工具矩阵」这个形态，不是它的收入量级。",
    ),
    # ── 2026-10-26 TrustMRR 档案已下架的两条（降级为 partial）──
    "gistly": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=2, competition=1,
        note="需求与预算都在：会话/通话质检在国内是成熟采购项（金融电销、教育、电商客服都在买），"
             "而且是既有 IT 预算里的科目，不用教育客户。搬不动的有三处。一是合规——它卖的核心是"
             "「全渠道 100% 捕获」，通话录音在国内属《个人信息保护法》下的敏感个人信息，要单独同意，"
             "抽样质检的合规风险远低于全量入库，这个卖点在国内是最难落地的那一档。二是改造量——"
             "中文/方言 ASR、话术规则库、与国内呼叫中心/企微/CRM 的对接全得重做。三是竞争——"
             "智能质检这一格已经站着阿里云、腾讯云、容联云、循环智能，而且不少是按坐席捆绑卖的。"
             "能搬的是产品定义本身：把「不抽样 + 客户自定义 QA 标准 + 结论挂源链接」这三句话照抄，"
             "在国内依然是个没被占死的定位。",
    ),
    "research-match": dict(
        demand=3, payment=2, compliance=2, acquisition=3, localization=1, competition=1,
        blocker="学术数据源",
        note="国内对应场景是保研/考研套磁与留学申请，需求真实且带截止日期，这一点比海外更硬。"
             "但三个环节都卡住：付费端——学生个人付不起 ¥19,172/月的客单价，能付的是留学中介和院系，"
             "而机构渠道靠关系不靠产品；合规端——替学生生成申请材料在国内已撞学术诚信红线，"
             "「只给素材不代写」的定位能减风险但不消除，2.5 亿篇论文索引的来源与授权也另有问题；"
             "最致命的是数据源——国内没有可直接索引的「教授库」，导师信息散在各校研究生院页面，"
             "CNKI 等文献库不开放 API，海外那套「按影响力排出教授」的地基在国内要从零自建。"
             "获客给 3：申请季搜索流量与小红书/知乎内容在国内外逻辑相近，是唯一能直接搬的部分。",
    ),
    # ── 2026-10-26 从 howtomakemi 官方披露线入库的 8 条 ──
    "arcads": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=2, competition=3,
        note="投放素材产能这个需求在国内是硬的（巨量/腾讯广告的素材消耗量比海外更凶），"
             "付费方手里有预算、价格敏感度低，这两点是可搬的。真正难搬的是两端："
             "一是获客——它的增长靠「自家产品跑自家广告」，国内等价物只有巨量千川，"
             "而千川的素材规则、审核口径全都不一样；二是形象合规——AI 生成的真人形象"
             "在国内要过肖像权授权与「AI 生成」标识义务，模型与素材库都得重做。"
             "竞争给 3：做 AI 视频的工具一堆，但「批量产出能直接投出去的素材」这层还没被占死。",
    ),
    "instantly": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="邮件群发合规",
        note="国内 B2B 触达的主战场不在邮件，而在企业微信、电话和短信——它的核心资产"
             "（1.5M 账户的暖箱网络）在国内没有对应物，等于把产品的地基换掉。"
             "更硬的是合规：批量冷邮件在国内直接撞《反垃圾邮件》治理与《个人信息保护法》，"
             "「抓联系人 + 群发」是明确红线。真要落地得换成企微 + 智能外呼，"
             "而那一格站着探迹、销售易、纷享销客。可搬的是定价结构：按发信账号数收费，"
             "客户业务越大客单价自动涨，这个设计跟平台无关。",
    ),
    "ahron": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        note="国内中大型企业的 HR 系统碎片化程度不比欧洲轻（ATS、入职、绩效、考勤各一套），"
             "「用一层 agent 串起来」的价值成立，企业也有预算。卡点在两头："
             "一是要接北森、i 人事、钉钉/飞书的既有数据，流程挖掘的输入在国内是另一套接口；"
             "二是人事数据属敏感个人信息，等保与个保合规跑不掉。"
             "竞争给 2：北森、Moka 已经在往 AI 走，但它们做的是「系统 + 助手」，"
             "「先流程挖掘再让 agent 执行」这条路径国内还空着。",
    ),
    "fyxer": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="它的产品形态完全长在 Gmail/Outlook 上——国内办公的主入口是飞书、企微、钉钉，"
             "邮箱只是附属，所以「嵌进邮箱」这一刀在国内要先换宿主，等于重做集成层。"
             "而飞书妙记、通义听悟已经把会议纪要做进免费额度，付费空间被压得很薄。"
             "真正值得抄的是两条不依赖平台的：① 面向高管卖「有人替你把事办了」，"
             "而不是卖工具；② 主动不代发、只起草，用限制权限换信任。",
    ),
    "heyreach": dict(
        demand=3, payment=3, compliance=1, acquisition=1, localization=1, competition=1,
        blocker="平台缺失 + 自动外发合规",
        note="它的需求来自 LinkedIn 的每日好友请求限流——而 LinkedIn 职场版在国内 2023 年就已关停，"
             "连舞台都没有，这是最彻底的不可移植。就算硬搬，"
             "「多账号轮换 + 自动加好友 + 自动私信」在国内同时撞《个人信息保护法》"
             "与平台反自动化治理两堵墙。可搬的只有方法论："
             "平台规则一收紧，「技巧」就变成所有人的共同刚需，做工具的人吃最大红利。",
    ),
    "campfire": dict(
        demand=4, payment=4, compliance=3, acquisition=1, localization=1, competition=1,
        blocker="会计准则 + 存量厂商",
        note="换 ERP 是十年一遇的决策，国内这一格被用友、金蝶、浪潮按行业分完，"
             "而且它们的护城河是实施商网络与发票/税务/审计的本地合规，不是产品体验。"
             "技术侧也要重做：它押的 ASC 606 收入确认在国内对应的是新收入准则与电子发票体系。"
             "唯一可搬的是叙事方法——把 ROI 收敛成一个会计圈都懂的单一指标（月结天数 15→3），"
             "让 CFO 自己算出账。",
    ),
    "wispr-flow": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="免费输入法占位",
        note="语音转文字在国内不是新需求，但付费这一环被堵死：讯飞输入法、搜狗输入法"
             "把「长按说话转文字」做成了免费基础功能，用户没有为它单独掏月费的习惯。"
             "它真正的差异（理解意图、自动改口、中英混说）技术上可以复刻，"
             "但要落地还得过输入法这个入口——而输入法是系统级权限，不是装个 App 就能抢的。"
             "可搬的是增长路径：单平台做到惊艳 → 等名单当发布会 → 补齐多端。",
    ),
    "build-concierge": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        note="国内家政、维修、装修、开锁这类上门服务公司的痛点一模一样——电话漏接、"
             "工单靠手写、师傅靠人调度，而且它们的付费能力与「一个漏单多少钱」直接挂钩，"
             "算得比 SaaS 客户更清楚。可移植性中上：合规压力小（不涉敏感数据），"
             "但要接微信、电话、短信三套入口，还要对接家政平台/工单系统，改造量不小。"
             "获客是软肋：这个行业极度分散，海外靠创始人行业人脉，国内只能地推或走加盟体系。",
    ),
    "gojiberryai": dict(
        demand=3, payment=3, compliance=1, acquisition=1, localization=2, competition=1,
        blocker="自动化触达合规",
        note="整套动作长在 LinkedIn / Reddit 的开放数据和自动外发上。国内两边都缺："
             "既没有等价的职业信号源（LinkedIn 职场版 2023 年就关停了），《个人信息保护法》"
             "又把「抓取 + 群发」直接定性为红线——与 meerkats-ai 撞的是同一堵墙。"
             "真要落地得把信号源换成企查查 / 脉脉，触达换成企业微信，等于重做一遍产品；"
             "而那一格还站着探迹、销售易、纷享销客。唯一不看国界的是它的方法论："
             "先用人工交付证明有人为结果付钱，再把它写成软件。",
    ),
    "sierra": dict(
        demand=4, payment=4, compliance=3, acquisition=1, localization=2, competition=1,
        note="按「问题被真正解决」收费的结果定价值得学，但客户是美国财富 500 强——"
             "靠的是创始人 Bret Taylor 的顶级人脉。国内对应的大客户被阿里云、火山引擎把着，"
             "一个人连门都进不去。学模式可以，抄生意不行。",
    ),
    "genius-ai": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="国内同类客户（美发店、美甲店）被美团收银、客如云、有赞占着，"
             "且预约流程必须接微信小程序和美团，等于把产品重做一遍。",
    ),
    "nitra": dict(
        demand=4, payment=4, compliance=1, acquisition=3, localization=1, competition=1,
        blocker="医疗合规",
        note="医疗数据在国内是强监管（等保、HIS 资质、数据不出院），"
             "且卫宁健康这类厂商已深耕二十年。移植的难度不在产品，在牌照。",
    ),
    "viktor": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        note="它的生态位是 Slack App Directory；国内对应的飞书 / 钉钉应用市场规模小得多，"
             "而且飞书、钉钉自己就把 AI 助手做进了免费版。",
    ),
    "chatbase": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2,
        note="国内这条赛道已经挤满——Dify、FastGPT、Coze 都能做，还有开源自部署。"
             "想切入只能打垂直行业（比如只做教培或医美的客服机器人）。",
    ),
    "rezi": dict(
        demand=2, payment=3, compliance=5, acquisition=3, localization=3, competition=2,
        blocker="C 端付费弱",
        note="简历工具在国内是「一次性、低客单、用完即走」，超级简历这类已经做到免费。"
             "收入来自订阅，但国内用户不习惯为简历订阅付费。",
    ),
    "postiz": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=2, competition=3,
        blocker="平台 API 授权",
        note="开源 + 口碑的获客模式很好复制，但国内平台（小红书、抖音、公众号）的"
             "发布接口基本不对外开放，拿授权要资质——这是最大的坑，也是护城河。",
    ),
    "shipfast": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=2, competition=2,
        note="「把脚手架卖给同行」在国内成立，但国内开发者习惯用若依这类免费模板，"
             "一次性 ¥1400 的定价很难维持。技术栈也得换成 uni-app / SpringBoot 主流款。",
    ),
    "trustmrr": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=4,
        blocker="核心机制无土壤",
        note="它的整个价值前提是「创始人愿意连 Stripe 只读 key 上榜」。"
             "国内既没有等价的支付网关生态，也没有大量做透明收入的独立开发者。"
             "国内确实没竞品，但那是因为没需求，不是没人想到。",
    ),
    "comp-ai": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=1, competition=2,
        note="检查项要整个换掉：国内客户要的是等保 2.0、密评，不是 SOC 2 / ISO 27001。"
             "产品逻辑（把六个月流程压到几周）可以平移，但知识库要重写，"
             "而且这门生意本身需要资质背书。",
    ),
    "aeo-engine": dict(
        demand=4, payment=4, compliance=5, acquisition=4, localization=4, competition=5,
        note="国内最接近真空的一条。把监测对象从 ChatGPT / Perplexity 换成"
             "豆包、DeepSeek、文心、夸克即可，产品骨架不用动。"
             "营销预算本来就存在——销售不用教育预算，只要教育方向。"
             "风险是这个品类寄生在第三方 AI 产品的黑盒上，规则随时会变。",
    ),
    "lancer-app": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=2, competition=3,
        note="它寄生在 Upwork / Fiverr 的招标数据上。国内没有对等平台"
             "（猪八戒的体量和数据结构差太远），等于要重找数据源 + 重教用户，"
             "而自由职业者在国内的付费能力本来就薄。",
    ),
    "speel-co": dict(
        demand=5, payment=4, compliance=4, acquisition=4, localization=3, competition=2,
        note="需求端最硬的一条：电商卖家的素材消耗是每天的、能算 ROI 的，国内盘子全球最大。"
             "代价是正面红海——剪映、即梦这类免费工具就在旁边。"
             "而且原产品 MoM 增长已经掉到 0%，说明光靠「能生成」没有壁垒，"
             "得往「投后数据反馈」那一步走。",
    ),
    "visualizee-ai": dict(
        demand=5, payment=4, compliance=5, acquisition=4, localization=3, competition=2,
        note="付费方很明确（装修公司、设计院、软装工作室），这类客户习惯为「出图效率」掏钱；"
             "小红书就是天然的获客池，获客话术也现成。"
             "压力来自酷家乐、三维家，但它们主打全屋定制，渲染这个细分仍有余地"
             "（D5 Render 就是国内团队做起来的）。",
    ),
    "checkvibe": dict(
        demand=2, payment=3, compliance=5, acquisition=3, localization=5, competition=4,
        blocker="需求端薄",
        note="本地化成本几乎为零——安全工具本身不看国界，代码一改就能跑。"
             "但卡点全在需求端：国内开发者对安全工具几乎不付费，"
             "企业采购又走传统安全厂商渠道。赛道是空的，钱包也是空的。",
    ),
    "storyshort-ai": dict(
        demand=3, payment=4, compliance=4, acquisition=4, localization=3, competition=1,
        note="国内这条赛道已经打成一锅粥（剪映、腾讯智影、一帧秒创……），大厂产品还免费。"
             "除非切一个极窄的场景（比如只做跨境电商的 TikTok 素材），否则没有生存空间。",
    ),
    "meerkats-ai": dict(
        demand=3, payment=4, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="自动化触达合规",
        note="它的核心动作是「找线索 → 自动外呼 → 个性化触达」，"
             "在国内直接撞《个人信息保护法》和骚扰电话治理。"
             "改成「只做存量客户的私域跟进」是能合法做，但那就不是这个产品了。",
    ),
    "coral": dict(
        demand=3, payment=3, compliance=5, acquisition=3, localization=4, competition=2,
        note="技术通用，改造成本不高。但国内已有 Dify、BISHENG、FastGPT 等开源方案，"
             "云厂商也在做托管，而且这类工具在国内很难直接收订阅费——用户会选自部署。",
    ),
    "startclaw": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=2,
        note="卖点是「省掉配 API key 这一步」，但国内用户可以直接用 Coze、百炼，"
             "连注册都省了。这个差异点在国内不成立。",
    ),
    "outrank": dict(
        demand=4, payment=4, compliance=4, acquisition=4, localization=3, competition=2,
        note="需求永续（获客是刚需），而且这类工具能自举获客——自己就是 SEO 工具。"
             "但整套逻辑围绕 Google 转，搬到国内要重做百度 / 搜狗 / 抖音搜索的规则，"
             "而且国内 SEO 服务已经很卷。",
    ),
    "marc-lou-portfolio": dict(
        demand=3, payment=2, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="支付基础设施",
        note="策略本身通用（多下注、快淘汰），但前提是「5 分钟接好 Stripe 就能开始收钱」。"
             "国内接支付要营业执照、要审核、要对接微信支付宝，试错成本高一个量级。"
             "「三年发 25 个产品」的打法在国内非常吃力。",
    ),
    "bustem": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=3, competition=3,
        note="国内售假治理的需求真实存在（品牌方每年花大钱），"
             "而且这个领域的时机门槛最低（原案例 timing = 1）。"
             "要懂的是淘宝 / 拼多多的投诉规则和平台关系——这是能攒的经验，不是拿不到的牌照。",
    ),
    "kibu": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        note="整套系统长在美国 Medicaid 的报销体系上，国内对应的残联 / 托养机构"
             "监管和结算逻辑完全不同，等于从头做一遍，而且这类客户要极深的行业关系。",
    ),
    "pieter-levels": dict(
        demand=3, payment=2, compliance=5, acquisition=3, localization=3, competition=2,
        blocker="支付基础设施",
        note="Photo AI 这类可以平移，但 Nomad List / Remote OK 建立在"
             "「全球数字游民」这个概念上，国内市场很小。同样受制于支付基础设施。",
    ),

    # ---- 2026-09-20 发布的一批 ----
    "1lookup": dict(
        demand=4, payment=4, compliance=2, acquisition=4, localization=2, competition=3,
        blocker="数据合规",
        note="需求真实：国内做反欺诈、去无效线索的公司一样要为号码校验付钱，"
             "而且计费方式（按调用量）与国内 SaaS 习惯不冲突。三个障碍："
             "一是手机号 / 邮箱的校验涉及个人信息，采集与存储要过《个人信息保护法》"
             "这条线，只能做「不落库的实时校验」；二是国内对应能力被腾讯云、阿里云"
             "的风控产品打包卖，单点工具的定价空间被压；三是免费替代（各家的"
             "正则 + 运营商接口）够用。适合做垂直场景（比如只做电商刷单识别）。",
    ),
    "stan": dict(
        demand=3, payment=3, compliance=2, acquisition=1, localization=1, competition=1,
        blocker="支付牌照 + 平台竞争",
        note="这是全库最难搬的一条。创作者收银台在国内要拿支付牌照或走二清，"
             "「代收代付 + 分账」是明确的监管红线，没有牌照就是非法经营。"
             "有牌照的那一档被有赞、小鹅通、知识星球占死，且抖音 / 小红书"
             "自己把带货闭环做完了。唯一可借鉴的是它的抽成模式和产品思路，"
             "生意本身在国内没有一个人做的口子。",
    ),
    "magicslides-app": dict(
        demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=1,
        note="需求端没问题：职场人做 PPT 是全球性痛点，国内也一样。"
             "合规上完全干净。问题在竞争格：国内已经有 WPS AI（直接内嵌在"
             "装机量最大的办公软件里）、AiPPT、Kimi 的 PPT 生成，全都是免费或"
             "低价。这个位置已经被占满了，一个人切入只能做极窄的垂直"
             "（比如只做某个行业的路演模板），而它的获客优势（App Store / SEO）"
             "在国内换成微信生态后又得重学一遍。",
    ),
    "autoreels-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="内容合规",
        note="批量短视频国内需求极大（切片带货、矩阵号），按产量计费的模式"
             "可以直接平移。两个坑：一是内容合规——AI 批量生产的内容受"
             "平台算法限制，且涉及「无人出镜」的资质与真实性标识要求；"
             "二是竞争惨烈，剪映 / 度加 / 万兴这些免费工具把基础功能做完了，"
             "收费空间只在高阶批量能力上。适合做「垂直行业 + 批量」"
             "（比如只做某个品类电商的素材流水线）。",
    ),
    "insect-bite-id": dict(
        demand=2, payment=4, compliance=3, acquisition=3, localization=3, competition=3,
        note="最微妙的一条。产品形态（拍照识别 + 订阅内购）在国内完全跑得通，"
             "合规上医疗建议要加免责声明但不构成红线。真正的问题是需求端："
             "国内用户对「花订阅费问一个小问题」的付费意愿远低于欧美，"
             "而且微信 / 支付宝里已经有大量免费的小程序工具和AI 问诊入口"
             "（腾讯医典、支付宝医疗健康）。它验证的是「长尾单点 + App Store"
             "自然流量」这条路，这条路在国内换成微信小程序后付费率会掉一个量级。",
    ),

    # ---- 2026-09-25 补的一批（TrustMRR 挂牌标的为主）----
    "voklit": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="增值电信牌照",
        note="「收不到验证码」这个痛点在国内同样存在（跨境卖家、出海团队），"
             "但虚拟号码与 VoIP 属增值电信业务，个人与小微公司拿不到牌照，"
             "而这条赛道上站着阿里云、腾讯云的号码认证与隐私号服务。"
             "它真正值得学的不是这门生意，是它选需求的方式："
             "找一个「不做就完全进行不下去」的前置条件来卖。",
    ),
    "prosp": dict(
        demand=3, payment=4, compliance=1, acquisition=1, localization=2, competition=1,
        blocker="自动化触达合规 + LinkedIn 已退出中国",
        note="与 gojiberryai、meerkats-ai 撞的是同一堵墙，而且更厚："
             "LinkedIn 职场版 2023 年就已退出中国，国内根本没有等价的"
             "「人 + 职位 + 公司」公开数据源；把信号源换成脉脉 / 企查查，"
             "《个人信息保护法》又把自动抓取与群发直接定性为红线。"
             "等于数据源、触达渠道、合规三处同时失效，只能重做一遍产品。"
             "能搬走的只有它的卖法：按「会议数」而不是功能点收费。",
    ),
    "divine-widgets": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="宗教内容合规",
        note="产品形态（小组件 + 每日一句）在国内技术上完全可做，"
             "但宗教内容在应用商店与内容审核里是明确的敏感区，"
             "而国内用户对「每日一句」类情绪产品的付费习惯也弱得多。"
             "能学的是它的结构：用 widget 把低频 App 变成高频曝光，"
             "再垂直到一个人群格子躲开竞争 —— 换一个非宗教的垂直主题即可复用。",
    ),
    "promptmonitor-io": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="这批里最可移植的一条。国内品牌同样开始问「豆包 / DeepSeek / 元宝 "
             "里提不提我」，需求正在起来；收款走微信/支付宝无障碍，"
             "合规上不涉及抓取个人信息，风险低。要改的是把监测对象换成"
             "国内主流模型，而这恰恰是壁垒 —— 谁先把国内模型的回答采样做扎实，"
             "谁就占住位置。竞争已经出现，但还没到免费的阶段。",
    ),
    "uplinked-b-v": dict(
        demand=3, payment=4, compliance=3, acquisition=1, localization=1, competition=1,
        blocker="LinkedIn 在国内无等价平台",
        note="它整套动作长在 LinkedIn 上：读账号、写帖子、排期、发布。"
             "国内没有等价的开放职业内容平台，公众号与小红书的发布接口"
             "基本不对外开放，朋友圈更是完全封闭 —— 产品等于要重做。"
             "而「帮顾问做内容代写」这个需求国内是真的，"
             "只是落地形态会变成代运营而不是 SaaS。",
    ),
    "le19emetrou": dict(
        demand=3, payment=5, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="价格战与仿品",
        note="最微妙的一条：收款、投放、物流在国内都是强项，"
             "抖音/小红书也有等价的投放渠道，单品 DTC 的打法可以直接平移。"
             "真正的问题是值不值——义乌同款开瓶器几块钱，"
             "创意被抄的速度快过建站的速度，而国内礼品电商的价格战"
             "会把 €34,99 的定价直接打穿。它的模型能跑，"
             "但盈利空间不在国内。想做只能走「海外市场的中国供应链」这条路。",
    ),
    "search1api": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        note="国内 agent 生态同样缺一层「联网」，需求真实且正在增长；"
             "收款无障碍，获客靠开发者社区这条路在国内也成立。"
             "两个要改的地方：搜索源要换成国内可用的（或合规抓取），"
             "而「抓取什么、能不能抓取」的合规边界比美国更模糊 —— 这是唯一需要谨慎的维度。"
             "竞争已经存在（各家大模型的搜索 API、博查一类），"
             "但 MCP 这一层还没人占死。",
    ),

    # ---- 2026-09-30 补的两条 ----
    "mort": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="招聘数据抓取合规",
        note="国内求职焦虑比英国更大，付费意愿也更高——这是它最值得学的地方："
             "「海投无效」在中国同样是真痛点，而国内还多一层结构性问题"
             "（春招秋招的节奏更集中、学历与院校筛选更前置）。"
             "支付与获客都没障碍，SEO 也能平移。"
             "两个硬伤：一是它的数据源在英国，迁移过来要重写整条采集链——"
             "国内招聘有 BOSS 直聘、猎聘、智联，但都反爬且商业授权要谈，"
             "career page 那段脏数据在国内被前程无忧、实习僧瓜分，"
             "拿不到就得换信源。二是自动投递在中文语境下的合规边界更模糊"
             "（简历含身份信息、自动化提交是否构成对平台的违约），"
             "这块欧美判例多、国内判例少，得谨慎。"
             "竞争上也挤：BOSS 直聘自己就在做 AI 匹配。",
    ),
    "quran-unlock": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=4,
        blocker="App Store 分发在国内不成立",
        note="它 90% 的流量来自 App Store 与 Google Play 的自然搜索，"
             "这个渠道在国内**结构性地不存在**——没有第三方安卓应用商店的"
             "自然量红利，微信小程序是唯一的等价物，但小程序没有「锁住其他 app」"
             "这个能力，整个机制的地基就没了。"
             "需求侧也有真实对应物（国内有大量信仰类工具与清真寺/伊斯兰教会的社群），"
             "但它证明的是「习惯机制 + ASO 分发」这条组合拳，"
             "在国内要换成分发渠道的结构性改造，难度远高于换文案。"
             "更实际的是竞争空位这一维：这类信仰类应用在国内几乎没有头部产品，"
             "但也意味着没有验证过的付费模型。",
    ),

    # ---- 2026-10-05 补的两条（premium 晋升时漏了，卡住 fit_ready 闸门）----
    "harperai": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=3, competition=3,
        blocker="公开抵押数据的商业化授权边界未明",
        note="它吃的是英国 Companies House 与 Land Registry 的**公开**记录——"
             "这一层国内对应物是齐的（企查查 / 天眼查 / 国家企业信用信息公示系统），"
             "且「企业有新增抵押 → 该找资金了」这个需求在 国内更旺，"
             "因为中小企业的融资信息不对称比英国更严重。"
             "真正的硬伤在合规：把公开征信数据加工成「谁在再融资窗口」的"
             "金融决策工具，在《个人信息保护法》与征信业务管理办法的框架下"
             "需要资质与人授权边界，说清「公开数据能不能商用加工」本身就是一道门槛。"
             "它卖的也不是工具而是「别错过」，要国内客户信你比英国客户信你难得多——"
             "英国客户本来就在按揭链条里，国内得先教育市场。",
    ),
    "easymix": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="国内独立音乐人数量全球第一，混音与母带是真实的付费刚需，"
             "而现有供给参差——大量录音棚仍靠人工接单。"
             "支付与合规这一段反而比海外顺：微信支付、支付宝直接可收，"
             "音频内容不触任何监管红线，没有任何一维构成硬伤。"
             "本地化量小：说唱在 国内同样主流，drill 那些风格词不用翻译。"
             "竞争这一维要扣分：腾讯音乐生态与大量国内 AI 混音工具在挤，"
             "且版权授权链在国内更复杂（词曲版权与录音版权分属不同权利人，"
             "商用授权要一层层签），这是海外没有的成本。",
    ),

    # ===== 2026-10-11 补：Stripe 深核通过的 7 条 =====
    # 六维口径与老案例一致：demand/payment/compliance/acquisition/
    # localization/competition，1 = 最差，5 = 最好；competition 分数越高
    # 意思是「竞争越友好/越空」，不是「竞争越激烈」。

    "cometly": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="企业销售与数据合规",
        note="需求真实且付费意愿强——国内 SaaS 公司在信息流与私域上花的钱不比美国少，"
             "「投放有没有回报」是真痛点。支付一维是顺的：企业级订阅、国内发票与"
             "对公结算都比美国创业公司走得更顺。"
             "难在两处：①归因数据要跨平台打通，个保法与《数据出境》对"
             "跨境广告数据的约束很紧；②它的打法是大客户销售 + 实施，"
             "国内这一格站着神策、 GrowingIO、易观等，"
             "而且客户要私有化部署、要对接自己的数仓 —— "
             "等于把产品重做一遍。学习价值在「切入角度」，不在照搬。",
    ),
    "vid-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        note="短视频批量生产的需求在国内更旺——抖音/视频号/小红书三头都吃，"
             "内容更新频率比海外高。支付顺、模型调用国内也有几家可选。"
             "合规不是问题（生成内容不涉硬红线）。"
             "竞争这一维要扣分：剪映、即梦、腾讯的AI 剪辑已是免费且在持续加强，"
             "把它当「现有工具已经免费内置」来判。"
             "⚠️ 而它自己峰值留存已腰到 46%，说明这个位置在海外都正在被平台收编，"
             "国内窗口只会更短。",
    ),
    "podawaa": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1,
        blocker="平台不可迁移",
        note="需求部分成立：国内做B2B 获客的人确实需要内容工具。"
             "但它的价值全部绑定在 LinkedIn 这个平台上 —— "
             "而国内没有等价物：领英职场版 2023 年已关停，"
             "现在占着这个位置的是脉脉 + 企业微信 + 公众号 + 视频号。"
             "产品等于要重做一遍（换平台、换内容形态、换获客逻辑），"
             "localization 直接判 1。且那一格已有大量国内内容工具占着。",
    ),
    "publbee": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        note="需求成立：亚马逊KDP 在中国的作者群体真实存在且在增长，"
             "他们也为工具付费，支付与合规都没有硬伤。"
             "获客靠 SEO 打「kindle 自出版怎么做」这类长尾词，"
             "这条路在国内同样跑得通。"
             "扣分在竞争：国内「AI 写书」「选题工具」已有不少，"
             "且国内作者的主要变现渠道是番茄、七猫等平台，"
             "那套生态里KDP 的关键词逻辑并不完全适用。",
    ),
    "vectosolve": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="需求在国内存在——激光切割、刺绣、小批量定制都需要位图转矢量，"
             "淘宝/1688 上的小商家就是这批客户。支付与合规都顺。"
             "获客靠搜索长尾（跟海外一个道理），"
             "但国内拿量的成本结构不同：这里靠付费搜索与电商平台内流量，"
             "单价 $7 的产品很难跑正。"
             "localization 扣分：DXF/DST 那套厂商格式在国内一样有人用，"
             "但雕花、激光切割的软件生态与工艺习惯不同，要重做素材库。",
    ),
    "augora-ai": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="隐私与跨平台数据",
        note="需求成立且比海外更痛：国内跨境电商与DTC 商家广告投放规模大，"
             "但 iOS 隐私政策对归因的破坏在国内的伤害一样重。"
             "支付顺。难在合规：它要接Meta CAPI、要把广告点击与转化对应起来，"
             "在国内这套链路还额外压着个保法与跨境数据传输要求。"
             "且它的主战场是 Shopify 商家 —— 国内这批人更常用店小秘/马帮/抖音小店，"
             "渠道接不上，等于换一套客户。",
    ),
    "wpconvert-ai-convert-ai-sites-to-wordpress": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=3,
        blocker="上游不给导出",
        note="需求成立但形态要换：国内也有一大批用 Lovable/v0 类工具搭的站，"
             "也搬不动。支付、合规都没问题 —— 这是纯工程服务，"
             "不涉任何数据红线，这是它四维里最漂亮的一档。"
             "要改的是渠道与获客：WordPress 在国内的使用率远低于美国，"
             "不少站建在国产建站平台上，localization 判 2。"
             "竞争这一维相对友好（1-5里给 3）：帮人从低代码平台搬走这件事国内还没"
             "形成成熟供给。但它的命门与海外一样 —— 上游 AI 建站工具一旦把导出做好，"
             "需求就被吃掉了。",
    ),

    # ===== 2026-10-15 补：门槛放开后批量晋升的 41 条 =====
    # 同口径：demand/payment/compliance/acquisition/localization/competition，
    # 1 = 最差，5 = 最好。competition 分数越高 = 这一格越空（不是竞争越激烈）。
    # 两条（corsidia / hypit-ai）官方只披露收入、不披露产品，评分是占位，
    # note 里已声明不构成移植判断。

    "alchie": dict(
        demand=2, payment=4, compliance=5, acquisition=2, localization=2, competition=1,
        blocker="LinkedIn 在国内无等价平台",
        note="它整个产品长在 LinkedIn 上，而领英职场版 2023 年就已退出中国——"
             "「帮管理者经营职业形象」这套话术在国内没有承载平台。"
             "换成公众号/脉脉之后，需求本身也掉了档：国内个人用户为"
             "「发帖子」付 $57/月 的意愿接近于零，而这个位置还站着"
             "一堆免费 AI 写作工具。收款和合规都不是问题，问题是没有人买。",
    ),
    "adelante-ltd": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1,
        blocker="平台自带客服 + 国内 SaaS 客单价",
        note="电商客服需求国内更大，但供给方是平台自己：淘宝店小蜜、京东言犀、"
             "抖音飞鸽都是免费内置，商家没有再买一套的动力。"
             "它 $900/月起 的托管式定价在国内更难——国内 SaaS 客单价普遍比美国低一个量级，"
             "而「我们替你跑」的人力部分在国内要按人头算，压不到 80% 毛利。"
             "能搬的只有卖法：把「托管」作为档位，而不是把「软件」作为产品。",
    ),
    "storyshort": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="国内平台无开放发布接口",
        note="「不用出镜、每天有片发」这个需求在国内比在海外更硬——"
             "想做号的人远多于愿意露脸的人。但它的另一半卖点是"
             "「自动按节奏发布到 TikTok / YouTube」，国内抖音、视频号、"
             "小红书的发布接口基本不对外开放，这一半要整个拿掉或改成半自动。"
             "竞争一维扣到底：剪映、即梦、腾讯智影全免费且更贴近本地审美。",
    ),
    "analytics-mates": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=1, competition=1,
        blocker="分析栈整体不同",
        note="入口产品是「免费 GA4 审计」，而国内网站用 GA4 的是少数——"
             "百度统计、友盟、神策、GrowingIO 各占一块，审计对象要整个换掉。"
             "更要命的是它的收入主体是一次性与项目款（MRR $4,648 vs 近 30 天 $23,948），"
             "本质是一门咨询生意：在国内要靠人脉和案例背书拿单，"
             "不是靠一个免费审计工具引流就能跑起来的。"
             "「读懂数据」的需求真，但这一格早就站着神策和 GrowingIO。",
    ),
    "brevilabs": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=2, competition=1,
        note="企业「想做 AI 但不知道怎么做」的需求国内更旺，这一维给 4。"
             "但它本质是按项目收费的工作室（四周 $45,000 起），"
             "收入里有大量人力，MRR $18,480 与 30 天收入 $25,032 的差额就是项目款。"
             "国内这一格挤满了咨询公司和外包团队，且甲方倾向找大厂；"
             "95% 毛利是因为人力没挂在账上，那部分在国内一样躲不掉。"
             "真正值得学的是包装方式：把开放式咨询切成「四周、固定价、有交付物」的冲刺。",
    ),
    "cartboss": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="短信营销牌照 + 平台自带召回",
        note="弃单召回的需求在国内盘子更大——电商交易量全球第一，"
             "而「按条付费、有效果才花钱」的计费方式和国内商家习惯也不冲突。"
             "两个硬伤：一是商业短信要走有资质的通道且需用户明示同意，"
             "《通信短信息服务管理规定》把未经同意的营销短信直接定性为违规，"
             "个人拿不到 SP 牌照；二是国内召回主要发生在平台内"
             "（淘宝旺旺、抖音私信、企微 SCRM），平台自己就把这一步做完了。"
             "45% 毛利（短信通道是硬成本）也意味着国内价格战下没有腾挪空间。",
    ),
    "based-labs-ai": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        note="AI 视频生成的需求国内极旺，但这是全库竞争最惨的位置之一："
             "即梦、可灵、海螺、剪映背后的大厂都在免费加码，"
             "而且它们的出片质量与本土审美贴合度远高于海外工具。"
             "合规一维要扣分：AI 生成内容在国内有强制标识要求，"
             "平台侧对未标识内容的处置是明确的。"
             "它 $33/月 × 873 订阅 的结构说明卖的是专业创作者，"
             "国内这一小撮人更可能被大厂工具直接圈走。",
    ),
    "draftly": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=3, competition=2,
        note="合规与收款这两维非常干净——纯生成工具，不碰任何数据红线，"
             "微信/支付宝直接可收。本地化量也小（换文案、换字体即可）。"
             "需求一维只能给 3：「cinematic scroll 3D 网站」在国内的审美与带宽"
             "环境下是偏小众的形态，主流企业建站要的是模板快和适配小程序。"
             "真有对应的是出海独立站那一小撮人（Shopify 卖家），"
             "他们要的恰恰就是这个；但那一格已经有大量建站工具在抢。",
    ),
    "ae-intelligence": dict(
        demand=3, payment=4, compliance=2, acquisition=4, localization=4, competition=2,
        blocker="学术诚信灰区",
        note="它的需求来自「学校用 AI 检测器查 AI 写作」，而这个驱动因素在国内同样成立、"
             "甚至更强（多家高校已出 AI 代写认定办法），搜索长尾词现成，"
             "获客这一维给 4；改造量也小，换掉检测对象即可。"
             "但合规必须扣分：产品的作用是让 AI 写的文字通过检测，"
             "在国内属于明确的学术不端辅助工具，平台与监管态度只会更严，"
             "而且这门生意是军备竞赛——检测器一更新，产品价值就归零。"
             "淘宝上几块钱的同类服务已经把价格打穿。",
    ),
    "abmaxx": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=2,
        note="本地化成本几乎为零：拍照 → 出计划，语言无关，中文化就能用。"
             "卡点在两头：一是付费意愿——国内健身用户对「腹部训练计划」的订阅付费极弱，"
             "$3.7/月 走量的模型要放大 10 倍用户才成立；"
             "二是分发——它靠 App Store 自然量，国内安卓没有 Google Play，"
             "只能改做微信小程序，而小程序里已有 Keep、乐刻等免费供给。"
             "合规一维要谨慎：体型照片属可识别的个人信息，存储和跨境都受限。",
    ),
    "backpedal-ltd": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=1, competition=3,
        blocker="线下履约 + 安保服务边界",
        note="需求端是全库最硬的一条：中国电动自行车保有量全球第一，"
             "车辆被盗后「只能认赔、没人帮你找」是真痛点——"
             "而传统保险只赔钱不找车，这个替换思路在国内同样成立。"
             "但它的交付物是「一支 24/7 的人替你把车取回来」，"
             "在国内既没有执法配合的通道，人力调度成本也完全不同，"
             "等于把整套线下履约重做一遍；持续定位他人位置还踩在个人信息"
             "与安保服务资质的边界上。竞争给 3 是因为这一格确实空着，"
             "但空着的原因恰恰是它难做。",
    ),
    "adspirer": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        note="中小广告主不会投、投放 ROI 算不清，这个需求国内比美国更普遍，"
             "而「AI 替你调预算改素材」的边际成本是模型调用不是人力——"
             "这是它一人能做到 MRR $5.5 万的根本原因，也是唯一值得搬的部分。"
             "改造点很明确：把广告账户从 Meta/Google 换成巨量引擎、腾讯广告、百度推广，"
             "而这几家的 API 授权门槛与代理资质要求比海外更细。"
             "竞争一维要扣分：国内代投放服务商极多且靠人力关系拿单，"
             "$485/年 的产品化定价能不能打过「一个优化师包月」是未知数。",
    ),
    "corsidia": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=3,
        note="⚠️ 占位评分，不构成移植判断：官方只披露了可验证收入"
             "（累计 $315 万、近 30 天 $8.2 万），官网简介故意写成"
             "「You'll have to figure it out.」，产品形态、定价、客户群一概未公开。"
             "在不知道它到底卖什么的前提下，六维只能给中间值；"
             "任何「能不能搬回国内」的结论都缺依据。"
             "它进库的理由是收入证据够硬，不是产品可复制。",
    ),
    "avenue-ticketing-inc": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=1, competition=1,
        blocker="演出票务资质 + 大麦猫眼垄断",
        note="需求端无疑巨大（演出、电影、餐饮预订），但这是全库最难搬的一条生意："
             "营业性演出票务在国内要持《营业性演出许可证》，票源与退票规则受强监管，"
             "预售票款还涉及资金监管；而供给端被大麦、猫眼、美团三分，"
             "「对主办方零手续费」这个武器打的是票源垄断，不是价格。"
             "它近 30 天 $50 万是平台流水口径（含代收票款），"
             "不是净收入——这一条本身就提醒：看到大额流水先看口径。",
    ),
    "autocontent-api": dict(
        demand=3, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="生成式 AI 备案 + 大厂 API 价格战",
        note="把内容生成做成 API 卖给开发者，这一层在国内的价值被两头挤压："
             "上游是大厂模型 API 直接卖（DeepSeek、豆包、通义，价格战到白菜价），"
             "下游是 Coze、Dify 这类把编排也做了的平台。"
             "合规是第二道坎：面向公众提供生成式 AI 服务在国内要过备案，"
             "且输出内容的安全责任在提供方——这是海外没有的固定成本。"
             "能成立的只有垂直化：不做通用内容 API，做某个行业固定格式的内容 API。",
    ),
    "chessigma": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=3, competition=2,
        note="合规与改造都没障碍（棋类平台不看国界），"
             "但需求一维要扣到底：国际象棋在国内是小众中的小众，"
             "棋类的位置被围棋和中国象棋占着，而这两块早已有成熟的免费对弈平台。"
             "它 100 万用户里只有 576 个付费（0.06%）——"
             "免费对战平台的转化率本身就极低，放到付费习惯更弱的国内只会更低。",
    ),
    "backlinker-ai": dict(
        demand=2, payment=4, compliance=1, acquisition=1, localization=1, competition=2,
        blocker="Google 生态不存在 + 自动外发合规",
        note="与 prosp、meerkats-ai、gojiberryai 撞同一堵墙，而且更厚。"
             "外链建设的价值全部建立在 Google 搜索权重上，"
             "而 Google 在国内不可用，需求本身就不成立；"
             "它的动作是自动抓取记者邮箱并批量投稿，"
             "在国内直接撞《个人信息保护法》与反垃圾邮件治理。"
             "三个维度同时失效（需求、渠道、合规），localization 判 1。"
             "唯一能搬的是定价锚：$300/月 锚的是「外包给外链机构几千刀」。",
    ),
    "ai-toolbox": dict(
        demand=2, payment=4, compliance=4, acquisition=3, localization=4, competition=1,
        note="品类本身搬不动：AI 工具导航站在国内已经是一片红海"
             "（AI 工具集、WayToAGI 等都是免费且做得更全），"
             "而它靠终身买断 $99/$199 收费，这个模式在国内更难——"
             "用户默认导航站就该免费。"
             "但它的成本结构值得单独记一笔：月成本不到 $100、毛利 93%、"
             "连续 23 个月盈利，近 30 天收入 $21,520 而 MRR 只有 $1,334。"
             "这是「一次性买断撑起来的生意」而不是订阅生意，"
             "这个结构本身是能复制的，复制的应该是结构不是品类。",
    ),
    "amicited": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=3,
        note="与 aeo-engine、promptmonitor-io 是同一条赛道："
             "「在 AI 的回答里有没有提到我」。国内品牌正在开始问同样的问题，"
             "而对象要从 ChatGPT 换成豆包、DeepSeek、元宝、夸克——"
             "这恰恰是壁垒，谁先把国内模型的回答采样做扎实谁占位。"
             "收款顺、不涉个人信息、无监管红线，合规给 4。"
             "需求给 3 是因为国内这波认知比欧美晚一年左右，"
             "销售要先教育市场；竞争给 3 是因为已有玩家但还没到免费阶段。",
    ),
    "angel-match": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1,
        note="融资找投资人的需求国内一样真实，但做法完全不同："
             "国内创业者靠 FA 和熟人介绍，不靠数据库冷邮件，"
             "而「谁在投、投什么阶段」这层数据国内由 IT 桔子、企查查、鲸准占着，"
             "数据源要整个重建（localization 判 1）。"
             "合规一维的疑虑和 harperai 同源：把公开信息加工成可售的"
             "商业数据库，授权边界在国内没有明确判例。"
             "它真正证明的是续费逻辑——靠「融完 A 轮还有 B 轮」把一次性需求变成连续订阅。",
    ),
    "calendesk": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="敏感个人信息 + 微信生态适配",
        note="心理咨询行业国内在增长，预约 + 收款 + 客户档案的 SaaS 需求是真的。"
             "两个坎：一是心理健康数据属《个人信息保护法》下的敏感个人信息，"
             "要单独同意、要严格约定存储与访问，做错了不是罚款级别的风险；"
             "二是国内预约几乎全发生在微信里，产品必须长出小程序形态"
             "并接微信支付，等于把前端重做一遍。"
             "价格也是问题：$79–117/月 对国内独立执业的咨询师偏高，"
             "而年付锁留存这一招在国内要重新建立信任才做得到。",
    ),
    "capgo": dict(
        demand=3, payment=4, compliance=2, acquisition=3, localization=2, competition=3,
        blocker="应用商店明令禁止绕过审核的热更新",
        note="需求真实（改 bug 不想重新发版），开发者社区获客这条路国内也成立，"
             "收款无障碍。但热更新在国内是明确的高压线：苹果曾大规模封杀"
             "JSPatch 一类方案，国内安卓应用商店（华为、小米、OPPO、vivo）"
             "在开发者协议里明文禁止用热更新绕过审核改功能，"
             "违规的代价是下架——这比海外严得多。"
             "另外它长在 Capacitor / Ionic 生态上，国内跨端主流是"
             "uni-app 和 Flutter，框架要换。竞争给 3：国内这块供给不多，"
             "但主要是因为它难做而不是没人想到。",
    ),
    "applyall": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        note="求职焦虑国内比美国更重，付费意愿也真实——这是它最值得学的："
             "卖的是「帮你拿到工作」这个结果，一次性 $249/$299，"
             "而不是卖工具功能。国内对应物是简历优化与求职辅导服务，"
             "但「代投」这个形态少见，因为平台（BOSS 直聘、猎聘、智联）"
             "反爬且明令禁止自动化操作账号，合规边界模糊；"
             "共享账号代投本身也可能违反平台协议。"
             "它与 mort 同赛道但更重人力，一个人很难规模化——"
             "这一点在国内人工便宜反而是优势，但毛利会掉下来。",
    ),
    "audn-ai": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="网络安全服务资质",
        note="「把一年一次的渗透测试变成 $999/月不限次订阅」是极漂亮的降维定价，"
             "思路完全值得学。但业务本身在国内几乎不可做："
             "《网络安全法》下未经授权对他人网络进行扫描即属违法，"
             "商用安全测试服务要相应资质与等保相关能力，个人与小微公司拿不到。"
             "而即便拿到，国内这一格站着奇安信、深信服、长亭、知道创宇，"
             "且它们的 SaaS 化产品已经在卖。需求给 3 是因为国内安全投入"
             "主要是合规驱动而非自驱，客户买的是「过等保」不是「找漏洞」。",
    ),
    "hypit-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=3,
        note="⚠️ 占位评分，不构成移植判断：公开档案里没有产品简介、没有定价、"
             "没有创始人，只有可验证收入（MRR $18,825、954 个订阅、上线不足两个月）。"
             "在不知道它卖什么之前，六维只能给中间值。"
             "它进库的理由是支付网关直读的收入证据，"
             "且月度时间线不足两个月——这条连增长结论都不给，更谈不上移植判断。",
    ),
    "apiframe": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="生成式 AI 备案 + 大厂 API 价格战",
        note="与 autocontent-api 是同一道墙。国内 agent 与应用开发需求极旺，"
             "「一套 API 调多家模型」的便利性是真需求，"
             "按 credits 计费也和国内开发者习惯不冲突。"
             "但上游是 DeepSeek、豆包、通义、千帆这类大厂在直接卖 API 且价格战激烈，"
             "中间层的价差空间被压到极薄；"
             "而面向公众提供生成式 AI 服务要过备案、输出内容要过安全过滤，"
             "这两项是国内独有的固定成本。竞争判 1：聚合网关国内已有不少，"
             "且大厂自己在做。",
    ),
    "chatseo": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2,
        note="它的卖点有两半：「排到 Google 第一」和「排到 ChatGPT 第一」。"
             "搬到国内，前半直接归零——Google 在国内不可用，"
             "SEO 规则要整个换成百度、搜狗、抖音搜索，"
             "而国内 SEO 服务已经很卷；"
             "后半（AI 露出）成立，但那半与 aeo-engine、amicited 撞在同一格，"
             "国内要先把豆包、DeepSeek、元宝的采样做扎实。"
             "产品等于要砍掉一半重做，这是它和纯 AI 可见性工具的差距。",
    ),
    "brawler": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=4, competition=2,
        note="本地化成本几乎为零（摄像头动作识别不看语言，中文化即可），"
             "合规也干净。但需求一维要扣到底：拳击在国内是小众运动，"
             "而「AI 私教」这种非社交非内容的健身形态付费极弱——"
             "国内健身用户的钱主要花在器械、场馆和社交型 App 上。"
             "分发同样是问题：它靠 App Store 自然量，国内安卓没有 Google Play。"
             "技术（实时动作识别 + 计数纠错）是可复用的部分，"
             "换成跳绳、羽毛球、太极这类国内有群众基础的项目反而更成立。",
    ),
    "cod-dex": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=3, competition=2,
        note="学编程的人国内全球最多，而合规一维意外地干净："
             "编程属非学科类培训，不需要学科办学许可。"
             "它的产品骨架（课程游戏化成「冒险旅程」+ 勋章）是差异点，"
             "国内现有供给（慕课网、力扣、freeCodeCamp 中文）大多是"
             "视频 + 题库的严肃形态，游戏化这一格还没被占死。"
             "要改的是课程翻译与社区重建（localization 3）；"
             "付费一维的风险在于 $9.9/月 对国内自学者不算贵但还是"
             "要跟「B站上一堆免费教程」竞争——它 6,686 个订阅说明"
             "低价走量可行，但国内要走更大的量才有同等收入。",
    ),
    "boothie-ai-photo-editor": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=3, competition=1,
        note="需求端毫无问题（修图是国内最强的 C 端赛道之一），"
             "但竞争一维必须判 1：美图秀秀、醒图、轻颜相机全免费、"
             "本土审美贴合度更高、且大厂在持续加码，"
             "「一键换胶片质感」这种功能在它们是模板级别的存在。"
             "付费一维也弱：国内 C 端对修图 App 的订阅习惯很薄，"
             "习惯的是免费 + 单点内购。本地化不只是翻译——"
             "国内要的是「美颜」「高级感」，不是「柯达金胶卷」，"
             "风格包要整个重做。",
    ),
    "nutria-9e3eaa": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        note="卡路里追踪在国内有薄荷健康、Keep 这样的免费强供给，"
             "而本地化是硬改造：食物识别库要整个换成中餐，"
             "中餐的烹饪方式、分量、油盐结构让「拍一张算热量」的"
             "准确度比西餐难一个量级——这不是翻译工作量，是重做数据。"
             "合规一维要谨慎：饮食与体重数据属健康信息，存储与共享受限。"
             "另外它自己近 30 天已下滑 25%，说明这个品类在海外都在退潮，"
             "国内窗口只会更短。",
    ),
    "fastwrite-io": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="学术诚信灰区",
        note="学生群体大、搜索需求现成（获客 3、改造 3），"
             "但它的定位是「帮助学生产出更高品质的学术作品」，"
             "与 ae-intelligence 站在同一条灰线上——国内高校已明确"
             "把 AI 代写纳入学术不端认定，做这门生意的平台风险很高。"
             "付费一维也不乐观：学生是付费能力最弱的群体，"
             "$21.8/月 在国内要跟大量免费 AI 写作工具竞争。"
             "能做的是往「合规侧」靠：查重、引用格式、文献梳理这些"
             "不替学生写、只帮学生写的部分。",
    ),
    "downconvert-sketchup-files": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=4, competition=4,
        note="这批里最干净的一条：极窄但真实的小痛点（同事的 SketchUp 版本"
             "打不开你的文件），纯工程工具，不碰任何数据红线，"
             "本地化几乎为零（界面翻译即可）。"
             "竞争给 4 是因为国内几乎没人做这个——"
             "小到没人做，但真有人付费（$87/月 仍有人买，说明是刚需）。"
             "需求只能给 3：SketchUp 在国内室内设计圈有用户，"
             "但主流是 AutoCAD、天正、3ds Max、酷家乐。"
             "真正的迁移思路不是照搬 SketchUp，"
             "而是把「版本兼容」这个痛点搬到国内主流设计软件上重做一遍。",
    ),
    "macwall": dict(
        demand=1, payment=3, compliance=5, acquisition=2, localization=5, competition=1,
        blocker="C 端壁纸付费不存在",
        note="本地化成本为零、合规完全干净，但需求一维判 1："
             "国内几乎没有人会为壁纸付订阅费，免费壁纸站与"
             "系统自带壁纸已经覆盖了绝大部分需求，"
             "而 Mac 在国内的用户基数本来就小。"
             "这条进库的意义在于成本结构参照，不在于可复制性。",
    ),
    "growingupvideo": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=3, competition=1,
        blocker="换脸/深度伪造内容标识监管",
        note="「把老照片变成一段成长视频」的情感需求国内同样真实，"
             "但国内对人脸生成与换脸的监管是全球最严的一档："
             "深度合成内容要显著标识、要取得被编辑人同意，"
             "未标识的传播会被平台处置——这正是它的核心功能。"
             "竞争也是问题：剪映、即梦里的「照片动起来」是免费模板。"
             "能搬的只有它的选题（成长/宠物/情侣的时间线叙事），"
             "落地形态得往「不碰人脸」的方向改。",
    ),
    "homy": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3,
        note="技术通用、改造量小、合规干净，"
             "但需求一维很薄：Mac 在国内是少数派，"
             "而「文件搜索更快」这件事国内用户习惯用 Everything（Windows）"
             "或直接靠网盘/微信的搜索解决，付费意愿接近于零。"
             "竞争给 3 是「没人做也没市场」的那种空，不是机会。"
             "这条的价值在于它验证了一个极窄的工具也能收到钱（近 30 天 $1,345），"
             "但那个钱是在海外 Mac 用户池里收到的。",
    ),
    "stealth-rdp": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="IDC/ISP 增值电信牌照",
        note="需求端毫无疑问（VPS 是国内最成熟的云市场之一），"
             "但这是个人完全进不去的生意：IDC/ISP 属增值电信业务，"
             "要牌照、要实名、要承担内容监管责任，"
             "而市场被阿里云、腾讯云、华为云三分，"
             "毛利只有 40% 且还在被价格战往下压。"
             "它与 divine-widgets、voklit 那类「牌照墙」条目不同的地方在于："
             "这里连「学它的模式换个形态做」的空间都没有——"
             "产品就是牌照本身。",
    ),
    "white-glove-content": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=2,
        note="「客户把稳定出内容这件事外包出去」的需求国内极大"
             "（本地商家、品牌方、MCN 都在买），"
             "但它 $117/月 的产品化定价在国内要跟「一个编导月薪几千块"
             "给你做全套」直接比价——国内人力便宜这件事"
             "反而让「用 AI 把代运营成本压到产品价」更难成立，"
             "因为参照系已经很便宜了。"
             "获客也是问题：国内代运营靠 BD 和关系，不是自助注册，"
             "而这一格挤满了代运营公司。"
             "真正值得学的是它把「无脸视频 + 策划 + 剪辑」打包成"
             "固定月费的切法，而不是照搬这个价格。",
    ),
    "pet-plus-us": dict(
        demand=4, payment=5, compliance=4, acquisition=4, localization=3, competition=2,
        note="这批里移植条件最好的一条：宠物经济是国内最热的消费赛道之一，"
             "宠物主为「毛孩子的肖像」付费意愿极高；"
             "收款一维给满分——微信/支付宝的小额支付顺畅度全球第一，"
             "几十块的冲动消费不需要任何决策成本；"
             "小红书就是天然获客池，而宠物内容在小红书上自带流量。"
             "合规干净（AI 生成宠物肖像不涉人脸与敏感内容）。"
             "要改的是风格包（国内审美偏日系插画与精修，不是油画肖像）；"
             "竞争一维扣分是因为淘宝与小红书上已经有大量人工宠物画像师，"
             "他们的单价不高但质量可控——AI 化的优势在速度和低价，不在画质。",
    ),
    "wagiff-app": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="宗教/涉性内容审核",
        note="产品整个长在伊斯兰文化背景上（中东北非男性戒除不良习惯的社群），"
             "国内既没有这个文化语境，也没有这个宗教社群结构——"
             "localization 判 1。更硬的是合规：它涉性内容 + UGC 社群，"
             "这在国内是内容审核的最高风险区，"
             "同类社区（戒色吧等）存在但完全不付费、且长期处在整改压力下。"
             "竞争给 3 只说明这一格没人做商业产品，不代表有空间。",
    ),
    "quotedeck": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="保险销售资质 + 产品体系不同",
        note="保险代理人要出专业报价，这个需求国内同样存在，"
             "但保险产品的条款、费率结构、核保规则与美国完全不同，"
             "报价引擎要整个重写（localization 判 1）；"
             "而面向代理人销售展业工具，在国内绕不开保险销售资质"
             "与持牌机构的边界，非持牌方做这套工具的合规位置很模糊。"
             "获客也不一样：美国靠代理人社群自下而上，"
             "国内是保险公司或中介机构自上而下配发系统，"
             "独立 SaaS 插不进去。它只有 4 个客户、MRR $1,750，"
             "本身就还是一个很早期的验证。",
    ),
    "adawcag": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "aeo-checker": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "ad-machine": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "advanced-gsc-mcp": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "3d-agent": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "adtruffle": dict(demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2, blocker='平台数据合规', note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Meta 广告库的抓取与搬运上，国内对应抖音/巨量创意有官方创意中心，灰色抓取路线不合规。'),
    "ai-designer": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "acquire-community": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。电商卖家是全球最大国内群体，选品/数据/运营工具需求硬，微信/支付宝收款顺畅，但同类工具已不少。'),
    "atelier-nuage": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "fireply-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "ismalicious": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "uizze": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='收款走 polar 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "barron-tech-llc": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "autoclip": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "chariot-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "openalternative": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "breadsync": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。交易/投资工具在国内绕不开金融牌照与合规边界，非持牌方做这类工具位置模糊。'),
    "cbt-artificial-intelligence-technology-inc-dba-zuuz": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "firecert": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "arcton": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "captureflow": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "corey-chiu": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "opentweet": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "apogee": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "blawgy-com": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "autosubtitles": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "axis-labc2026": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "astonish-email-limited": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "bluff-ai": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Threads/TikTok/X 等平台生态上，国内等价渠道（抖音/小红书）接口不开放，拿授权要资质。'),
    "baller": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "athenify": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "christian-tech-jobs": dict(demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。招聘/求职寄生在 LinkedIn 等海外平台上，国内对应 Boss 直聘/猎聘格局已定，跨境需求薄。'),
    "bouncer": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "blitzcut": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Threads/TikTok/X 等平台生态上，国内等价渠道（抖音/小红书）接口不开放，拿授权要资质。'),
    "bidva-buy-linkedin-accounts": dict(demand=3, payment=3, compliance=1, acquisition=2, localization=3, competition=1, blocker='平台规则/账号交易', note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖账号/养号直接违反平台 ToS，国内同样不合规且灰产化，没有可持续的商业结构。'),
    "coprep-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "canopy-api": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。电商卖家是全球最大国内群体，选品/数据/运营工具需求硬，微信/支付宝收款顺畅，但同类工具已不少。'),
    "billionverify": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "algofuse-ai": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。电商卖家是全球最大国内群体，选品/数据/运营工具需求硬，微信/支付宝收款顺畅，但同类工具已不少。'),
    "botric": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "checkform-gymnastics": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "claws-host": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "coco": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "blogr-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "client-one": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "coralflavor": dict(demand=3, payment=3, compliance=1, acquisition=3, localization=4, competition=2, blocker='内容合规', note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。国内内容审核是最高风险区，UGC/角色扮演/成人向社区长期处在整改压力下，独立商业化几乎无空间。'),
    "apexvol": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。交易/投资工具在国内绕不开金融牌照与合规边界，非持牌方做这类工具位置模糊。'),
    "kitchendesign": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "blacktwist": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "cloud-backup-for-podio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "spark-playground": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "clawlink": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3, note='收款走 polar 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。电商卖家是全球最大国内群体，选品/数据/运营工具需求硬，微信/支付宝收款顺畅，但同类工具已不少。'),
    "ai-text-humanizer-netherlands-belgium": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "agentskit": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "altitude-cleaning-crew": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。本地实体服务（清洁/烘焙）难跨境，国内同类靠美团/大众点评与地推，获客渠道要整个重建。'),
    "anyapi": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "aprs-conseil": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "athletiks": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "bakering-app-sl": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。本地实体服务（清洁/烘焙）难跨境，国内同类靠美团/大众点评与地推，获客渠道要整个重建。'),
    "bettrbot": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "brandmylaptop": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "brandmymac": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "brokenstudios": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "chronoid": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "firstrep": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "orbio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "ai-prank-call": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "betting-tips": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "ai-voice-calls": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "demopolish": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "chatsell-co": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "roster-for-slack": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "static-run": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "ai-math-solver": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "ai-smart-decor": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "verse": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "clawbox": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "bankstatemently": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。本地实体服务（清洁/烘焙）难跨境，国内同类靠美团/大众点评与地推，获客渠道要整个重建。'),
    "fit-it-on": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 lemon squeezy 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "ai-influencer-generator": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "flowy": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "check-id": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "chat-whisperer": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "quickvoc": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "blue-esim": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "captime": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "alodata": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "chiptune": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "calsy": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "firsto": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "atslebenslauf-com": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "anymelo": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "reddit-grow": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "cleaner": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "arbichat-s-l": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "bromeai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "fire-tech-apps-llc": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "bolsai": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。交易/投资工具在国内绕不开金融牌照与合规边界，非持牌方做这类工具位置模糊。'),
    "sportsjobs-online": dict(demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。招聘/求职寄生在 LinkedIn 等海外平台上，国内对应 Boss 直聘/猎聘格局已定，跨境需求薄。'),
    "chartvio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "brikk": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "elispeak": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "myterrace-net": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "cat-games": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "c12s-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "forage": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "aiwith-me": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。发现/目录类平台（联盟计划、资源索引）需求看覆盖品类，国内同类靠内容引流与微信生态，冷启动获客是难点。'),
    "june": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "blockit": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Threads/TikTok/X 等平台生态上，国内等价渠道（抖音/小红书）接口不开放，拿授权要资质。'),
    "orbit-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "aidelly": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "car-docs-ro": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "ai-swap-facetik": dict(demand=4, payment=3, compliance=2, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。人脸/监控/VPN 类在国内合规要求高。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "climbx": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "blainy": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "aistudios": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。电商卖家是全球最大国内群体，选品/数据/运营工具需求硬，微信/支付宝收款顺畅，但同类工具已不少。'),
    "car-dealer-tracker": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "careerkit": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "chunks-microlearning": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "alienart-io": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "chartsgpt": dict(demand=3, payment=3, compliance=1, acquisition=3, localization=3, competition=3, blocker='加密资产合规', note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。交易/投资工具在国内绕不开金融牌照与合规边界，非持牌方做这类工具位置模糊。'),
    "brandiseer": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "clear-food": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "bolty": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='收款走 revenuecat 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "chefgpt": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "basincheck": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "alphascope": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "business-freelance": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "aura-ai-ai-video-image-generator": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "coaching-marketing-kit": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "fishingai": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "cooldock": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "auraclaw": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "bloxg-roblox-marketing-agency": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "archifruits-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "cheatmate": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "chatedu-inc": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "agentsguild": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "ai-baby-generator": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='收款走 revenuecat 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "adstudio": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Threads/TikTok/X 等平台生态上，国内等价渠道（抖音/小红书）接口不开放，拿授权要资质。'),
    "adriselab": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "ai-cover-music-video": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "affililist": dict(demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。金融/投资类在国内绕不开牌照与合规边界，非持牌方做这类工具位置模糊。发现/目录类平台（联盟计划、资源索引）需求看覆盖品类，国内同类靠内容引流与微信生态，冷启动获客是难点。'),
    "ai-detector-image-checker": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。教育/备考是国内最卷也最愿付费的赛道，获客靠内容引流，但同类产品（刷题/辅导）竞争激烈。'),
    "admirror": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "account-expense-manager": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "anon-2": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "7running": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。健身/训练类需求在国内真实且付费意愿强，小红书/抖音是天然获客池，但同类 App 已多。'),
    "private-venture": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "aftersora": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。视频生成/字幕/剪辑需求大，但国内有剪映、即梦等免费工具贴身竞争，只能切极窄场景。'),
    "3dplotter": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "agence-web-abstract27": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。SEO/搜索可见度是刚需，国内对应百度/搜狗/抖音搜索，获客可自举，但 SEO 服务在国内已很卷。'),
    "20-0": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "ai-directories": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。发现/目录类平台（联盟计划、资源索引）需求看覆盖品类，国内同类靠内容引流与微信生态，冷启动获客是难点。'),
    "adstellar": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。卖的是「替客户做」的人力服务，在国内要 BD/关系获客，一个人能吃下但规模受限于时间。'),
    "aesty": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。图像/设计/3D/室内需求广，酷家乐/Canva 等已占大头，AI 化的优势在速度与低价。'),
    "adle": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。长在 Threads/TikTok/X 等平台生态上，国内等价渠道（抖音/小红书）接口不开放，拿授权要资质。'),
    "acsperf": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "the-stealthiest-of-ventures": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "gtm-help": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    "mac-duo": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。通用 AI 工具需求存在，但国内大厂与开源方案密集，差异化与获客是关键。'),
    "dope-data": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "orior-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。社区/买卖平台需求看品类，国内同类靠微信生态与信任机制，冷启动获客是难点。'),
    "agenkit": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 polar 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
    "kreapro": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3, note='默认走 Stripe 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。效率/邮件/CRM 类工具需求通用，国内有飞书/钉钉/企微生态，切入点要够垂直才不被自带功能吃掉。'),
    "toonthis": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2, note='收款走 revenuecat 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。广告/创意工具需求永续，但 Meta/Google 投放逻辑搬到国内要重做巨量/腾讯广告规则，且国内卷。'),
    # ── 2026-10-27 批次漏补的两条（2026-10-29 补）──
    # 同 SOLO 表：537b443 入库时漏了这两条，CI 一直红。没有套下面的通用文案 ——
    # 这两条的形状差得远：一条是「需求在、但被免费替代」，一条是「渠道本身错位」。
    "upvoty": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="免费替代与获客渠道",
        note="需求是有的：国内做 B 端产品的团队同样被「反馈散在工单/群聊/表格里」折磨，"
             "但**付费意愿被免费品压着** —— 腾讯兔小巢免费且自带微信生态，"
             "Worktile / PingCode / TAPD 把「需求池」当附属功能送，愿意单买投票板的"
             "只剩重视公开路线图的团队，故需求 3、竞争 2。支付 4：订阅制走支付宝/微信"
             "顺畅，不涉及虚拟币或跨境结算。合规 4：只存用户自己提交的反馈，不碰内容审核"
             "红线；做政企要大客户等保，属可选不是必须。获客 2 是真正的坎：它的增长全压在"
             "「Alternatives to 竞品」的英文 SEO 和 Product Hunt 上，这两样在国内没有等价物"
             "（百度对独立站不友好、PH 无对应场），换知乎/公众号/小红书要重新攒。"
             "本地化 3：投票板本身通用，要换的是集成那一层（企业微信、飞书、钉钉）。",
    ),
    "flodesk": dict(
        demand=2, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="渠道错位 —— 国内没有邮件文化",
        note="这条的硬伤不在执行而在**客群**：国内小企业做客户触达走的是微信/企微/短信，"
             "邮件营销是外贸、跨境电商和出海 SaaS 的工具，盘子比美国小一个量级，需求给 2。"
             "支付 4：订阅制收款无障碍。合规 3：邮件营销在国内受反垃圾邮件规则约束，"
             "要退订链路、发信资质与备案，营销邮件里的用户数据也归个人信息保护法管，"
             "不致命但得逐项做对。获客 3：它靠「标杆用户案例 + 口碑」起量，这一套能平移"
             "到小红书/公众号，但要换成中国客户的故事，且没有销售团队意味着冷启动全靠内容。"
             "本地化 2：模板排版、中文断行、送达与退订合规、SMTP 通道都要重做一遍。"
             "竞争 2：国内这块已萎缩，微信生态把「触达」吃掉了，剩下的小众工具价格被压得低。",
    ),
    # ── 2026-10-29 补：fit_ready --check 报的 26 条 ──
    # 评分口径见 DIMS：每项 5 分制，越高越容易搬（合规一项是「越不碰红线分越高」）。
    # 这 26 条里有 18 条是本轮晋升的，所以判断是按案例已有字段现做的，不是抄来的。
    "flowmance-templates": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=2,
        blocker="模板生态不对应",
        note="模板本身不碰红线，合规给 4。真正的问题是生态：它卖的是 Webflow、Framer、"
             "Figma 三个站的模板，国内对应的是即时设计、稿定，格式与组件体系都不同，"
             "等于把货架拆了重建。需求 3：小团队确实愿意为「省一次设计」付钱，但模板"
             "品类国内免费资源极多，竞争只给 2。获客 3：模板靠长尾关键词，这条路国内"
             "也成立，只是搜索盘子小一档。",
    ),
    "neoscene": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="深度合成合规 + 算力成本",
        note="图生视频的需求在国内是真的，剪映、即梦已经把它做成了大众功能，说明市场在，"
             "但也说明空位很小。合规 2：AI 生成视频要过深度合成标识与内容审核，出问题要"
             "能溯源。本地化 2：支付、短信、分发全要重接。获客 2：流量被抖音与剪映把住，"
             "独立 App 买量极贵。成本侧还有个硬约束——它自述毛利率只有 10%，算力一涨价"
             "就没有缓冲。",
    ),
    "boxer-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="安卓机型碎片化",
        note="健身与拳击在国内有付费人群，Keep 这类产品已经教育过市场，付费意愿给 3。"
             "难点在交付侧而不是需求侧：靠摄像头做动作识别，iOS 尚可，安卓机型与摄像头"
             "差异会把适配成本吃光毛利。合规 3：只做动作识别、不采集人脸特征，风险可控。"
             "获客 2：健身 App 靠内容平台导流，成本高。竞争 2：Keep 与一批拳击课程 App "
             "已经把位置占了。",
    ),
    "kori": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="内容要重做（韩语教材）",
        note="韩语学习在国内是小盘子，且被多邻国、沪江这类成熟产品占住，付费意愿给 2。"
             "最重的是改造：课程、例句、发音示范全是按英语母语者做的，换到中文用户要"
             "整本重写，本地化只能给 1。好消息是它不碰任何合规红线（4），且走 App Store "
             "收付费倒是现成的。这类案子可借的是「极简订阅 + 单一动作」的产品形态，"
             "不是内容本身。",
    ),
    "admitodds-ai-college-admissions-platform": dict(
        demand=2, payment=2, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="升学体系不同 + 咨询资质",
        note="它吃的是 Common App 这套美国申请数据，国内对应的是高考志愿填报，数据在各省"
             "考试院、规则按年变，产品逻辑搬不过来，本地化 1。合规 2：「录取概率评估」在"
             "国内容易被认定为教育咨询甚至志愿填报服务，属于要资质且有纠纷风险的品类。"
             "需求 2：家长愿意付钱，但付给的是「一对一规划」这种人力服务，不是自助工具。",
    ),
    "clamshell": dict(
        demand=3, payment=2, compliance=4, acquisition=3, localization=1, competition=3,
        blocker="无（唯一障碍是定价模式）",
        note="合盖不休眠是跨平台的通用痛点，macOS 全球一致，本地化几乎为零（1 分是给"
             "「中文说明与客服」留的）。合规 4：不碰数据、不碰内容。真正要改的是收钱方式："
             "$9.99 一次买断在国内转化率很低，得改成订阅或加增值。竞争 3：同类免费工具"
             "（Amphetamine 那类）已经把位置占住，做得比它好才有机会。",
    ),
    "colorcraft": dict(
        demand=3, payment=2, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="KDP 生态不在国内",
        note="它的卖点是「一句话做成过 KDP 检查的 PDF」，而 KDP 是亚马逊的自出版体系，"
             "国内对应的是微信读书、番茄这类平台，排版与审核规则完全不同，卖点直接失效，"
             "本地化 2。需求 3：自出版人群在国内也在增长。支付 2：一次性小额付费在国内"
             "要走小程序或支付平台，转化不如海外的信用卡。",
    ),
    "kodo": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="设计工具红海",
        note="AI 设计在国内是最挤的赛道之一：即时设计、MasterGo、稿定都在做生成式设计，"
             "而且对个人免费，竞争只能给 1。需求本身是实的（3），付款方式也现成（3），"
             "但「对话生成可编辑元素」这个能力已经没有差异化，新入局只能往垂直场景钻"
             "（比如只做电商详情页）。获客 2：这个品类的投放成本被大厂抬得很高。",
    ),
    "freelance-writing-club": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="业态不存在（付费约稿情报）",
        note="它卖的是「每个工作日一封人工筛过的约稿线索」，国内对应的形态是接单群、"
             "稿费平台与自媒体联盟，这些都不向写手收费，所以付费意愿给 2，本地化给 1——"
             "不是改造成本高，而是这个业态本身不存在。合规 3：转发公开约稿信息不违规，"
             "但要小心把媒体联系信息当商品卖。可借的是「人工筛选本身就是价值」这个定位。",
    ),
    "agent-media": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="AI 内容备案与平台审核",
        note="AI 短剧在国内需求很旺（红果、抖音都在推），付费也有，但合规是最硬的一堵墙："
             "生成式内容要备案、要加深度合成标识，平台对 AI 剧集另有一套审核口径，"
             "合规只能给 1。竞争 2：平台自己在做同类内容，独立订阅很难留住人。"
             "真正能借的是「固定演员阵容 + 连载」这个降低生成成本的做法，而不是这门生意。",
    ),
    "lapsula": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="病患数据与行业 Know-how",
        note="诊所管理在国内是成熟市场（领健、牙医管家等），需求与付费都真实，但两块难："
             "一是涉及病患数据，要过等保与个人信息保护，合规 2；二是各家诊所的排班、"
             "收费、医保结算差异极大，本地化只能给 1。竞争 2：已有产品覆盖了大部分功能，"
             "空位只在很细的专科。可借的是「只做诊所、不做全院 HIS」的切口。",
    ),
    "lyro": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="音乐版权与分账",
        note="AI 音乐国内已有网易天音、昆仑万维这类玩家，需求被验证过（3），订阅收款也现成（3）。"
             "难点在版权：生成物的权利归属、平台分账规则、以及「成品能不能商用」都要逐条"
             "写清楚，合规 2。成本侧同样是算力按次烧。可借的是它的定价结构——按月给 credits，"
             "把重度用户和尝鲜用户分开。",
    ),
    "flint": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="iMessage 渠道在国内不存在",
        note="它的分发是整个产品的一部分：不下载 App，直接在 iMessage 里聊天。国内没有"
             "iMessage 生态，改成微信服务号又要过主体与内容审核，等于重做，本地化 1。"
             "宗教内容在国内也属于要谨慎处理的题材（合规 3 是因为它只做灵修文本、不涉及"
             "组织活动）。可借的是「用现有聊天工具当产品壳」这个思路，国内对应微信/企微。",
    ),
    "pdf-vault": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="可转售授权业态不存在",
        note="卖「可转售的 PDF 授权」在国内几乎没有对应市场——知识付费更认课程与会员，"
             "不认转售权，付费意愿 2。法律边界也模糊：数字商品的分销授权在合同上要写得很"
             "清楚，否则一有纠纷就说不清（合规 3）。可借的是它的定价结构：一次性授权 + "
             "月费托管，把「买断」和「持续服务」拆成两条收入。",
    ),
    "coldcaller-pro": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="个人信息保护法 + 外呼治理",
        note="它的产品就是一本 B2B 联系人/电话线索库，并鼓励按名单外呼。这套在国内直接撞"
             "《个人信息保护法》与商业外呼的管理规定，合规只能给 1，且数据源要全部重建，"
             "本地化也 1。需求 3：B2B 销售确实缺线索。可借的只有「按行业切线索包」的"
             "产品结构，数据来源必须换成公开渠道并做授权留痕。",
    ),
    "coldsire": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="工具链不同（Workspace / Instantly）",
        note="它卖的是「把 Google Workspace 的 DNS 配好、接上 Instantly 就能发冷邮件」。"
             "国内这套工具链整个不存在：邮箱是企业微信、阿里邮箱，冷邮件本身也不成气候，"
             "本地化 1。需求 3：国内对应的是「企业邮箱批量开通 + 送达率调优」这类需求，"
             "确实有人愿意付钱。可借的是「把配置这件脏活做成订阅」的思路。",
    ),
    "knightvision": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="广告素材合规",
        note="电商与投放素材在国内是最大的付费场景之一，需求给 4，付款路径也现成。"
             "难点在合规 2：生成的广告图要过平台审核，极限词、虚假宣传、人物肖像都要盯，"
             "出事是品牌背。竞争 2：阿里妈妈、巨量都在把生成能力免费塞给广告主，第三方"
             "工具只能靠「更懂某个品类」活。",
    ),
    "conductor": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="QuickBooks 生态不对应",
        note="它是 QuickBooks 的 API 中间层：帮公司把财务数据接出来、接进别的系统。"
             "国内对应的是用友、金蝶，生态不通，本地化只能给 1。但它不碰内容合规（4），"
             "竞争 3——国内做「老系统 API 化」的厂商不多，空位比想象中大。真正可以照搬的是"
             "这个形态：找一个装在几十万家公司里的老系统，给它在外面套一层干净的 API。",
    ),
    "headshotpro": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=1,
        blocker="人脸数据 + 红海竞争",
        note="证件照在国内是彻头彻尾的红海：智能证件照类小程序遍地，且大多很便宜甚至免费，"
             "竞争只能给 1。人脸属于敏感个人信息，要单独同意、要能删（合规 2）。它的启动"
             "靠的是英文 SEO 加作者存量受众，国内 SEO 盘子小（获客 2）。唯一能借的是"
             "「只做一张图、做到比照相馆好」的极致单点。",
    ),
    "klar": dict(
        demand=4, payment=2, compliance=2, acquisition=3, localization=1, competition=2,
        blocker="考纲本地化",
        note="备考在国内是最刚性的需求之一，付费意愿给 4。但内容必须按国内考纲重做"
             "（高考、考研、公考各有各的题库与命题习惯），本地化只能给 1，这是整条路里"
             "最重的一块。合规 2：涉及教育内容，要小心「押题」「保过」这类表述。"
             "获客 3：校园口碑这条路国内同样成立。可借的是「三个不懂代码的人用 Lovable "
             "把产品搭出来」的路径本身。",
    ),
    "refactoring-ui": dict(
        demand=2, payment=2, compliance=4, acquisition=3, localization=2,
        competition=2,
        blocker="买家在英文社区",
        note="内容本身跨语言通用，合规上什么都不碰（4）。真正的问题是人不在：它一上线就"
             "卖爆，是因为作者在 Twitter 有 5 万开发者听众，这套人情账在国内要重新攒"
             "（获客 3，靠内容社区是可行的，但要时间）。国内对应的是极客时间、掘金小册，"
             "分发渠道换了，定价也要按国内习惯压（付费 2）。",
    ),
    "pdf-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="域名红利不可复制",
        note="它起量的一大半功劳属于 pdf.ai 这个域名带来的搜索意图，这一条在国内无法"
             "复制——百度已把这类词喂给自己的产品（获客 2）。产品能力（跟 PDF 对话）国内"
             "免费替代很多，竞争 2。但需求是真的（3），按月订阅也被接受（3），合规上"
             "只涉及用户上传文档的存储，注意告知与删除即可（3）。",
    ),
    "david-ai": dict(
        demand=3, payment=2, compliance=1, acquisition=1, localization=1,
        competition=2,
        blocker="直邮触达与个人信息",
        note="它的获客是「AI 批量给没网站的小店建站，再寄带二维码的明信片」。明信片要用"
             "商家地址与经营者姓名，这些属于个人信息，来源不合法就是硬伤，合规只能给 1；"
             "线下直邮搬到国内还要重新摸一遍投递与转化（获客 1）。本地化 1：建站本身国内"
             "有微盟、有赞，改造成本不高但空位也没了。可借的是「先做出成品再去敲门」这个"
             "动作顺序，不依赖平台流量。",
    ),
    "sleek": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="设计工具红海（与 kodo 同因）",
        note="判断与 kodo 基本一致：即时设计、MasterGo 对个人免费且都在做 AI 生成，"
             "竞争给 1。它的差异化是「3 周做完、一个月起量」，这个速度值得学，但产品本身"
             "在国内没有位置。获客 2：这类工具的投放成本被大厂抬高。",
    ),
    "3ak-track": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1,
        competition=2,
        blocker="训练内容要按国内体考重写",
        note="它卖的是校队田径训练计划。国内对应的是体育中考与校队训练，需求其实更刚性，"
             "但内容得按国内考试项目与评分标准重写（本地化 1）。合规 4：只涉及训练数据，"
             "不碰医疗也不碰内容。它的启动几乎不可复制——两个人在 IG/TikTok 有 15 万粉，"
             "产品是「粉丝变现」而不是「找到需求」（获客 2）。",
    ),
    "plausible-analytics": dict(
        demand=3, payment=2, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="免费替代太强",
        note="网站统计在国内被百度统计、CNZZ 这类免费产品占满，「隐私友好、不收集个人数据」"
             "这个卖点在国内的付费意愿很弱（付费 2）。合规 2：统计本身要注意数据出境与"
             "等保要求。可借的是它的开源 + 云托管双轨：代码公开换信任，托管收费换收入，"
             "这个结构在任何市场都能用。",
    ),
    # ── 2026-11-04 晋升批 A 档的 3 条 ──
    # 口径提醒：这三条门面收入都很小（$283–$1,308/近 30 天），分档测的是
    # 「能不能原样搬」，不是「这门生意值不值得做」，别拿去排推荐位。
    "appmockupgenerator": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=3,
        blocker="品类天花板低（12 个月累计仅 $4,657）",
        note="需求 3：给 App 商店出整套截图是独立开发者的真实小刚需，但买的人限于「马上要"
             "发版」那一小撮，且淘宝 5 元出图、ASO 工具自带模板两头分流，付费意愿只能算中性。"
             "付费 3：$9 起的小额，线上可自助，国内要接微信/支付宝小额收单，通道不是障碍，"
             "障碍是客单太低、退款一多就吃光毛利。合规 4：只处理用户自己上传的截图，不生成"
             "敏感内容；唯一风险点是截图里可能含他人 App 界面与商标，做站时写清「素材由用户"
             "提供」即可，不构成硬伤。获客 4：独立开发者集中在 V2EX、即刻、少数派、GitHub 这几个"
             "地方，冷启动不靠投放。本地化 4：模板要按华为/小米/OPPO 商店的尺寸与华为应用市场"
             "的截图规范重做一遍，工作量按天算。竞争 3：国内没有同名品类领跑者，但设计模板站与"
             "免费样机工具占了「出图」这个位置，空位只在「2 分钟出全套尺寸」这一层。"
             "⚠ 体量提醒：近 30 天 $283、近 7 天 $57（2026-11-03 同步），12 个月累计 $4,657，"
             "月流水从 5 月 $1,364 一路跌到 10 月 $199 —— 形态可搬，但别当增长案例讲。",
    ),
    "blazedocs-io": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=3,
        blocker="开源免费方案分流（MinerU 一类）",
        note="需求 3：PDF/扫描件转 Markdown 是 RAG 与文档解析链条上的真问题，法务、研究、"
             "内容营销都有人在买，但总量小、且很多人自己写脚本解决。付费 3：$7.99 起，"
             "开发者能自助付费；企业客户走得走报销流程，回款慢但客单稳。合规 4：转换本身"
             "不涉监管，风险只在用户上传文档的内容与「文档是否走境外模型」，部署在国内云上"
             "就规避了。获客 4：面向开发者与研究者，社区与搜索能直达，不需要投放。"
             "本地化 4：要接国内网盘/微信文件入口，并换成本地模型跑解析（成本与合规双赢），"
             "改造属于工程活不是重做产品。竞争 3：MinerU、PDFMathTranslate 这类开源方案免费"
             "但部署门槛高，「网页即用、不用配环境」这个位置还空着。"
             "⚠ 口径提醒：它是非订阅制，MRR 恒 0，近 30 天 $513；2026-06 峰值 $1,319 后"
             "连降 5 个月到 10 月 $268 —— 讲增长要用流水口径，别写成月收入。",
    ),
    "ihatepdf-cv": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=2,
        blocker="PDF 工具箱已被 WPS/夸克/iLovePDF 占满",
        note="需求 3：PDF 处理是高频长尾需求，但用户习惯是「搜一个免费的就用」，愿意为它"
             "付费的人极少。付费 3：它靠 $199 这一档撑着（6 个订阅 × $199 = $1,194），"
             "这个价位在国内至少要企业发票与本地化服务才卖得动，个人不会买。合规 4：文件在"
             "浏览器本地处理、不上传，反而是国内最好讲的合规故事（数据不出设备），"
             "不涉监管。获客 4：工具型 SEO 打法，落地页按「PDF 转 XX」长尾铺词，"
             "这套在国内同样有效。本地化 4：要接微信文件与国内网盘入口、补中文 OCR，"
             "纯工程量。竞争 2：WPS 免费、夸克扫描王、iLovePDF 中文版已经把 60+ 工具里的"
             "绝大多数覆盖掉，「本地处理」这个差异点在国内付费意愿弱，空位很窄。"
             "⚠ 收入口径存疑：累计 $1,340 = 近 30 天 $1,308 = 3/6/12 个月全部 $1,340，"
             "说明支付账户 9 月才接通、全部流水集中在最近一个月；同期档案还写着「历史峰值"
             " $31」。$1,194 的 MRR 不宜当稳定收入引用，本行判的是可搬性，不是它已跑通。",
    ),

    # ── 2026-11-04 B 档（综合 50–70）合并，共 121 条 ──
    "abstract27-ltd": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="Ghost 生态国内不存在",
        note="需求 2：Ghost 用户极少，国内几乎为零。付费 3：£25–40/月，"
             "$402 MRR。合规 4：建站托管本身合规，注意备案。获客 2：靠 Ghost 社区推荐。"
             "本地化 1：国内对应的是 WordPress 与 SaaS 建站，Ghost 生态不成立。"
             "竞争 3：没有竞品。它月流水从 $1,556 掉到 $33，几乎归零。",
    ),
    "adverse-monitor": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="威胁情报需本地数据源",
        note="需求 4：勒索软件点名后的「免责证据」是真需求，国内企业也怕。付费 4："
             "$388–3,888/年，年付高客单。合规 3：情报采集涉暗网与第三方数据，"
             "来源合法性要交代。获客 3：靠安全圈口碑与合规驱动采购。本地化 2："
             "情报源要换成本地黑产与漏洞库，境外情报对国内威胁覆盖差。竞争 2：国内安全厂商已提供同类服务。"
             "它的年付 + 高客单模式值得学，但要有人脉。",
    ),
    "aerie": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="服务型交付",
        note="需求 3：初创公司要人帮搭 GTM 技术栈，需求存在但很小众。付费 4："
             "客单 $373，4 个订阅 $1,495。合规 4：技术服务，风险低。"
             "获客 2：靠创始人网络与内容，不可复制。本地化 2：国内 GTM 语境与渠道完全不同。"
             "竞争 2：国内是营销技术公司在做。它月流水从 $18,741 掉到 $1,278，"
             "说明项目制收入不稳。",
    ),
    "agent-berlin": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道已拥挤",
        note="需求 3：把 SEO 执行自动化，需求存在但客户更想要结果不是工具。"
             "付费 4：客单 $99，4 个订阅 $396，小体量高价。合规 3："
             "批量生成内容与自动外链都踩平台规则。获客 3：靠创始人销售。本地化 3："
             "换国内搜索与内容平台。竞争 2：同类代理产品多。它月流水从 $743 掉到 $99，"
             "是「自动 SEO」这个品类难以证明效果的缩影。",
    ),
    "ai-money-group": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="培训内卷 + 交付重",
        note="需求 4：企业主想「用 AI 员工省钱」，这个焦虑在国内更盛。付费 4："
             "客单 $93.88，103 个订阅 $9,670，月付复购。合规 2："
             "培训承诺收益、涉预付费与虚假宣传，国内监管明确。获客 3：靠短视频与私域，"
             "转化路径成熟。本地化 3：内容换成国内工具即可。竞争 1：同类培训多到烂。"
             "它月流水从 $25,053 掉到 $527，说明「卖焦虑」的复购极差——第一批客户上完就没人"
             "续。",
    ),
    "ai-song-clip-generator": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="版权 + 国内免费",
        note="需求 3：做歌做短片是玩梗与内容需求，国内抖音生态更旺。付费 3：$8.99 周 / "
             "$59.99 年，180 个订阅 $904，客单 $5.02。合规 2："
             "AI 音乐版权归属不明，模仿歌手音色还涉人格权，平台对 AI 音乐有专门规则。"
             "获客 3：靠短视频传播，天然适合。本地化 3：换中文歌词与国内模型。"
             "竞争 1：网易天音、海绵音乐免费。它月流水从 $6,586 掉到 $434。",
    ),
    "ai-suitup": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="竞争者免费",
        note="需求 4：求职与社交需要体面头像，国内需求同样旺。付费 3：$27–57 一次性，"
             "客单不错但复购为零。合规 2：人脸是敏感个人信息，要单独同意与存储合规，"
             "生成像还可能被用于伪造。获客 3：靠案例与投放。本地化 3：换中文场景与国内支付。"
             "竞争 1：国内 AI 写真免费额度极大，价格战打到底。它累计 $648,743 但近 30 "
             "天只有 $910，是「曾经很好、现在被免费吃掉」的样本。",
    ),
    "aicasadesign-com": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="房产广告合规",
        note="需求 4：房产经纪人要视频带看，国内需求更旺（抖音卖房已成型）。付费 3："
             "$42 客单，31 个订阅 $1,302。合规 2：房源视频属广告，"
             "国内对学区、价格、面积宣称有严格规定，AI 生成的「效果图」容易被认定虚假宣传。"
             "获客 3：靠房产中介社群与本地代理。本地化 2：要接抖音/视频号的房源内容规范与真实房源核验。"
             "竞争 2：国内已有房产短视频工具。这条的合规风险大于技术风险。",
    ),
    "aiselfi-es": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="人脸信息 + 竞争者多",
        note="需求 4：职业头像与社交头像的「体面需求」在国内同样成立，求职季尤其明显。"
             "付费 3：$29–39 一次性买断，决策成本低但复购为零。合规 2："
             "处理人脸信息属敏感个人信息，要单独同意 + 存储合规；生成的人像还可能被用于伪造身份。"
             "获客 3：靠案例图与投放，LinkedIn 场景在国内换成求职平台。"
             "本地化 3：换中文场景与国内支付即可。竞争 2：国内 AI 写真产品极多，"
             "价格战激烈。它累计 $58,307 但近 30 天只有 $320，是典型的一次性生意衰减。",
    ),
    "aiwritebook": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="出版链路需资质",
        note="需求 4：想写书的人多，AI 代写降低了门槛，需求真实。付费 3：$28.91 客单，"
             "434 个订阅 $12,548，低价走量。合规 2：国内出版必须有书号与出版社，"
             "AI 生成内容还要标识；「AI 代写出版」这个卖点在合规上站不稳。获客 3："
             "靠内容营销与自出版社群。本地化 2：要接国内排版与电子书渠道（微信读书、"
             "番茄），出版链路完全不同。竞争 2：国内 AI 写作工具多。它月流水从 $37,910 掉到"
             " $3,500。",
    ),
    "all-love-here": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="实物供应链",
        note="需求 4：个性化礼品是稳定需求，国内更旺（生日、纪念日）。付费 4："
             "近 30 天 $1,600，客单几十美元。合规 3：商品质量与广告宣称要守规矩，"
             "定制内容还要注意版权。获客 2：靠 Shopify 投放，成本高。本地化 2："
             "国内供应链在义乌/东莞，反而更有优势，但渠道要换成抖音/小红书。竞争 2："
             "国内定制礼品商家极多。它提醒：实物生意可搬，但搬的是供应链不是产品。",
    ),
    "anon-1": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="公开资料过少",
        note="需求 2：档案里只有收入数字，没有产品形态与客户，无法判断需求。付费 3："
             "按次或按项目结算，近 30 天 $2,955，累计 $52,000+。"
             "合规 3：不明。获客 2：不明。本地化 3：不明。竞争 3：不明。这条的问题不在分数，"
             "在信息缺失——单看「不订阅、按次结算、$174,549 峰值」只能猜。"
             "建议标为「资料不足，需补官网与产品截图」，暂不作为案例讲。",
    ),
    "apparelai": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="电商图赛道拥挤",
        note="需求 4：服装电商的模特图是刚性支出，一次拍摄几千元，AI 出图能直接省钱。"
             "付费 4：$9–199 四档，商家按出图量加钱，付费逻辑自然。合规 3："
             "生成模特图用于商品页，涉「图片是否真实反映商品」的消费者权益问题，平台规则也要求标注。"
             "获客 4：1688、拼多多、抖音商家聚集，案例图一放就能转化。本地化 3："
             "要适配国内平台的图片规范与「白底图」要求。竞争 2：国内已有大量 AI 商拍产品在打价格战。"
             "它的 $1,020 MRR 说明小团队能吃到，但价格战会很快吃掉利润。",
    ),
    "appitstudio-apps-bundle-and-services": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="Mac 付费习惯弱",
        note="需求 2：多屏工作区切换是 Mac 重度用户的痛点，人群窄。付费 3："
             "$69.99 单机买断，近 30 天 $536。合规 4：纯本地工具，"
             "无数据风险。获客 2：靠 Mac 效率社区与少数派这类媒体，量小。本地化 3："
             "功能与语言无关，改的是渠道与支付。竞争 2：国内 Mac 工具付费氛围弱，"
             "同类多有免费替代。它月流水从 $2,279 掉到 $355，买断制的天花板很明显。",
    ),
    "archirise": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="国内出图工具已多",
        note="需求 4：效果图在国内是成熟的按张付费市场，设计院与工作室年年有预算。"
             "付费 4：$19–99/月对设计公司不敏感。合规 3：生成图对外投标或宣传要标注 AI 生成，"
             "用在房源广告上还涉虚假宣传。获客 4：室内设计与建筑社区、小红书案例都能直接带客。"
             "本地化 3：要接国内常用渲染器与素材库，改的是上下游而不是核心。竞争 2："
             "酷家乐、模袋云、各种 SD 工作流已经把位置占住。它月流水从 $1,136 回落到 "
             "$235，说明单靠「更像照片」撑不住复购。",
    ),
    "artkit-cc": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖海外流量",
        note="需求 2：给 AI 代理做可索引的文档位，是新兴的流量玩法，需求方主要是海外工具。"
             "付费 3：按展示位或引流收费，近 30 天 $2,675，累计 $76,072。"
             "合规 4：公开内容。获客 2：全部依赖 Google 收录，国内搜索引擎的 AI 收录还没成"
             "形。本地化 2：要改成豆包/Kimi 的收录玩法，规则不明。竞争 2："
             "同类站点在快速增加。它月流水从 $12,876 掉到 $1,675，"
             "说明这种流量的窗口很短。",
    ),
    "assignmentgpt-ai": dict(
        demand=4, payment=3, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="学科培训监管",
        note="需求 4：作业辅导需求极大，国内更旺。付费 3：$574 MRR，49 个订阅。"
             "合规 1：面向学生的 AI 作业代写在国内属明确违规（学科类培训监管 + 学术诚信），"
             "平台与学校都在打击。获客 3：靠校园传播。本地化 2：要按国内教材重做内容。"
             "竞争 2：作业帮等已被规范。这条只可看产品形态，不可在国内做。",
    ),
    "aswati-studio": dict(
        demand=1, payment=3, compliance=2, acquisition=2, localization=2, competition=4,
        blocker="宗教内容 + 人群极小",
        note="需求 1：面向穆斯林创作者的合规音频素材，国内这个人群与内容生态都很小。"
             "付费 3：$7.5–16/月，48 个订阅 $575。合规 2：宗教内容在国内属敏感领域，"
             "传播有明确管理规定。获客 2：靠社群。本地化 2：素材要按本地宗教习惯重制。"
             "竞争 4：几乎没有竞品，因为没人做。它说明「垂类素材库」能养活人，但要选无监管风险的垂类。",
    ),
    "august": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="通用助手被大厂免费覆盖",
        note="需求 3：办公自动化是真实需求，但客户更想买具体结果。付费 3：$166 客单，"
             "只有 2 个订阅 $332，说明定价与价值没对上。合规 4：只处理客户自己的办公数据。"
             "获客 2：靠创始人销售，无渠道。本地化 3：接飞书/企微即可。竞争 2："
             "扣子、Dify 免费把这类需求吃掉。它的样本意义是反面的：$166 的通用助手，"
             "客户会拿它跟免费方案比。",
    ),
    "aura-for-creators": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="国内生成工具免费且强",
        note="需求 3：可商用的生成图是内容团队的需求，但国内即梦、可灵免费额度大。"
             "付费 3：$20–40/credits 三档，只有 3 个订阅 $800，"
             "客单 $266 说明是重度用户。合规 3：商用授权与肖像权要写清。获客 2："
             "靠创作者社群。本地化 3：换国内模型即可。竞争 1：免费工具已经足够好。"
             "它月流水从 $3,375 掉到 $200，3 个客户撑不起生意。",
    ),
    "authex-labs": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="国内邮件送达已有玩家",
        note="需求 4：外贸与 B2B 冷邮件进垃圾箱是真痛点，企业愿意为送达率付年费。"
             "付费 4：£249/域名/年，一次性预付，现金流好。合规 3：邮件认证本身合规，"
             "但客户拿它去群发就涉反垃圾与个人信息保护。获客 3：客户是外贸与 SaaS，"
             "需要案例与代理商。本地化 3：国内要接阿里云/腾讯云邮推与备案域名，"
             "规则不同。竞争 2：国内邮件送达服务商已有多家，且和 ESP 绑定。"
             "它月流水从 $3,091 掉到 $827，说明年费模式续费压力大。",
    ),
    "authoritystack-ai": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="LDT 模式国内接受度低",
        note="需求 4：本地商家要「在搜索里被看见」，这个需求在国内同样硬，且预算从广告费里出。"
             "付费 3：终身买断 $6,680/月流水说明卖得动，但国内用户对 LDT 不熟悉，"
             "更认年付。合规 3：批量生成落地页涉内容质量与 SEO 作弊风险，平台会打击。"
             "获客 3：靠 LDT 平台与联盟推广，国内渠道不同。本地化 3：要接百度/高德/抖音本地生活，"
             "不是 Google Business。竞争 2：国内本地 SEO 服务分散且卷。"
             "它月流水从 $22,784 掉到 $3,330，LDT 的「一次性卖完就没了」"
             "问题很明显。",
    ),
    "automate-chill": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2,
        blocker="本地 SEO 服务分散",
        note="需求 3：本地商家要「搜得到、打得通」，国内对应的是百度爱采购、抖音本地生活。"
             "付费 3：$299/年，68 个订阅 $674，年付低价靠量。合规 4："
             "建站本身合规，但批量生成的低质站点会被搜索引擎判定为垃圾站。获客 3："
             "靠 LDT 与联盟推广，国内渠道不同。本地化 2：国内本地流量的入口是百度与抖音，"
             "不是 Google Business，产品逻辑要重做。竞争 2：本地建站服务商多且便宜。"
             "它月流水从 $3,053 掉到 $238。",
    ),
    "autoprintemail": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="场景较窄",
        note="需求 3：自动打印订单/发票是小微仓库与餐饮的真实痛点，但只在这类场景成立。"
             "付费 4：$29/月或 $499 买断，一次性付费的接受度高。合规 3："
             "读取企业邮箱内容，涉商业秘密与授权边界。获客 2：客户是线下小商户，"
             "只能靠搜索与地推，量起得慢。本地化 3：要接国内常用邮箱、飞书/企业微信与国产打印机驱动。"
             "竞争 3：国内有「自动打印」类小工具，但没形成品牌。它月流水从 $10,354 掉到 "
             "$667，说明买断制留不住人。",
    ),
    "autotube": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="剪映免费",
        note="需求 3：无脸频道要产能，国内做号人群大但工具已被免费覆盖。付费 3："
             "$9–119 五档，$624 MRR。合规 3：生成视频要标识，YouTube 对批量 "
             "AI 内容也有政策。获客 2：靠 YouTube 生态，国内不通用。"
             "本地化 2：抖音/视频号的规则完全不同。竞争 1：剪映把这条路铺平了。",
    ),
    "babelbot": dict(
        demand=2, payment=2, compliance=4, acquisition=1, localization=1, competition=3,
        blocker="绑 Discord",
        note="需求 2：跨语言服务器的沟通需求，只存在于 Discord 生态。付费 2："
             "€9.99–19.99，60 个订阅 $787，客单低。合规 4：翻译消息内容，"
             "注意隐私。获客 1：全部靠 Discord 机器人目录。本地化 1："
             "国内用 QQ/微信群，机器人生态完全不同。竞争 3：同类机器人多且免费。"
             "它只能当「机器人分发」的案例看。",
    ),
    "beautyflow": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="美业 SaaS 已有强势者",
        note="需求 4：美容美发店的预约与会员管理是成熟付费项，店主习惯为软件付月费。"
             "付费 4：$438 MRR 来自 110 个订阅，客单低但留存好。合规 3："
             "会员信息、消费记录涉个人信息，营销触达还要守短信规则。获客 3：靠本地服务商与门店口碑，"
             "一店带一片。本地化 2：要接微信预约、企业微信、微信支付、电子发票，"
             "等于按国内门店流程重做。竞争 2：美团、有赞美业、博卡已经占住。它的启示是「小店主愿意为省事"
             "付钱」，产品本身搬不动。",
    ),
    "bidder-iq": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="客群在国内极窄",
        note="需求 2：保时捷藏家拍卖定价，国内这个人群极小。付费 3：$396/年，"
             "近 30 天 $2,052，年付高客单。合规 3：拍卖数据来源与使用要授权。"
             "获客 2：靠藏家圈层，不可扩。本地化 2：国内是二手车与拍卖平台（如司法拍卖），"
             "数据源完全不同。竞争 3：没有竞品，人群太窄。它的模型（$396 年费 + 极窄人群）"
             "适合有行业背景的人做副业。",
    ),
    "blabla": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="平台接口封闭",
        note="需求 4：社媒评论与私信转成订单，是商家真实的漏单痛点。付费 4：€9–49/月，"
             "83 个订阅 $5,731，价格带设计得好（按平台数加价）。合规 2："
             "抓平台私信数据、自动回复营销信息，在国内直接撞个人信息保护与平台规则，"
             "接口也不开放。获客 3：靠电商社群与案例。本地化 2：微信/抖音私信的自动化属于平台零容忍项，"
             "只能走官方客服接口。竞争 2：国内企微 SCRM 已占位。它月流水从 $9,550 掉到 "
             "$858，平台政策一变就腰斩。",
    ),
    "blipix": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费",
        note="需求 4：无脸短视频账号需要持续产能，这条需求在国内更旺（抖音、视频号）。"
             "付费 3：$39–129/月，43 个订阅 $1,646，客单高但留存靠产出。"
             "合规 3：批量 AI 视频要标识，内容审核也更严。获客 3：靠「批量做号」"
             "教程与社群。本地化 3：要适配抖音/视频号的比例与音乐版权。竞争 1："
             "剪映、即梦把这条路免费铺好了。它月流水从 $3,860 掉到 $1,093，"
             "说明「批量出片」的窗口在关闭。",
    ),
    "blitzreels": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费",
        note="需求 4：把长视频拆成短视频，是每个做账号的人都要干的活，需求刚性。"
             "付费 3：$29–149 三档，能卖但续费靠产出量。合规 3：切片涉原素材版权，"
             "二创在国内平台还有原创度判定。获客 3：靠创作者社区与案例拆解。本地化 3："
             "要适配抖音/视频号的比例与审核规则。竞争 1：剪映的智能切片免费，且就在用户手里。"
             "它的 $1,979 MRR 说明海外还有人愿意为「不用学剪映」付费，"
             "国内这条路走不通。",
    ),
    "blog2video": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费覆盖",
        note="需求 3：把长文转视频是内容团队的真实需求，但国内用户第一反应是打开剪映。"
             "付费 3：$4/条或 $25 起订阅，13 个订阅 $531，量很小。"
             "合规 3：生成视频要标识 AI 合成，用于资讯还要核实内容真实性。获客 3："
             "靠内容营销与案例，能带量但慢。本地化 3：要接公众号与国内视频号/抖音的尺寸与审核。"
             "竞争 1：剪映的图文成片免费且效果好，独立产品几乎没位置。它月流水从 $1,550 掉到 "
             "$203，就是被免费工具吃掉的样子。",
    ),
    "bobby": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="投顾资质红线",
        note="需求 4：投资决策辅助需求极大，国内更旺。付费 3：Freemium + 月订阅，"
             "近 30 天 $1,436。合规 1：给具体标的建议在国内属证券投资咨询业务，"
             "必须持牌；无牌做这个就是非法经营，红线不能碰。获客 2：金融内容投放受严格限制。"
             "本地化 2：要接 A 股与国内基金数据，还要过金融数据授权。竞争 1："
             "持牌机构与券商 App 占满。这条只能看「结构化思考」的产品形态，产品不可做。",
    ),
    "brainote": dict(
        demand=3, payment=3, compliance=4, acquisition=1, localization=3, competition=2,
        blocker="学习工具国内免费多",
        note="需求 3：学习辅助是长期需求，但国内免费工具太强。付费 3：$4.99 周 / "
             "$44.99 终身，靠冲动内购。合规 4：只涉学习数据。获客 1：全部流量来自 App "
             "Store，安卓与国内渠道几乎没有入口。本地化 3：换学科与考试内容。"
             "竞争 2：同类免费工具多。它月流水从 $1,376 掉到 $384，"
             "客单低 + 无渠道，是这个品类的天花板。",
    ),
    "brainrot-mov": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="免费替代强",
        note="需求 4：短视频账号要量，AI 头像视频是低成本方案，国内做号人群庞大。"
             "付费 3：$8–32/年，年付低价，靠量堆（客单 $18.87）。合规 3："
             "AI 数字人出镜要标识，且名人形象不能碰。获客 3：靠短视频平台的案例与教程，"
             "传播极快。本地化 3：换国内数字人模型与平台比例。竞争 1：剪映数字人、"
             "腾讯智影免费额度大。它月流水从 $36,456 掉到 $1,089，"
             "跌了 97%，是这轮「数字人热」退潮的典型样本。",
    ),
    "buma-baby-tracker": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="儿童个人信息合规",
        note="需求 4：新手父母记录喂养与睡眠是刚需，国内这类 App 有真实活跃。"
             "付费 3：$8.14 客单，149 个订阅 $1,213，低价订阅靠量。"
             "合规 2：收集的是婴幼儿健康与家庭信息，属敏感个人信息，国内对儿童个人信息有专门规定（监护人"
             "同意、单独存储），门槛不低。获客 3：靠妈妈群与小红书口碑传播。本地化 3："
             "换中文与国内育儿习惯。竞争 2：亲宝宝、宝宝树已占位。",
    ),
    "calma": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="ADHD 人群认知弱",
        note="需求 2：ADHD 人群在国内缺少自我认知与付费习惯，需求真实但转化极难。"
             "付费 3：$1.99 周 / $39.99 年，106 个订阅 $456。"
             "合规 3：涉健康与心理状态数据，属敏感信息。获客 2：只能靠垂类社群与内容，"
             "量很小。本地化 3：换中文语音即可。竞争 3：没有强竞品，但也没有市场。"
             "它月流水从 $3,234 掉到 $57，跌了 98%，说明这个切入点撑不起来。",
    ),
    "car-identifier": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker="低客单识别类获客难",
        note="需求 2：识车是「用一次就够」的工具，复购靠新鲜感，国内懂车帝/汽车之家已经把车型库做成免费"
             "功能。付费 3：$9.99/月或 $39.99 买断，105 个订阅每月 $576。"
             "合规 4：只识别车辆外观，不采集人脸与车牌就不是敏感信息，但拍照时很容易一起拍到。"
             "获客 2：靠 App Store 榜单与短视频，投放成本高。本地化 4："
             "换国内车型库即可。竞争 3：没有强竞品，但也没有需求缺口。它月流水从 $806 掉到 "
             "$269。",
    ),
    "car-play-connect": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=3,
        blocker="CarPlay 授权门槛",
        note="需求 3：把手机变车载仪表盘，是有车一族的痒点，但被原厂车机与高德/百度车机版覆盖了大半。"
             "付费 3：$29.99 买断，704 个订阅说明低价买断能起量。合规 3："
             "行车中显示内容涉驾驶安全，国内对车机应用有专门要求。获客 2：只能靠 App Store 与"
             "车友论坛，安卓车机生态完全不通用。本地化 4：功能与国内生态无关，改的是上架渠道与车机适配。"
             "竞争 3：国内车机市场被高德、百度、华为占据，独立开发者的位置在「老车改造」"
             "这一小块。它月流水从 $5,789 掉到 $477。",
    ),
    "carry-save-chatgpt-claude-grok-to-markdown-automatically": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker="受众绑海外聊天工具",
        note="需求 3：把散在各处的对话沉淀成本地笔记，是重度用户才有的痛，人群不大但很硬。"
             "付费 3：$59 终身买断，一次性决策，靠信任成交。合规 4：只处理用户自己的对话数据，"
             "不出境、不生成内容。获客 2：它的用户就是 ChatGPT/Claude 的重度使用者，"
             "国内这批人少，且渠道在海外社区。本地化 4：接豆包、Kimi、DeepSeek 的导出接口即"
             "可，工程量小。竞争 3：Obsidian 插件生态里有免费方案。它的累计收入几乎等于近 "
             "30 天，说明还在起量早期，别当成熟案例讲。",
    ),
    "casa-fran-concepts-llc": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 LinkedIn",
        note="需求 3：创始人要做个人品牌，国内需求在公众号与视频号。付费 4：$499/月一档，"
             "2 个客户就够。合规 4：只发自己的内容。获客 2：靠人脉与案例，不可投放。"
             "本地化 1：整个价值绑在 LinkedIn 的算法与人群上，国内没有对应物（公众号生态完全不"
             "同）。竞争 2：国内代运营多。它月流水从 $900 掉到 $2，客户全跑了——单客户依赖是这"
             "类服务最脆的地方。",
    ),
    "castplus": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="播客生态国内另成体系",
        note="需求 3：播客托管是稳定的小众需求，国内有小宇宙、喜马拉雅。付费 3："
             "$15–75/月，57 个订阅 $2,065。合规 3：音频内容要审核，"
             "涉版权音乐更麻烦。获客 2：靠播客社群与口碑，量小。本地化 2：国内分发要通过喜马拉雅/小宇"
             "宙/网易云，RSS 生态不成立。竞争 2：国内托管服务已有。它月流水从 $4,021 掉到 "
             "$359。",
    ),
    "catalister": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="绑 Shopify",
        note="需求 4：铺货与上架是电商运营最烦的活，国内（1688→淘宝、拼多多）"
             "更痛。付费 3：€14.99–59.99 四档，近 30 天 $1,382。"
             "合规 3：批量采集商品信息与图片涉版权与平台规则。获客 2：只能靠 Shopify 应用市场，"
             "渠道被平台卡死。本地化 2：国内是淘宝/拼多多/抖音的接口，且多数不开放，"
             "只能做 RPA。竞争 2：国内有店小秘、芒果店长这类成熟工具。它月流水从 $21,534 掉"
             "到 $1,294，平台一变就掉。",
    ),
    "cattus-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费聚合站已泛滥",
        note="需求 3：多模型切换省时间，是重度用户的便利需求。付费 3：16–25 欧/月，"
             "32 个订阅 $595。合规 3：聚合境外模型涉数据出境，国内还要求生成式服务备案。"
             "获客 2：靠社群与插件。本地化 3：换成国内模型即可。竞争 1：免费聚合站和官方 App 已"
             "经把这层需求吃平。它月流水从 $2,330 掉到 $324，说明「聚合」"
             "本身没有护城河。",
    ),
    "chatdash-llc": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="白标需要技术支持",
        note="需求 4：代理商要自己的品牌做 AI 客服，这个需求在国内同样成立，"
             "且愿意按 $175 的客单付。付费 4：102 个订阅撑起 $17,935 MRR，"
             "说明白标这个价位是成立的。合规 3：客服对话涉个人信息，还要过运营商的通信管理要求。"
             "获客 3：卖给代理商要靠渠道与人脉，冷启动慢；但一个代理商能带几十个终端客户。"
             "本地化 3：要接企业微信、公众号、抖音私信，接口与合规都得重做。竞争 2："
             "国内 SCRM 与智能客服厂商已经在做白标。它月流水从 $50,323 掉到 $5,691，"
             "跌幅 89%，要警惕平台政策变化带来的连带崩塌。",
    ),
    "chateasy": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 LINE 生态",
        note="需求 4：东南亚商家的全渠道客服是刚需，国内对应的是企微 + 抖音私信。"
             "付费 3：฿299–2499 三档，但近 30 天只有 $58.95，"
             "几乎没有收入。合规 4：客服内容合规，注意个人信息。获客 2：靠本地代理，"
             "不可跨市场复制。本地化 1：整套逻辑绑在 LINE 官方账号上，国内没有 LINE。"
             "竞争 2：国内企微服务商已成型。它月流水从 $6,690 掉到 $236，"
             "是「绑单一平台」的典型崩塌。",
    ),
    "chatium": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="平台层被钉钉企微占死",
        note="需求 3：企业要把零散系统整合，需求真实但决策慢。付费 3：非订阅，"
             "近 30 天 $698，几乎没跑起来。合规 4：企业自有数据。获客 2："
             "靠渠道与售前。本地化 1：国内这个位置是钉钉、企微、飞书的开放平台，"
             "第三方做平台层没有生存空间。竞争 2：低代码厂商多。它月流水从 $2,740 掉到 "
             "$391。",
    ),
    "chatwith": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="大厂免费方案",
        note="需求 4：把官网文档变成问答机器人，是企业的标准需求。付费 3：$9–399 四档，"
             "但客户心理价位被免费方案拉低。合规 3：要处理企业文档与用户对话，涉个人信息与内容安全审核。"
             "获客 3：靠模板与案例，SaaS 目录能带量。本地化 3：接国内模型与微信/企微入口，"
             "工程活。竞争 1：扣子、Dify、腾讯云智能体开发平台都在免费送这个能力，"
             "独立产品几乎没有位置。它月流水从 $13,185 掉到 $1,144，"
             "正是被免费方案挤掉的样子。",
    ),
    "check-position": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=2, competition=2,
        blocker="抓取源要换国内搜索引擎",
        note="需求 4：排名监控在国内是成熟采购项，SEO 团队按关键词付费已经形成习惯。"
             "付费 4：$15/月一档，线上自助，预算从市场费用出。合规 3：抓第三方结果页本身处在灰区，"
             "换百度/360 源还要面对反爬与授权问题。获客 4：SEO 人群高度集中，"
             "搜索与社群能直达。本地化 2：关键词库、结果页解析、排名口径全要重写，"
             "等于重做数据层。竞争 2：5118、爱站、站长之家已把这个位置占住。"
             "另注意它的月流水从峰值 $6,510 掉到最近一个月 $210，讲案例时要写清「绑单一搜索源」"
             "这个死穴。",
    ),
    "clairon-ai": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道已拥挤",
        note="需求 3：B2B 对「AI 里有没有我」的关注还在教育期。付费 4："
             "$49–249 三档，客单不错。合规 3：只做监测与内容建议，风险可控。"
             "获客 3：靠内容营销与冷邮件，B2B 周期长。本地化 3：接国内模型答案源。"
             "竞争 2：同赛道产品太多，功能差异小。它月流水从 $3,067 掉到 $99，"
             "6 个订阅，几乎归零——这是 GEO 工具过剩的直接证据。",
    ),
    "clarity-search-ai-inc": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 拥挤",
        note="需求 4：AI 搜索里的品牌可见度已成为市场部议题。付费 4：客单 $611，"
             "只有 4 个订阅就撑起 $2,446——高价低量，适合咨询式销售。合规 3："
             "监测低风险，但「优化 AI 推荐」的承诺容易越界。获客 3：靠创始人销售与行业案例。"
             "本地化 3：国内模型答案源不同，评测要重做。竞争 2：GEO 工具扎堆。"
             "它的模型说明了另一条路：不拼客户数，只服务少数高价客户，一个人也能做。",
    ),
    "claude-cowork-course": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="课程赛道极度内卷",
        note="需求 4：AI 工具课在国内卖得比海外还凶，焦虑就是需求。付费 4："
             "$840/月流水，客单几百元，属于典型知识付费。合规 2：课程承诺「学完能赚钱」"
             "会踩广告法与教育培训监管，虚拟课程还有预付费退费争议。获客 3：靠 SEO 与免费课引流，"
             "这套在国内同样有效。本地化 3：换成本地工具与案例即可。竞争 1：国内 AI 课已经卷到 "
             "9.9 元。它真正卖的是邮件列表与流量，不是课程内容——要抄就抄引流结构，"
             "别抄课。",
    ),
    "claw-mart": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态",
        note="需求 2：买现成的提示词与工作流，需求有但价值感弱。付费 3：$1–99，"
             "53 个订阅 $1,060，靠低价走量。合规 4：卖的是配置文件，风险低。"
             "获客 2：只能寄生在 OpenClaw 社区。本地化 2：生态不在国内，"
             "等于没有用户。竞争 2：提示词市场普遍免费。它月流水从 $107,721 掉到 $568，"
             "跌了 99.5%——寄生型生意的典型曲线。",
    ),
    "clawdi": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="通用助手免费",
        note="需求 3：担心 AI 代理动本地文件的人需要隔离环境，需求存在但小众。"
             "付费 3：$29–99/月，64 个订阅 $1,948。合规 4：只提供运行环境，"
             "风险在客户跑什么。获客 2：靠开发者社区，量小。本地化 3：接国内云主机即可。"
             "竞争 1：大厂云端沙箱免费送，独立产品没有位置。它月流水从 $7,523 掉到 $1,"
             "053。",
    ),
    "clawhost": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态",
        note="需求 2：特定框架的托管，用户池就是框架的用户池。付费 3：$10/月，"
             "12 个订阅 $375，价格低到没有利润空间。合规 4：注意备案与内容责任。"
             "获客 2：寄生社区。本地化 2：生态不在国内。竞争 2：同类托管商多。"
             "它月流水从 $1,817 掉到 $190，$10 定价撑不住运维。",
    ),
    "clawhosters-com": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态",
        note="需求 2：给一个特定框架做托管，用户群被框架大小决定，很窄。付费 3："
             "€19–59/月，24 个订阅 $736。合规 4：托管服务本身合规，"
             "注意备案与内容责任。获客 2：寄生在框架社区。本地化 2：生态不在国内，"
             "国内云厂商也提供类似托管。竞争 2：同类托管商多。它月流水从 $4,341 掉到 $232。",
    ),
    "clawrapid": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态",
        note="需求 2：特定框架的托管，池子很小。付费 3：$45/月，6 个订阅 $302。"
             "合规 4：注意备案与内容责任。获客 2：寄生社区。本地化 2：生态不在国内。"
             "竞争 2：同类多。它月流水从 $1,321 掉到 $101——$45 × 6 户，"
             "说明这个生意连一个人的工资都不够。",
    ),
    "cleopatra-ai": dict(
        demand=2, payment=3, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="涉医疗美容边界",
        note="需求 2：术前模拟是真需求，但决策人群小且敏感。付费 3：年 $29.99 / 周 "
             "$5.99，35 个订阅 $496。合规 2：医美模拟涉医疗器械与诊疗边界，"
             "宣传「效果」在国内属于医疗广告监管范围，还要保护人脸信息。获客 2："
             "只能靠医美机构渠道，投放受限。本地化 3：换中文与亚洲面部数据。竞争 2："
             "国内医美机构自有模拟工具。它月流水从 $867 掉到 $186。",
    ),
    "clipspal": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="代运营不可复制",
        note="需求 4：老板要短视频但不想学剪辑，代运营需求在国内更大。付费 4："
             "$499/月起，近 30 天 $1,167，客单高、现金流好。合规 3："
             "内容版权与平台原创度判定是主要风险。获客 3：靠案例与转介绍，本地商家一圈一圈扩。"
             "本地化 3：换国内平台与语言即可，方法通用。竞争 2：国内代运营团队极多且价格战。"
             "它的启示：这是人力生意，规模由人决定，一个人做 3–5 个客户就到顶。",
    ),
    "cnvs": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="编码工具有大厂免费",
        note="需求 3：开发者愿意为提效工具付费，但国内付费意愿低于海外。付费 3："
             "$199 一次性，近 30 天 $2,786，累计 $50,782。"
             "合规 3：生成代码的授权与安全责任要客户自担。获客 2：靠开发者社区与内容，"
             "冷启动慢。本地化 3：接国内模型与 IDE 插件市场。竞争 2：Cursor、"
             "通义灵码把注意力吸走。它月流水从 $23,718 掉到 $1,990，"
             "买断制没有复购是硬伤。",
    ),
    "codefast": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="课程内卷",
        note="需求 4：教人用 AI 快速做出产品，是当下最热的课程题材。付费 4："
             "$169–299 一次性，近 30 天 $3,536，客单高。合规 2："
             "承诺「学完能赚钱」踩广告法；编程课还涉教育培训资质与退费争议。获客 3："
             "靠创始人个人品牌与免费内容引流，Marc Lou 的模式不可复制到无名者身上。"
             "本地化 3：换中文案例与国内技术栈。竞争 1：国内课程价格战到 9.9 元。"
             "要抄的是「课程 + 工具」捆绑，不是课程本身。",
    ),
    "codowave": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="大厂编码代理已覆盖",
        note="需求 3：Issue 自动变 PR，是团队想要的提效。付费 3：客单 $166，"
             "只有 3 个订阅 $498。合规 3：代码授权与安全扫描责任要写清。"
             "获客 2：靠 GitHub 市场。本地化 3：接国内代码托管与模型。"
             "竞争 2：Cursor、通义灵码、Copilot 都在做。它的样本意义是："
             "3 个客户就能撑一个产品，但也止步于此。",
    ),
    "cole-striler": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=3, competition=1,
        blocker="音乐版权与免费竞品",
        note="需求 3：音乐人要灵感起点，需求真实但被免费工具覆盖。付费 3：$16.04 客单，"
             "125 个订阅 $2,005。合规 2：训练数据与生成结果的版权归属在国内有争议，"
             "商用授权要写清。获客 2：靠音乐人社群。本地化 3：中文界面即可。竞争 1："
             "Suno、网易天音免费额度大。它月流水从 $6,548 掉到 $1,088。",
    ),
    "coloring-book": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker="",
        note="需求 3：亲子手工与幼教场景在国内有真实使用，但付费意愿被「免费打印模板」"
             "压着。付费 3：周订阅 $4.99 靠冲动，年费 $19.99 拉留存。"
             "合规 4：处理的是家庭照片（含儿童），要注意未成年人信息保护，但不出境、"
             "不公开就还好。获客 2：低客单 App 只能靠榜单与妈妈群推荐，投放 ROI 差。"
             "本地化 4：换中文文案与国内打印尺寸即可。竞争 3：国内有免费涂色模板站与小红书素材，"
             "工具位没被强产品占住，但需求也没被验证。",
    ),
    "comic-translator": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="版权风险",
        note="需求 4：看外文漫画要翻译，是汉化组的真实工作，需求刚性。付费 3："
             "$5.99–13.99/月，123 个订阅 $929。合规 2：翻译的对象几乎都是未授权作品，"
             "工具等于给侵权加速，在国内属明确风险。获客 3：汉化与二次元社群传播极快。"
             "本地化 2：要接日漫/韩漫的排版与竖排识别，中文嵌字还要处理气泡变形。"
             "竞争 2：国内有汉化工具但是灰产形态。这条只能当技术参考（竖排 OCR + 气泡重排），"
             "产品不宜做。",
    ),
    "comicsai": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="生成内容标识",
        note="需求 3：短漫与条漫创作者要提效，国内有需求但付费能力弱。付费 3："
             "$12.63 客单，27 个订阅 $341。合规 2：AI 生成图像在国内要标识，"
             "且画风模仿在版权上风险高。获客 3：靠创作者社群。本地化 3：换中文与国内画风偏好。"
             "竞争 2：国内 AI 绘画工具极多。它的客单与体量说明：单点 AI 绘画工具很难独立成立。",
    ),
    "conecta-fitness": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="双边撮合需运营",
        note="需求 3：找私教的需求真实，但国内已被 Keep、超级猩猩与美团覆盖。"
             "付费 4：按课时抽成，客单 $9.35，75 个订阅 $701。合规 4："
             "撮合本身合规，教练资质与预付课费是风险点。获客 3：双边冷启动极难，"
             "一边补贴一边拉。本地化 2：国内要先解决教练资质与预付资金监管。竞争 1："
             "大平台把供需两端都占住了。这条不适合一个人做，双边运营是团队活。",
    ),
    "connor-cedro-seo-tampa-seo-consultant": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="个人服务不可扩",
        note="需求 3：本地商家要 SEO，需求一直在，但国内更认「效果付费」而不是月费。"
             "付费 4：$1,250/月起、10 个客户撑 $6,196，单价高、"
             "现金流稳。合规 4：SEO 咨询本身不涉监管，风险在承诺排名。获客 2："
             "靠个人品牌与本地口碑，不可复制也不可投放到别处。本地化 3：换成百度/小红书/抖音本地生活即"
             "可，方法通用。竞争 2：国内 SEO 服务商极多且价格战。这条只借方法："
             "把「Senior 亲自做」卖成溢价，是个人服务唯一能立住的差异。",
    ),
    "contextbolt": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="绑海外 AI 工具生态",
        note="需求 3：AI 编程工具要外部数据，是重度用户的痛。付费 3：$35–39/月，"
             "MRR 只有 $891。合规 4：只读公开网页与用户自己的书签。获客 2："
             "靠插件市场与开发者社区，国内用户用 Claude/Cursor 的比例低。"
             "本地化 2：要改接通义灵码、Trae 这类国内工具，生态完全不同。竞争 2："
             "同类 MCP 连接器大量免费。它的形态对：只做「一个数据源 + 一个协议」。",
    ),
    "controlresell": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="绑法国转售平台",
        note="需求 3：法国二手卖家的合规账本是硬需求（Vinted 等平台的税务要求）。"
             "付费 4：档案自述 2999–11999 欧四档，但按 449 个订阅与 $21,606 "
             "MRR 反推客单约 $48，原数字疑为单位错误，引用前要回源。合规 3："
             "涉交易与税务数据。获客 2：靠平台卖家社群。本地化 1：国内二手是闲鱼/转转，"
             "账本与税务逻辑不同。竞争 2：国内有电商 ERP 但没做这块。",
    ),
    "convertlabs": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="本地实施重",
        note="需求 4：本地服务商（家政、清洁、维修）要线上接单与调度，国内同样有，"
             "且愿意付 $67–397/月。付费 4：477 个订阅撑 $28,579，"
             "说明这个价位是市场价。合规 3：客户与订单涉个人信息，营销短信要守通信管理。"
             "获客 2：只能地推 + 行业口碑，一个城市一个城市啃，这是它的护城河也是它长不快的原因。"
             "本地化 2：要接微信预约、微信支付、电子发票与本地生活平台。竞争 2："
             "国内有赞、微盟与垂直家政 SaaS 已占位。它的启示是：本地 B2B 值钱，"
             "但慢，一个人做不了。",
    ),
    "convitede": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="小程序竞品多",
        note="需求 4：电子请柬在国内是刚需，婚礼、寿宴、满月酒都要用，且愿意为「好看」"
             "付费。付费 4：373 个订阅撑起 $1,148，单份价格低但量大。"
             "合规 3：电子请柬要收集宾客信息与礼金记录，涉个人信息；营销短信还要守通信管理规定。"
             "获客 3：靠婚礼纪这类平台与本地婚庆渠道，冷启动靠地推。本地化 2："
             "国内要改成小程序形态、接微信分享与微信支付，等于重做载体。竞争 2："
             "婚礼纪、各种小程序模板商已经把这个位置占满。它的形态可借，产品不好直接搬。",
    ),
    "cosden-code": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费教程覆盖",
        note="需求 4：学 React 的人多，愿意为「有人答疑」付费的也不少。付费 4："
             "$23–47/月，57 个订阅 $1,380，订阅制学习产品能跑。合规 3："
             "教育培训与预付退费是风险点，不涉资质。获客 2：靠个人品牌与内容，国内同类账号已饱和。"
             "本地化 3：换中文教程与技术栈。竞争 1：B 站免费教程 + 掘金文章把「学前端」"
             "这件事的付费意愿压到极低。它月流水从 $7,516 掉到 $128，"
             "跌了 98%。",
    ),
    "coupleai": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="情感陪伴强监管",
        note="需求 4：情感陪伴是真实的大需求，国内用户愿意付费。付费 3：$7.99 月 / "
             "$39.99 年，145 个订阅 $2,923。合规 1：AI 情感陪伴在国内属强监管地带—"
             "—内容审核、未成年人保护、深度合成标识三重要求，且「AI 恋人」类产品多次被约谈下架。"
             "获客 2：只能靠 ASO 与软文，投放渠道对这类产品关闭。本地化 3："
             "换中文语料即可，技术上不难。竞争 2：国内同类产品多但都活得谨慎。这条建议只看形态，"
             "不要碰合规。",
    ),
    "cramberry": dict(
        demand=4, payment=3, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="学科培训监管",
        note="需求 4：考前速成是学生刚需，国内更旺。付费 3：$4.99 周 / $14.99 月，"
             "61 个订阅 $881。合规 1：面向中小学的学科类培训在国内属严格监管范围，"
             "AI 作业辅导已被明确纳入管理，个人做这个产品风险极高。获客 3：靠校园与内容号传播。"
             "本地化 2：要按国内教材与考纲重做题库。竞争 2：作业帮、猿辅导占满。"
             "这条只可看形态，不建议在国内做。",
    ),
    "crawlbyte": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="数据抓取合规",
        note="需求 4：B2B 要结构化网页数据，需求真实且付费能力强。付费 4："
             "按量 $0.005/任务，4 个订阅 $1,246，客单高。合规 1："
             "绕过 CAPTCHA 与封禁抓第三方数据，在国内直接踩数据安全与不正当竞争，"
             "这是产品核心能力也是最大风险。获客 2：靠 B2B 销售。本地化 2："
             "国内目标站点反爬更强，客户也更怕合规风险。竞争 2：国内有八爪鱼等。"
             "它的定价方式（按成功任务计费）值得学。",
    ),
    "creads": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="同质化",
        note="需求 3：小商家要持续发内容，需求真实但差异化很难。付费 3：€21/月 + API 用量，"
             "17 个订阅 $593。合规 4：内容生成要注意各平台的低质内容规则。"
             "获客 2：靠代理渠道，量小。本地化 2：要接国内平台与中文语气。竞争 2："
             "同质化严重，工具与代写都很多。它证明了一件事：没有独占数据的社媒工具，"
             "只剩价格战。",
    ),
    "creativeclaw": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="",
        note="需求 4：创意团队要在多个模型之间比较与选品，这是真实工作流痛点。付费 4："
             "credits 计价（$10 = 1000 credits），220 个订阅 $11,"
             "590，付费逻辑顺。合规 3：生成内容的版权与商用授权要客户自己担，"
             "平台要给提示。获客 2：团队采购靠销售与案例，冷启动慢。本地化 3："
             "接国内模型与素材库，工程活。竞争 2：国内有类似聚合平台。它的价值在「按 credits 结"
             "算」这个定价结构，团队客户认这个。",
    ),
    "creator-creator": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=2,
        blocker="写作工具免费替代多",
        note="需求 3：写作辅助是普遍需求，但国内用户已经习惯用豆包、Kimi 免费解决。"
             "付费 3：$4.99 周 / $9.99 月，75 个订阅每月 $664，"
             "靠低价内购。合规 4：只生成文本，风险在内容审核与抄袭。获客 2：靠 App Store 榜"
             "单与 ASO，国内安卓渠道另说。本地化 4：换中文模型与输入法习惯即可。"
             "竞争 2：免费替代太多，付费墙很难立。它月流水从 $1,803 掉到 $314，"
             "正是被免费大模型挤压的结果。",
    ),
    "creatorskit": dict(
        demand=3, payment=3, compliance=4, acquisition=1, localization=2, competition=3,
        blocker="绑 TikTok 创作者生态",
        note="需求 3：UGC 创作者要可验证的数据背书，需求在品牌投放侧更强。付费 3："
             "$9–19/月，27 个订阅 $253。合规 4：只展示公开数据。获客 1："
             "全部依赖 TikTok 生态与官方合作，离开平台就没有流量。本地化 2："
             "国内对应的是抖音星图与小红书蒲公英，官方已把撮合做成闭环。竞争 3："
             "没有直接竞品，因为位置被平台占了。它月流水从 $4,481 掉到 $210。",
    ),
    "dewstack": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="协作文档被大厂占位",
        note="需求 3：文档要「自动保持更新」是真实痛点，但客户会先想到飞书、语雀。"
             "付费 3：$39–149 三档，只有 5 个订阅 $508。合规 3："
             "企业文档涉商业秘密。获客 2：靠开发者内容与 SEO。本地化 2：国内文档生态被飞书/语雀/"
             "钉钉占满。竞争 2：同类产品多。它月流水从 $3,315 掉到 $128。",
    ),
    "dreamini-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费聚合站已泛滥",
        note="需求 3：一个地方用所有模型，是重度用户的便利需求。付费 3：$27.36 客单，"
             "25 个订阅 $684。合规 3：聚合境外模型在国内涉数据出境与备案。"
             "获客 2：靠插件与社群。本地化 3：换国内模型即可。竞争 1：免费聚合站与官方 App 已经"
             "泛滥。注意它的累计收入几乎等于近 30 天，属刚起量，别当稳定案例。",
    ),
    "elmo": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道拥挤",
        note="需求 4：品牌方对「AI 搜索里有没有我」的焦虑是真实的，预算从内容营销口出。"
             "付费 4：$29/月起，还能自托管免费版做钩子，转化路径设计得干净。"
             "合规 3：只做监测不碰内容生成，风险低；但给「怎么被 AI 推荐」的建议容易滑向操纵排名。"
             "获客 3：客户是市场部，得靠内容与案例教育，投放成本不低。本地化 3："
             "要接豆包、元宝、Kimi 这些国内模型的答案源，评测口径要重建。竞争 2："
             "GEO 工具这一年扎堆出现，国内也有团队在做。它的可取处是自托管免费版换信任——这个结构值得"
             "抄。",
    ),
    "fitcal": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="免费竞品强",
        note="需求 4：减脂人群的卡路里记录是长期需求，国内薄荷健康已经教育过市场。"
             "付费 3：$11.99/月，2,177 个订阅撑 $4,763，订阅习惯成立。"
             "合规 3：涉健康数据与体重信息，属敏感个人信息，采集要单独同意。获客 3："
             "健身与减脂内容号能带量，投放也跑得通。本地化 3：要换中式食物库与外卖菜品数据，"
             "这是最重的一块。竞争 1：薄荷、Keep、各种免费记账 App 把位置占满，"
             "付费墙很难立。它月流水从 $12,239 掉到 $656。",
    ),
    "frontrun": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="数据源在海外",
        note="需求 3：投融资早期情报有价值，但国内用户更依赖人脉与 FA。付费 3："
             "$49–99/月，99 个订阅 $8,164，客单 $82。合规 2："
             "抓取工商与融资数据并转售，涉数据来源合法性；「提前知道融资」还可能涉内幕信息。"
             "获客 2：靠 VC 与创业者社群。本地化 2：数据源要换成国内工商与融资数据库，"
             "成本和授权都不同。竞争 3：国内有 IT 桔子等。",
    ),
    "garagelab": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=3, competition=2,
        blocker="",
        note="需求 4：车主想把爱车拍成「大片」，国内玩车人群愿意为这个效果付费。"
             "付费 3：$39 客单，51 个订阅 $1,998，靠一次性冲动撑。"
             "合规 4：只处理车辆照片，不涉人脸与敏感信息。获客 3：车友社群与短视频案例能带量，"
             "投放也跑得通。本地化 3：换中文与国内支付即可，但要适配微信小程序的传播路径。"
             "竞争 2：国内汽车摄影 App 与 AI 滤镜不少。它的形态很轻：一个模型 + 一套模板，"
             "就够一个人做。",
    ),
    "getmentioned": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 拥挤",
        note="需求 4：品牌方开始追「AI 搜索里的可见度」，预算真实。付费 4："
             "89–199 欧按月按提示词数计价，客单高。合规 3：只做监测，风险低；"
             "给「怎么被推荐」的建议容易滑向操纵。获客 3：靠内容与案例教育，B2B 销售周期长。"
             "本地化 3：要接豆包、元宝、DeepSeek 的答案源，评测口径重建。"
             "竞争 2：GEO 工具扎堆。它只有 6 个订阅、月流水从 $4,363 掉到 $775，"
             "说明高端定价还没跑通。",
    ),
    "guidanz-inc": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="需对接企业系统",
        note="需求 3：企业报表自动化是刚需，但国内被帆软、FineBI 这类产品覆盖。"
             "付费 4：客单 $430 的年度许可，只有 3 个订阅就撑 $1,291——高单价低客户数。"
             "合规 4：只做内部数据，风险在企业数据分级。获客 2：只能靠渠道与售前，"
             "周期长。本地化 2：要接用友、金蝶、钉钉的接口与权限模型。竞争 2："
             "国内 BI 厂商已成规模。它的模型（3 个客户养活一个产品）值得注意，"
             "但要有人脉。",
    ),
    "hand-off": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="代运营重交付",
        note="需求 4：美国小微商家要人替他们管电商，国内商家同样需要。付费 4："
             "$29–349 三档，客单 $189，说明卖的是服务不是软件。合规 3："
             "代管店铺涉客户账号与资金，责任界定要清楚。获客 2：靠本地商家社群与转介绍，"
             "不可投放。本地化 2：国内平台规则、发货与售后流程完全不同。竞争 2："
             "国内代运营团队多。它的本质是「一个人服务 6 个客户」，赚得稳但扩不了。",
    ),
    "leadmetrics": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="数据合规 + 要接国内广告平台",
        note="需求 4：投放预算的归因是代理商的刚需，国内更痛（多平台、多头结算）。"
             "付费 4：€49/月起，33 个订阅 $5,183，客单高、留存好。"
             "合规 2：跨平台采集线索与用户标识，直接撞个人信息保护法与数据出境；"
             "「线索买卖」在国内还有专门整治。获客 3：客户是代理商，靠案例与转介绍。"
             "本地化 2：要接巨量、腾讯广告、百度信息流的归因口径，还得过平台的数据接口审核。"
             "竞争 2：国内有 GrowingIO、神策这类成熟产品。这条方法可借，"
             "数据链路不可照搬。",
    ),
    "lina": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="处方药信息合规",
        note="需求 3：GLP-1 用药人群在增长，依从性管理有真实需求。付费 3："
             "$5.99 月 / $39.99 年，126 个订阅 $422，客单 $3.35。"
             "合规 1：涉处方药信息与用药建议，在国内属医疗健康内容强监管，没有资质不能做，"
             "平台也禁止推广处方药。获客 2：只能靠垂类社群，投放受限。本地化 2："
             "要按国内药品与医保体系重做。竞争 3：没有直接竞品，因为门槛在合规不在技术。",
    ),
    "linkerflow": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Webflow",
        note="需求 2：Webflow 站点的内链优化是小众需求，国内几乎没有 Webflow 用户。"
             "付费 3：$29–39/月，$945 MRR。合规 4：只动自己站点内容。"
             "获客 2：靠 Webflow 生态市场。本地化 1：国内建站是 WordPress 与 "
             "SaaS 建站，没有对应生态。竞争 3：没有竞品也没有市场。它说明「生态插件」"
             "这条路只能寄生在成熟平台上。",
    ),
    "lunarbuild": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="项目制不可复制",
        note="需求 4：企业要定制站点，国内需求一直在。付费 4：客单 $753.88，"
             "8 个订阅 $6,031——高价少客户，现金流好。合规 4：只提供开发服务，"
             "风险在客户内容。获客 2：靠人脉与案例，不可投放。本地化 3：换国内技术栈与备案流程。"
             "竞争 1：国内建站外包价格战到几千元。它提醒一件事：$753 的月费能撑起一个人，"
             "但这是外包不是产品，规模随人手线性增长。",
    ),
    "mausbot": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="大厂桌面助手免费",
        note="需求 4：把重复的电脑操作交给 AI，是白领的普遍幻想，2026 年这条赛道被验证过。"
             "付费 3：$29–89/月，126 个订阅 $7,327，能卖动但不算稳。"
             "合规 3：桌面代理要拿屏幕与键鼠权限，等于把整台电脑交出去，企业采购会卡在这一步。"
             "获客 3：靠演示视频与开发者社区，国内也可以走同样路径。本地化 3："
             "要适配 Windows 与国产办公软件。竞争 1：微软、字节、阿里都在送桌面助手，"
             "独立产品的窗口很窄。注意它的累计收入几乎等于近 30 天，2026 年 8 月才上线，"
             "属于刚起量，别当成熟案例。",
    ),
    "mentiondesk": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="同类工具已多",
        note="需求 4：品牌方开始把「AI 推荐里有没有我」列进 KPI，预算真实。"
             "付费 4：$29–349 四档，能按监测提示词数量加价。合规 3：监测本身低风险，"
             "但「怎么让 AI 多提你」的咨询很容易越过内容操纵的线。获客 3：需要案例与内容教育，"
             "客户分散在各行业。本地化 3：要接国内模型的答案源，评测方法得重写。"
             "竞争 2：GEO/SEO 工具扎堆，国内已有团队切进来。它只有 6 个订阅、"
             "月流水从 $1,174 掉到 $87，说明高价档没卖动。",
    ),
    "mind-reach": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="冥想付费习惯弱",
        note="需求 3：睡眠与焦虑是普遍需求，但国内用户习惯用免费音频（喜马拉雅、"
             "B 站）。付费 3：试用 7 天后订阅，212 个订阅 $2,149，"
             "客单 $10 左右。合规 4：冥想内容不涉医疗宣称就没事，一旦说「治疗失眠」"
             "就踩线。获客 2：靠 App Store 与内容号，投放成本高。本地化 3："
             "要换成中文引导音与国内配音。竞争 2：潮汐、Now 冥想已占住。它的做法值得学："
             "把「9 分钟/天」写成可量化的承诺，比讲疗效安全也更好卖。",
    ),
    "nagringa": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=1, competition=2,
        blocker="本地招聘网络难复制",
        note="需求 3：工程师想进外企/远程岗，信息差是真金。付费 3：客单 $10.65，"
             "79 个订阅 $841，低价会员制。合规 4：职位信息与会员资料，注意个人信息保护。"
             "获客 3：靠社群口碑与内容，巴西本地网络是它的全部资产。本地化 1："
             "这套东西的价值就在「本地人脉 + 本地企业名单」，换国家等于从零开始。"
             "竞争 2：国内有大量求职社群。它月流水从 $2,760 掉到 $440。",
    ),
    "parlai": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 WhatsApp",
        note="需求 3：练口语是长期需求，国内更认「和外教对话」。付费 3：€12/月，"
             "79 个订阅 $759。合规 4：只涉学习数据。获客 2：靠 WhatsApp 传播与口碑。"
             "本地化 1：国内用户不在 WhatsApp 上，要改成微信或小程序，"
             "产品逻辑要重搭。竞争 2：国内口语陪练产品多。它月流水从 $1,607 掉到 $386。",
    ),
    "pluma": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="记账 App 极卷",
        note="需求 4：个人记账是长期需求，巴西市场的付费习惯还比国内好。付费 4："
             "214 个订阅 $1,410，订阅制成立。合规 3：财务数据属敏感个人信息，"
             "还要注意不构成投顾。获客 3：靠应用商店与内容号。本地化 2：要接国内银行账单导入、"
             "微信/支付宝账单解析，这是最硬的一块。竞争 1：随手记、鲨鱼记账、各类免费 App 把位置占"
             "满。它的可借点：只做「自动分类」一件事，比大而全的记账 App 好卖。",
    ),
    "rankspot": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="内容平台规则",
        note="需求 4：中小站点要持续内容，按篇付费比养一个编辑便宜，需求真实。付费 4："
             "$3/篇的计价方式很好卖，客单 $81.47 说明客户会批量下单。合规 2："
             "批量 AI 出稿在国内要遵守生成式 AI 标识要求，且各平台都在打击低质 AI 内容，"
             "一旦被判定为机器批量生产，客户站点会被降权——这是模式级风险，不是文案风险。"
             "获客 3：客户是站长与 SEO，社区能直达。本地化 2：要换百度/头条/公众号的发布接口与收"
             "录规则，等于重做分发层。竞争 2：国内有大量 SEO 代写与自动化工具。"
             "它的可取处是「按产出计费」而不是按月，客户算得清账。",
    ),
    "rareui": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="开源付费在国内难",
        note="需求 2：国内开发者习惯用免费开源组件，付费支持的市场很小。付费 3："
             "3 个订阅 $650，客单 $216，靠少数企业客户。合规 4：代码开源，"
             "无数据风险。获客 2：靠 GitHub 与社区，转化靠长期信任。本地化 3："
             "文档中文化即可。竞争 2：Ant Design、Element 这类免费库已占满。"
             "它的可借处是「免费库换信任，付费支持收钱」，但国内这条路极窄。",
    ),
    "rock-identifier": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="低客单识别类靠榜单获客",
        note="需求 2：识石头、识植物、识鸟这类工具在国内是「玩一次就删」的品类，"
             "复购极弱。付费 3：$2.74 的客单靠冲动内购，104 个订阅每月 $372。"
             "合规 5：不采集敏感数据，也不生成内容。获客 2：只能买榜单与投放，"
             "国内安卓商店还有一堆马甲包抢词。本地化 4：换中文矿物数据库即可。竞争 3："
             "国内有「形色」这类识花识物产品做大了，识别类的位置不算空。它月流水从 $1,473 掉到 "
             "$133，正是这个品类的常态。",
    ),
    "sbc-crafter": dict(
        demand=1, payment=2, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="受众在国内极窄",
        note="需求 1：EA FC 的 SBC 工具，国内玩这个模式的人极少。付费 2："
             "€2.99/月，10 个订阅 $360。合规 3：游戏数据使用要注意官方条款。"
             "获客 2：靠游戏社区。本地化 3：换中文即可，但没人用。竞争 3：没有竞品。"
             "它的启示只有一条：游戏工具必须跟着国内有版号的游戏走，否则人群为零。",
    ),
    "screenshot-to-code-whimsyworks-inc": dict(
        demand=4, payment=3, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="海外同类工具强",
        note="需求 4：把设计稿或截图转成能跑的第一版，是前端真实省时点，独立开发者与外包团队都想要。"
             "付费 3：$15–40/月，但同类免费额度多，续费靠惯性。合规 3："
             "生成的代码可能复用他人界面设计，商用要自己把关。获客 4：开发者社区与短视频演示转化极好。"
             "本地化 3：要适配 Vue、小程序、Ant Design 这些国内主流栈，"
             "模型也要能读中文设计稿。竞争 2：v0、Bolt 这类大厂产品把注意力吸走，"
             "国内也有团队做。它的 $4,458 MRR 说明这个位置有钱，但国内打的是生态不是单点。",
    ),
    "selltora": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Etsy/Pinterest",
        note="需求 2：Etsy 卖家要 Pinterest 流量，国内做跨境的人有这个需求但小众。"
             "付费 2：$19–99/月，$517 MRR。合规 4：发自己的商品内容。"
             "获客 2：靠跨境电商社群。本地化 1：国内电商不做 Pinterest。"
             "竞争 3：跨境工具里已有类似功能。它的可借点：把「免费流量」当卖点，"
             "但只对跨境卖家成立。",
    ),
    "social-genius-text-analyzer": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="交友类内容审核",
        note="需求 3：不会聊天的人愿意为「怎么回」付费，需求真实但羞于分享，复购靠焦虑。"
             "付费 3：$6.99 周 / $39.99 年，34 个订阅 $384。"
             "合规 3：涉情感与交友内容，容易被拿去 PUA 或诈骗，平台对这类工具有专门的审核要求。"
             "获客 2：只能靠 ASO 与短视频软广，投放受限。本地化 3：换中文语料即可。"
             "竞争 2：国内情感咨询与话术 App 不少。注意它的累计收入几乎等于近 30 天，"
             "2026 年 8 月上线，属刚起量。",
    ),
    "social-post-flow": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2,
        blocker="多平台发布工具已多",
        note="需求 3：WordPress 站点做多平台分发，需求存在但小众。付费 3："
             "$49–199/年，年付制现金流好，客单低。合规 4：只搬运自己的内容，"
             "不涉数据采集。获客 3：WordPress 生态内的插件目录是天然渠道。"
             "本地化 2：要接公众号、头条、百家号、小红书的发布接口，而这些接口基本不对外开放，"
             "只能走 RPA 或人工。竞争 2：国内有一键分发工具，但都不稳定。它的价值在「年付 + 插件"
             "内获客」这个结构。",
    ),
    "sprinta-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=1, competition=2,
        blocker="绑 WhatsApp",
        note="需求 4：WhatsApp 上的自动销售是拉美与欧洲商家的刚需。付费 4："
             "$59.9–200/月，$5,000+ MRR，价格带能立。合规 3："
             "自动外呼与营销消息在国内受严格限制。获客 3：靠本地服务商。本地化 1："
             "国内没有 WhatsApp，微商与企微的规则完全不同。竞争 2：国内企微 SCRM 已占位。"
             "方法可借（把成交话术产品化），载体不可搬。",
    ),
    "stampsnap": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="集邮人群萎缩",
        note="需求 2：集邮人群在国内老龄化且规模在缩，愿意为 App 付费的更少。"
             "付费 3：$3.68 的客单价靠订阅习惯堆量，104 个订阅撑起 $383。"
             "合规 5：只识别用户拍的照片，不涉版权也不涉个人信息。获客 2：垂类人群只能靠收藏论坛与线下"
             "协会，量小且分散。本地化 4：换成中国邮票目录即可，工程量小。竞争 3："
             "国内没有同类强产品，但也没有需求在等你。这条的价值在「极窄垂类也能养活一个人」"
             "这个形态，不在邮票本身。",
    ),
    "static-forms": dict(
        demand=3, payment=3, compliance=3, acquisition=4, localization=2, competition=2,
        blocker="需适配国内云与备案",
        note="需求 3：静态站加表单是开发者的小痛点，国内也有，但很多人直接用云函数自己写。"
             "付费 3：$6–19/月，127 个订阅 $1,009，价格带很薄。"
             "合规 3：代收表单数据等于托管用户个人信息，涉数据存储与备案主体责任。"
             "获客 4：开发者社区与文档站能直达，SEO 效果好。本地化 2：要接国内域名备案、"
             "短信通道与邮件服务商，等于重建基础设施。竞争 2：国内有金数据、腾讯问卷这类成熟产品，"
             "但都不是开发者向。它的做法值得学：把「一个 API 解决一件事」卖成订阅。",
    ),
    "studio-zero": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="电商图工具已多",
        note="需求 4：小商家要「不用摄影棚也能出商品图」，国内需求比海外更旺。付费 3："
             "$0–30/月，客单 $11.36，低价但量大。合规 3：生成图用于商品页要真实反映商品，"
             "食品与化妆品还有广告法限制。获客 2：靠 App Store 与短视频投放，"
             "国内安卓渠道要另铺。本地化 3：要适配淘宝/拼多多主图规范与白底要求。"
             "竞争 2：国内 AI 商拍产品已经在打价格战，免费额度一个比一个大。",
    ),
    "supascribe": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Substack",
        note="需求 2：Substack 生态里的增长工具，国内没有 Substack。"
             "付费 2：$9–20/月，160 个订阅 $2,433，客单低。合规 4："
             "只处理自己的订阅者数据。获客 2：靠生态内推荐。本地化 1：国内对应的是公众号与知识星球，"
             "官方工具不同。竞争 3：没有竞品也没有市场。它的年付 $249 高档说明「增长服务」"
             "在国内可以卖，但载体要换。",
    ),
    "themedrop": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Shopify 生态",
        note="需求 2：Shopify 卖家要快速出主题，国内对应的是淘宝/抖店装修。"
             "付费 3：€9.99–44.99 三档，$362 MRR。合规 4："
             "只生成页面模板。获客 2：靠 Shopify 应用市场。本地化 1："
             "国内电商装修是平台自带的，第三方空间小。竞争 3：没有强竞品，也没有需求缺口。",
    ),
    "url2pin": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Pinterest",
        note="需求 2：Pinterest 联盟营销在国内几乎没有人做。付费 3："
             "$15.75 客单，53 个订阅 $835。合规 4：只发自己的内容。"
             "获客 2：靠联盟营销社群。本地化 1：国内没有 Pinterest，"
             "对应的小红书有官方商业化路径。竞争 3：没有竞品，因为没有市场。它的启示是："
             "联盟流量生意可以一个人做，但要选对平台。",
    ),
    "velisto": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="房产广告合规",
        note="需求 4：经纪人要持续发内容获客，国内更依赖抖音与视频号。付费 3："
             "$59–249 四档，8 个订阅 $361，起步价偏高。合规 2：房源内容属广告，"
             "学区/价格/面积宣称受严格限制。获客 3：靠门店与行业社群。本地化 2："
             "要接抖音房产内容规范与真实房源核验。竞争 2：国内有同类工具。它 2026 年 8 月才上线，"
             "数据太早，别当验证过的案例。",
    ),
    "vibeling": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="免费竞品极强",
        note="需求 3：背单词是刚需，但国内已经卷了十年。付费 3：$3.92 客单、"
             "102 个订阅 $400，低价内购。合规 4：只涉学习数据。获客 2："
             "靠 App Store 与校园渠道，投放 ROI 差。本地化 3：换词库与考试大纲即可。"
             "竞争 1：百词斩、扇贝、墨墨免费且成熟，独立产品没有位置。它的可借处是「一个人 + 一个垂类"
             "词库」，但要选没人做的语种。",
    ),
    "videototext": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="讯飞等免费额度",
        note="需求 4：音视频转文字是刚需（会议、课程、字幕）。付费 3：$14.67 客单，"
             "49 个订阅 $719。合规 3：转写内容可能涉他人隐私，本地处理反而是加分项。"
             "获客 2：靠应用商店与工具站，量有限。本地化 3：中文识别要用本地模型，"
             "否则准确率不够。竞争 1：讯飞、飞书妙记、剪映都能免费转，独立产品很难立住。"
             "它的唯一差异点是「无广告、本地跑」，这个点在国内反而值钱。",
    ),
    "vincere-dev": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="人力外包不可复制",
        note="需求 4：企业要能随时增减的工程师产能，国内需求更大（外包市场成熟）。"
             "付费 4：$3,200/月一人，1 个客户就够活。合规 4：技术服务本身合规，"
             "注意劳务与社保边界。获客 2：靠人脉与转介绍，不可投放。本地化 2："
             "国内是外包公司与灵活用工平台的地盘。竞争 2：国内人力外包价格战激烈。"
             "它的启示只有一条：把「随时可停」当卖点，比卖工时好卖，但仍然是人力生意。",
    ),
    "volume-booster-louder-sound": dict(
        demand=3, payment=3, compliance=5, acquisition=2, localization=4, competition=2,
        blocker="免费替代多",
        note="需求 3：「手机声音太小」是普遍抱怨，但解决方案太多——外放、蓝牙音箱、"
             "系统设置都能凑合。付费 3：周订阅 $5.99 是典型的冲动定价，靠内购页促成。"
             "合规 5：纯本地音频处理，不采集、不上传，没有任何监管面。获客 2："
             "低客单 App 只能靠 App Store 榜单与 ASO，国内安卓渠道还要另铺。"
             "本地化 4：功能与语言都不依赖本地生态，改的是上架渠道。竞争 2：免费同类应用一抓一把，"
             "它靠订阅习惯而不是技术活着。",
    ),
    "walldecor-io": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=3,
        blocker="家装预览有免费替代",
        note="需求 2：买墙饰前预览效果，是极小众的决策辅助，国内用户更习惯直接问客服要效果图。"
             "付费 3：$29 终身买断，累计只有 $577，说明量很小。合规 4："
             "只处理墙面照片。获客 2：靠 Etsy/Amazon 卖家捆绑，独立获客几乎无门。"
             "本地化 3：换中文与国内电商平台。竞争 3：免费修图工具能凑合。这条只说明一件事："
             "$29 买断 + 极窄场景，一个人能赚点零钱，但做不成生意。",
    ),
    "webclaw": dict(
        demand=3, payment=3, compliance=1, acquisition=4, localization=3, competition=2,
        blocker="抓取合规",
        note="需求 3：AI 代理要网页内容，抓取 API 是标准需求。付费 3："
             "$19–399 五档，9 个订阅 $362，价目表比收入好看。合规 1："
             "批量抓取第三方站点，在国内直接撞《数据安全法》与平台规则，绕过反爬还有刑事风险，"
             "这是硬伤。获客 4：开发者社区与文档站能直达。本地化 3：换国内云与代理池即可。"
             "竞争 2：国内有爬虫 API 服务商但都要资质。它说明：合规是这类工具的生死线，"
             "不是扣分项。",
    ),

    # ── 2026-11-04 守卫拦下 8 条，人工重写后晋升，共 8 条 ──
    "adscriptly": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=3,
        blocker="Google Ads / Meta 投放本身在国内不可用，只能服务出海团队",
        note="需求 3：需求真实但只属于出海投放团队，国内本地投放用不上这套。付费 4："
             "客户按投放预算衡量，$349 一档相当于浪费掉的点击，价格敏感度低。"
             "合规 4：广告投放优化不涉资质，只要不代客户操作账户就干净。获客 2："
             "要靠出海社群与代理商转介，投放平台本身不给流量。本地化 2：能力绑在 Google 与 "
             "Meta 的接口上，换成腾讯广告与巨量要重做一遍。竞争 3：国内投放工具多，"
             "但「把线下成交回填广告平台」这一格人少。",
    ),
    "aera-browser": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="浏览器自动化易触平台反爬与账号风控",
        note="需求 3：跨站重复操作确实多，但用户往往先试现成的 RPA 或脚本。"
             "付费 4：$77 的客单价说明买的是「省一个人」，不是省几分钟。合规 3："
             "跨站抓取与代操作踩目标站条款，做灰产工具会被封，做正经业务要挑场景。"
             "获客 2：靠产品演示视频与技术社区，讲清楚能力门槛高。本地化 3：浏览器与 MCP 生态国内"
             "外一致，换成国内站点要重写连接器。竞争 3：RPA 厂商与企业级方案都在，"
             "个人向的轻量产品少。",
    ),
    "arc-raiders-map": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=3,
        blocker="吃单款游戏生命周期，且地图素材涉游戏版权",
        note="需求 3：单机与联机射击玩家都要查点位图，但只在这一款游戏热的时候成立。"
             "付费 4：$1.88 是随手买的价格，内购解锁不设订阅墙，转化率高。"
             "合规 3：地图与掉落数据来自游戏，用官方素材做商业化有版权灰区，量大了会被要求下架。"
             "获客 3：靠游戏社区、Discord 与攻略站导流，不需要投放。本地化 3："
             "游戏是全球同一版本，工具本身几乎不用改，换的只是支付与分发渠道。竞争 3："
             "攻略站更新慢，工具化产品少，但窗口跟着游戏热度走。",
    ),
    "dropapost": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="多平台自动发布踩各平台的自动化规则，账号风险高",
        note="需求 4：小微企业确实没人专职管社媒，一人公司尤其需要。付费 4：$30 一档不用报销审批，"
             "决策人就是使用者。合规 3：自动发布与自动回评触碰平台自动化条款，做大了容易被封号，"
             "这不是资质问题而是规则问题。获客 2：靠代理商与本地商家圈子，跨境投放无效。"
             "本地化 2：接的是 Instagram、Pinterest、Google，"
             "国内要换成微信、小红书、抖音，接口与内容规则全不一样。竞争 2：国内有矩阵工具，"
             "价格更低，切入点只剩「本地语言加本地节日日历」。",
    ),
    "pageturner": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="有声书版权与内容审核，个人开发者很难自证合规",
        note="需求 3：本地文件播放有稳定小众需求，但国内用户更习惯在线书库而不是自己找文件。"
             "付费 4：$7.99 买断，价格低到不用思考，退款率也低。合规 2："
             "播放用户自带的有声书，一旦内容涉侵权，开发者很难免责；国内上架还要内容审核与版权承诺。"
             "获客 2：靠隐私社区与应用商店长尾搜索，没有投放空间。本地化 3：播放器逻辑通用，"
             "改的是支付与云盘接入。竞争 2：番茄、喜马拉雅用免费内容占住了场景，"
             "本地播放器的用户是刻意逃离版权生态的那批人。",
    ),
    "picoo": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=3,
        blocker="Roblox 在国内的访问与创作者生态都受限",
        note="需求 3：Roblox 创作者确实缺写 Luau 的能力，但国内这批人基数小。"
             "付费 3：订阅加积分包的结构在开发者工具里成立，$19 一档决策快。"
             "合规 4：生成代码与模型，不涉内容资质，平台自有审核。获客 3：靠 Roblox 开发者社区"
             "与教程内容，不需要买量。本地化 2：Roblox 在国内可用性差，同一套能力要换到迷你世界或"
             " Cocos 上重做。竞争 3：通用编程助手能写 Luau，但不懂 Studio 的工程结构。",
    ),
    "stat-ai": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="体育分析变现依赖下注场景，内地没有合法标的",
        note="需求 4：赛前判断是真需求，按赛季复现。付费 3：内购订阅在苹果生态成熟，"
             "但国内用户对分析类订阅的付费意愿低于工具类。合规 2：收入结构挂在体育分析加赔率上，"
             "内地没有合法的体育竞猜标的，做成内容可以、做成产品不行。获客 2：靠 App Store 搜"
             "索与赛季投放，买量成本随大赛周期上涨。本地化 2：联赛数据、赔率口径与用户习惯都要重建。"
             "竞争 3：国内体育资讯重，分析工具轻，位置是空的，但空位的原因是合规而不是没人做。",
    ),
    "stivio": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=4, competition=2,
        blocker="国内已有酷家乐、美间这类成熟玩家，且都是免费或低价",
        note="需求 4：装修、房产、电商都要效果图，出图速度直接影响成交。付费 3："
             "$19 一档对个人设计师友好，但国内同类工具有免费额度，付费意愿被压低。"
             "合规 4：生成渲染图不涉内容资质，商用授权由用户自行确认。获客 3："
             "靠设计师社群与效果图样张传播，素材本身就是广告。本地化 4：平面图转效果图这件事没有语言与文"
             "化门槛，接上国内支付就能用。竞争 2：酷家乐、美间已经把家装设计链路吃透，"
             "单点出图工具的生存空间在「不想学软件的人」。",
    ),

    # ── 2026-11-04 404 档案 4 条降级口径入库 ──
    "changelogfy": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2,
        blocker="反馈工具与 GitHub Issues、Lark 文档重叠，付费意愿低",
        note="需求 3：产品经理需要收集反馈，但用免费工具替代容易。付费 4：$30–$50 区间能被小团"
             "队接受。合规 4：无资质要求。获客 3：Product Hunt 与 indie "
             "hacker 社区。本地化 3：工具类无语言障碍。竞争 2：与 GitHub Issues、"
             "飞书文档高度重叠。",
    ),
    "citron-app": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=2,
        blocker="国内小红书/抖音菜谱内容生态与海外不同，且多语言是唯一壁垒",
        note="需求 3：菜谱需求稳定，但短视频菜谱转化率不高。付费 3：App Store 订阅用户的付费"
             "习惯比安卓好。合规 3：菜谱内容无合规风险。获客 2：App Store 获客贵，"
             "依赖社交媒体传播。本地化 4：多语言是核心能力。竞争 2：下厨房、豆果美食等成熟玩家。",
    ),
    "ryn-vpn": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="国内 VPN 业务属电信增值业务，无资质不能做",
        note="需求 4：翻墙与隐私需求稳定存在。付费 3：VPN 用户付费意愿中等，"
             "被免费工具挤压。合规 1：VPN 在国内属电信增值业务，个人开发无资质。"
             "获客 2：App Store 与 Google Play 获客贵。本地化 3："
             "产品本身无语言障碍。竞争 2：Surfshark、NordVPN 等巨头林立。",
    ),
    "ton-piston": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="法国求职入口（France Travail、La Bonne Alternance）不开放给外国开发者",
        note="需求 3：求职是长期刚需，但单价低、频次低。付费 4：学生群体价格敏感，"
             "但 Pro 套餐锚点 $30–$50 可接受。合规 3：求职数据涉及个人隐私，"
             "法国有 RGPD，国内有个人信息保护法。获客 2：法国本土入口被国企垄断，"
             "外国人很难拿到。本地化 2：法国求职市场与国内完全不重叠。竞争 3："
             "AI 简历工具多，但本地化搜索入口是壁垒。",
    ),

    # ── 2026-11-04 sotie 人工确认 still_alive 后入库 ──
    "sotie-ai-best-friend": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="情感陪伴类 AI 在国内有深度合成标识与未成年人保护两道合规要求",
        note="需求 3：陪伴型 AI 需求真实但不是刚需。付费 4：App Store 内购是成熟通道，"
             "62 个订阅说明早期留存成立。合规 2：生成内容涉及深度合成标识要求，"
             "且面向情感倾诉场景，未成年人保护必须前置设计。获客 2：倾诉类产品难做公开投放，"
             "渠道受限。本地化 3：对话无语言障碍，但文化语境与国内差很多。竞争 2："
             "国内已有星野、筑梦岛等成熟玩家。",
    ),

    # ── 2026-11-04 C 档（综合 < 50，别搬）合并，共 22 条 ──
    "100percentmarket": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="字体版权与双边供给",
        note="卡点是字体版权与双边供给。需求 3：按需印刷卖家确实要买可商用字体与素材，"
             "预算真实。付费 3：B 端小额采购走对公或平台支付都通，但客单不高。"
             "合规 2：字体授权链与商用范围界定国内纠纷多，平台要担审核责任。获客 2："
             "它靠海外按需印刷社区起量，国内对应的是电商设计群体，渠道完全不同。本地化 2："
             "授权协议、发票、结算与内容审核全要重建。竞争 2：字由、站酷已经把正版字体分发占住，"
             "个人做不出供应链。",
    ),
    "access-labs-pty-ltd": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="NFT 涉虚拟货币禁令",
        note="卡点是 NFT 涉虚拟货币禁令。需求 2：国内有定制开发需求，但它用的 NFT 获客入口在境"
             "内不可用，需求要整体换赛道。付费 3：项目制对公收款通。合规 1：虚拟货币相关业务在境内被禁，"
             "用 NFT 做入口本身就不合法，连带营销与结算都受限。获客 2：它靠 Web3 圈层与项目"
             "转介绍，国内无对应合规渠道。本地化 1：业务形态、客户群、交付方式全要重建，"
             "等于换一家公司。竞争 3：国内定制开发竞争者极多，这一档没有差异化。",
    ),
    "akari-tickets": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="倒票合规",
        note="卡点是倒票合规。需求 3：热门演出与赛事的转票需求在国内真实存在，愿意买的不少。"
             "付费 3：平台担保交易与对公结算都通，但抽佣模式要看资质。合规 1："
             "票务实名制与倒卖有价票证的违法风险直接压在这条链上，越成功越危险。获客 2："
             "它靠海外票务社区与二级市场渠道起量，国内平台把转售收进官方入口。本地化 2："
             "实名核验、票源核验、履约与退款规则都要重建。竞争 2：大麦、猫眼已把票务入口占住，"
             "二级转售没有放开空间。",
    ),
    "anonymous-bot-for-slack": dict(
        demand=2, payment=2, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="平台与合规双卡",
        note="卡点是平台与合规双卡。需求 2：匿名反馈在团队管理里确有场景，但国内企业更倾向实名可控的工具，"
             "愿意付费的少。付费 2：海外按席位订阅，国内要换对公采购链路。合规 1："
             "企业内匿名发声与内容留痕要求冲突，出事时责任主体不清。获客 1：它靠 Slack 应用目录分"
             "发，国内协作工具是飞书与企微，宿主生态整个换掉。本地化 1：平台接口、"
             "权限模型、审核策略全要重做。竞争 3：国内直接对手少，因为产品形态连同市场一起消失。",
    ),
    "apparate-pty-ltd": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="服务重 + 外呼合规",
        note="卡点是服务重加外呼合规。需求 3：国内 B2B 确实买获客结果，按通话或按线索付费也有人接受。"
             "付费 4：对公合同与开票都顺，客单能撑住。合规 1：外呼是骚扰电话治理重点，"
             "未同意的商业呼叫与号码来源都受严格约束。获客 2：它靠本地企业名录与外呼本身获客，"
             "国内这条链既要数据合法又要线路合规。本地化 2：话术、线路、行业名单、"
             "结算口径全要重做。竞争 2：国内呼叫中心与获客外包成熟，但玩家都在合规框架内，"
             "个人更难进。",
    ),
    "asta": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="保健食品资质",
        note="卡点是保健食品资质。需求 3：自然补剂在国内有真实消费人群，保健需求不缺。"
             "付费 4：电商收款、平台结算都成熟，没有通道问题。合规 1：宣称保健功能要保健食品注册（蓝帽"
             "子），跨境也要走正面清单，个人主体基本拿不到。获客 2：它靠 Shopify 独立站加海外社"
             "媒投放，国内流量集中在平台电商，投放逻辑不同。本地化 1：供应链、注册、"
             "标签、售后全要重建，几乎等于换一门生意。竞争 2：同类补剂品牌已多，"
             "但真正的门槛在资质不在产品。",
    ),
    "berace": dict(
        demand=1, payment=2, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="受众窄 + 涉竞猜",
        note="卡点是受众窄加涉竞猜。需求 1：F1 与 MotoGP 在国内是小众兴趣，"
             "愿意为预测结果付费的人群很薄，且付费动机容易滑向博彩联想。付费 2："
             "App 内购本身通，但一旦被判为竞猜类，支付与上架都会卡。合规 3："
             "纯赛事数据展示不违法，可只要沾竞猜或奖励就变性质，边界窄。获客 2："
             "海外靠赛车社区与赛事直播导流，国内缺同量级社区。本地化 2：赛事版权、"
             "中文术语与推送节奏都要重做。竞争 3：国内直接对手少，但空位来自受众太小。",
    ),
    "bibly": dict(
        demand=1, payment=3, compliance=1, acquisition=1, localization=2, competition=4,
        blocker="宗教内容合规",
        note="卡点是宗教内容合规。需求 1：圣经阅读在国内没有对应的公开付费人群，"
             "内容分发也受限。付费 3：App 内购通道本身可用。合规 1：宗教类内容公开运营受严格管理，"
             "未经批准不得在互联网提供宗教信息服务，主体资质个人拿不到。获客 1："
             "海外靠教会与信仰社区自然传播，国内等价渠道不存在。本地化 2：内容、"
             "语言、社区运营全要重做，而主题本身不可保留。竞争 4：国内几乎没有同类付费产品，"
             "空位来自制度而非市场空白。整条形态不适合上站推广，也不适合当可复制样本。",
    ),
    "book-the-move": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="线索买卖涉个人信息",
        note="卡点是线索买卖涉个人信息。需求 3：搬家与仓储公司确实买线索，但采购方集中在少数区域龙头。"
             "付费 4：对公打款、开票都顺，通道无障碍。合规 2：货本身是有搬家意向的个人信息，"
             "按个人信息保护法要单独同意且来源合法，转售这条链在监管口径里。获客 2："
             "它靠海外本地商家名录加冷邮件铺开，国内同类数据握在地图与生活服务平台手里，"
             "不外流。本地化 2：报价、派单、结算规则全要重做。竞争 2：58、货拉拉已把分发入口占住。",
    ),
    "buzzrush": dict(
        demand=1, payment=3, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="酒类内容合规",
        note="卡点是酒类内容合规。需求 1：派对饮酒游戏在国内既缺对应的聚会文化土壤，"
             "也不适合公开推广，付费人群薄。付费 3：App 内购通道本身通畅。合规 1："
             "以饮酒为主题的内容触到酒类营销限制，面向未成年人分发尤其敏感，上架审核难过。"
             "获客 2：海外靠派对场景与社媒自传播，国内这条传播链会被平台限流。本地化 2："
             "题库、玩法与社交关系链都要重做，但主题本身不能留。竞争 3：同类聚会游戏不多，"
             "空位来自题材受限而非没人做。",
    ),
    "cash-cow-inc": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="外呼营销治理",
        note="卡点是外呼营销治理。需求 3：按结果付费的获客外包在国内需求真实，中小企业愿意试。"
             "付费 3：对公收款没问题，但按效果结算的对账成本高。合规 1：AI 外呼是骚扰治理重点，"
             "语音合成加自动拨打还叠加深度合成标识要求。获客 2：它靠海外本地商家名单与外呼自身获客，"
             "国内号码来源合法性是前置门槛。本地化 1：线路、话术、行业名单、合规留痕全要重做，"
             "且做完仍可能被叫停。竞争 2：国内已有合规外呼与智能客服玩家，个人没有牌照优势。",
    ),
    "classyaction": dict(
        demand=1, payment=2, compliance=1, acquisition=2, localization=1, competition=4,
        blocker="制度不存在",
        note="卡点是制度不存在。需求 1：集体诉讼和解金申报依赖美国法律体系，国内没有对应的可申报池，"
             "用户连要找什么都不存在。付费 2：内购通道本身通，但付费理由不成立。"
             "合规 1：涉及法律信息服务的资质与代理申报边界，个人主体难过。获客 2："
             "海外靠诉讼公告与法务社群导流，国内无等价信息源。本地化 1：整套产品的数据源、"
             "规则与用户动作都要从零设计。竞争 4：国内几乎没有同类产品，空位来自制度差异，"
             "不是市场空白。",
    ),
    "commentions-automated-comments-with-mentions": dict(
        demand=2, payment=3, compliance=2, acquisition=1, localization=1, competition=3,
        blocker="平台反作弊 + 绑 YouTube",
        note="卡点是平台反作弊加绑 YouTube。需求 2：靠评论区曝光引流确有人买，"
             "但这是灰产边缘的需求，规模有限。付费 3：订阅收款本身通。合规 2："
             "批量自动评论与提及属平台明确禁止的自动化行为，账号封禁是常态。获客 1："
             "它整套价值绑在 YouTube 评论区机制上，国内短视频平台的评论生态与开放接口都不同，"
             "获客链直接断。本地化 1：平台、账号体系、反检测策略全要重做，做完同样被治理。"
             "竞争 3：国内同类工具少，是因为做了就封。",
    ),
    "copykitten": dict(
        demand=2, payment=2, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="深度合成合规 + 绑 Discord",
        note="卡点是深度合成合规加绑 Discord。需求 2：语音克隆当作社交道具有真实好奇，"
             "但付费场景薄。付费 2：海外订阅走 Discord 内支付，国内要另建收款与会员体系。"
             "合规 1：语音克隆落进深度合成管理规定，要算法备案、显著标识与被克隆人授权，"
             "个人几乎做不到。获客 1：它整套分发绑在 Discord 机器人目录上，"
             "国内社群生态不同，宿主不存在。本地化 1：平台、支付、合规留痕全要重做。"
             "竞争 3：国内同类少是因为门槛高。可借的只有社交道具这个卖点。",
    ),
    "cosmo": dict(
        demand=1, payment=2, compliance=4, acquisition=1, localization=1, competition=3,
        blocker="绑 Discord",
        note="卡点是绑 Discord。需求 1：Discord 内嵌结账在国内没有对应生态，"
             "社群电商的需求已被微信小店、小程序商城接住，没有独立付费位。付费 2："
             "海外高客单订阅靠 Discord 社群转化，国内这条收款与信任链要另建。"
             "合规 4：纯电商工具本身合规负担小，这是六维里唯一不低的一项。获客 1："
             "它靠 Discord 社群与机器人目录分发，国内无等价入口。本地化 1："
             "整个宿主平台要换，产品等于重做。竞争 3：国内直接对手少，因为需求已被平台原生功能吃掉。",
    ),
    "deeplead-io": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="外展合规",
        note="卡点是外展合规。需求 3：B2B 找客户的痛在国内一样真，销售团队愿意为线索付费。"
             "付费 4：企业订阅对公收款顺畅，代运营档也能开票。合规 1：批量采集企业联系人信息并发冷邮件，"
             "国内受个人信息保护与反垃圾规则双重约束，源头数据合法性是硬伤。获客 2："
             "它靠海外商业数据库与邮件生态起量，国内没有等价的公开联系人库可买。本地化 2："
             "要换数据源、换送达通道、重做话术模板。竞争 2：国内 SCRM 与获客工具已多，"
             "但真正卡住的还是合规。",
    ),
    "esim-panel": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="电信资质 + eSIM 未开放",
        note="卡点是电信资质加 eSIM 未开放。需求 2：出境漫游流量确实有需求，"
             "但国内用户更习惯直接在平台买套餐，白标分销空间小。付费 3：B 端对公结算通。"
             "合规 1：转售流量需要电信业务经营许可与运营商合约，个人主体拿不到；"
             "国内 eSIM 手机端也尚未开放，两头都堵。获客 2：它靠海外旅行社群与分销商起量，"
             "国内渠道被运营商与平台占住。本地化 1：资质、套餐、结算、实名核验全要重做。"
             "竞争 3：直接对手少，但空位来自牌照门槛。",
    ),
    "fill": dict(
        demand=2, payment=3, compliance=1, acquisition=1, localization=2, competition=2,
        blocker="匿名社交强监管",
        note="卡点是匿名社交强监管。需求 2：匿名问答在校园场景有真实好奇，但付费意愿弱、"
             "留存短。付费 3：内购通道通。合规 1：匿名发布在国内属强监管形态，"
             "要实名后台、内容审核与举报处置，个人主体配置不起。获客 1：冷启动高度依赖校园地推与社群自传"
             "播，而这类传播正是平台治理重点。本地化 2：审核策略、社区规则、举报链路都要重建。"
             "竞争 2：国内匿名社交产品多次被下架，留下的空位不是机会。",
    ),
    "jarvis-bet": dict(
        demand=1, payment=1, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="博彩在内地违法",
        note="卡点是博彩在内地违法。需求 1：体育博彩分析在国内没有合法标的，付费入口本身不可用，"
             "需求不成立。付费 1：面向博彩用户的收款通道在国内主体下无法合规开通。"
             "合规 1：为博彩提供分析与引流属明确违法，不是备案或改造能解决。获客 1："
             "它靠博彩社群与推广返佣起量，国内这类推广同样被禁。本地化 1：标的、"
             "用户、结算全要换，换完就不再是这个产品。竞争 3：国内无同类是法律结果，"
             "不是市场空位。整条线不可碰。",
    ),
    "polyalerthub": dict(
        demand=1, payment=1, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="预测市场在境内被禁",
        note="卡点是预测市场在境内被禁。需求 1：Polymarket 本身在境内不可用，"
             "交易者群体不存在，整套产品的标的消失。付费 1：面向境外交易者的收款与合规通道在国内主体下也"
             "走不通。合规 1：预测市场属被禁业务，提供行情与投注分析同样在打击范围内。"
             "获客 1：它靠加密社群与交易者论坛分发，国内无合规渠道。本地化 1："
             "数据源、标的、用户群全要换，换完就不是这个产品。竞争 3：国内没有同类，"
             "是因为业务本身不可做。整条线不可碰。",
    ),
    "replymer": dict(
        demand=2, payment=3, compliance=2, acquisition=1, localization=1, competition=3,
        blocker="平台反自动化",
        note="卡点是平台反自动化。需求 2：自动回帖抢求购线索确有人愿意付钱，但依赖平台默许，"
             "需求不稳。付费 3：订阅收款通道通。合规 2：自动回复 Reddit 与 X 的求购帖属平台"
             "明确禁止的自动化行为，轻则限流重则封号，国内平台同样零容忍。获客 1："
             "目标平台在国内不可用，整套获客场景连同账号体系一起消失。本地化 1："
             "平台、语义识别、回复策略全要重做，做完仍面临反自动化治理。竞争 3："
             "国内同类少，是因为平台不给做。",
    ),
    "torrentclaw": dict(
        demand=3, payment=2, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="盗版与侵权风险",
        note="卡点是盗版与侵权风险。需求 3：磁力聚合搜索确有真实检索需求，但付费集中在少数重度用户。"
             "付费 2：支付通道对这类站点基本不开放，收款本身是问题。合规 1：国内直接落进侵权责任与内容"
             "违规，主体与域名都难保全。获客 2：海外靠搜索引擎与论坛自然流量，国内搜索侧不会给这类词排名。"
             "本地化 2：索引源、质量评分、去重规则都要重做，而资源本身不合法。"
             "竞争 3：同类工具少，但空位来自不敢做，不是没人想到。可借的只有质量评分这个产品思路。",
    ),
}


# ------------------------------------------------------------------ 计算

def load_json(name, default=None):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _orig_newline(path):
    """原文件用哪种换行就写回哪种（红线 23）。

    2026-10-29 实测：`cases.json` 本来是 LF，这里原来的 `open(..., "w")` 没给
    `newline=""`，Windows 文本模式把每一个 `\\n` 翻成 `\\r\\n` —— 42268 行全变、
    diff 全红，而数据一个字没改，极易误判成「算分把案例库搞乱了」。
    harvest.save_json 早就踩过同一个坑并写了长注释，这两个脚本漏了。
    """
    try:
        with open(path, "rb") as f:
            return "\r\n" if b"\r\n" in f.read(4096) else "\n"
    except OSError:
        return "\n"


def save_json(name, payload):
    path = os.path.join(DATA_DIR, name + ".json")
    nl = _orig_newline(path)
    tmp = path + ".tmp"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", nl))
    os.replace(tmp, path)


def weighted_total(dims):
    return round(sum(dims[k] * WEIGHTS[k] for k in WEIGHTS), 1)


def to_score(raw):
    return round(raw / MAX_RAW * 100, 1)


def medal_for(rank):
    return {1: "gold", 2: "silver", 3: "bronze"}.get(rank)


def main():
    ap = argparse.ArgumentParser(description="国内移植可行性评分")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--top", type=int, default=0, help="只打印前 N 名（0 = 全部）")
    args = ap.parse_args()

    cases = load_json("cases", [])
    if not cases:
        print("[!] data/cases.json 为空")
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    missing = []
    scored = []

    for c in cases:
        cid = c.get("id")
        s = SCORES.get(cid)
        if not s:
            missing.append(cid)
            continue
        dims = {k: s[k] for k, _label, _desc in DIMS}
        raw = weighted_total(dims)
        scored.append({
            "case": c,
            "dims": dims,
            "raw": raw,
            "score": to_score(raw),
            "note": _plain(s.get("note", "")),
            "blocker": s.get("blocker"),
        })

    if missing:
        print("[!] 以下案例还没评分（%d 条）：%s" % (len(missing), ", ".join(missing)))
        print("    请在 SCORES 里补上，否则它们的 china_fit 会被清空。\n")

    # 排名：分数降序；同分时把有 blocker 的排后面，再按名称
    scored.sort(key=lambda x: (-x["score"], 1 if x["blocker"] else 0,
                               x["case"].get("name") or ""))

    for i, item in enumerate(scored, 1):
        item["rank"] = i
        item["medal"] = medal_for(i)
        c = item["case"]
        c["china_fit"] = {
            "score": item["score"],
            "raw": item["raw"],
            "max_raw": MAX_RAW,
            "rank": i,
            "medal": item["medal"],
            "dims": item["dims"],
            "weights": WEIGHTS,
            "note": _plain(item["note"]),
            "blocker": item["blocker"],
            "scored_at": today,
        }

    # 没评分的案例：清掉旧分，避免留下过期排名
    for c in cases:
        if c.get("id") in missing:
            c.pop("china_fit", None)

    # --- 打印 ---
    print("=" * 78)
    print("  国内移植可行性排行 · 满分 %.1f → 归一化 100" % MAX_RAW)
    print("  权重：付费意愿 1.5｜支付可达 1.2｜合规空间 1.2｜其余 1.0")
    print("=" * 78)

    label = {1: "🥇 金牌", 2: "🥈 银牌", 3: "🥉 铜牌"}
    shown = scored[:args.top] if args.top > 0 else scored
    for item in shown:
        c = item["case"]
        tag = label.get(item["rank"], "   ")
        flag = "  [硬伤: %s]" % item["blocker"] if item["blocker"] else ""
        print("\n%s  %2d. %-24s %5.1f / 100%s"
              % (tag, item["rank"], c.get("name", "")[:24], item["score"], flag))
        bars = "  ".join("%s %d" % (lab, item["dims"][k]) for k, lab, _d in DIMS)
        print("      " + bars)
        print("      %s" % item["note"])

    if len(shown) < len(scored):
        print("\n  … 其余 %d 条略（--top 0 看全部）" % (len(scored) - len(shown)))

    print("\n" + "=" * 78)
    if scored:
        top3 = scored[:3]
        print("  金银铜：%s" % " / ".join(
            "%s %s" % (n, i["case"].get("name"))
            for n, i in zip(["金", "银", "铜"], top3)))
    print("=" * 78)

    if args.dry_run:
        print("\n--dry-run：未写入 data/cases.json")
        return 0

    save_json("cases", cases)
    print("\n已写入 data/cases.json（%d 条带 china_fit）" % len(scored))
    print("页面刷新即可看到「国内移植排行」视图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
