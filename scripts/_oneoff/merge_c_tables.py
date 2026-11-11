#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 C 档（综合 < 50，别搬）22 条合并进 score_solo_fit.DELIVERY / score_china_fit.SCORES。

分数一律取 tmp/fit_proposal_2026-11-04.json 的原值，只补写理由文案 ——
B 档那次「写理由时二次判断导致 53 条分数与提案不同」不重演。

用法：python -X utf8 tmp/merge_c_tables.py [--dry]
"""
import ast
import json
import os
import re
import sys
import py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS = ["demand", "payment", "compliance", "acquisition", "localization", "competition"]
PUNCT = "。，；、：」，）"
LABEL = {"demand": "需求", "payment": "付费", "compliance": "合规",
         "acquisition": "获客", "localization": "本地化", "competition": "竞争"}

# 补写后的理由。分数不动，只写文字；数字只取素材原文出现过的，不做换算。
NOTES = {
    "book-the-move": (
        "卖的是线索不是软件：每条搬家需求都要人工核实、跟单、跟搬家公司对账，交付本身就是一支销售团队，"
        "一个人接不住，收入也随跟单人力线性增长。",
        "卡点是线索买卖涉个人信息。需求 3：搬家与仓储公司确实买线索，但采购方集中在少数区域龙头。"
        "付费 4：对公打款、开票都顺，通道无障碍。合规 2：货本身是有搬家意向的个人信息，按个人信息保护法"
        "要单独同意且来源合法，转售这条链在监管口径里。获客 2：它靠海外本地商家名录加冷邮件铺开，"
        "国内同类数据握在地图与生活服务平台手里，不外流。本地化 2：报价、派单、结算规则全要重做。"
        "竞争 2：58、货拉拉已把分发入口占住。"),
    "torrentclaw": (
        "纯搜索工具，用户自助订阅，没有实施环节；负担在索引源要持续维护，链接失效与资源下架都得跟着更新。",
        "卡点是盗版与侵权风险。需求 3：磁力聚合搜索确有真实检索需求，但付费集中在少数重度用户。"
        "付费 2：支付通道对这类站点基本不开放，收款本身是问题。合规 1：国内直接落进侵权责任与内容违规，"
        "主体与域名都难保全。获客 2：海外靠搜索引擎与论坛自然流量，国内搜索侧不会给这类词排名。"
        "本地化 2：索引源、质量评分、去重规则都要重做，而资源本身不合法。"
        "竞争 3：同类工具少，但空位来自不敢做，不是没人想到。可借的只有质量评分这个产品思路。"),
    "100percentmarket": (
        "双边市场，一边要谈字体作者授权、一边要维护按需印刷的供给与结算，上架审核、版权链、分成都要人盯，"
        "不是装完即用的工具。",
        "卡点是字体版权与双边供给。需求 3：按需印刷卖家确实要买可商用字体与素材，预算真实。"
        "付费 3：B 端小额采购走对公或平台支付都通，但客单不高。合规 2：字体授权链与商用范围界定"
        "国内纠纷多，平台要担审核责任。获客 2：它靠海外按需印刷社区起量，国内对应的是电商设计群体，"
        "渠道完全不同。本地化 2：授权协议、发票、结算与内容审核全要重建。"
        "竞争 2：字由、站酷已经把正版字体分发占住，个人做不出供应链。"),
    "deeplead-io": (
        "外展送达率要持续运维：域名预热、发信额度、退信处理都在链条上，另有单价很高的代运营档，"
        "说明产品做不轻、服务比重不低。",
        "卡点是外展合规。需求 3：B2B 找客户的痛在国内一样真，销售团队愿意为线索付费。"
        "付费 4：企业订阅对公收款顺畅，代运营档也能开票。合规 1：批量采集企业联系人信息并发冷邮件，"
        "国内受个人信息保护与反垃圾规则双重约束，源头数据合法性是硬伤。获客 2：它靠海外商业数据库"
        "与邮件生态起量，国内没有等价的公开联系人库可买。本地化 2：要换数据源、换送达通道、重做话术模板。"
        "竞争 2：国内 SCRM 与获客工具已多，但真正卡住的还是合规。"),
    "berace": (
        "App 内自助订阅，装上即用、零实施；长期负担是赛事数据要跟着赛程更新，预测模型与结果页也要维护。",
        "卡点是受众窄加涉竞猜。需求 1：F1 与 MotoGP 在国内是小众兴趣，愿意为预测结果付费的人群很薄，"
        "且付费动机容易滑向博彩联想。付费 2：App 内购本身通，但一旦被判为竞猜类，支付与上架都会卡。"
        "合规 3：纯赛事数据展示不违法，可只要沾竞猜或奖励就变性质，边界窄。获客 2：海外靠赛车社区"
        "与赛事直播导流，国内缺同量级社区。本地化 2：赛事版权、中文术语与推送节奏都要重做。"
        "竞争 3：国内直接对手少，但空位来自受众太小。"),
    "asta": (
        "实物电商，选品、备货、跨境物流、售后与合规注册都是交付本身，软件只占很小一环，"
        "一个人扛不动整条供应链。",
        "卡点是保健食品资质。需求 3：自然补剂在国内有真实消费人群，保健需求不缺。"
        "付费 4：电商收款、平台结算都成熟，没有通道问题。合规 1：宣称保健功能要保健食品注册（蓝帽子），"
        "跨境也要走正面清单，个人主体基本拿不到。获客 2：它靠 Shopify 独立站加海外社媒投放，"
        "国内流量集中在平台电商，投放逻辑不同。本地化 1：供应链、注册、标签、售后全要重建，"
        "几乎等于换一门生意。竞争 2：同类补剂品牌已多，但真正的门槛在资质不在产品。"),
    "apparate-pty-ltd": (
        "按效果付费的 B2B 外呼获客，交付就是一支打电话的团队；高额部署档靠人做 GTM，"
        "规模与人力同步增长，不是可自助的软件。",
        "卡点是服务重加外呼合规。需求 3：国内 B2B 确实买获客结果，按通话或按线索付费也有人接受。"
        "付费 4：对公合同与开票都顺，客单能撑住。合规 1：外呼是骚扰电话治理重点，未同意的商业呼叫"
        "与号码来源都受严格约束。获客 2：它靠本地企业名录与外呼本身获客，国内这条链既要数据合法"
        "又要线路合规。本地化 2：话术、线路、行业名单、结算口径全要重做。"
        "竞争 2：国内呼叫中心与获客外包成熟，但玩家都在合规框架内，个人更难进。"),
    "akari-tickets": (
        "票务转售社区要同时运营买家与卖家、处理履约与纠纷，供需两端都得有人盯，"
        "不是挂上去就自动成交的工具。",
        "卡点是倒票合规。需求 3：热门演出与赛事的转票需求在国内真实存在，愿意买的不少。"
        "付费 3：平台担保交易与对公结算都通，但抽佣模式要看资质。合规 1：票务实名制与倒卖有价票证"
        "的违法风险直接压在这条链上，越成功越危险。获客 2：它靠海外票务社区与二级市场渠道起量，"
        "国内平台把转售收进官方入口。本地化 2：实名核验、票源核验、履约与退款规则都要重建。"
        "竞争 2：大麦、猫眼已把票务入口占住，二级转售没有放开空间。"),
    "cash-cow-inc": (
        "AI 外呼代理含人工运营环节：话术配置、名单清洗、结果复核都要人参与，"
        "按结果付费意味着交付责任在你这边。",
        "卡点是外呼营销治理。需求 3：按结果付费的获客外包在国内需求真实，中小企业愿意试。"
        "付费 3：对公收款没问题，但按效果结算的对账成本高。合规 1：AI 外呼是骚扰治理重点，"
        "语音合成加自动拨打还叠加深度合成标识要求。获客 2：它靠海外本地商家名单与外呼自身获客，"
        "国内号码来源合法性是前置门槛。本地化 1：线路、话术、行业名单、合规留痕全要重做，"
        "且做完仍可能被叫停。竞争 2：国内已有合规外呼与智能客服玩家，个人没有牌照优势。"),
    "buzzrush": (
        "App 内小游戏，下载即用、零交付；负担只在关卡与题库更新，属于最轻的一类 C 端产品。",
        "卡点是酒类内容合规。需求 1：派对饮酒游戏在国内既缺对应的聚会文化土壤，也不适合公开推广，"
        "付费人群薄。付费 3：App 内购通道本身通畅。合规 1：以饮酒为主题的内容触到酒类营销限制，"
        "面向未成年人分发尤其敏感，上架审核难过。获客 2：海外靠派对场景与社媒自传播，"
        "国内这条传播链会被平台限流。本地化 2：题库、玩法与社交关系链都要重做，但主题本身不能留。"
        "竞争 3：同类聚会游戏不多，空位来自题材受限而非没人做。"),
    "commentions-automated-comments-with-mentions": (
        "自助订阅工具，配置好账号即可跑，没有实施环节；但账号被限制后要用户自己处理，"
        "售后压力集中在平台风控上。",
        "卡点是平台反作弊加绑 YouTube。需求 2：靠评论区曝光引流确有人买，但这是灰产边缘的需求，"
        "规模有限。付费 3：订阅收款本身通。合规 2：批量自动评论与提及属平台明确禁止的自动化行为，"
        "账号封禁是常态。获客 1：它整套价值绑在 YouTube 评论区机制上，国内短视频平台的评论生态"
        "与开放接口都不同，获客链直接断。本地化 1：平台、账号体系、反检测策略全要重做，"
        "做完同样被治理。竞争 3：国内同类工具少，是因为做了就封。"),
    "bibly": (
        "App 自助订阅，装上即用、零交付；长期负担是经文版本、阅读计划与内容校对的维护，"
        "属于内容型轻产品。",
        "卡点是宗教内容合规。需求 1：圣经阅读在国内没有对应的公开付费人群，内容分发也受限。"
        "付费 3：App 内购通道本身可用。合规 1：宗教类内容公开运营受严格管理，未经批准不得"
        "在互联网提供宗教信息服务，主体资质个人拿不到。获客 1：海外靠教会与信仰社区自然传播，"
        "国内等价渠道不存在。本地化 2：内容、语言、社区运营全要重做，而主题本身不可保留。"
        "竞争 4：国内几乎没有同类付费产品，空位来自制度而非市场空白。整条形态不适合上站推广，"
        "也不适合当可复制样本。"),
    "replymer": (
        "工具本身自助开通，但要持续对抗平台风控：规则一变就得改匹配与回复策略，用户发不出去"
        "第一个找的是你。",
        "卡点是平台反自动化。需求 2：自动回帖抢求购线索确有人愿意付钱，但依赖平台默许，需求不稳。"
        "付费 3：订阅收款通道通。合规 2：自动回复 Reddit 与 X 的求购帖属平台明确禁止的自动化行为，"
        "轻则限流重则封号，国内平台同样零容忍。获客 1：目标平台在国内不可用，整套获客场景"
        "连同账号体系一起消失。本地化 1：平台、语义识别、回复策略全要重做，做完仍面临反自动化治理。"
        "竞争 3：国内同类少，是因为平台不给做。"),
    "cosmo": (
        "插件加支付，安装后要维护商品、订单与服务器权限，有持续运维但不需要实施团队。",
        "卡点是绑 Discord。需求 1：Discord 内嵌结账在国内没有对应生态，社群电商的需求已被微信小店、"
        "小程序商城接住，没有独立付费位。付费 2：海外高客单订阅靠 Discord 社群转化，"
        "国内这条收款与信任链要另建。合规 4：纯电商工具本身合规负担小，这是六维里唯一不低的一项。"
        "获客 1：它靠 Discord 社群与机器人目录分发，国内无等价入口。本地化 1：整个宿主平台要换，"
        "产品等于重做。竞争 3：国内直接对手少，因为需求已被平台原生功能吃掉。"),
    "fill": (
        "App 自助，装上即用；负担在内容审核与社区治理，匿名形态下这块的成本会随用户量涨。",
        "卡点是匿名社交强监管。需求 2：匿名问答在校园场景有真实好奇，但付费意愿弱、留存短。"
        "付费 3：内购通道通。合规 1：匿名发布在国内属强监管形态，要实名后台、内容审核与举报处置，"
        "个人主体配置不起。获客 1：冷启动高度依赖校园地推与社群自传播，而这类传播正是平台治理重点。"
        "本地化 2：审核策略、社区规则、举报链路都要重建。竞争 2：国内匿名社交产品多次被下架，"
        "留下的空位不是机会。"),
    "esim-panel": (
        "白标 SaaS 要接运营商、配流量套餐与结算，每加一个客户都要做实施与合规对接，"
        "不是纯自助的产品。",
        "卡点是电信资质加 eSIM 未开放。需求 2：出境漫游流量确实有需求，但国内用户更习惯"
        "直接在平台买套餐，白标分销空间小。付费 3：B 端对公结算通。合规 1：转售流量需要"
        "电信业务经营许可与运营商合约，个人主体拿不到；国内 eSIM 手机端也尚未开放，两头都堵。"
        "获客 2：它靠海外旅行社群与分销商起量，国内渠道被运营商与平台占住。"
        "本地化 1：资质、套餐、结算、实名核验全要重做。竞争 3：直接对手少，但空位来自牌照门槛。"),
    "access-labs-pty-ltd": (
        "主体是周付的定制开发公司，交付即团队：需求澄清、排期、验收都靠人，产品化部分只是获客入口。",
        "卡点是 NFT 涉虚拟货币禁令。需求 2：国内有定制开发需求，但它用的 NFT 获客入口在境内不可用，"
        "需求要整体换赛道。付费 3：项目制对公收款通。合规 1：虚拟货币相关业务在境内被禁，"
        "用 NFT 做入口本身就不合法，连带营销与结算都受限。获客 2：它靠 Web3 圈层与项目转介绍，"
        "国内无对应合规渠道。本地化 1：业务形态、客户群、交付方式全要重建，等于换一家公司。"
        "竞争 3：国内定制开发竞争者极多，这一档没有差异化。"),
    "classyaction": (
        "纯信息型 App，用户自助订阅，零实施；负担在和解金项目库要持续收录与核验，"
        "属于内容维护型产品。",
        "卡点是制度不存在。需求 1：集体诉讼和解金申报依赖美国法律体系，国内没有对应的可申报池，"
        "用户连要找什么都不存在。付费 2：内购通道本身通，但付费理由不成立。"
        "合规 1：涉及法律信息服务的资质与代理申报边界，个人主体难过。"
        "获客 2：海外靠诉讼公告与法务社群导流，国内无等价信息源。"
        "本地化 1：整套产品的数据源、规则与用户动作都要从零设计。"
        "竞争 4：国内几乎没有同类产品，空位来自制度差异，不是市场空白。"),
    "copykitten": (
        "Discord 机器人装上即用，不需要实施；负担在语音模型调用成本与服务器维护，属于轻量自助工具。",
        "卡点是深度合成合规加绑 Discord。需求 2：语音克隆当作社交道具有真实好奇，但付费场景薄。"
        "付费 2：海外订阅走 Discord 内支付，国内要另建收款与会员体系。合规 1：语音克隆落进"
        "深度合成管理规定，要算法备案、显著标识与被克隆人授权，个人几乎做不到。"
        "获客 1：它整套分发绑在 Discord 机器人目录上，国内社群生态不同，宿主不存在。"
        "本地化 1：平台、支付、合规留痕全要重做。竞争 3：国内同类少是因为门槛高。"
        "可借的只有社交道具这个卖点。"),
    "anonymous-bot-for-slack": (
        "机器人装上即用，没有实施环节；负担在权限配置与内容审核，企业客户会要求审计与留痕。",
        "卡点是平台与合规双卡。需求 2：匿名反馈在团队管理里确有场景，但国内企业更倾向实名可控的工具，"
        "愿意付费的少。付费 2：海外按席位订阅，国内要换对公采购链路。合规 1：企业内匿名发声"
        "与内容留痕要求冲突，出事时责任主体不清。获客 1：它靠 Slack 应用目录分发，"
        "国内协作工具是飞书与企微，宿主生态整个换掉。本地化 1：平台接口、权限模型、审核策略全要重做。"
        "竞争 3：国内直接对手少，因为产品形态连同市场一起消失。"),
    "polyalerthub": (
        "自助订阅的警报终端，注册即用、零交付；负担在行情接口与推送稳定性的持续维护。",
        "卡点是预测市场在境内被禁。需求 1：Polymarket 本身在境内不可用，交易者群体不存在，"
        "整套产品的标的消失。付费 1：面向境外交易者的收款与合规通道在国内主体下也走不通。"
        "合规 1：预测市场属被禁业务，提供行情与投注分析同样在打击范围内。获客 1：它靠加密社群"
        "与交易者论坛分发，国内无合规渠道。本地化 1：数据源、标的、用户群全要换，"
        "换完就不是这个产品。竞争 3：国内没有同类，是因为业务本身不可做。整条线不可碰。"),
    "jarvis-bet": (
        "自助订阅的分析工具，注册即用、零交付；负担在赛事数据与模型更新，属于内容维护型产品。",
        "卡点是博彩在内地违法。需求 1：体育博彩分析在国内没有合法标的，付费入口本身不可用，"
        "需求不成立。付费 1：面向博彩用户的收款通道在国内主体下无法合规开通。"
        "合规 1：为博彩提供分析与引流属明确违法，不是备案或改造能解决。获客 1：它靠博彩社群"
        "与推广返佣起量，国内这类推广同样被禁。本地化 1：标的、用户、结算全要换，"
        "换完就不再是这个产品。竞争 3：国内无同类是法律结果，不是市场空位。整条线不可碰。"),
}


def wrap(text, cont_col=22, width=34):
    """中文长句切成隐式字符串拼接；不切断 ASCII 词组与数字，标点不得起行。"""
    segs, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width and ch in PUNCT:
            segs.append(cur)
            cur = ""
        elif len(cur) >= width + 12:
            cut = len(cur)
            while cut > 1 and re.match(r"[A-Za-z0-9$%.]", cur[cut - 1]):
                cut -= 1
            if cut <= 1:
                cut = len(cur)
            segs.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        segs.append(cur)
    fixed = []
    for s in segs:
        while fixed and s and s[0] in PUNCT:
            fixed[-1] += s[0]
            s = s[1:]
        if s:
            fixed.append(s)
    lines = ['"%s"' % fixed[0]]
    for s in fixed[1:]:
        lines.append(" " * cont_col + '"%s"' % s)
    return "\n".join(lines)


def build_entries():
    p = json.load(open(os.path.join(ROOT, "tmp", "fit_proposal_2026-11-04.json"), encoding="utf-8"))
    cases = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
    ids = {c["id"] for c in cases}
    C = {k: v for k, v in p.items() if v["preview"]["composite"] < 50}
    out = {}
    for cid, prop in C.items():
        if cid not in NOTES:
            raise SystemExit("[!] 缺补写理由：%s" % cid)
        dn, cn = NOTES[cid]
        e = json.loads(json.dumps(prop, ensure_ascii=False))  # 深拷贝，分数原样
        e["delivery"]["note"] = dn
        e["china"]["note"] = cn
        out[cid] = e
    return out, ids


def validate(entries, ids):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import score_solo_fit as S
    import score_china_fit as C
    bad = []
    missing = sorted(i for i in ids if i not in S.DELIVERY or i not in C.SCORES)
    if set(entries) != set(missing):
        bad.append(("*", "C 档集合 != 缺表集合：多 %s / 少 %s"
                    % (sorted(set(entries) - set(missing)), sorted(set(missing) - set(entries)))))
    for cid, e in entries.items():
        if cid not in ids:
            bad.append((cid, "不在 cases.json"))
        vals = [e["delivery"]["score"]] + [e["china"][k] for k in KEYS]
        if any(not 1 <= v <= 5 for v in vals):
            bad.append((cid, "分数越界 %s" % vals))
        if e["preview"]["composite"] >= 50:
            bad.append((cid, "不属于 C 档"))
        if not e["china"].get("blocker"):
            bad.append((cid, "缺 blocker"))
        if len(e["delivery"]["note"]) < 24:
            bad.append((cid, "delivery note 过短 %d" % len(e["delivery"]["note"])))
        if len(e["china"]["note"]) < 80:
            bad.append((cid, "china note 过短 %d" % len(e["china"]["note"])))
        # 理由里显式写的维度分必须与表里的分数一致
        for k, label in LABEL.items():
            m = re.search(label + r"\s*(\d)", e["china"]["note"])
            if m and int(m.group(1)) != e["china"][k]:
                bad.append((cid, "%s 写 %s 但分数是 %s" % (label, m.group(1), e["china"][k])))
        # 文案里不许出现会破坏 Python 字符串字面量的字符
        if '"' in e["delivery"]["note"] or '"' in e["china"]["note"]:
            bad.append((cid, "note 含 ASCII 双引号"))
        if "\\" in e["delivery"]["note"] or "\\" in e["china"]["note"]:
            bad.append((cid, "note 含反斜杠"))
    if bad:
        for b in bad:
            print("[!] %s: %s" % b)
        raise SystemExit("校验不过：%d 处，未写入" % len(bad))
    print("[i] %d 条通过集合/分数/文案校验；缺表 %d 条" % (len(entries), len(missing)))
    return missing


def emit_delivery(entries):
    parts = ["\n    # ── 2026-11-04 C 档（综合 < 50，别搬）合并，共 %d 条 ──" % len(entries)]
    for cid in sorted(entries):
        e = entries[cid]["delivery"]
        parts.append('    "%s": (%d, %s),' % (cid, e["score"], wrap(e["note"])))
    return "\n".join(parts)


def emit_china(entries):
    parts = ["\n    # ── 2026-11-04 C 档（综合 < 50，别搬）合并，共 %d 条 ──" % len(entries)]
    for cid in sorted(entries):
        c = entries[cid]["china"]
        parts.append('    "%s": dict(' % cid)
        parts.append("        " + ", ".join("%s=%d" % (k, c[k]) for k in KEYS) + ",")
        parts.append('        blocker="%s",' % c["blocker"])
        parts.append("        note=%s," % wrap(c["note"], cont_col=13))
        parts.append("    ),")
    return "\n".join(parts)


def insert(path, block):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = None
    for m in re.finditer(r"^\}\s*$", src, re.M):
        pass
    if not m:
        raise SystemExit("[!] %s 里找不到表收尾" % path)
    head, tail = src[:m.start()], src[m.start():]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head.rstrip("\n") + "\n" + block + "\n" + tail)


def check_generated(block):
    """生成后扫：任何一行不得以标点开头（B 档换行器踩过的坑）。"""
    for ln in block.splitlines():
        s = ln.strip()
        if s.startswith('"') and len(s) > 1 and s[1] in PUNCT:
            raise SystemExit("[!] 标点起行：%s" % s[:60])


def check_roundtrip(entries):
    """换行器最强锁：把生成的隐式拼接字面量还原，必须与原文一字不差。

    B 档那次「句号被推到行首」是肉眼扫出来的；这里直接让 Python 自己
    求值每个 wrap() 结果，杜绝吞字、重复、漏字、加字四类静默错误。
    """
    for cid, e in entries.items():
        for kind, text in (("delivery", e["delivery"]["note"]),
                           ("china", e["china"]["note"])):
            col = 22 if kind == "delivery" else 13
            wrapped = wrap(text, cont_col=col)
            got = ast.literal_eval("(" + wrapped + ")")
            if got != text:
                raise SystemExit("[!] %s/%s 换行器丢字：%d -> %d" % (cid, kind, len(text), len(got)))
    print("[i] 44 段理由经 ast.literal_eval 还原，与原文逐字一致")


def main():
    dry = "--dry" in sys.argv
    entries, ids = build_entries()
    validate(entries, ids)
    check_roundtrip(entries)
    d, c = emit_delivery(entries), emit_china(entries)
    check_generated(d)
    check_generated(c)
    if dry:
        print("--- DELIVERY 预览 ---")
        print(d[:900])
        print("--- SCORES 预览 ---")
        print(c[:900])
        return
    targets = ["score_solo_fit.py", "score_china_fit.py"]
    for f in targets:
        p = os.path.join(ROOT, "scripts", f)
        if "C 档（综合 < 50，别搬）合并" in open(p, encoding="utf-8").read():
            raise SystemExit("[!] %s 已合并过 C 档，先回滚" % f)
        # 备份，回滚锚点
        bak = os.path.join(ROOT, "_backups", f + ".pre-C-1950")
        with open(bak, "w", encoding="utf-8", newline="\n") as o:
            o.write(open(p, encoding="utf-8").read())
    insert(os.path.join(ROOT, "scripts", targets[0]), d)
    insert(os.path.join(ROOT, "scripts", targets[1]), c)
    for f in targets:
        py_compile.compile(os.path.join(ROOT, "scripts", f), doraise=True)
    print("[✓] 已写入两张表并编译通过；备份在 _backups/*.pre-C-1950")


if __name__ == "__main__":
    main()
