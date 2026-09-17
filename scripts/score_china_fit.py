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
