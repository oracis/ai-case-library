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
    python scripts/score_solo_fit.py              # 打分并写回 case_library（+重导镜像）
    python scripts/score_solo_fit.py --dry-run    # 只看结果
    python scripts/score_solo_fit.py --top 10     # 只看前 10
    python scripts/score_solo_fit.py --threshold 75   # 换及格线
"""

import argparse
import json
import os
import sys
from datetime import datetime

import score_note_policy as SNP   # 文案裁定单一源（PRD #183）

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


# ------------------------------------------------------------------ 评分表
# PRD #184：文案（delivery_note）的唯一真值在 case_library。表里第二位恒为
# NOTE_IN_LIBRARY（空串），只为维持 (分数, 理由) 的历史 tuple 形状；要改文案
# 请回库里改，不要写回这张表 —— 库里的数字会随快照刷新，源码副本必然漂移。
NOTE_IN_LIBRARY = ""

DELIVERY = {
    # ── 2026-10-26 backlog「有条件」档入库的 6 条 ──
    "serviceform": (2, NOTE_IN_LIBRARY),
    "salesrobot": (3, NOTE_IN_LIBRARY),
    "jobright": (4, NOTE_IN_LIBRARY),
    "sandra-ai": (3, NOTE_IN_LIBRARY),
    "outward": (2, NOTE_IN_LIBRARY),
    "workhero": (1, NOTE_IN_LIBRARY),
    # ── 2026-10-26 backlog「形态可抄」第一梯队入库的 3 条 ──
    "autoshorts": (3, NOTE_IN_LIBRARY),
    "rosie": (4, NOTE_IN_LIBRARY),
    "boredhumans": (3, NOTE_IN_LIBRARY),
    # ── 2026-10-26 TrustMRR 档案已下架的两条 ──
    "gistly": (2, NOTE_IN_LIBRARY),
    "research-match": (4, NOTE_IN_LIBRARY),
    # ── 2026-10-26 从 howtomakemi 官方披露线入库的 8 条 ──
    "arcads": (4, NOTE_IN_LIBRARY),
    "instantly": (4, NOTE_IN_LIBRARY),
    "ahron": (2, NOTE_IN_LIBRARY),
    "fyxer": (3, NOTE_IN_LIBRARY),
    "heyreach": (3, NOTE_IN_LIBRARY),
    "campfire": (1, NOTE_IN_LIBRARY),
    "wispr-flow": (5, NOTE_IN_LIBRARY),
    "build-concierge": (2, NOTE_IN_LIBRARY),
    "gojiberryai": (3, NOTE_IN_LIBRARY),
    "sierra": (1, NOTE_IN_LIBRARY),
    "genius-ai": (3, NOTE_IN_LIBRARY),
    "nitra": (1, NOTE_IN_LIBRARY),
    "viktor": (2, NOTE_IN_LIBRARY),
    "chatbase": (4, NOTE_IN_LIBRARY),
    "rezi": (5, NOTE_IN_LIBRARY),
    "postiz": (4, NOTE_IN_LIBRARY),
    "shipfast": (5, NOTE_IN_LIBRARY),
    "trustmrr": (5, NOTE_IN_LIBRARY),
    "comp-ai": (2, NOTE_IN_LIBRARY),
    "aeo-engine": (5, NOTE_IN_LIBRARY),
    "lancer-app": (4, NOTE_IN_LIBRARY),
    "speel-co": (4, NOTE_IN_LIBRARY),
    "visualizee-ai": (4, NOTE_IN_LIBRARY),
    "checkvibe": (5, NOTE_IN_LIBRARY),
    "storyshort-ai": (4, NOTE_IN_LIBRARY),
    "meerkats-ai": (2, NOTE_IN_LIBRARY),
    "coral": (4, NOTE_IN_LIBRARY),
    "startclaw": (4, NOTE_IN_LIBRARY),
    "outrank": (5, NOTE_IN_LIBRARY),
    "marc-lou-portfolio": (5, NOTE_IN_LIBRARY),
    "bustem": (4, NOTE_IN_LIBRARY),
    "kibu": (1, NOTE_IN_LIBRARY),
    "pieter-levels": (5, NOTE_IN_LIBRARY),

    # ---- 2026-09-20 发布的一批 ----
    "1lookup": (4, NOTE_IN_LIBRARY),
    "stan": (2, NOTE_IN_LIBRARY),
    "magicslides-app": (5, NOTE_IN_LIBRARY),
    "autoreels-ai": (4, NOTE_IN_LIBRARY),
    "insect-bite-id": (5, NOTE_IN_LIBRARY),

    # ---- 2026-09-25 补的一批（TrustMRR 挂牌标的为主）----
    "voklit": (2, NOTE_IN_LIBRARY),
    "prosp": (3, NOTE_IN_LIBRARY),
    "divine-widgets": (5, NOTE_IN_LIBRARY),
    "promptmonitor-io": (4, NOTE_IN_LIBRARY),
    "uplinked-b-v": (2, NOTE_IN_LIBRARY),
    "le19emetrou": (5, NOTE_IN_LIBRARY),
    "search1api": (4, NOTE_IN_LIBRARY),

    # ---- 2026-09-30 补的两条 ----
    "mort": (4, NOTE_IN_LIBRARY),
    "quran-unlock": (5, NOTE_IN_LIBRARY),

    # ---- 2026-10-05 补的两条（premium 晋升时漏了，卡住 fit_ready 闸门）----
    "harperai": (2, NOTE_IN_LIBRARY),
    "easymix": (2, NOTE_IN_LIBRARY),

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
    "cometly": (1, NOTE_IN_LIBRARY),

    # vid-ai 判 3：产品侧是自助的，但内容质量要人兜。
    # AI 出的片子客户总要人看一遍、要改，批量客户（一天要10 条）会持续提要求。
    # 团队 2–5 人 —— 一个人能跑，但要有人接需求、改流程。
    "vid-ai": (3, NOTE_IN_LIBRARY),

    # podawaa 判 4：LinkedIn 工具本身是自助订阅，分析报告由平台出。
    # 交付轻，唯一重的地方是账号封禁申诉—— 那要人跟。
    "podawaa": (4, NOTE_IN_LIBRARY),

    # publbee 判 4：纯自助工具，作者自己配关键词与生成。
    # 交付几乎为零，只有出版合规类问题（会不会被 KDP 拒）偶尔要答。
    "publbee": (4, NOTE_IN_LIBRARY),

    # vectosolve 判 5：纯转换工具，跑完出文件，没有交付环节。
    # 反推单价约 $7，这个价位不可能也不需要有交付。
    "vectosolve": (5, NOTE_IN_LIBRARY),

    # augora-ai 判 4：追踪配置可在后台自助完成。
    # 但 Shopify 商家接不好追踪会来问「为什么我的数字对不上」，
    # 这类问题一半是配置一半是 Meta 侧的事，答起来琐碎但不需要驻场。
    "augora-ai": (4, NOTE_IN_LIBRARY),

    # wpconvert 判 4：这批里最接近自助的一条，也是唯一拿到 solo_possible 的。
    # 转换本身是纯工程交付（站点转好就完事）；偶尔要答「某个页面没转对」。
    "wpconvert-ai-convert-ai-sites-to-wordpress": (
        4, NOTE_IN_LIBRARY),

    # ---- 2026-10-15 补：门槛放宽后新晋升的 29 条 ----
    # 判据一律按**交付形态**，不按收入高低：体量大往往正因为它需要团队。
    "alchie": (5, NOTE_IN_LIBRARY),
    "ae-intelligence": (5, NOTE_IN_LIBRARY),
    "abmaxx": (5, NOTE_IN_LIBRARY),
    "autocontent-api": (5, NOTE_IN_LIBRARY),
    "apiframe": (5, NOTE_IN_LIBRARY),
    "ai-toolbox": (5, NOTE_IN_LIBRARY),
    "chessigma": (5, NOTE_IN_LIBRARY),
    "storyshort": (4, NOTE_IN_LIBRARY),
    "based-labs-ai": (4, NOTE_IN_LIBRARY),
    "draftly": (4, NOTE_IN_LIBRARY),
    "audn-ai": (4, NOTE_IN_LIBRARY),
    "hypit-ai": (4, NOTE_IN_LIBRARY),
    "chatseo": (4, NOTE_IN_LIBRARY),
    "brawler": (4, NOTE_IN_LIBRARY),
    "amicited": (4, NOTE_IN_LIBRARY),
    "angel-match": (4, NOTE_IN_LIBRARY),
    "calendesk": (4, NOTE_IN_LIBRARY),
    "capgo": (4, NOTE_IN_LIBRARY),
    # ⚠ 这里原来误写成「OTA 热更新」——那是 capgo 的判据，串条了。
    # cod-dex 是互动编程学习平台（课程 + 练习 + 勋章 + Club 订阅），
    # 交付形态是「内容一次性做好、后续自助学」，不是按客户交付。
    "cod-dex": (4, NOTE_IN_LIBRARY),
    "backlinker-ai": (4, NOTE_IN_LIBRARY),
    "cartboss": (4, NOTE_IN_LIBRARY),
    "adspirer": (3, NOTE_IN_LIBRARY),
    "avenue-ticketing-inc": (3, NOTE_IN_LIBRARY),
    "corsidia": (3, NOTE_IN_LIBRARY),
    "applyall": (2, NOTE_IN_LIBRARY),
    "analytics-mates": (2, NOTE_IN_LIBRARY),
    "adelante-ltd": (2, NOTE_IN_LIBRARY),
    "brevilabs": (1, NOTE_IN_LIBRARY),
    "backpedal-ltd": (1, NOTE_IN_LIBRARY),

    # ---- 同日补：上一批里只有 DELIVERY 没有 replicability 的 12 条 ----
    "boothie-ai-photo-editor": (4, NOTE_IN_LIBRARY),
    "nutria-9e3eaa": (5, NOTE_IN_LIBRARY),
    "fastwrite-io": (5, NOTE_IN_LIBRARY),
    "downconvert-sketchup-files": (5, NOTE_IN_LIBRARY),
    "macwall": (5, NOTE_IN_LIBRARY),
    "growingupvideo": (4, NOTE_IN_LIBRARY),
    "homy": (5, NOTE_IN_LIBRARY),
    "stealth-rdp": (2, NOTE_IN_LIBRARY),
    "white-glove-content": (2, NOTE_IN_LIBRARY),
    "pet-plus-us": (4, NOTE_IN_LIBRARY),
    "wagiff-app": (3, NOTE_IN_LIBRARY),
    "quotedeck": (3, NOTE_IN_LIBRARY),
    # PRD #185：删掉 "private-venture-1" —— 166 条晋升时留下的库外幽灵 id，
    # git 历史里从未进过 data/cases.json。它会混进 fallback_delivery() 的
    # 中位数样本，让兜底文案虚报「全库 466 条判断」（真实 465 条），且与
    # SCORES 的 465 条对不上。中位数实测不变（4），99 条兜底分值零影响。
    "adawcag": (5, NOTE_IN_LIBRARY),
    "aeo-checker": (3, NOTE_IN_LIBRARY),
    "ad-machine": (2, NOTE_IN_LIBRARY),
    "advanced-gsc-mcp": (5, NOTE_IN_LIBRARY),
    "3d-agent": (5, NOTE_IN_LIBRARY),
    "adtruffle": (4, NOTE_IN_LIBRARY),
    "ai-designer": (3, NOTE_IN_LIBRARY),
    "acquire-community": (4, NOTE_IN_LIBRARY),
    "atelier-nuage": (5, NOTE_IN_LIBRARY),
    "fireply-ai": (3, NOTE_IN_LIBRARY),
    "ismalicious": (5, NOTE_IN_LIBRARY),
    "uizze": (5, NOTE_IN_LIBRARY),
    "barron-tech-llc": (2, NOTE_IN_LIBRARY),
    "autoclip": (3, NOTE_IN_LIBRARY),
    "chariot-ai": (5, NOTE_IN_LIBRARY),
    "openalternative": (3, NOTE_IN_LIBRARY),
    "breadsync": (2, NOTE_IN_LIBRARY),
    "cbt-artificial-intelligence-technology-inc-dba-zuuz": (4, NOTE_IN_LIBRARY),
    "firecert": (3, NOTE_IN_LIBRARY),
    "arcton": (4, NOTE_IN_LIBRARY),
    "captureflow": (2, NOTE_IN_LIBRARY),
    "corey-chiu": (5, NOTE_IN_LIBRARY),
    "opentweet": (2, NOTE_IN_LIBRARY),
    "apogee": (3, NOTE_IN_LIBRARY),
    "blawgy-com": (5, NOTE_IN_LIBRARY),
    "autosubtitles": (4, NOTE_IN_LIBRARY),
    "axis-labc2026": (3, NOTE_IN_LIBRARY),
    "astonish-email-limited": (3, NOTE_IN_LIBRARY),
    "bluff-ai": (4, NOTE_IN_LIBRARY),
    "baller": (4, NOTE_IN_LIBRARY),
    "athenify": (4, NOTE_IN_LIBRARY),
    "christian-tech-jobs": (3, NOTE_IN_LIBRARY),
    "bouncer": (4, NOTE_IN_LIBRARY),
    "blitzcut": (4, NOTE_IN_LIBRARY),
    "bidva-buy-linkedin-accounts": (5, NOTE_IN_LIBRARY),
    "coprep-ai": (4, NOTE_IN_LIBRARY),
    "canopy-api": (4, NOTE_IN_LIBRARY),
    "billionverify": (5, NOTE_IN_LIBRARY),
    "algofuse-ai": (4, NOTE_IN_LIBRARY),
    "botric": (5, NOTE_IN_LIBRARY),
    "checkform-gymnastics": (4, NOTE_IN_LIBRARY),
    "claws-host": (5, NOTE_IN_LIBRARY),
    "coco": (4, NOTE_IN_LIBRARY),
    "blogr-ai": (5, NOTE_IN_LIBRARY),
    "client-one": (5, NOTE_IN_LIBRARY),
    "coralflavor": (5, NOTE_IN_LIBRARY),
    "apexvol": (2, NOTE_IN_LIBRARY),
    "kitchendesign": (5, NOTE_IN_LIBRARY),
    "blacktwist": (2, NOTE_IN_LIBRARY),
    "cloud-backup-for-podio": (5, NOTE_IN_LIBRARY),
    "spark-playground": (4, NOTE_IN_LIBRARY),
    "clawlink": (4, NOTE_IN_LIBRARY),
    "ai-text-humanizer-netherlands-belgium": (4, NOTE_IN_LIBRARY),
    "agentskit": (3, NOTE_IN_LIBRARY),
    "altitude-cleaning-crew": (1, NOTE_IN_LIBRARY),
    "anyapi": (5, NOTE_IN_LIBRARY),
    "aprs-conseil": (2, NOTE_IN_LIBRARY),
    "athletiks": (3, NOTE_IN_LIBRARY),
    "bakering-app-sl": (1, NOTE_IN_LIBRARY),
    "bettrbot": (5, NOTE_IN_LIBRARY),
    "brandmylaptop": (3, NOTE_IN_LIBRARY),
    "brandmymac": (3, NOTE_IN_LIBRARY),
    "brokenstudios": (5, NOTE_IN_LIBRARY),
    "chronoid": (5, NOTE_IN_LIBRARY),
    "firstrep": (4, NOTE_IN_LIBRARY),
    "orbio": (5, NOTE_IN_LIBRARY),
    "ai-prank-call": (5, NOTE_IN_LIBRARY),
    "betting-tips": (5, NOTE_IN_LIBRARY),
    "ai-voice-calls": (3, NOTE_IN_LIBRARY),
    "demopolish": (4, NOTE_IN_LIBRARY),
    "chatsell-co": (4, NOTE_IN_LIBRARY),
    "roster-for-slack": (4, NOTE_IN_LIBRARY),
    "static-run": (5, NOTE_IN_LIBRARY),
    "ai-math-solver": (5, NOTE_IN_LIBRARY),
    "ai-smart-decor": (5, NOTE_IN_LIBRARY),
    "verse": (4, NOTE_IN_LIBRARY),
    "clawbox": (5, NOTE_IN_LIBRARY),
    "bankstatemently": (1, NOTE_IN_LIBRARY),
    "fit-it-on": (2, NOTE_IN_LIBRARY),
    "ai-influencer-generator": (3, NOTE_IN_LIBRARY),
    "flowy": (3, NOTE_IN_LIBRARY),
    "check-id": (5, NOTE_IN_LIBRARY),
    "chat-whisperer": (2, NOTE_IN_LIBRARY),
    "quickvoc": (3, NOTE_IN_LIBRARY),
    "blue-esim": (5, NOTE_IN_LIBRARY),
    "captime": (4, NOTE_IN_LIBRARY),
    "alodata": (5, NOTE_IN_LIBRARY),
    "chiptune": (5, NOTE_IN_LIBRARY),
    "calsy": (5, NOTE_IN_LIBRARY),
    "firsto": (5, NOTE_IN_LIBRARY),
    "atslebenslauf-com": (4, NOTE_IN_LIBRARY),
    "anymelo": (4, NOTE_IN_LIBRARY),
    "reddit-grow": (3, NOTE_IN_LIBRARY),
    "cleaner": (4, NOTE_IN_LIBRARY),
    "arbichat-s-l": (5, NOTE_IN_LIBRARY),
    "bromeai": (5, NOTE_IN_LIBRARY),
    "fire-tech-apps-llc": (5, NOTE_IN_LIBRARY),
    "bolsai": (2, NOTE_IN_LIBRARY),
    "sportsjobs-online": (3, NOTE_IN_LIBRARY),
    "chartvio": (5, NOTE_IN_LIBRARY),
    "brikk": (3, NOTE_IN_LIBRARY),
    "elispeak": (4, NOTE_IN_LIBRARY),
    "myterrace-net": (5, NOTE_IN_LIBRARY),
    "cat-games": (5, NOTE_IN_LIBRARY),
    "c12s-ai": (3, NOTE_IN_LIBRARY),
    "forage": (5, NOTE_IN_LIBRARY),
    "aiwith-me": (3, NOTE_IN_LIBRARY),
    "june": (4, NOTE_IN_LIBRARY),
    "blockit": (4, NOTE_IN_LIBRARY),
    "orbit-ai": (5, NOTE_IN_LIBRARY),
    "aidelly": (2, NOTE_IN_LIBRARY),
    "car-docs-ro": (3, NOTE_IN_LIBRARY),
    "ai-swap-facetik": (4, NOTE_IN_LIBRARY),
    "climbx": (3, NOTE_IN_LIBRARY),
    "blainy": (4, NOTE_IN_LIBRARY),
    "aistudios": (4, NOTE_IN_LIBRARY),
    "car-dealer-tracker": (4, NOTE_IN_LIBRARY),
    "careerkit": (4, NOTE_IN_LIBRARY),
    "chunks-microlearning": (4, NOTE_IN_LIBRARY),
    "alienart-io": (3, NOTE_IN_LIBRARY),
    "chartsgpt": (2, NOTE_IN_LIBRARY),
    "brandiseer": (5, NOTE_IN_LIBRARY),
    "clear-food": (5, NOTE_IN_LIBRARY),
    "bolty": (4, NOTE_IN_LIBRARY),
    "chefgpt": (4, NOTE_IN_LIBRARY),
    "basincheck": (4, NOTE_IN_LIBRARY),
    "alphascope": (4, NOTE_IN_LIBRARY),
    "business-freelance": (4, NOTE_IN_LIBRARY),
    "aura-ai-ai-video-image-generator": (4, NOTE_IN_LIBRARY),
    "coaching-marketing-kit": (4, NOTE_IN_LIBRARY),
    "fishingai": (4, NOTE_IN_LIBRARY),
    "cooldock": (5, NOTE_IN_LIBRARY),
    "auraclaw": (5, NOTE_IN_LIBRARY),
    "bloxg-roblox-marketing-agency": (5, NOTE_IN_LIBRARY),
    "archifruits-ai": (5, NOTE_IN_LIBRARY),
    "cheatmate": (4, NOTE_IN_LIBRARY),
    "chatedu-inc": (4, NOTE_IN_LIBRARY),
    "agentsguild": (5, NOTE_IN_LIBRARY),
    "ai-baby-generator": (5, NOTE_IN_LIBRARY),
    "adstudio": (4, NOTE_IN_LIBRARY),
    "adriselab": (3, NOTE_IN_LIBRARY),
    "ai-cover-music-video": (4, NOTE_IN_LIBRARY),
    "affililist": (3, NOTE_IN_LIBRARY),
    "ai-detector-image-checker": (4, NOTE_IN_LIBRARY),
    "admirror": (3, NOTE_IN_LIBRARY),
    "account-expense-manager": (4, NOTE_IN_LIBRARY),
    "anon-2": (2, NOTE_IN_LIBRARY),
    "7running": (4, NOTE_IN_LIBRARY),
    "private-venture": (5, NOTE_IN_LIBRARY),
    "aftersora": (4, NOTE_IN_LIBRARY),
    "3dplotter": (5, NOTE_IN_LIBRARY),
    "agence-web-abstract27": (5, NOTE_IN_LIBRARY),
    "20-0": (5, NOTE_IN_LIBRARY),
    "ai-directories": (3, NOTE_IN_LIBRARY),
    "adstellar": (2, NOTE_IN_LIBRARY),
    "aesty": (5, NOTE_IN_LIBRARY),
    "adle": (4, NOTE_IN_LIBRARY),
    "acsperf": (5, NOTE_IN_LIBRARY),
    "the-stealthiest-of-ventures": (5, NOTE_IN_LIBRARY),
    "gtm-help": (3, NOTE_IN_LIBRARY),
    "mac-duo": (5, NOTE_IN_LIBRARY),
    "dope-data": (5, NOTE_IN_LIBRARY),
    "orior-ai": (3, NOTE_IN_LIBRARY),
    "agenkit": (5, NOTE_IN_LIBRARY),
    "kreapro": (4, NOTE_IN_LIBRARY),
    "toonthis": (3, NOTE_IN_LIBRARY),
    # ── 2026-10-27 批次漏补的两条（2026-10-29 补）──
    # ⚠ 537b443 把 upvoty / flodesk 写进 cases.json 时只带了 replicability，
    # 没跟 SOLO / CHINA 两张判断表 ⇒ test_fit_ready 的「不许缺判断」从 10-27
    # 起一直红（本地与 CI 同红，不是环境差异）。补的时候没套下面的通用文案：
    # 这两条都是自助订阅，但交付负担不同 —— 一条在集成维护，一条在送达率。
    "upvoty": (4, NOTE_IN_LIBRARY),
    "flodesk": (3, NOTE_IN_LIBRARY),
    # ── 2026-10-29 补：fit_ready --check 报的 26 条 ──
    # 18 条是本轮晋升进来的，8 条是更早发布时漏掉的。判据一律按**交付形态**
    # 而不是收入高低：有些案子体量很大（headshotpro、conductor），但交付仍然是
    # 自助的；有些体量很小（freelance-writing-club、agent-media），交付却是
    # 每天要有人真干活 —— 后者才是「一个人做不了」的那类。
    "flowmance-templates": (4, NOTE_IN_LIBRARY),
    "neoscene": (4, NOTE_IN_LIBRARY),
    "boxer-ai": (4, NOTE_IN_LIBRARY),
    "kori": (4, NOTE_IN_LIBRARY),
    "admitodds-ai-college-admissions-platform": (3, NOTE_IN_LIBRARY),
    "clamshell": (5, NOTE_IN_LIBRARY),
    "colorcraft": (4, NOTE_IN_LIBRARY),
    "kodo": (4, NOTE_IN_LIBRARY),
    "freelance-writing-club": (2, NOTE_IN_LIBRARY),
    "agent-media": (2, NOTE_IN_LIBRARY),
    "lapsula": (3, NOTE_IN_LIBRARY),
    "lyro": (4, NOTE_IN_LIBRARY),
    "flint": (3, NOTE_IN_LIBRARY),
    "pdf-vault": (3, NOTE_IN_LIBRARY),
    "coldcaller-pro": (3, NOTE_IN_LIBRARY),
    "coldsire": (3, NOTE_IN_LIBRARY),
    "knightvision": (4, NOTE_IN_LIBRARY),
    "conductor": (3, NOTE_IN_LIBRARY),
    "headshotpro": (4, NOTE_IN_LIBRARY),
    "klar": (3, NOTE_IN_LIBRARY),
    "refactoring-ui": (5, NOTE_IN_LIBRARY),
    "pdf-ai": (4, NOTE_IN_LIBRARY),
    "david-ai": (2, NOTE_IN_LIBRARY),
    "sleek": (4, NOTE_IN_LIBRARY),
    "3ak-track": (4, NOTE_IN_LIBRARY),
    "plausible-analytics": (3, NOTE_IN_LIBRARY),

    # ── 2026-11-04 晋升批 A 档（可搬性综合 ≥70）的 3 条 ──
    # 三条都是「传文件—出文件」的纯工具，交付环节天然为零；要长期盯的
    # 都不是客户，而是版本（机型尺寸、解析格式、浏览器内核）。
    "appmockupgenerator": (5, NOTE_IN_LIBRARY),
    "blazedocs-io": (5, NOTE_IN_LIBRARY),
    "ihatepdf-cv": (5, NOTE_IN_LIBRARY),

    # ── 2026-11-04 B 档（综合 50–70）合并，共 121 条 ──
    "abstract27-ltd": (3, NOTE_IN_LIBRARY),
    "adverse-monitor": (2, NOTE_IN_LIBRARY),
    "aerie": (1, NOTE_IN_LIBRARY),
    "agent-berlin": (4, NOTE_IN_LIBRARY),
    "ai-money-group": (2, NOTE_IN_LIBRARY),
    "ai-song-clip-generator": (5, NOTE_IN_LIBRARY),
    "ai-suitup": (5, NOTE_IN_LIBRARY),
    "aicasadesign-com": (4, NOTE_IN_LIBRARY),
    "aiselfi-es": (5, NOTE_IN_LIBRARY),
    "aiwritebook": (4, NOTE_IN_LIBRARY),
    "all-love-here": (1, NOTE_IN_LIBRARY),
    "anon-1": (4, NOTE_IN_LIBRARY),
    "apparelai": (4, NOTE_IN_LIBRARY),
    "appitstudio-apps-bundle-and-services": (5, NOTE_IN_LIBRARY),
    "archirise": (4, NOTE_IN_LIBRARY),
    "artkit-cc": (4, NOTE_IN_LIBRARY),
    "assignmentgpt-ai": (4, NOTE_IN_LIBRARY),
    "aswati-studio": (5, NOTE_IN_LIBRARY),
    "august": (4, NOTE_IN_LIBRARY),
    "aura-for-creators": (5, NOTE_IN_LIBRARY),
    "authex-labs": (3, NOTE_IN_LIBRARY),
    "authoritystack-ai": (3, NOTE_IN_LIBRARY),
    "automate-chill": (4, NOTE_IN_LIBRARY),
    "autoprintemail": (4, NOTE_IN_LIBRARY),
    "autotube": (5, NOTE_IN_LIBRARY),
    "babelbot": (4, NOTE_IN_LIBRARY),
    "beautyflow": (4, NOTE_IN_LIBRARY),
    "bidder-iq": (4, NOTE_IN_LIBRARY),
    "blabla": (3, NOTE_IN_LIBRARY),
    "blipix": (5, NOTE_IN_LIBRARY),
    "blitzreels": (5, NOTE_IN_LIBRARY),
    "blog2video": (5, NOTE_IN_LIBRARY),
    "bobby": (4, NOTE_IN_LIBRARY),
    "brainote": (5, NOTE_IN_LIBRARY),
    "brainrot-mov": (5, NOTE_IN_LIBRARY),
    "buma-baby-tracker": (5, NOTE_IN_LIBRARY),
    "calma": (5, NOTE_IN_LIBRARY),
    "car-identifier": (5, NOTE_IN_LIBRARY),
    "car-play-connect": (5, NOTE_IN_LIBRARY),
    "carry-save-chatgpt-claude-grok-to-markdown-automatically": (5, NOTE_IN_LIBRARY),
    "casa-fran-concepts-llc": (2, NOTE_IN_LIBRARY),
    "castplus": (4, NOTE_IN_LIBRARY),
    "catalister": (5, NOTE_IN_LIBRARY),
    "cattus-ai": (5, NOTE_IN_LIBRARY),
    "chatdash-llc": (3, NOTE_IN_LIBRARY),
    "chateasy": (3, NOTE_IN_LIBRARY),
    "chatium": (2, NOTE_IN_LIBRARY),
    "chatwith": (4, NOTE_IN_LIBRARY),
    "check-position": (5, NOTE_IN_LIBRARY),
    "clairon-ai": (4, NOTE_IN_LIBRARY),
    "clarity-search-ai-inc": (4, NOTE_IN_LIBRARY),
    "claude-cowork-course": (2, NOTE_IN_LIBRARY),
    "claw-mart": (5, NOTE_IN_LIBRARY),
    "clawdi": (3, NOTE_IN_LIBRARY),
    "clawhost": (5, NOTE_IN_LIBRARY),
    "clawhosters-com": (5, NOTE_IN_LIBRARY),
    "clawrapid": (5, NOTE_IN_LIBRARY),
    "cleopatra-ai": (5, NOTE_IN_LIBRARY),
    "clipspal": (1, NOTE_IN_LIBRARY),
    "cnvs": (4, NOTE_IN_LIBRARY),
    "codefast": (2, NOTE_IN_LIBRARY),
    "codowave": (4, NOTE_IN_LIBRARY),
    "cole-striler": (5, NOTE_IN_LIBRARY),
    "coloring-book": (5, NOTE_IN_LIBRARY),
    "comic-translator": (4, NOTE_IN_LIBRARY),
    "comicsai": (4, NOTE_IN_LIBRARY),
    "conecta-fitness": (3, NOTE_IN_LIBRARY),
    "connor-cedro-seo-tampa-seo-consultant": (1, NOTE_IN_LIBRARY),
    "contextbolt": (5, NOTE_IN_LIBRARY),
    "controlresell": (2, NOTE_IN_LIBRARY),
    "convertlabs": (2, NOTE_IN_LIBRARY),
    "convitede": (4, NOTE_IN_LIBRARY),
    "cosden-code": (4, NOTE_IN_LIBRARY),
    "coupleai": (5, NOTE_IN_LIBRARY),
    "cramberry": (4, NOTE_IN_LIBRARY),
    "crawlbyte": (3, NOTE_IN_LIBRARY),
    "creads": (4, NOTE_IN_LIBRARY),
    "creativeclaw": (3, NOTE_IN_LIBRARY),
    "creator-creator": (5, NOTE_IN_LIBRARY),
    "creatorskit": (4, NOTE_IN_LIBRARY),
    "dewstack": (4, NOTE_IN_LIBRARY),
    "dreamini-ai": (5, NOTE_IN_LIBRARY),
    "elmo": (4, NOTE_IN_LIBRARY),
    "fitcal": (5, NOTE_IN_LIBRARY),
    "frontrun": (4, NOTE_IN_LIBRARY),
    "garagelab": (5, NOTE_IN_LIBRARY),
    "getmentioned": (4, NOTE_IN_LIBRARY),
    "guidanz-inc": (2, NOTE_IN_LIBRARY),
    "hand-off": (2, NOTE_IN_LIBRARY),
    "leadmetrics": (3, NOTE_IN_LIBRARY),
    "lina": (5, NOTE_IN_LIBRARY),
    "linkerflow": (5, NOTE_IN_LIBRARY),
    "lunarbuild": (1, NOTE_IN_LIBRARY),
    "mausbot": (4, NOTE_IN_LIBRARY),
    "mentiondesk": (3, NOTE_IN_LIBRARY),
    "mind-reach": (5, NOTE_IN_LIBRARY),
    "nagringa": (3, NOTE_IN_LIBRARY),
    "parlai": (5, NOTE_IN_LIBRARY),
    "pluma": (4, NOTE_IN_LIBRARY),
    "rankspot": (4, NOTE_IN_LIBRARY),
    "rareui": (4, NOTE_IN_LIBRARY),
    "rock-identifier": (5, NOTE_IN_LIBRARY),
    "sbc-crafter": (5, NOTE_IN_LIBRARY),
    "screenshot-to-code-whimsyworks-inc": (4, NOTE_IN_LIBRARY),
    "selltora": (5, NOTE_IN_LIBRARY),
    "social-genius-text-analyzer": (5, NOTE_IN_LIBRARY),
    "social-post-flow": (5, NOTE_IN_LIBRARY),
    "sprinta-ai": (3, NOTE_IN_LIBRARY),
    "stampsnap": (5, NOTE_IN_LIBRARY),
    "static-forms": (5, NOTE_IN_LIBRARY),
    "studio-zero": (5, NOTE_IN_LIBRARY),
    "supascribe": (4, NOTE_IN_LIBRARY),
    "themedrop": (5, NOTE_IN_LIBRARY),
    "url2pin": (5, NOTE_IN_LIBRARY),
    "velisto": (4, NOTE_IN_LIBRARY),
    "vibeling": (5, NOTE_IN_LIBRARY),
    "videototext": (5, NOTE_IN_LIBRARY),
    "vincere-dev": (1, NOTE_IN_LIBRARY),
    "volume-booster-louder-sound": (5, NOTE_IN_LIBRARY),
    "walldecor-io": (4, NOTE_IN_LIBRARY),
    "webclaw": (4, NOTE_IN_LIBRARY),

    # ── 2026-11-04 守卫拦下 8 条，人工重写后晋升，共 8 条 ──
    "adscriptly": (4, NOTE_IN_LIBRARY),
    "aera-browser": (4, NOTE_IN_LIBRARY),
    "arc-raiders-map": (5, NOTE_IN_LIBRARY),
    "dropapost": (4, NOTE_IN_LIBRARY),
    "pageturner": (5, NOTE_IN_LIBRARY),
    "picoo": (5, NOTE_IN_LIBRARY),
    "stat-ai": (5, NOTE_IN_LIBRARY),
    "stivio": (5, NOTE_IN_LIBRARY),

    # ── 2026-11-04 404 档案 4 条降级口径入库 ──
    "changelogfy": (5, NOTE_IN_LIBRARY),
    "citron-app": (5, NOTE_IN_LIBRARY),
    "ryn-vpn": (5, NOTE_IN_LIBRARY),
    "ton-piston": (4, NOTE_IN_LIBRARY),

    # ── 2026-11-04 sotie 人工确认 still_alive 后入库 ──
    "sotie-ai-best-friend": (5, NOTE_IN_LIBRARY),

    # ── 2026-11-04 C 档（综合 < 50，别搬）合并，共 22 条 ──
    "100percentmarket": (2, NOTE_IN_LIBRARY),
    "access-labs-pty-ltd": (1, NOTE_IN_LIBRARY),
    "akari-tickets": (3, NOTE_IN_LIBRARY),
    "anonymous-bot-for-slack": (4, NOTE_IN_LIBRARY),
    "apparate-pty-ltd": (1, NOTE_IN_LIBRARY),
    "asta": (1, NOTE_IN_LIBRARY),
    "berace": (5, NOTE_IN_LIBRARY),
    "bibly": (5, NOTE_IN_LIBRARY),
    "book-the-move": (1, NOTE_IN_LIBRARY),
    "buzzrush": (5, NOTE_IN_LIBRARY),
    "cash-cow-inc": (2, NOTE_IN_LIBRARY),
    "classyaction": (4, NOTE_IN_LIBRARY),
    "commentions-automated-comments-with-mentions": (4, NOTE_IN_LIBRARY),
    "copykitten": (4, NOTE_IN_LIBRARY),
    "cosmo": (3, NOTE_IN_LIBRARY),
    "deeplead-io": (2, NOTE_IN_LIBRARY),
    "esim-panel": (2, NOTE_IN_LIBRARY),
    "fill": (4, NOTE_IN_LIBRARY),
    "jarvis-bet": (4, NOTE_IN_LIBRARY),
    "polyalerthub": (4, NOTE_IN_LIBRARY),
    "replymer": (3, NOTE_IN_LIBRARY),
    "torrentclaw": (4, NOTE_IN_LIBRARY),
    # ── 2026-11-06 晋升的 4 条（人工裁定，依据 tmp/tm_md/*.md；草拟见 tmp/htm_screen/fit_scores_draft_20261106.md） ──
    "catering-funnels": (2, NOTE_IN_LIBRARY),
    "talkeasy": (4, NOTE_IN_LIBRARY),
    "bubblav": (4, NOTE_IN_LIBRARY),
    "arq": (3, NOTE_IN_LIBRARY),
}


# ------------------------------------------------------------------ 工具

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
    """原文件用哪种换行就写回哪种（红线 23，详见 score_china_fit 同名函数）。

    cases.json 是 LF；这里原来没给 `newline=""`，Windows 文本模式会把它整份
    翻成 CRLF，42268 行全变而数据没变。
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


