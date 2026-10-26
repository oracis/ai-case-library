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
SCORES = {
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
    "private-venture-1": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3, note='收款走 lemon squeezy 等海外网关（国内不可直连，换微信/支付宝要重做支付与对账）。开发者工具/API 语言无关、本地化成本低，国内开发者付费习惯改善，但同类开源/云厂商方案不少。'),
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
}


# ------------------------------------------------------------------ 计算

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
            "note": s.get("note", ""),
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
            "note": item["note"],
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
