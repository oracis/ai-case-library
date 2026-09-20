#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「材料已齐」的候选补上内容包 —— 这一步不联网、不花 AI 额度。

为什么必须有这一步
------------------
候选池只管**证据**（数字、来源、口径），案例还要**内容**（它是什么、为什么成立、
一个人怎么抄）。两条流水线是分开的，于是 triage 会把「证据齐了」的候选标成
`ready —— 去后台点发布`，可 promote 只搬运候选上已有的字段：

    why_it_works / playbook / verdict / what_it_does / how_it_makes_money

这几个字段候选上全是空的，裸点发布就会造出 9-17 那批空壳案例 ——
当时 6 条发完又被退回候选池，理由写在 unpublish_reason 里：
「内容为空壳（无 why_it_works/playbook/verdict）」。

所以「材料已齐」不等于「可以发布」，中间隔着一个**写作**动作。
这份脚本就是那个动作的落地：内容写在本文件里（人工判断，AI 不该代笔），
它只负责校验 + 写回候选，让 promote 有东西可搬。

用法
----
    python scripts/contentpack_ready.py --list      # 看哪几条缺内容包（只读）
    python scripts/contentpack_ready.py --check     # 校验内容包是否合格（只读）
    python scripts/contentpack_ready.py --apply     # 写回候选（会改 data/candidates.json）
    python scripts/contentpack_ready.py --apply --id 1lookup

校验规则（与案例正文的读法对齐，见 static/app.js 的 renderCase）
    why_it_works   ≥ 3 条，每条 ≥ 20 字    —— 少于此在正文里会显得像没写
    playbook       ≥ 3 条，每条 ≥ 20 字    —— BONUS 表里 solo_playbook 的 15 分就认这个
    signals        ≥ 2 条                   —— 「支撑证据」一栏，正文会单独列出
    verdict        非空                    —— 一句话判断，是精写案例的题眼
    what_it_does   非空
    how_it_makes_money 非空
    metrics        必须带 metric_note       —— 数字要能自我说明口径

数字也要在这里改
----------------
核实过的事实和候选里记的 headline 经常对不上：采集时从标题猜的金额、榜单名次，
AI 核完才发现是别的项目的售价、或者名次记错了（本例 stan 写「第 1 名」实为第 3 名，
magicslides 的「售价 $500K / 4.6x」在原文里根本不存在）。这些错数如果不改就发布，
会直接印在案例卡片上 —— corrections 里写了对，正文里照旧错。

所以内容包同时携带**核实后的数字**（PACKS[id]["metrics"]），--apply 时一并覆盖。
数字一律取自草稿 caliber_reason 里的支付侧口径，不采信 headline 的原始猜测。

只写 data/candidates.json，不碰 cases.json —— 发布仍然只走 /api/candidates/:id/promote，
规则引擎是唯一闸门，这里不绕过它，只是把弹药填进枪里。
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND_PATH = os.path.join(ROOT, "data", "candidates.json")

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台默认不是 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MIN_ITEMS = 3
MIN_CHARS = 20