def fallback_delivery():
    """缺 DELIVERY 时的兜底单人交付分（+ 理由）：取全库中位数。

    2026-11-06 放宽（用户：「这两张表放宽」）：DELIVERY 不再强制人工逐条填。
    兜底值取自既有 N 条人工判断的中位数 —— 是统计量，不是凭空编的判断；
    它会写进 `delivery_note` 且在 `solo_fit.provisional` 上标死，
    人工填了表就优先用表，兜底永不覆盖真实判断。
    """
    vals = [v[0] for v in DELIVERY.values()
            if isinstance(v, (tuple, list)) and v and isinstance(v[0], int)]
    score = _median_int(vals) if vals else 3
    return (score, "未人工评分：单人交付分按全库 %d 条判断的中位数兜底（%d 分）。"
                   "要精确结论请补进 DELIVERY。" % (len(vals), score))


def _median_int(vals):
    """中位数取整（.5 向上）。"""
    vals = sorted(v for v in vals if isinstance(v, int))
    if not vals:
        return 3
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return int((vals[mid - 1] + vals[mid]) / 2 + 0.5)


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
    kept = []
    notes_kept = []
    rows = []
    fb_delivery = fallback_delivery()

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

        # 单人交付：人工判断优先，缺表则按全库中位数兜底（2026-11-06 放宽）。
        # 以前这里是 `continue`，缺表案例直接不进榜 ⇒ 首页「个人可做性」视图空一块，
        # 而且没有任何地方会因此失败。
        d = DELIVERY.get(cid)
        provisional = False
        preserve = False
        prev_solo = c.get("solo_fit") or {}
        if not d:
            if prev_solo.get("score") is not None and not prev_solo.get("provisional"):
                # 表里没有、库里已有真实判断 ⇒ 保留原分，只重排名次
                kept.append(cid)
                preserve = True
                dv = (prev_solo.get("dims") or {}).get("delivery")
                d = (dv if isinstance(dv, int) else fb_delivery[0],
                     NOTE_IN_LIBRARY)
            else:
                d = fb_delivery
                provisional = True
                no_delivery.append(cid)
        dims["delivery"] = d[0]
        # 文案以库里为唯一真值（PRD #182 → #183 → #184）：DELIVERY 第二位恒为
        # 空串，表里已无理由副本；库里没判断才用中位数兜底说明当初值。
        delivery_note, note_kept = SNP.resolve_note(
            c, "solo_fit", fb_delivery[1])
        if note_kept:
            notes_kept.append(cid)

        raw = round(sum(dims[k] * SOLO_WEIGHTS[k] for k in SOLO_WEIGHTS), 1)
        solo = to_score(raw, SOLO_MAX)
        if preserve:                       # 保留原分，避免重算值盖掉人工判断
            solo = prev_solo["score"]
            raw = prev_solo.get("raw", raw)

        china = (c.get("china_fit") or {}).get("score")
        if china is None:
            no_china.append(cid)

        rows.append({
            "case": c, "dims": dims, "raw": raw, "solo": solo,
            "delivery_note": delivery_note, "china": china,
            "provisional": provisional, "preserve": preserve,
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
        if r["preserve"]:
            # 表里没有但库里已有判断：只更新名次，其余原样留着
            c["solo_fit"]["rank"] = r["solo_rank"]
            c["solo_fit"]["medal"] = r["solo_medal"]
            if r["china"] is None:
                c.pop("composite", None)
                continue
            c["composite"] = {
                "score": r["composite"], "solo": r["solo"], "china": r["china"],
                "rank": r["composite_rank"], "medal": r["composite_medal"],
                "quadrant": r["quadrant"],
                "quadrant_label": QUADRANTS[r["quadrant"]][0],
                "formula": "0.6×min(solo,china) + 0.4×mean(solo,china)",
                "threshold": args.threshold, "scored_at": today,
            }
            continue
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
        if r["provisional"]:
            c["solo_fit"]["provisional"] = True
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
        print("\n[!] DELIVERY 表里没填，已按全库中位数兜底并标记 provisional（%d 条）：%s"
              % (len(no_delivery), ", ".join(no_delivery)))
        print("    页面照常显示；要精确结论就补进 DELIVERY。")
    if kept:
        print("\n[i] DELIVERY 表里没有、但库里已有判断，已保留原分（%d 条）：%s"
              % (len(kept), ", ".join(kept)))
    if notes_kept:
        print("\n[i] 库里 delivery_note 是唯一真值，已保留文案（%d 条）；"
              "DELIVERY 表已无理由副本，也没有覆盖开关（PRD #184）。"
              % len(notes_kept))
    if no_china:
        print("\n[i] 以下案例还没跑 score_china_fit.py，本次不算综合分：%s"
              % ", ".join(no_china))

    if args.dry_run:
        print("\n--dry-run：未写入 data/cases.json")
        return 0

    save_json("cases", cases)
    print("\n已写入 case_library（solo_fit %d 条，composite %d 条），镜像 data/cases.json 已重导"
          % (len(rows), len(both)))
    print("页面刷新即可看到「综合排行」视图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
