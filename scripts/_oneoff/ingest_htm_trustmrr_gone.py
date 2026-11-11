#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 howtomakemi 里标注「TrustMRR Stripe 授权验证」的两条（gistly / research-match）
写入 data/cases.json —— 但**降级为 partial**，因为源档案已经查不到了。

## 为什么是降级而不是 stripe

这两条的页面原话是「TrustMRR 平台 Stripe 授权验证，2026-07-15 快照」。
按流程应该回 TrustMRR 官方通道核一遍现值，四条路径实测全不通（2026-10-26）：

    GET  trustmrr.com/startup/gistly.md           → 404
    GET  trustmrr.com/startup/gistly              → 404
    GET  trustmrr.com/startup-sitemap.xml         → 11,989 条 loc，grep gistly / research-match 命中 0
    GET  trustmrr.com/llms.txt                    → 8,199 字节，命中 0
    GET  trustmrr.com/search?q=gistly             → 页面不含 gistly（0 次）
    GET  trustmrr.com/api/v1/startups/gistly      → 401（需 API key，本库没有）

对照组 `startup/1milliontaps-lol.md` 同批 200，说明不是网络问题，是档案没了。

按 MEMORY 红线「聚合站的收入字段是快照、不是现值，一律回源后再入库」：
回源失败 ⇒ 不能标 stripe，只能标「口径待核」，并用 verification_downgraded 留痕。
收入数字照抄快照并在 metric_note 里写清时点，随时可以回页核对。

官网两条都实测 200（2026-10-26）：
    gistly.ai            → 301 → https://www.gistly.ai/   title 含 Conversation Intelligence Platform
    researchmatch.site   → 200  title 含 Find Research Professors in Minutes
⇒ 产品存在性成立、定位与页面描述一致；不成立的只有「收入是多少」。

用法： python scripts/_oneoff/ingest_htm_trustmrr_gone.py [--dry]
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

DOWNGRADE_REASON = (
    "页面标注「TrustMRR 平台 Stripe 授权验证，2026-07-15 快照」，但 2026-10-26 回源核验四条路径全不通："
    "startup/%s.md 与 startup/%s 均 404；实时 startup-sitemap（11,989 条）与 llms.txt 均无该 slug；"
    "站内搜索无记录；api/v1 需 API key（401）。同批对照 startup/1milliontaps-lol.md 返回 200，"
    "排除网络因素 ⇒ 档案已下架，收入无法回源核验，按证据降到「口径待核」。"
    "官网本身仍 200 在线，产品存在性成立。"
)


def usd(cny):
    """¥ → $，只做这一个除法，四舍五入到整数。"""
    return int(round(cny / RATE))


def src(slug, name, site):
    return [
        {"label": "howtomakemi 案例页（聚合；收入源自 TrustMRR 授权快照）",
         "url": HOME % slug, "kind": "aggregator"},
        {"label": "%s 官网" % name, "url": site, "kind": "official"},
    ]


def case(**kw):
    slug = kw["id"]
    base = {
        "status": "curated",
        "verification": "partial",
        "tier": "standard",
        "tier_reason": "收入只有聚合站转述的 TrustMRR 快照（2026-07-15），且该档案 2026-10-26 已 404 下架，"
                       "无法回源核验 ⇒ 口径待核；官网在线可证产品存在，但证不了收入",
        "quality_score": 0,
        "needs_review": True,
        "human_read": False,
        "human_read_at": "",
        "corrections": [],
        "published_from": slug,
        "verified_at": TODAY,
        "updated_at": TODAY,
        "china_fit": None,
        "solo_fit": None,
        "verification_downgraded": {
            "at": TODAY,
            "from": "stripe",
            "to": "partial",
            "reason": DOWNGRADE_REASON % (slug, slug),
            "tool": "scripts/_oneoff/ingest_htm_trustmrr_gone.py",
        },
    }
    base.update(kw)
    return base


NEW = []

