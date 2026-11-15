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
    python scripts/score_china_fit.py             # 打分并写回 case_library（+重导镜像）
    python scripts/score_china_fit.py --dry-run   # 只看结果，不写入
    python scripts/score_china_fit.py --top 8     # 只打印前 8
"""

import argparse
import json
import os
import sys
from datetime import datetime

import score_note_policy as SNP   # 文案裁定单一源（PRD #183）

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


def _median_int(vals):
    """中位数取整（.5 向上）。缺表兜底只求「全库的典型值」，不追求精度。"""
    vals = sorted(v for v in vals if isinstance(v, int))
    if not vals:
        return 3
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return int((vals[mid - 1] + vals[mid]) / 2 + 0.5)


def fallback_dims():
    """缺表时的兜底六维：取全库既有评分的**中位数**。

    2026-11-06 放宽（用户：「这两张表放宽」）：SCORES 不再强制人工逐条填。
    兜底值来自既有 N 条人工判断的中位数 —— 是统计量，不是凭空编的判断；同时
    在 note 与 `provisional: true` 上标死，一眼能看出「这条没人工评过」。
    人工填了表就优先用表，兜底永不覆盖真实判断。
    """
    return {k: _median_int([s.get(k) for s in SCORES.values()])
            for k, _label, _desc in DIMS}


def fallback_note(fb):
    """库里没判断时的文案初值（PRD #184：文案只从库里或这里来）。

    表里不再存 note 副本；这段是统计量说明，不是人工结论。
    """
    return ("未人工评分：六维按全库 %d 条已评分案例的中位数兜底（%s）。"
            "要精确结论请补进 SCORES。"
            % (len(SCORES), "/".join(str(fb[k]) for k, _l, _d in DIMS)))


# ------------------------------------------------------------------ 评分表
# PRD #184：SCORES 只存六维分数与 blocker。判断依据（china_fit.note）的唯一
# 真值在 case_library —— 库里数字会随快照刷新，源码副本必然漂移。改分数改这里，
# 改文案回库里改，别再往表里写 note。
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
        blocker="渠道与本地生态对接"
    ),
    "salesrobot": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="平台生态与封号治理"
    ),
    "jobright": dict(
        demand=5, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="招聘平台数据不开放"
    ),
    "sandra-ai": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="DMS 封闭与电话营销治理"
    ),
    "outward": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="民调资质与数据合规"
    ),
    "workhero": dict(
        demand=3, payment=2, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="小 B 付费能力与地推成本"
    ),
    # ── 2026-10-26 backlog「形态可抄」第一梯队入库的 3 条 ──
    "autoshorts": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="平台发布接口"
    ),
    "rosie": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="电信实名与外呼治理"
    ),
    "boredhumans": dict(
        demand=2, payment=2, compliance=3, acquisition=1, localization=3,
        competition=2
    ),
    # ── 2026-10-26 TrustMRR 档案已下架的两条（降级为 partial）──
    "gistly": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=2, competition=1
    ),
    "research-match": dict(
        demand=3, payment=2, compliance=2, acquisition=3, localization=1, competition=1,
        blocker="学术数据源"
    ),
    # ── 2026-10-26 从 howtomakemi 官方披露线入库的 8 条 ──
    "arcads": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=2, competition=3
    ),
    "instantly": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="邮件群发合规"
    ),
    "ahron": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2
    ),
    "fyxer": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),
    "heyreach": dict(
        demand=3, payment=3, compliance=1, acquisition=1, localization=1, competition=1,
        blocker="平台缺失 + 自动外发合规"
    ),
    "campfire": dict(
        demand=4, payment=4, compliance=3, acquisition=1, localization=1, competition=1,
        blocker="会计准则 + 存量厂商"
    ),
    "wispr-flow": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="免费输入法占位"
    ),
    "build-concierge": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=2, competition=2
    ),
    "gojiberryai": dict(
        demand=3, payment=3, compliance=1, acquisition=1, localization=2, competition=1,
        blocker="自动化触达合规"
    ),
    "sierra": dict(
        demand=4, payment=4, compliance=3, acquisition=1, localization=2, competition=1
    ),
    "genius-ai": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),
    "nitra": dict(
        demand=4, payment=4, compliance=1, acquisition=3, localization=1, competition=1,
        blocker="医疗合规"
    ),
    "viktor": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1
    ),
    "chatbase": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2
    ),
    "rezi": dict(
        demand=2, payment=3, compliance=5, acquisition=3, localization=3, competition=2,
        blocker="C 端付费弱"
    ),
    "postiz": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=2, competition=3,
        blocker="平台 API 授权"
    ),
    "shipfast": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=2, competition=2
    ),
    "trustmrr": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=4,
        blocker="核心机制无土壤"
    ),
    "comp-ai": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=1, competition=2
    ),
    "aeo-engine": dict(
        demand=4, payment=4, compliance=5, acquisition=4, localization=4, competition=5
    ),
    "lancer-app": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=2, competition=3
    ),
    "speel-co": dict(
        demand=5, payment=4, compliance=4, acquisition=4, localization=3, competition=2
    ),
    "visualizee-ai": dict(
        demand=5, payment=4, compliance=5, acquisition=4, localization=3, competition=2
    ),
    "checkvibe": dict(
        demand=2, payment=3, compliance=5, acquisition=3, localization=5, competition=4,
        blocker="需求端薄"
    ),
    "storyshort-ai": dict(
        demand=3, payment=4, compliance=4, acquisition=4, localization=3, competition=1
    ),
    "meerkats-ai": dict(
        demand=3, payment=4, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="自动化触达合规"
    ),
    "coral": dict(
        demand=3, payment=3, compliance=5, acquisition=3, localization=4, competition=2
    ),
    "startclaw": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=2
    ),
    "outrank": dict(
        demand=4, payment=4, compliance=4, acquisition=4, localization=3, competition=2
    ),
    "marc-lou-portfolio": dict(
        demand=3, payment=2, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="支付基础设施"
    ),
    "bustem": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=3, competition=3
    ),
    "kibu": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2
    ),
    "pieter-levels": dict(
        demand=3, payment=2, compliance=5, acquisition=3, localization=3, competition=2,
        blocker="支付基础设施"
    ),

    # ---- 2026-09-20 发布的一批 ----
    "1lookup": dict(
        demand=4, payment=4, compliance=2, acquisition=4, localization=2, competition=3,
        blocker="数据合规"
    ),
    "stan": dict(
        demand=3, payment=3, compliance=2, acquisition=1, localization=1, competition=1,
        blocker="支付牌照 + 平台竞争"
    ),
    "magicslides-app": dict(
        demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=1
    ),
    "autoreels-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="内容合规"
    ),
    "insect-bite-id": dict(
        demand=2, payment=4, compliance=3, acquisition=3, localization=3, competition=3
    ),

    # ---- 2026-09-25 补的一批（TrustMRR 挂牌标的为主）----
    "voklit": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="增值电信牌照"
    ),
    "prosp": dict(
        demand=3, payment=4, compliance=1, acquisition=1, localization=2, competition=1,
        blocker="自动化触达合规 + LinkedIn 已退出中国"
    ),
    "divine-widgets": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="宗教内容合规"
    ),
    "promptmonitor-io": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),
    "uplinked-b-v": dict(
        demand=3, payment=4, compliance=3, acquisition=1, localization=1, competition=1,
        blocker="LinkedIn 在国内无等价平台"
    ),
    "le19emetrou": dict(
        demand=3, payment=5, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="价格战与仿品"
    ),
    "search1api": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2
    ),

    # ---- 2026-09-30 补的两条 ----
    "mort": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="招聘数据抓取合规"
    ),
    "quran-unlock": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=4,
        blocker="App Store 分发在国内不成立"
    ),

    # ---- 2026-10-05 补的两条（premium 晋升时漏了，卡住 fit_ready 闸门）----
    "harperai": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=3, competition=3,
        blocker="公开抵押数据的商业化授权边界未明"
    ),
    "easymix": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),

    # ===== 2026-10-11 补：Stripe 深核通过的 7 条 =====
    # 六维口径与老案例一致：demand/payment/compliance/acquisition/
    # localization/competition，1 = 最差，5 = 最好；competition 分数越高
    # 意思是「竞争越友好/越空」，不是「竞争越激烈」。

    "cometly": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="企业销售与数据合规"
    ),
    "vid-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2
    ),
    "podawaa": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1,
        blocker="平台不可迁移"
    ),
    "publbee": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2
    ),
    "vectosolve": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),
    "augora-ai": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="隐私与跨平台数据"
    ),
    "wpconvert-ai-convert-ai-sites-to-wordpress": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=3,
        blocker="上游不给导出"
    ),

    # ===== 2026-10-15 补：门槛放开后批量晋升的 41 条 =====
    # 同口径：demand/payment/compliance/acquisition/localization/competition，
    # 1 = 最差，5 = 最好。competition 分数越高 = 这一格越空（不是竞争越激烈）。
    # 两条（corsidia / hypit-ai）官方只披露收入、不披露产品，评分是占位，
    # note 里已声明不构成移植判断。

    "alchie": dict(
        demand=2, payment=4, compliance=5, acquisition=2, localization=2, competition=1,
        blocker="LinkedIn 在国内无等价平台"
    ),
    "adelante-ltd": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1,
        blocker="平台自带客服 + 国内 SaaS 客单价"
    ),
    "storyshort": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="国内平台无开放发布接口"
    ),
    "analytics-mates": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=1, competition=1,
        blocker="分析栈整体不同"
    ),
    "brevilabs": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=2, competition=1
    ),
    "cartboss": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="短信营销牌照 + 平台自带召回"
    ),
    "based-labs-ai": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1
    ),
    "draftly": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=3, competition=2
    ),
    "ae-intelligence": dict(
        demand=3, payment=4, compliance=2, acquisition=4, localization=4, competition=2,
        blocker="学术诚信灰区"
    ),
    "abmaxx": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=2
    ),
    "backpedal-ltd": dict(
        demand=4, payment=4, compliance=2, acquisition=2, localization=1, competition=3,
        blocker="线下履约 + 安保服务边界"
    ),
    "adspirer": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2
    ),
    "corsidia": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=3
    ),
    "avenue-ticketing-inc": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=1, competition=1,
        blocker="演出票务资质 + 大麦猫眼垄断"
    ),
    "autocontent-api": dict(
        demand=3, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="生成式 AI 备案 + 大厂 API 价格战"
    ),
    "chessigma": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=3, competition=2
    ),
    "backlinker-ai": dict(
        demand=2, payment=4, compliance=1, acquisition=1, localization=1, competition=2,
        blocker="Google 生态不存在 + 自动外发合规"
    ),
    "ai-toolbox": dict(
        demand=2, payment=4, compliance=4, acquisition=3, localization=4, competition=1
    ),
    "amicited": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=3
    ),
    "angel-match": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=1
    ),
    "calendesk": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="敏感个人信息 + 微信生态适配"
    ),
    "capgo": dict(
        demand=3, payment=4, compliance=2, acquisition=3, localization=2, competition=3,
        blocker="应用商店明令禁止绕过审核的热更新"
    ),
    "applyall": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2
    ),
    "audn-ai": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="网络安全服务资质"
    ),
    "hypit-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=3
    ),
    "apiframe": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=1,
        blocker="生成式 AI 备案 + 大厂 API 价格战"
    ),
    "chatseo": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=2
    ),
    "brawler": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=4, competition=2
    ),
    "cod-dex": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=3, competition=2
    ),
    "boothie-ai-photo-editor": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=3, competition=1
    ),
    "nutria-9e3eaa": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1
    ),
    "fastwrite-io": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="学术诚信灰区"
    ),
    "downconvert-sketchup-files": dict(
        demand=3, payment=4, compliance=5, acquisition=3, localization=4, competition=4
    ),
    "macwall": dict(
        demand=1, payment=3, compliance=5, acquisition=2, localization=5, competition=1,
        blocker="C 端壁纸付费不存在"
    ),
    "growingupvideo": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=3, competition=1,
        blocker="换脸/深度伪造内容标识监管"
    ),
    "homy": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3
    ),
    "stealth-rdp": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="IDC/ISP 增值电信牌照"
    ),
    "white-glove-content": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=2
    ),
    "pet-plus-us": dict(
        demand=4, payment=5, compliance=4, acquisition=4, localization=3, competition=2
    ),
    "wagiff-app": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="宗教/涉性内容审核"
    ),
    "quotedeck": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="保险销售资质 + 产品体系不同"
    ),
    "adawcag": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "aeo-checker": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "ad-machine": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "advanced-gsc-mcp": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "3d-agent": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "adtruffle": dict(demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2, blocker='平台数据合规'),
    "ai-designer": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "acquire-community": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3),
    "atelier-nuage": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "fireply-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ismalicious": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "uizze": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "barron-tech-llc": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "autoclip": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "chariot-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "openalternative": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "breadsync": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3),
    "cbt-artificial-intelligence-technology-inc-dba-zuuz": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "firecert": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "arcton": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "captureflow": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "corey-chiu": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "opentweet": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "apogee": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "blawgy-com": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "autosubtitles": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "axis-labc2026": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "astonish-email-limited": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "bluff-ai": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "baller": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "athenify": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "christian-tech-jobs": dict(demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "bouncer": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "blitzcut": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "bidva-buy-linkedin-accounts": dict(demand=3, payment=3, compliance=1, acquisition=2, localization=3, competition=1, blocker='平台规则/账号交易'),
    "coprep-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "canopy-api": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3),
    "billionverify": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "algofuse-ai": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3),
    "botric": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "checkform-gymnastics": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "claws-host": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "coco": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "blogr-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "client-one": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "coralflavor": dict(demand=3, payment=3, compliance=1, acquisition=3, localization=4, competition=2, blocker='内容合规'),
    "apexvol": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3),
    "kitchendesign": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "blacktwist": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "cloud-backup-for-podio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "spark-playground": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "clawlink": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3),
    "ai-text-humanizer-netherlands-belgium": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "agentskit": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "altitude-cleaning-crew": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2),
    "anyapi": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "aprs-conseil": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "athletiks": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "bakering-app-sl": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2),
    "bettrbot": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "brandmylaptop": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "brandmymac": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "brokenstudios": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "chronoid": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "firstrep": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "orbio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "ai-prank-call": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "betting-tips": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ai-voice-calls": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "demopolish": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2),
    "chatsell-co": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "roster-for-slack": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "static-run": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "ai-math-solver": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ai-smart-decor": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "verse": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2),
    "clawbox": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "bankstatemently": dict(demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2),
    "fit-it-on": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ai-influencer-generator": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "flowy": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "check-id": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "chat-whisperer": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "quickvoc": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "blue-esim": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "captime": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "alodata": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "chiptune": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "calsy": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "firsto": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "atslebenslauf-com": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "anymelo": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "reddit-grow": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "cleaner": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "arbichat-s-l": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "bromeai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "fire-tech-apps-llc": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "bolsai": dict(demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=3),
    "sportsjobs-online": dict(demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "chartvio": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "brikk": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "elispeak": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "myterrace-net": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "cat-games": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "c12s-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "forage": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "aiwith-me": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "june": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "blockit": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "orbit-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "aidelly": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "car-docs-ro": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ai-swap-facetik": dict(demand=4, payment=3, compliance=2, acquisition=4, localization=3, competition=2),
    "climbx": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "blainy": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "aistudios": dict(demand=4, payment=4, compliance=5, acquisition=4, localization=3, competition=3),
    "car-dealer-tracker": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "careerkit": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "chunks-microlearning": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "alienart-io": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "chartsgpt": dict(demand=3, payment=3, compliance=1, acquisition=3, localization=3, competition=3, blocker='加密资产合规'),
    "brandiseer": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "clear-food": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "bolty": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "chefgpt": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "basincheck": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "alphascope": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "business-freelance": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "aura-ai-ai-video-image-generator": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2),
    "coaching-marketing-kit": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "fishingai": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "cooldock": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "auraclaw": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "bloxg-roblox-marketing-agency": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "archifruits-ai": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "cheatmate": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "chatedu-inc": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "agentsguild": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "ai-baby-generator": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "adstudio": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "adriselab": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "ai-cover-music-video": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2),
    "affililist": dict(demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=3),
    "ai-detector-image-checker": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=3),
    "admirror": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "account-expense-manager": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "anon-2": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "7running": dict(demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2),
    "private-venture": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "aftersora": dict(demand=4, payment=3, compliance=4, acquisition=4, localization=3, competition=2),
    "3dplotter": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "agence-web-abstract27": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "20-0": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "ai-directories": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "adstellar": dict(demand=2, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "aesty": dict(demand=4, payment=3, compliance=5, acquisition=4, localization=3, competition=2),
    "adle": dict(demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2),
    "acsperf": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "the-stealthiest-of-ventures": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "gtm-help": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    "mac-duo": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "dope-data": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "orior-ai": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=3),
    "agenkit": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=4, competition=3),
    "kreapro": dict(demand=3, payment=3, compliance=5, acquisition=3, localization=3, competition=3),
    "toonthis": dict(demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2),
    # ── 2026-10-27 批次漏补的两条（2026-10-29 补）──
    # 同 SOLO 表：537b443 入库时漏了这两条，CI 一直红。没有套下面的通用文案 ——
    # 这两条的形状差得远：一条是「需求在、但被免费替代」，一条是「渠道本身错位」。
    "upvoty": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="免费替代与获客渠道"
    ),
    "flodesk": dict(
        demand=2, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="渠道错位 —— 国内没有邮件文化"
    ),
    # ── 2026-10-29 补：fit_ready --check 报的 26 条 ──
    # 评分口径见 DIMS：每项 5 分制，越高越容易搬（合规一项是「越不碰红线分越高」）。
    # 这 26 条里有 18 条是本轮晋升的，所以判断是按案例已有字段现做的，不是抄来的。
    "flowmance-templates": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=3, competition=2,
        blocker="模板生态不对应"
    ),
    "neoscene": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="深度合成合规 + 算力成本"
    ),
    "boxer-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="安卓机型碎片化"
    ),
    "kori": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="内容要重做（韩语教材）"
    ),
    "admitodds-ai-college-admissions-platform": dict(
        demand=2, payment=2, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="升学体系不同 + 咨询资质"
    ),
    "clamshell": dict(
        demand=3, payment=2, compliance=4, acquisition=3, localization=1, competition=3,
        blocker="无（唯一障碍是定价模式）"
    ),
    "colorcraft": dict(
        demand=3, payment=2, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="KDP 生态不在国内"
    ),
    "kodo": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="设计工具红海"
    ),
    "freelance-writing-club": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="业态不存在（付费约稿情报）"
    ),
    "agent-media": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="AI 内容备案与平台审核"
    ),
    "lapsula": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="病患数据与行业 Know-how"
    ),
    "lyro": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="音乐版权与分账"
    ),
    "flint": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="iMessage 渠道在国内不存在"
    ),
    "pdf-vault": dict(
        demand=2, payment=2, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="可转售授权业态不存在"
    ),
    "coldcaller-pro": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="个人信息保护法 + 外呼治理"
    ),
    "coldsire": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=2,
        blocker="工具链不同（Workspace / Instantly）"
    ),
    "knightvision": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="广告素材合规"
    ),
    "conductor": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="QuickBooks 生态不对应"
    ),
    "headshotpro": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=1, competition=1,
        blocker="人脸数据 + 红海竞争"
    ),
    "klar": dict(
        demand=4, payment=2, compliance=2, acquisition=3, localization=1, competition=2,
        blocker="考纲本地化"
    ),
    "refactoring-ui": dict(
        demand=2, payment=2, compliance=4, acquisition=3, localization=2,
        competition=2,
        blocker="买家在英文社区"
    ),
    "pdf-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="域名红利不可复制"
    ),
    "david-ai": dict(
        demand=3, payment=2, compliance=1, acquisition=1, localization=1,
        competition=2,
        blocker="直邮触达与个人信息"
    ),
    "sleek": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="设计工具红海（与 kodo 同因）"
    ),
    "3ak-track": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1,
        competition=2,
        blocker="训练内容要按国内体考重写"
    ),
    "plausible-analytics": dict(
        demand=3, payment=2, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="免费替代太强"
    ),
    # ── 2026-11-04 晋升批 A 档的 3 条 ──
    # 口径提醒：这三条门面收入都很小（$283–$1,308/近 30 天），分档测的是
    # 「能不能原样搬」，不是「这门生意值不值得做」，别拿去排推荐位。
    "appmockupgenerator": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=3,
        blocker="品类天花板低（12 个月累计仅 $4,657）"
    ),
    "blazedocs-io": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=3,
        blocker="开源免费方案分流（MinerU 一类）"
    ),
    "ihatepdf-cv": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=4, competition=2,
        blocker="PDF 工具箱已被 WPS/夸克/iLovePDF 占满"
    ),

    # ── 2026-11-04 B 档（综合 50–70）合并，共 121 条 ──
    "abstract27-ltd": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="Ghost 生态国内不存在"
    ),
    "adverse-monitor": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="威胁情报需本地数据源"
    ),
    "aerie": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="服务型交付"
    ),
    "agent-berlin": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道已拥挤"
    ),
    "ai-money-group": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="培训内卷 + 交付重"
    ),
    "ai-song-clip-generator": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="版权 + 国内免费"
    ),
    "ai-suitup": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="竞争者免费"
    ),
    "aicasadesign-com": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="房产广告合规"
    ),
    "aiselfi-es": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="人脸信息 + 竞争者多"
    ),
    "aiwritebook": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="出版链路需资质"
    ),
    "all-love-here": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="实物供应链"
    ),
    "anon-1": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="公开资料过少"
    ),
    "apparelai": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="电商图赛道拥挤"
    ),
    "appitstudio-apps-bundle-and-services": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="Mac 付费习惯弱"
    ),
    "archirise": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="国内出图工具已多"
    ),
    "artkit-cc": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖海外流量"
    ),
    "assignmentgpt-ai": dict(
        demand=4, payment=3, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="学科培训监管"
    ),
    "aswati-studio": dict(
        demand=1, payment=3, compliance=2, acquisition=2, localization=2, competition=4,
        blocker="宗教内容 + 人群极小"
    ),
    "august": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="通用助手被大厂免费覆盖"
    ),
    "aura-for-creators": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="国内生成工具免费且强"
    ),
    "authex-labs": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="国内邮件送达已有玩家"
    ),
    "authoritystack-ai": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="LDT 模式国内接受度低"
    ),
    "automate-chill": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2,
        blocker="本地 SEO 服务分散"
    ),
    "autoprintemail": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="场景较窄"
    ),
    "autotube": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=1,
        blocker="剪映免费"
    ),
    "babelbot": dict(
        demand=2, payment=2, compliance=4, acquisition=1, localization=1, competition=3,
        blocker="绑 Discord"
    ),
    "beautyflow": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="美业 SaaS 已有强势者"
    ),
    "bidder-iq": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="客群在国内极窄"
    ),
    "blabla": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="平台接口封闭"
    ),
    "blipix": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费"
    ),
    "blitzreels": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费"
    ),
    "blog2video": dict(
        demand=3, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="剪映免费覆盖"
    ),
    "bobby": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=2, competition=1,
        blocker="投顾资质红线"
    ),
    "brainote": dict(
        demand=3, payment=3, compliance=4, acquisition=1, localization=3, competition=2,
        blocker="学习工具国内免费多"
    ),
    "brainrot-mov": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="免费替代强"
    ),
    "buma-baby-tracker": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="儿童个人信息合规"
    ),
    "calma": dict(
        demand=2, payment=3, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="ADHD 人群认知弱"
    ),
    "car-identifier": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker="低客单识别类获客难"
    ),
    "car-play-connect": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=3,
        blocker="CarPlay 授权门槛"
    ),
    "carry-save-chatgpt-claude-grok-to-markdown-automatically": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker="受众绑海外聊天工具"
    ),
    "casa-fran-concepts-llc": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 LinkedIn"
    ),
    "castplus": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="播客生态国内另成体系"
    ),
    "catalister": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="绑 Shopify"
    ),
    "cattus-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费聚合站已泛滥"
    ),
    "chatdash-llc": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="白标需要技术支持"
    ),
    "chateasy": dict(
        demand=4, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 LINE 生态"
    ),
    "chatium": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="平台层被钉钉企微占死"
    ),
    "chatwith": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="大厂免费方案"
    ),
    "check-position": dict(
        demand=4, payment=4, compliance=3, acquisition=4, localization=2, competition=2,
        blocker="抓取源要换国内搜索引擎"
    ),
    "clairon-ai": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道已拥挤"
    ),
    "clarity-search-ai-inc": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 拥挤"
    ),
    "claude-cowork-course": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="课程赛道极度内卷"
    ),
    "claw-mart": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态"
    ),
    "clawdi": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="通用助手免费"
    ),
    "clawhost": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态"
    ),
    "clawhosters-com": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态"
    ),
    "clawrapid": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="依赖 OpenClaw 生态"
    ),
    "cleopatra-ai": dict(
        demand=2, payment=3, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="涉医疗美容边界"
    ),
    "clipspal": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="代运营不可复制"
    ),
    "cnvs": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="编码工具有大厂免费"
    ),
    "codefast": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=3, competition=1,
        blocker="课程内卷"
    ),
    "codowave": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="大厂编码代理已覆盖"
    ),
    "cole-striler": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=3, competition=1,
        blocker="音乐版权与免费竞品"
    ),
    "coloring-book": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=3,
        blocker=""
    ),
    "comic-translator": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="版权风险"
    ),
    "comicsai": dict(
        demand=3, payment=3, compliance=2, acquisition=3, localization=3, competition=2,
        blocker="生成内容标识"
    ),
    "conecta-fitness": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=2, competition=1,
        blocker="双边撮合需运营"
    ),
    "connor-cedro-seo-tampa-seo-consultant": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="个人服务不可扩"
    ),
    "contextbolt": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="绑海外 AI 工具生态"
    ),
    "controlresell": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=1, competition=2,
        blocker="绑法国转售平台"
    ),
    "convertlabs": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="本地实施重"
    ),
    "convitede": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=2,
        blocker="小程序竞品多"
    ),
    "cosden-code": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费教程覆盖"
    ),
    "coupleai": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="情感陪伴强监管"
    ),
    "cramberry": dict(
        demand=4, payment=3, compliance=1, acquisition=3, localization=2, competition=2,
        blocker="学科培训监管"
    ),
    "crawlbyte": dict(
        demand=4, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="数据抓取合规"
    ),
    "creads": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="同质化"
    ),
    "creativeclaw": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=3, competition=2,
        blocker=""
    ),
    "creator-creator": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=4, competition=2,
        blocker="写作工具免费替代多"
    ),
    "creatorskit": dict(
        demand=3, payment=3, compliance=4, acquisition=1, localization=2, competition=3,
        blocker="绑 TikTok 创作者生态"
    ),
    "dewstack": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="协作文档被大厂占位"
    ),
    "dreamini-ai": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="免费聚合站已泛滥"
    ),
    "elmo": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 赛道拥挤"
    ),
    "fitcal": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="免费竞品强"
    ),
    "frontrun": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="数据源在海外"
    ),
    "garagelab": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=3, competition=2,
        blocker=""
    ),
    "getmentioned": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="GEO 拥挤"
    ),
    "guidanz-inc": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="需对接企业系统"
    ),
    "hand-off": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="代运营重交付"
    ),
    "leadmetrics": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="数据合规 + 要接国内广告平台"
    ),
    "lina": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="处方药信息合规"
    ),
    "linkerflow": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Webflow"
    ),
    "lunarbuild": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="项目制不可复制"
    ),
    "mausbot": dict(
        demand=4, payment=3, compliance=3, acquisition=3, localization=3, competition=1,
        blocker="大厂桌面助手免费"
    ),
    "mentiondesk": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=3, competition=2,
        blocker="同类工具已多"
    ),
    "mind-reach": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="冥想付费习惯弱"
    ),
    "nagringa": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=1, competition=2,
        blocker="本地招聘网络难复制"
    ),
    "parlai": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=1, competition=2,
        blocker="绑 WhatsApp"
    ),
    "pluma": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=2, competition=1,
        blocker="记账 App 极卷"
    ),
    "rankspot": dict(
        demand=4, payment=4, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="内容平台规则"
    ),
    "rareui": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=2,
        blocker="开源付费在国内难"
    ),
    "rock-identifier": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="低客单识别类靠榜单获客"
    ),
    "sbc-crafter": dict(
        demand=1, payment=2, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="受众在国内极窄"
    ),
    "screenshot-to-code-whimsyworks-inc": dict(
        demand=4, payment=3, compliance=3, acquisition=4, localization=3, competition=2,
        blocker="海外同类工具强"
    ),
    "selltora": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Etsy/Pinterest"
    ),
    "social-genius-text-analyzer": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="交友类内容审核"
    ),
    "social-post-flow": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=2,
        blocker="多平台发布工具已多"
    ),
    "sprinta-ai": dict(
        demand=4, payment=4, compliance=3, acquisition=3, localization=1, competition=2,
        blocker="绑 WhatsApp"
    ),
    "stampsnap": dict(
        demand=2, payment=3, compliance=5, acquisition=2, localization=4, competition=3,
        blocker="集邮人群萎缩"
    ),
    "static-forms": dict(
        demand=3, payment=3, compliance=3, acquisition=4, localization=2, competition=2,
        blocker="需适配国内云与备案"
    ),
    "studio-zero": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=3, competition=2,
        blocker="电商图工具已多"
    ),
    "supascribe": dict(
        demand=2, payment=2, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Substack"
    ),
    "themedrop": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Shopify 生态"
    ),
    "url2pin": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=1, competition=3,
        blocker="绑 Pinterest"
    ),
    "velisto": dict(
        demand=4, payment=3, compliance=2, acquisition=3, localization=2, competition=2,
        blocker="房产广告合规"
    ),
    "vibeling": dict(
        demand=3, payment=3, compliance=4, acquisition=2, localization=3, competition=1,
        blocker="免费竞品极强"
    ),
    "videototext": dict(
        demand=4, payment=3, compliance=3, acquisition=2, localization=3, competition=1,
        blocker="讯飞等免费额度"
    ),
    "vincere-dev": dict(
        demand=4, payment=4, compliance=4, acquisition=2, localization=2, competition=2,
        blocker="人力外包不可复制"
    ),
    "volume-booster-louder-sound": dict(
        demand=3, payment=3, compliance=5, acquisition=2, localization=4, competition=2,
        blocker="免费替代多"
    ),
    "walldecor-io": dict(
        demand=2, payment=3, compliance=4, acquisition=2, localization=3, competition=3,
        blocker="家装预览有免费替代"
    ),
    "webclaw": dict(
        demand=3, payment=3, compliance=1, acquisition=4, localization=3, competition=2,
        blocker="抓取合规"
    ),

    # ── 2026-11-04 守卫拦下 8 条，人工重写后晋升，共 8 条 ──
    "adscriptly": dict(
        demand=3, payment=4, compliance=4, acquisition=2, localization=2, competition=3,
        blocker="Google Ads / Meta 投放本身在国内不可用，只能服务出海团队"
    ),
    "aera-browser": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=3, competition=3,
        blocker="浏览器自动化易触平台反爬与账号风控"
    ),
    "arc-raiders-map": dict(
        demand=3, payment=4, compliance=3, acquisition=3, localization=3, competition=3,
        blocker="吃单款游戏生命周期，且地图素材涉游戏版权"
    ),
    "dropapost": dict(
        demand=4, payment=4, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="多平台自动发布踩各平台的自动化规则，账号风险高"
    ),
    "pageturner": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="有声书版权与内容审核，个人开发者很难自证合规"
    ),
    "picoo": dict(
        demand=3, payment=3, compliance=4, acquisition=3, localization=2, competition=3,
        blocker="Roblox 在国内的访问与创作者生态都受限"
    ),
    "stat-ai": dict(
        demand=4, payment=3, compliance=2, acquisition=2, localization=2, competition=3,
        blocker="体育分析变现依赖下注场景，内地没有合法标的"
    ),
    "stivio": dict(
        demand=4, payment=3, compliance=4, acquisition=3, localization=4, competition=2,
        blocker="国内已有酷家乐、美间这类成熟玩家，且都是免费或低价"
    ),

    # ── 2026-11-04 404 档案 4 条降级口径入库 ──
    "changelogfy": dict(
        demand=3, payment=4, compliance=4, acquisition=3, localization=3, competition=2,
        blocker="反馈工具与 GitHub Issues、Lark 文档重叠，付费意愿低"
    ),
    "citron-app": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=4, competition=2,
        blocker="国内小红书/抖音菜谱内容生态与海外不同，且多语言是唯一壁垒"
    ),
    "ryn-vpn": dict(
        demand=4, payment=3, compliance=1, acquisition=2, localization=3, competition=2,
        blocker="国内 VPN 业务属电信增值业务，无资质不能做"
    ),
    "ton-piston": dict(
        demand=3, payment=4, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="法国求职入口（France Travail、La Bonne Alternance）不开放给外国开发者"
    ),

    # ── 2026-11-04 sotie 人工确认 still_alive 后入库 ──
    "sotie-ai-best-friend": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=3, competition=2,
        blocker="情感陪伴类 AI 在国内有深度合成标识与未成年人保护两道合规要求"
    ),

    # ── 2026-11-04 C 档（综合 < 50，别搬）合并，共 22 条 ──
    "100percentmarket": dict(
        demand=3, payment=3, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="字体版权与双边供给"
    ),
    "access-labs-pty-ltd": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="NFT 涉虚拟货币禁令"
    ),
    "akari-tickets": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="倒票合规"
    ),
    "anonymous-bot-for-slack": dict(
        demand=2, payment=2, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="平台与合规双卡"
    ),
    "apparate-pty-ltd": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="服务重 + 外呼合规"
    ),
    "asta": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="保健食品资质"
    ),
    "berace": dict(
        demand=1, payment=2, compliance=3, acquisition=2, localization=2, competition=3,
        blocker="受众窄 + 涉竞猜"
    ),
    "bibly": dict(
        demand=1, payment=3, compliance=1, acquisition=1, localization=2, competition=4,
        blocker="宗教内容合规"
    ),
    "book-the-move": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="线索买卖涉个人信息"
    ),
    "buzzrush": dict(
        demand=1, payment=3, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="酒类内容合规"
    ),
    "cash-cow-inc": dict(
        demand=3, payment=3, compliance=1, acquisition=2, localization=1, competition=2,
        blocker="外呼营销治理"
    ),
    "classyaction": dict(
        demand=1, payment=2, compliance=1, acquisition=2, localization=1, competition=4,
        blocker="制度不存在"
    ),
    "commentions-automated-comments-with-mentions": dict(
        demand=2, payment=3, compliance=2, acquisition=1, localization=1, competition=3,
        blocker="平台反作弊 + 绑 YouTube"
    ),
    "copykitten": dict(
        demand=2, payment=2, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="深度合成合规 + 绑 Discord"
    ),
    "cosmo": dict(
        demand=1, payment=2, compliance=4, acquisition=1, localization=1, competition=3,
        blocker="绑 Discord"
    ),
    "deeplead-io": dict(
        demand=3, payment=4, compliance=1, acquisition=2, localization=2, competition=2,
        blocker="外展合规"
    ),
    "esim-panel": dict(
        demand=2, payment=3, compliance=1, acquisition=2, localization=1, competition=3,
        blocker="电信资质 + eSIM 未开放"
    ),
    "fill": dict(
        demand=2, payment=3, compliance=1, acquisition=1, localization=2, competition=2,
        blocker="匿名社交强监管"
    ),
    "jarvis-bet": dict(
        demand=1, payment=1, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="博彩在内地违法"
    ),
    "polyalerthub": dict(
        demand=1, payment=1, compliance=1, acquisition=1, localization=1, competition=3,
        blocker="预测市场在境内被禁"
    ),
    "replymer": dict(
        demand=2, payment=3, compliance=2, acquisition=1, localization=1, competition=3,
        blocker="平台反自动化"
    ),
    "torrentclaw": dict(
        demand=3, payment=2, compliance=1, acquisition=2, localization=2, competition=3,
        blocker="盗版与侵权风险"
    ),
    # ── 2026-11-06 晋升的 4 条（人工裁定，依据 tmp/tm_md/*.md；草拟见 tmp/htm_screen/fit_scores_draft_20261106.md） ──
    "catering-funnels": dict(
        demand=3, payment=3, compliance=3, acquisition=2, localization=2, competition=2,
        blocker="本地生活渠道与代运营交付"
    ),
    "talkeasy": dict(
        demand=3, payment=3, compliance=4, acquisition=4, localization=3, competition=2,
        blocker="国内免费口语陪练挤压"
    ),
    "bubblav": dict(
        demand=3, payment=4, compliance=2, acquisition=2, localization=2, competition=2,
        blocker="海外集成生态与生成式服务备案"
    ),
    "arq": dict(
        demand=4, payment=4, compliance=4, acquisition=3, localization=3, competition=2,
        blocker=None
    ),
}


# ------------------------------------------------------------------ 计算

def load_json(name, default=None):
    if name == "cases":
        import case_store as CS          # 真值在 case_library（2026-11-05 起）
        return CS.load_cases()
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
    # 2026-11-05：`cases` 的真值已迁进 SQLite（case_library 表），
    # `data/cases.json` 只是镜像。收口到 case_store：写库 + 重导镜像，
    # 否则跑一次算分就让「库」和「镜像」分叉（算的分数只落进了文件）。
    if name == "cases":
        import case_store as CS
        CS.save_cases(payload)
        return
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
    kept = []
    notes_kept = []
    scored = []
    fb = fallback_dims()
    initial_note = fallback_note(fb)   # 唯一的文案初值源：中位数兜底说明

    for c in cases:
        cid = c.get("id")
        s = SCORES.get(cid)
        provisional = False
        if not s:
            # 表里没有，但库里已经有一份**非兜底**的判断 ⇒ 保留原值，只重排 rank。
            # 宁可少算一次，也不能用兜底分盖掉真实判断（表与库偶尔会不同步）。
            prev = c.get("china_fit") or {}
            if prev.get("score") is not None and not prev.get("provisional"):
                kept.append(cid)
                scored.append({
                    "case": c,
                    "preserve": True,
                    "dims": prev.get("dims"),
                    "raw": prev.get("raw"),
                    "score": prev["score"],
                    "note": prev.get("note", ""),
                    "blocker": prev.get("blocker"),
                    "provisional": False,
                })
                continue
            # 放宽：没有人工判断也照样算分，用全库中位数兜底并标记 provisional。
            # 2026-11-06 之前这里 `continue` + 末尾 `pop("china_fit")`，跑一次就把
            # 未填案例的历史评分清空 —— 而 preflight 只是 WARN，没人会注意到。
            s = dict(fb)
            provisional = True
            missing.append(cid)
        dims = {k: s[k] for k, _label, _desc in DIMS}
        raw = weighted_total(dims)
        # 文案以库里为唯一真值（PRD #182 → #183 → #184）：SCORES 只存分数，
        # 表里已无 note 副本；库里有人工判断就保留，没有才用兜底初值。
        note, note_kept = SNP.resolve_note(c, "china_fit", initial_note)
        if note_kept:
            notes_kept.append(cid)
        scored.append({
            "case": c,
            "dims": dims,
            "raw": raw,
            "score": to_score(raw),
            "note": note,
            "blocker": s.get("blocker"),
            "provisional": provisional,
            "preserve": False,
        })

    if missing:
        print("[!] 以下案例尚无人工评分，本次按全库中位数兜底（%d 条）：%s"
              % (len(missing), ", ".join(missing)))
        print("    兜底结果带 provisional 标记，页面照常显示；要精确结论就补进 SCORES。\n")
    if kept:
        print("[i] 表里没有、但库里已有判断，已保留原值（%d 条）：%s\n"
              % (len(kept), ", ".join(kept)))
    if notes_kept:
        print("[i] 库里 note 是唯一真值，已保留文案（%d 条）；SCORES 表已无文案副本，"
              "也没有覆盖开关（PRD #184）。\n" % len(notes_kept))

    # 排名：分数降序；同分时把有 blocker 的排后面，再按名称
    scored.sort(key=lambda x: (-x["score"], 1 if x["blocker"] else 0,
                               x["case"].get("name") or ""))

    for i, item in enumerate(scored, 1):
        item["rank"] = i
        item["medal"] = medal_for(i)
        c = item["case"]
        if item["preserve"]:
            c["china_fit"]["rank"] = i            # 只重排，不动 dims / note / score
            c["china_fit"]["medal"] = item["medal"]
            continue
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
        if item["provisional"]:
            c["china_fit"]["provisional"] = True

    # 2026-11-06 起不再「没评分就清掉旧分」：现在每条都会被算到（缺表走中位数
    # 兜底），历史分不会被一次重跑抹掉。原先那段 pop 是 preflight 只报 WARN、
    # 没人察觉丢分的根源。

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
    print("\n已写入 case_library（%d 条带 china_fit），镜像 data/cases.json 已重导" % len(scored))
    print("页面刷新即可看到「国内移植排行」视图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
