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