# ── 1. Gistly ───────────────────────────────────────────────────────────────
NEW.append(case(
    id="gistly", name="Gistly", name_en="Gistly", origin="日本",
    one_liner="对话智能平台：把每一通电话、聊天、邮件按客户自己的 QA 标准打分，100% 覆盖不抽样，"
              "155 个客户做到 MRR 约 ¥32万",
    category="企业服务", industry="对话智能 / 销售质检与合规",
    caliber="mrr",
    metrics={
        "mrr": usd(319_872),
        "all_time": usd(7_623_645),
        "subscriptions": 155,
        "arpu": usd(2_063),
        "headline": "MRR 约 ¥32万（≈$47.6K，2026-07-15 快照），155 个付费客户，ARPU 约 ¥2,063/月",
        "metric_note": "页面原文：「MRR 约 ¥32万，累计营收约 ¥762万，付费客户 155 个，当月新增 54 个、流失 8 个，"
                       "月流失率 5.1%，月增长率 34.84%，客单价约 ¥2,063/月，客户终身价值约 ¥40,219」"
                       "「155 个客户 × ¥2,063 ≈ ¥32万，正好等于申报的 MRR」（页面自做乘法校验，能乘回来）。"
                       "原始精度：MRR ¥319,872、累计 ¥7,623,645、LTV ¥40,219。"
                       "⚠ 这是 2026-07-15 的 TrustMRR 快照，该档案 2026-10-26 已 404，无法核实现值；"
                       "¥→$ 按站点汇率 6.72 折算。定价官网不公开，唯一入口是 Book a demo。",
        "growth": "月增长率 34.84%，当月新增 54 个客户 / 流失 8 个，月流失率 5.1%",
        "price_point": "不公开定价，Book a demo 唯一入口；按客户算 ARPU 约 ¥2,063/月（≈$307），LTV 约 ¥40,219（≈$5,986）",
    },
    models=["订阅制", "企业销售"],
    replicability={"tech": 4, "distribution": 4, "capital": 2, "timing": 3},
    verdict="会话质检这个品类默认接受「抽 2% 听一听」，它做的唯一一件事就是把这个默认翻过来写进标题——"
            "把「不抽样」当产品名，比任何准确率数字都好卖。",
    what_it_does="对话智能平台，覆盖销售、客服、催收三个场景。全渠道捕获通话/聊天/邮件，"
                 "按客户自己设定的 QA 标准给每一条对话自动打分（不是平台预设的通用模型），100% 覆盖不抽样；"
                 "分析层输出三件事：顶尖销售与普通销售的行为差异按人排名、风险与合规缺口提前预警、"
                 "以及带源链接的答案（AI 每个结论都能点回原始对话核对）。支持 10+ 语言与 code-switching。",
    how_it_makes_money="B2B 订阅，官网不公开定价，唯一转化入口是 Book a demo，走企业直销。"
                       "2026-07-15 快照：MRR 约 ¥32万、累计营收约 ¥762万、155 个付费客户、"
                       "ARPU 约 ¥2,063/月、月流失率 5.1%、月增长 34.84%。",
    why_it_works=[
        "打击的是行业公开的妥协：抽样质检是所有人都默认接受的现实，「Not a Sample」把它变成一句广告语。",
        "评分标准交给客户定，绕开 B2B 采购里最硬的那道质疑——「你凭什么替我判断我的员工」。",
        "一套捕获+分析+评分引擎横向切三个场景（销售/客服/催收），同一份技术卖三次，边际成本几乎为零。",
        "答案挂源链接：不能核查的 AI 结论在企业采购里等于没有，这一条直接把黑盒变成可审计的证据。",
    ],
    playbook=[
        "去找一个「全行业都默认忍受」的妥协，然后公开宣布不妥协——这是最省钱的定位方式。",
        "把判定标准的所有权让给客户（自定义 QA 规则），比把模型做得更准更能降低采购阻力。",
        "一个引擎切相邻场景：先在销售跑通，再复制到客服与催收，比重新做三个产品便宜一个数量级。",
    ],
    signals=[
        "155 个付费客户 × ¥2,063 ARPU ≈ ¥32万 MRR，页面自做乘法校验能对上",
        "月增长 34.84%、当月新增 54 个客户，是收录批次里增速最快的之一",
        "月流失率 5.1%、LTV ¥40,219 ⇒ 卖的是团队级席位而非个人订阅，迁移成本高",
    ],
    founder="TrustMRR 登记创始人 Yuki Tanaka（账号 yukiai），日本；建号 2025-12-03。"
            "官网首页挂「Trusted by teams at」客户 Logo 墙，未设团队介绍页——面向企业客户时产品说服力优先于创始人故事。",
    traffic_sources=["官网 + Book a demo 直销", "客户 Logo 墙做社会证明", "未公开披露获客渠道"],
    tags=["对话智能", "销售质检", "合规", "B2B订阅", "日本"],
    sources=src("gistly", "Gistly", "https://www.gistly.ai"),
))