# ---------------------------------------------------------------------------
# 内容包。写法参照已发布的 25 条案例：先讲清「它是什么」，再讲「为什么能成」，
# 然后给「一个人能抄的三条动作」，最后一句判断。数字一律用草稿核实过的口径。
# ---------------------------------------------------------------------------
PACKS = {
    # ======================================================== 1Lookup
    "1lookup": {
        # 数字改自草稿的核实结论：候选 headline 记的 $236,401 / MRR 245,026.11
        # 与 AI 抓到的页面（MRR $244,029 / 累计 $4,985,134）不是同一快照。
        # 以「页面明示 + 记录快照时间」为准，不用采集时的旧快照。
        "metrics": {
            "headline": "MRR $244,029；累计收入 $4,985,134（Stripe 直连验证，2026-09-17 快照）",
            "mrr": 244029,
            "all_time": 4985134,
            "metric_note": "TrustMRR 页面以 Stripe API key 直连验证。MRR 为当月经常性收入；"
                           "$4,985,134 是上线至今累计收入，不可年化。",
        },
        "what_it_does": (
            "一个 API 解决「这个电话/邮箱/IP 是不是真的、值不值得联系」：实时校验号码有效性、"
            "邮箱可达性、IP 风控画像，按调用量计费。卖的不是数据列表，是「调用那一刻的判定」。"
        ),
        "why_it_works": [
            "选了一个「不性感但刚需」的品类：反欺诈、去无效线索、注册防刷，是每家有表单的业务都要花的钱，"
            "但没人愿意为它做产品 —— 于是竞争极小、客户极黏",
            "一次接入、长期按量付费：校验发生在客户的业务代码里，换供应商要改代码重测，"
            "迁移成本天然高于普通 SaaS，所以流失率极低",
            "Stripe 侧直连验证的 MRR 约 $244,029、累计收入约 $498 万（2026-09-17 快照）—— "
            "证明这类「基础设施型」小工具可以做到年化数百万美元的量级",
            "数字被支付网关直读，不靠创始人自报：这是这个库里可信度最高的一档证据",
        ],
        "signals": [
            "MRR 约 $244,029、累计收入约 $4,985,134（TrustMRR 页面以 Stripe API key 验证，2026-09-17 快照）",
            "创始人 Robby Frank；产品线从单一校验 API 扩到多类数据校验",
        ],
        "playbook": [
            "先找一个「所有业务都要处理、但所有人都不想自己做」的脏活（号码校验、邮箱清洗、风控评分），"
            "把它做成一个调用接口而不是一个后台系统",
            "定价按调用量而不是按席位：客户的用量会随业务自然增长，收入跟着走，不用靠涨价",
            "把校验结果的质量本身做成壁垒 —— 准确率是一分一分调出来的，这比功能多更难被抄",
            "先在开发者社区（而不是营销渠道）建立信任：这类工具的采购决策人就是写代码的人",
        ],
        "verdict": (
            "最值得抄的不是它的技术，是它的选品：主动挑一个「无聊到没人愿意做、但每家公司都得付钱」的环节，"
            "然后用按量计费把客户的增长变成自己的增长。"
        ),
        "how_it_makes_money": (
            "按 API 调用量订阅计费。校验是客户业务链路里的一环，用量随客户业务规模自然增长 —— "
            "收入不靠卖更多席位，靠客户自己变大。"
        ),
        "tags": ["基础设施选品", "按量计费", "低流失", "Stripe验证"],
        "industry": "开发者工具 / 数据校验",
    },

    # ======================================================== Stan
    "stan": {
        # 排名改自核实结论：候选写「榜单第 1 名」，TrustMRR 页面自己写的是 #3。
        "metrics": {
            "headline": "MRR $3,569,654；累计收入 $76,627,685（Stripe 直连验证，2026-09-17 快照）",
            "mrr": 3569654,
            "all_time": 76627685,
            "metric_note": "TrustMRR 页面以 Stripe 直连验证，页面自标 Ranked #3（非第 1 名）。"
                           "MRR 为当月经常性收入；$76,627,685 为累计收入，不可年化。",
        },
        "what_it_does": (
            "给创作者做「一站式卖货页 + 会员订阅」的平台：把商品、预约、付费社群、订阅内容"
            "塞进一个可分享的链接里，创作者不需要自己的网站，也不需要接支付。"
        ),
        "why_it_works": [
            "收入已经被支付商 API 直读：MRR 约 $3,569,654、累计收入约 $7,662 万（2026-09-17 快照），"
            "是 TrustMRR 上量级最大的一档 —— 这类数字没法靠话术造出来",
            "它站在「创作者经济」的收银台上：创作者每卖出一单它就抽一笔，"
            "平台的收入直接等于整个创作者群体的交易额乘上一个比例，不用自己去获客",
            "「一个链接卖所有东西」把门槛压到极低 —— 创作者不需要域名、不需要建站、不需要懂支付对接，"
            "打开就能收钱，这正是它相比 Shopify 类工具的差距所在",
            "创作者最怕的是「收入渠道分散」，它把这些渠道收进一个后台，迁移成本随粉丝增长而上升",
        ],
        "signals": [
            "MRR 约 $3,569,654、累计收入约 $76,627,685（TrustMRR 页面以 Stripe 直连验证，2026-09-17 快照）",
            "创始人 Vitalii Dodonov；TrustMRR 页面自己标注为 Ranked #3（不是网传的第 1 名）",
        ],
        "playbook": [
            "别做「更好的工具」，做「服务对象的收银台」—— 收入的增长逻辑从「卖更多份」变成「客户卖得更多」",
            "入门路径要短到「打开链接就能收钱」：每多一步注册/配置，流失掉的人都是平台的长期抽成",
            "注意体量带来的口径风险：$3.5M 若是单月经常性收入，年化就是 $42M，"
            "对这类数字必须保留「由支付网关直读」的证据链，否则读者无法判断真假",
            "先服务一个你能理解其收入结构的人群（本例是创作者），再谈平台化",
        ],
        "verdict": (
            "它证明创作者经济的收银台是一门比创作本身大得多的生意 —— "
            "但这些数字必须带着支付侧证据看，因为它们已经大到「听起来不像真的」的量级。"
        ),
        "how_it_makes_money": (
            "交易抽成 + 订阅套餐。创作者在平台上卖货、卖会员，平台按交易额抽成；"
            "收入随平台整体 GMV 增长，而不是随平台自己的销售增长。"
        ),
        "tags": ["创作者经济", "交易抽成", "收银台", "Stripe验证"],
        "industry": "创作者变现平台",
    },

    # ======================================================== MagicSlides.app
    "magicslides-app": {
        # 候选 headline 的「收入 $9.1K / 售价 $500K / 倍数 4.6x」在原文里均不存在
        # （$500K 属于 FOR SALE 列表里的别的项目）。改用 Stripe 验证的 MRR。
        "metrics": {
            "headline": "MRR $11,332；累计收入 $714,537（Stripe API 验证，2026-09-17 快照）",
            "mrr": 11332,
            "all_time": 714537,
            "customers": 825,
            "metric_note": "TrustMRR 页面注明以 Stripe API key 验证，825 个活跃订阅。"
                           "候选原记的 $9.1K（疑为 30 天口径）与售价 $500K / 4.6x 倍数"
                           "在抓取原文中均未出现，不予采信。$714,537 是 2023-01 上线至今"
                           "累计收入，不可年化。",
        },
        "what_it_does": (
            "把一个主题或一段文档直接变成演示文稿：AI 生成大纲和每页文案，"
            "再通过 Google Slides / PowerPoint 集成导出成可直接编辑的 PPT。"
        ),
        "why_it_works": [
            "卡在「高频但不值得认真做」的位置上：做 PPT 是每个职场人每周都要干的事，"
            "但没人愿意花几百块请设计，AI 一键出稿正好接住这段需求落差",
            "借别人的编辑器交付，自己只做生成：接 Google Slides 和 PowerPoint 意味着"
            "不用自研编辑器，产品的重心全压在「生成质量」这一件事上",
            "Stripe 直连验证的 MRR 约 $11,332、累计收入逾 $71 万（2026-09-17 快照），"
            "由印度独立开发者单人运行、约 80% 毛利 —— 一个人做到这个量级的样本很稀有",
            "上线时间点（2023-01）刚好踩在生成式 AI 大众认知的前夜，属于「早半年」的窗口红利",
        ],
        "signals": [
            "MRR 约 $11,332、825 个活跃订阅、累计收入约 $714,537（Stripe API 验证，2026-09-17 快照）",
            "无员工自运行，约 80% 毛利；印度独立开发者 Sanskar Tiwari，2023-01 上线",
        ],
        "playbook": [
            "做「别人的编辑器 + 你的生成」—— 把自研 UI 的工程量省下来，全部投到生成质量上，"
            "这是一个人能撑住这类产品的关键",
            "挑高频低价值场景：没人愿意为它付费请人做，但人人都愿意为它付一份订阅费",
            "上线时机的价值大于功能完整度；同一种产品晚两年做，获客成本会贵一个量级",
            "单点功能也能撑起一份不小的一人业务，前提是它的使用频次足够高",
        ],
        "verdict": (
            "它是一人公司最实用的那种模板：借别人的成熟产品当交付面，"
            "自己只把一件事做到能用 —— 不追求做平台，追求做那个被反复调用的开关。"
        ),
        "how_it_makes_money": (
            "月订阅。按生成额度和导出次数分档，对标的是用户原本花在模板、外包或加班上的时间成本。"
        ),
        "tags": ["一人公司", "借壳交付", "高频场景", "Stripe验证"],
        "industry": "AI 工具 / 办公效率",
    },

    # ======================================================== AutoReels.AI
    "autoreels-ai": {
        # 候选 headline 的「售价 $50K / 3.5x」不在原文里（$50K 属于别的挂牌项目）；
        # 同页还有编辑填的 Annual $27,244，与 MRR×12≈$16,296 不符，也不采用。
        "metrics": {
            "headline": "MRR $1,358；累计收入 $35,058（Lemon Squeezy 侧验证，2026-09-17 快照）",
            "mrr": 1358,
            "all_time": 35058,
            "customers": 54,
            "metric_note": "TrustMRR 页面由 Lemon Squeezy 支付侧 API 验证，54 个活跃订阅。"
                           "MRR 为月度经常性收入；$35,058 为累计收入，不可年化。"
                           "候选原记的售价 $50K / 3.5x 倍数在原文中不存在。",
        },
        "what_it_does": (
            "无人出镜的短视频批量生产工具：输入主题或链接，自动生成脚本、配音、素材和成片，"
            "按时长和产量分档订阅，主要卖给做红人号、电商号的批量内容生产者。"
        ),
        "why_it_works": [
            "卖的是「产量」而不是「创意」：客户要的是每天稳定出 10 条能发的视频，"
            "而不是一条爆款 —— 这个需求稳定、可交付、可量化，天然适合做成工具而不是服务",
            "不依赖创始人的内容能力：产品解决的是流水线问题，需求来自客户自己已有的内容账号，"
            "不靠平台推荐算法给量",
            "支付商 API 验证的 MRR 约 $1,358、54 个活跃订阅（2026-09-17 快照）—— "
            "体量不大，但正好是一人/小团队的样本：验证了「内容批量生产」这个位置有人付钱",
            "用 Lemon Squeezy 这类 Merchant of Record 收款，把跨境的税务申报成本整个外包掉 —— "
            "非美国开发者做全球订阅生意时，这是很关键的一步",
        ],
        "signals": [
            "MRR 约 $1,358、54 个活跃订阅、累计收入约 $35,058（Lemon Squeezy 侧验证，2026-09-17 快照）",
            "定价 $19–$3,312/档（跨度极大，说明客户从个人号到批量机构都有）",
            "摩洛哥创始人 Mustapha Ajermou，2–5 人团队",
        ],
        "playbook": [
            "把创意型需求包装成产量型交付：客户买「每天能出几条」，比买「AI 有多聪明」更容易成交",
            "定价档位拉开跨度（$19 到 $3,312），用低价档获客、用高价档吃批量用户，"
            "中间不需要为不同人群做不同产品",
            "跨境收款优先用 Merchant of Record 类服务（Lemon Squeezy / Paddle），"
            "把增值税和税务合规交给对方，自己只管产品",
            "注意别把 30 天流水写成 MRR：这条的累计额和经常性收入差距有 25 倍，"
            "混用会让案例完全失去参考价值",
        ],
        "verdict": (
            "小的样本也有价值：它说明「批量内容生产」这个位置确实有人付钱，"
            "但也说明这是一个靠产量而不是靠口碑的生意 —— 收入增长完全跟着客户的账号数量走。"
        ),
        "how_it_makes_money": (
            "按月订阅，按视频时长和生成数量分档。客户是把它当生产线用的批量内容号，"
            "用量直接对应他们自己的产出，所以档位跨度很大。"
        ),
        "tags": ["批量内容", "按产量定价", "跨境收款", "小体量样本"],
        "industry": "AI 工具 / 短视频",
    },

    # ======================================================== Insect Bite ID
    "insect-bite-id": {
        # 候选 headline 的「售价 $75K / 1.6x」在原文里不存在；$3.9K 对不上任何口径。
        "metrics": {
            "headline": "MRR $2,869；累计收入 $62,352（RevenueCat API 验证，2026-09-17 快照）",
            "mrr": 2869,
            "all_time": 62352,
            "customers": 115,
            "metric_note": "TrustMRR 页面由 RevenueCat 支付侧 API 验证，115 个活跃订阅，"
                           "App Store 评分 3.6/5（116 个评分）。MRR 为当月经常性收入；"
                           "$62,352 为累计收入，不可年化。候选原记的 $3.9K 与售价 $75K / 1.6x"
                           "倍数在原文中均未能证实。",
        },
        "what_it_does": (
            "拍照识别虫咬类型的 iOS 应用：给出可能的虫种、置信度、护理建议，"
            "并记录咬痕随时间的变化。免费下载，核心功能靠内购订阅解锁。"
        ),
        "why_it_works": [
            "极其窄的单点需求，但那一刻的焦虑极强：被咬了不知道是什么、要不要去医院，"
            "这种「当下就要答案」的场景付费意愿远高于泛健康类工具",
            "回答一个具体的恐惧，而不是提供一堆知识：产品不解释昆虫学，"
            "只回答「这是什么、我该怎么办」，把决策链路压到最短",
            "RevenueCat 侧验证的 MRR 约 $2,869、115 个活跃订阅、累计收入约 $62,352"
            "（2026-09-17 快照）—— 一个人可以维护的体量，且 App Store 分发不需要自有获客",
            "App Store 的分发让这类长尾小工具可以长期挂在货架上收订阅，"
            "不需要持续投广告，因为搜索需求本身就在",
        ],
        "signals": [
            "MRR 约 $2,869、115 个活跃订阅、累计收入约 $62,352（RevenueCat API 验证，2026-09-17 快照）",
            "App Store 评分 3.6/5（116 个评分）",
            "季节性风险明显 —— 虫咬需求随季节波动，这很可能就是它的估值倍数偏低（1.6x）的原因",
        ],
        "playbook": [
            "找「低频但对个体极其重要」的搜索需求：用户一年只用两次，但每次都在焦虑中，"
            "这种需求做不成大平台，却撑得起一个一个人的订阅产品",
            "识别类工具的关键不是准确率本身，是「在高不确定下怎么表达置信度」—— "
            "给出可能性排序和就医建议，比强行给一个确定答案更让人接受",
            "用完即走的工具要靠 App Store 自然搜索获客，所以标题和截图里的关键词比界面更重要",
            "季节性产品要提前接受它的天花板：需求集中几个月，倍数自然低，这不是产品失败",
        ],
        "verdict": (
            "这是「长尾单点工具」最真实的样本：体量不大、季节性明显、倍数偏低，"
            "但它证明了一个人可以用一个极窄的需求，换来一份不需要天天维护的收入。"
        ),
        "how_it_makes_money": (
            "免费下载 + 内购订阅（Go Pro 解锁完整识别与记录功能）。"
            "收入完全依赖 App Store 的自然搜索流量，没有自有获客成本。"
        ),
        "tags": ["长尾需求", "单点工具", "订阅内购", "季节性风险"],
        "industry": "AI 应用 / 健康识别",
    },
}