# ── 2. Research Match ───────────────────────────────────────────────────────
NEW.append(case(
    id="research-match", name="Research Match", name_en="Research Match", origin="美国",
    one_liner="帮本科生写「教授愿意回」的套磁邮件：索引 2.5 亿篇论文 + 1000 所大学，"
              "只给可核实的素材、不代写，19 个客户做到 MRR 约 ¥36.4万",
    category="垂直行业 SaaS", industry="教育 / 学术申请",
    caliber="mrr",
    metrics={
        "mrr": usd(364_224),
        "all_time": usd(9_574_327),
        "subscriptions": 19,
        "arpu": usd(19_172),
        "headline": "MRR 约 ¥36.4万（≈$54.2K，2026-07-15 快照），19 个付费客户，ARPU 约 ¥19,172/月",
        "metric_note": "页面原文：「MRR 约 ¥36.4万，累计营收约 ¥957.6万，付费客户 19 个，当月新增 7 个、流失 1 个，"
                       "月流失率 4.5%，月增长率 38.34%，客单价（ARPU）约 ¥19,172/月，LTV 约 ¥42.6万」。"
                       "原始精度：MRR ¥364,224、累计 ¥9,574,327。"
                       "⚠ 两处存疑：① 页面自述「19 个客户对应 ¥36.4万 MRR，人均月付接近 ¥19,488，对「学生工具」"
                       "这个定位高得反常」，推测实际是院系/机构批量授权，TrustMRR 未披露细口径；"
                       "② 该档案 2026-10-26 已 404 下架（建号 2026-06-26，存活不足 4 个月），无法核实现值。"
                       "¥→$ 按站点汇率 6.72 折算。",
        "growth": "月增长率 38.34%，当月新增 7 个客户 / 流失 1 个，月流失率 4.5%",
        "price_point": "订阅制，按学生卖；ARPU 约 ¥19,172/月（≈$2,853）——与「学生工具」定位不符，页面自己标了异常",
        "usage": "2.5 亿+ 论文索引、1000+ 所大学；官网口径 <24 小时首次教授回复",
    },
    models=["订阅制", "自助增长"],
    replicability={"tech": 3, "distribution": 4, "capital": 2, "timing": 3},
    verdict="它把「AI 的短板」写成了卖点：AI 替你写的邮件教授一眼识破，所以它不写，只给你能核实的那几句素材。"
            "在一个所有人都在同一边卷的赛道里，站到反面本身就是定位。",
    what_it_does="三步工具，全站只有这三个功能：① 搜索匹配——输入研究方向，从 2.5 亿篇已验证文献里"
                 "按影响力排出真正在该方向发文的教授，覆盖 1000+ 所大学；② AI 通俗摘要——每位教授配一段新人"
                 "能读懂的研究摘要并标出关键发现（可直接引用）；③ 邮件检查器——按 20+ 位教授的真实反馈建的规则，"
                 "在发送前把「I found your work fascinating and groundbreaking」这类谄媚句式标出来并要求删掉。",
    how_it_makes_money="订阅制，面向学生自助注册付费。2026-07-15 快照：MRR 约 ¥36.4万、累计营收约 ¥957.6万、"
                       "19 个付费客户、ARPU 约 ¥19,172/月、月流失率 4.5%、月增长 38.34%。"
                       "ARPU 与「学生工具」定位不符，疑似机构批量授权，页面未披露细口径。",
    why_it_works=[
        "把 AI 的短板做成卖点：别人说「我帮你写」，它说「AI 写的教授一眼识破，所以我不写」，用竞品的失败模式做定位。",
        "首页正中间摆一张与 ChatGPT 的逐条对比表（幻觉/写法/效率），用户心里的问号先由自己说出来，信任就成立一半。",
        "专家知识沉淀成机制而不是内容：20+ 位教授的意见没有写成博客，而是变成邮件检查器里每次使用都会触发的规则。",
        "场景有硬截止时间：暑研、套磁、申请季都有窗口期，用户不会「以后再说」——付费意愿来自截止时间而不只是需求强度。",
    ],
    playbook=[
        "在 AI 生成内容被普遍识破的场景里，卖「可核实的原材料」比卖「漂亮的成品」更值钱。",
        "把行业老大做成首页对比表：不回避竞品，而是替用户完成竞品教育，这比自夸功能有效。",
        "选有截止日期的场景，别选「提升效率」的场景——前者有购买时点，后者永远可以拖。",
    ],
    signals=[
        "19 个付费客户、MRR 约 ¥36.4万、ARPU 约 ¥19,172/月（页面自标该客单价对「学生工具」偏高，疑似机构授权）",
        "月增长 38.34%、月流失率 4.5%，累计营收约 ¥957.6万",
        "2.5 亿篇论文索引 + 1000 所大学；规则来自 20+ 位教授的真实反馈",
    ],
    founder="TrustMRR 登记为匿名（Anonymous），国家填美国；官网无团队介绍，只强调「所有工具都经过 20+ 位教授的建议"
            "打磨并验证可用」。TrustMRR 建号 2026-06-26，是该站最年轻的档案之一，且已在 4 个月内消失。",
    traffic_sources=["申请季/暑研的搜索流量", "「可信学生来自 Stanford、MIT」的社会证明", "官网未公开披露获客渠道"],
    tags=["教育", "学术申请", "反AI代写", "订阅制", "匿名创始人"],
    sources=src("research-match", "Research Match", "https://www.researchmatch.site"),
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
        print("  + %-16s %-6s %-4s %s" % (c["id"], c["verification"], c["caliber"],
                                          m.get("headline", "")[:60]))
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