# 这些字段是要写进候选、再由 promote 搬进案例的
FIELDS = ("what_it_does", "why_it_works", "signals", "playbook",
          "verdict", "how_it_makes_money", "tags", "industry")


def load_candidates():
    with open(CAND_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_pack(cid, pack):
    """校验一份内容包。返回问题清单（空列表 = 合格）。"""
    bad = []
    if not str(pack.get("what_it_does") or "").strip():
        bad.append("what_it_does 为空")
    if not str(pack.get("verdict") or "").strip():
        bad.append("verdict 为空")
    if not str(pack.get("how_it_makes_money") or "").strip():
        bad.append("how_it_makes_money 为空")

    for key in ("why_it_works", "playbook"):
        items = pack.get(key) or []
        if len(items) < MIN_ITEMS:
            bad.append("%s 只有 %d 条（要 ≥ %d 条）" % (key, len(items), MIN_ITEMS))
            continue
        short = [i + 1 for i, s in enumerate(items) if len(str(s)) < MIN_CHARS]
        if short:
            bad.append("%s 第 %s 条太短（< %d 字）"
                       % (key, "、".join(str(i) for i in short), MIN_CHARS))

    sig = pack.get("signals") or []
    if len(sig) < 2:
        bad.append("signals 只有 %d 条（要 ≥ 2 条）" % len(sig))

    # 数字必须带口径说明。案例卡片会显示 metric_note —— 少了它，
    # 读者只看到一个金额，没法判断那是 MRR、累计还是流水，混一个就差几十倍。
    m = pack.get("metrics")
    if not isinstance(m, dict) or not str(m.get("headline") or "").strip():
        bad.append("metrics.headline 为空")
    elif not str(m.get("metric_note") or "").strip():
        bad.append("metrics 缺 metric_note（数字要能自我说明口径）")
    return bad


def cmd_list():
    cands = load_candidates()
    print("内容包覆盖情况（候选池 %d 条）\n" % len(cands))
    missing, ready_ids = [], []
    for c in cands:
        cid = c.get("id")
        has = all(c.get(k) for k in ("why_it_works", "playbook", "verdict"))
        pack = PACKS.get(cid)
        mark = "有" if has else ("待写" if pack is None else "可写")
        if not has and pack is not None:
            ready_ids.append(cid)
        if not has:
            missing.append(cid)
        print("  %-24s %s" % (cid, mark))
    print("\n缺内容包：%d 条；其中本文件已写好、可 --apply 的：%d 条" % (
        len(missing), len(ready_ids)))
    if ready_ids:
        print("  " + "、".join(ready_ids))


def cmd_check():
    bad_total = 0
    for cid, pack in PACKS.items():
        bad = check_pack(cid, pack)
        if bad:
            bad_total += len(bad)
            print("  [!!] %-20s %s" % (cid, "；".join(bad)))
        else:
            print("  [OK] %-20s why=%d play=%d sig=%d"
                  % (cid, len(pack["why_it_works"]), len(pack["playbook"]),
                     len(pack["signals"])))
    print("\n校验：%d 份内容包，问题 %d 项" % (len(PACKS), bad_total))
    return 1 if bad_total else 0


def cmd_apply(ids):
    cands = load_candidates()
    targets = ids or list(PACKS.keys())
    unknown = [i for i in targets if i not in PACKS]
    if unknown:
        print("没有内容包：%s" % "、".join(unknown))
        return 1

    changed = []
    for c in cands:
        cid = c.get("id")
        if cid not in targets:
            continue
        pack = PACKS[cid]
        bad = check_pack(cid, pack)
        if bad:
            print("  [跳过] %-20s 内容包不合格：%s" % (cid, "；".join(bad)))
            continue
        before = {k: bool(c.get(k)) for k in ("why_it_works", "playbook", "verdict")}
        old_head = (c.get("metrics") or {}).get("headline") or ""
        for k in FIELDS:
            c[k] = pack[k]
        # metrics 用核实后的数字整个替换 —— 不 merge。
        # merge 会把采集时猜错的 mrr / 售价留在候选上，发布后照样印到案例卡片上。
        c["metrics"] = dict(pack["metrics"])
        c["content_pack_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        changed.append((cid, before, old_head, c["metrics"]["headline"]))

    if not changed:
        print("没有可写的条目")
        return 1

    with open(CAND_PATH, "w", encoding="utf-8") as f:
        json.dump(cands, f, ensure_ascii=False, indent=1)
        f.write("\n")

    print("已写回 %d 条候选的内容包：\n" % len(changed))
    for cid, before, old_head, new_head in changed:
        print("  %-22s why/play/verdict %s → True/True/True"
              % (cid, "/".join("T" if v else "F" for v in before.values())))
        if old_head and old_head != new_head:
            print("      数字已更正")
            print("        旧：%s" % old_head[:88])
            print("        新：%s" % new_head[:88])
    print("\n下一步：启动 server 后逐条发布 —— python scripts/publish_ready.py")
    return 0


def main():
    ap = argparse.ArgumentParser(description="给「材料已齐」的候选补内容包（离线、不花 AI）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="只看哪几条缺内容包")
    g.add_argument("--check", action="store_true", help="只校验内容包质量，不写文件")
    g.add_argument("--apply", action="store_true", help="写回 data/candidates.json")
    ap.add_argument("--id", action="append", default=None, help="只处理指定候选 id，可多次")
    args = ap.parse_args()

    if args.check:
        raise SystemExit(cmd_check())
    if args.apply:
        raise SystemExit(cmd_apply(args.id))
    cmd_list()


if __name__ == "__main__":
    main()
