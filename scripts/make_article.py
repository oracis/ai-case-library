#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/cases.json 里的精写案例转成公众号文章草稿。

用法：
    python scripts/make_article.py --list                 # 只看传播力排名，不写文件
    python scripts/make_article.py --id nitra             # 只生成一条
    python scripts/make_article.py --top 10               # 传播力前 10 条
    python scripts/make_article.py --all                  # 全部精写案例
    python scripts/make_article.py --all --format html    # 输出可直接粘进公众号编辑器的 HTML
    python scripts/make_article.py --top 5 --out out/drafts

产物：
    out/articles/<id>.md     公众号草稿（Markdown，带占位符与核对用来源注释）
    out/articles/<id>.html   内联样式 HTML，粘进公众号编辑器基本能保住样式

三条设计原则（写在代码里，别绕过）：
    1. 传播力不纯自动算 —— 脚本给基础分，EDITORIAL_WEIGHT 是人工覆盖表
    2. 每个数字都带原始来源 —— 来源只写进 HTML 注释，不进入正文（公众号正文不能放外链）
    3. 不生成「我的判断」—— 留占位符。判断是你唯一不可替代的资产，机器别碰

只读 data/，不写任何数据文件。
"""

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lead_templates  # noqa: E402  四型开头钩子模板库

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台默认不是 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES_PATH = os.path.join(ROOT, "data", "cases.json")
DEFAULT_OUT = os.path.join(ROOT, "out", "articles")

# ------------------------------------------------------------------ 维度标签
# 与 score_china_fit.py / score_solo_fit.py 保持一致；导入失败则用这里的副本兜底
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from text_clean import clean_snapshot_marks, end_sentences, clean_placeholders  # noqa: E402

try:
    from score_china_fit import DIMS as CHINA_DIMS       # noqa: E402
    from score_solo_fit import SOLO_DIMS, QUADRANTS      # noqa: E402
except Exception:                                          # noqa: BLE001
    CHINA_DIMS = [
        ("demand", "付费意愿", ""), ("payment", "支付可达", ""),
        ("compliance", "合规空间", ""), ("acquisition", "获客迁移", ""),
        ("localization", "改造成本", ""), ("competition", "竞争空位", ""),
    ]
    SOLO_DIMS = [
        ("build", "造得出来", ""), ("delivery", "单人交付", ""),
        ("reach", "够得着客户", ""), ("capital", "启动轻", ""),
        ("window", "窗口还开着", ""),
    ]
    QUADRANTS = {
        "go": ("可以开干", ""), "export": ("能做，但别在国内卖", ""),
        "partner": ("有市场，但一个人啃不动", ""), "skip": ("别碰", ""),
    }

# 注：这里**故意不放 price_point**。价格属于「钱从哪来」那一节，正文那句
# 通常已经写了；表格里再来一行「定价」，加上原来单独一行的「定价：」，
# 同一件事会在正文里出现三遍（2026-09-21 实测命中 25/30 篇）。
# 价格改由 build_sections 的 money 段兜底：只有正文没提过的数字才补一行。
METRIC_LABELS = [
    ("headline", "官方口径"), ("arr", "年化收入"), ("customers", "客户"),
    ("team", "团队"), ("funding", "融资"), ("growth", "增长"),
    ("metric_note", "备注"),
]

# ------------------------------------------------------------------ 人工覆盖表
# 传播力首先是编辑判断，公式只是辅助。基础分只认三件可计算的事：
#   纠错条数（每条 +2）、metrics 里的最大金额量级、四象限（go/export 加分）。
# 它算不出「哪条更值得讲」，所以加减权都在这里手工调。建议范围 -3 ~ +5。
# 表里没写的案例权重为 0，只吃基础分。
# 这个表的顺序 = 发布优先级，改动它就是在改你的选题排期。
EDITORIAL_WEIGHT = {
    # —— 首发十篇 ——
    "nitra": 3.5,           # 客户数被写多一个量级，全库最强钩子
    "shipfast": 4.5,        # 爆款衰退，情绪张力最大
    "chatbase": 3.5,        # 累计 ≠ 年入，概念纠偏类转发率最高
    "speel-co": 5.0,        # 收入真、增长归零：反向结论
    "pieter-levels": 5.0,   # 名人效应，用来撬第一波外部流量
    "meerkats-ai": 4.0,     # 反面教材，立「核不到就不写」的公信力
    "comp-ai": 2.5,         # 口径陷阱：月 ×12 不等于年入
    "visualizee-ai": 3.5,   # 第一个正向样本（go），得给希望
    "rezi": 2.5,            # 反直觉：技术含量最低反而最赚
    "aeo-engine": 3.0,      # 中国移植分全库第一，天然引流社群
    # —— 第二批 ——
    "bustem": 0.4, "outrank": 1.3, "kibu": 1.7,
    "storyshort-ai": 0.5, "postiz": 0.8, "marc-lou-portfolio": 2.2,
    "lancer-app": 1.6, "coral": 1.8, "startclaw": 1.7, "trustmrr": 1.6,
    "checkvibe": 2.9,       # 基础分算出来是 0（无纠错、金额小、无象限加分），手工补一点
    # —— 压后：数字很大但钩子弱 ——
    "sierra": -1.4,         # $200M ARR 很响，但「定价被传错」撑不起一篇
    "viktor": -0.6,         # $15M/10 周，同类叙事已经被讲透
}

# 已手改好的标题（三条备选）。没写的走模板，并在草稿里标注「建议手改」。
# 2026-09-25：这批 TrustMRR 挂牌标的的 metrics.headline 全是「收入/售价/倍数」的
# 挂牌数字，模板会把它们当钩子拼进标题（「Dolar App：收入 $1.1K / 售价 $30K」），
# 既不好读，还会把无法复核的数字放进读者第一眼看到的地方。所以这里全部手改。
TITLE_OVERRIDES = {
    "voklit": [
        "卖海外虚拟号码这门生意，最后为什么停机挂牌",
        "Voklit：把「收得到验证码」做成订阅，然后卖掉",
        "一个能收 OTP 的海外号码，值多少钱",
    ],
    "prosp": [
        "PROSP：把 B2B 销售的一整天，压成一个按钮",
        "流水看着大，但命脉长在 LinkedIn 上",
        "卖「会议数」而不是卖功能：一个 AI 外呼工具的选择",
    ],
    "divine-widgets": [
        "Divine Widgets：一个人做的圣经小组件，锁屏上每天一句",
        "低频 App 的解药：把内容放进锁屏，而不是等用户打开",
        "垂直到「女性 + 信仰」这一格，竞争几乎为零",
    ],
    "promptmonitor-io": [
        "品牌开始问「AI 回答里提不提我」，于是有了这门生意",
        "Promptmonitor.io：跟着预算迁移走，不跟着技术热潮走",
        "当「搜索排名」不再是唯一 KPI，监测工具该怎么办",
    ],
    "uplinked-b-v": [
        "UpLinked：领英上自动加客户",
        "把「我知道要发内容，但没时间写」做成订阅",
        "UpLinked B.V.：交付终点应该是发布，不是草稿",
        "面向「有钱没时间」的人收费，比反过来容易一个量级",
    ],
    "le19emetrou": [
        "一笔广告费，卖一支高尔夫开瓶器",
        "le19emetrou：一个单品 DTC 的最小闭环能有多小",
        "履约全自动：订单进来之后零人工，是单人生意的生死线",
    ],
    "search1api": [
        "Search1API：不赌哪个 AI 会赢，只卖它们都需要的那次联网",
        "做淘金热里的管道——一个搜索 API 的选择",
        "累计收入是 lifetime，不是 ARR",
    ],
    "nitra": [
        "中文媒体把这家美国医疗公司的客户数，写多了 10 倍",
        "一年量级翻了好多倍，但它真正的门槛不是技术，是牌照",
        "「7000 家诊所在用」——一条被抄错了一个数量级的案例",
    ],
    "shipfast": [
        "被吹爆的 ShipFast，近 30 天收入已经塌了",
        "从峰值到谷底：一个爆款的完整衰退",
        "同一个作者三年后换个产品，赚得比它还多",
    ],
    "chatbase": [
        "「年入」和「累计」差了一整个量级",
        "同样的产品放到今天做，成本高十倍",
        "一条关于「时机窗口」的教科书",
    ],
    "speel-co": [
        "增长是真的，但它已经停止增长了",
        "一个人做到可观规模，然后增长归零",
        "收入曲线是真的，天花板也是真的",
    ],
    # —— 2026-10-05 新入库两条。**必须手改标题**，原因是
    # `make_wechat_title()` 的 `name + one_liner` 拼接对它们都超 64 字，
    # 于是退化成「只剩 name」的兜底分支（实测：harperai 变成 8 字的
    # 「HarperAI」，easymix 变成 53 字的整句 one_liner）——
    # 一个太空，一个太长，两边都不合格。
    #记忆铁律 10：公众号草稿标题与正文 h1 是两套逻辑，改数字要连带改。
    "harperai": [
        "把英国公开抵押记录连成一张网，然后卖给贷款经纪",
        "HarperAI：卖给最怕错过再融资机会的一群人",
        "数据人人能查，但没人愿意整理 —— 这就是护城河",
    ],
    "easymix": [
        "法国录音棚的一单，有人把混音做成了订阅",
        "easymix：上传人声和伴奏，一键出能上架的成品",
        "它替代的不是时间，是一笔本来就存在的支出",
    ],
    # —— 2026-10-11 新入库 7 条（Stripe 深核候选晋升）。全部手改标题，理由同上：
    # metrics.headline 全是「MRR + 累计 + 订阅数」的挂牌三联，模板拼出来既不好读，
    # 又把「近 30 天 -4.9%」这种负增长和「峰值留存 46%」这种弱项放在第一眼——
    # 读者点进来是为了看一个能学的东西，不是看一堆数字。
    "cometly": [
        "Cometly：广告数据归因分析",
        "营销归因生意，但不是一个人能做的",
        "Cometly：9 人团队撑起的生意，做法一个人抄不来",
        "营销归因这条赛道有大生意，但别照抄它的交付结构",
    ],
    "podawaa": [
        "把 LinkedIn 内容发布做成订阅，靠平台自己的增长起飞",
        "Podawaa：近 30 天掉了 15%——一个渠道红利样本",
        "红利会退潮，但它证明了一件事：渠道方的注意力就是需求",
    ],
    "vid-ai": [
        "两年冲起来，然后峰值留存掉到 46%",
        "Vid.AI：批量短视频生成的护城河，比想象中浅得多",
        "几百个付费客户撑起月收——需求是真的，黏性不是",
    ],
    "wpconvert-ai-convert-ai-sites-to-wordpress": [
        "WPConvert：AI 建站转 WP",
        "把 AI 建站工具生成的网站，转成 WordPress——一个工程师的生意",
        "WPConvert.ai：客户被平台锁住的那一刻，就是需求触发的那一刻",
        "这批里唯一能一个人做的案例，但上游风险不小",
    ],
    "publbee": [
        "自助出版的作者愿意为工具付钱——只是撑不起大生意",
        "Publbee：一个教你怎么选品而不是怎么扩张的样本",
        "KDP 作者的工具箱：需求真实，天花板也真实",
    ],
    "vectosolve": [
        "唯一在增长的一个：把 PNG 转成可编辑的 SVG",
        "VectoSolve：急活 + 工业格式 + 低单价走量，一个小而清晰的模型",
        "260 个付费客户，它在逆势增长，凭什么",
    ],
    "augora-ai": [
        "Shopify 商家的 Meta 追踪本来就是错的，于是有了这门生意",
        "Augora AI：靠「你的数字是错的」立足",
        "峰值留存 65%、收入结构透明——一个小而诚实的样本",
    ],
    # —— 2026-10-14 红线补：下列案例的 one_liner 里嵌了金额/收入指标，拼进标题
    # 违反「标题禁金额」红线。one_liner 正文照旧（正文可含数字），这里只给标题
    # 一条干净的覆盖（产品名：一句话描述，去掉金额钩子）。
    "nutria-9e3eaa": [
        "Nutria (9e3eaa)：AI 卡路里追踪应用，但近 30 天在下滑",
        "Nutria：用 AI 记卡路里，但增长已经掉头向下",
        "$46K MRR 的 AI 卡路里应用，为什么在往下掉",
    ],
    "downconvert-sketchup-files": [
        "DOWNCONVERT SKETCHUP FILES：SketchUp 文件版本转换工具",
        "把高版本 SketchUp 文件降回低版本，一门小而稳的生意",
        "一个只解决「文件打不开」的工具，月入 $3,748",
    ],
    "stealth-rdp": [
        "Stealth RDP：跨美欧的 Windows/Linux VPS 托管服务",
        "给远程办公的人提供跨大西洋的 VPS 托管",
        "跨大西洋的 VPS 托管，月入一千多美元的生意怎么做",
    ],
    "pet-plus-us": [
        "Pet Plus Us：AI 生成宠物与主人个性化肖像",
        "用 AI 把宠物和主人画成一对肖像，按张收费",
        "把宠物画进肖像里：一个要价 7.5 万美元的小生意",
    ],
    "postiz": [
        "Postiz：社媒多平台一键排期，靠 agent 营销起飞",
        "Postiz：把社媒排期做成订阅，agent 帮它做营销",
        "MRR 从 1.7 万到 20 万：一个开源排期工具的陡坡",
    ],
    "visualizee-ai": [
        "Visualizee.ai：建筑与室内设计的效果图渲染工具",
        "给建筑师做效果图渲染，一次转型跳对了赛道",
        "卡在 $150/月两年，转型后跳到 $8.6K：它改了什么",
    ],
    "outrank": [
        "Outrank：从 AI 博客生成器到自动化 SEO 引擎",
        "Outrank：把 AI 博客生成器做成自动化 SEO 引擎",
        "月入 $300 到 30 万：一个 AI 博客生成器的 15 个月",
    ],
    # —— 2026-10-16：8 条公众号草稿标题/正文 h1 清脏（与 xhs_title_overrides.json
    # 同源）：boothie 原是纯英文产品名，其余是带金额指标的超长模板句
    # （"Bustem：$397,729（TrustMRR 榜单值）"这种），既违反标题禁金额红线，
    # 按微信计数（中文 2 字符/字）又超 64 字符上限会被截断。统一换成
    # 「品牌：一句话」且 ≤20 字的干净标题。
    "boothie-ai-photo-editor": [
        "Boothie：AI 照片编辑器",
        "Boothie：把产品图修到能直接上架",
        "电商卖家自己修图，还是雇人修图",
    ],
    "meerkats-ai": [
        "Meerkats：替营销公司跑获客",
        "Meerkats：从找线索到跟进，一条流水线跑完",
        "营销机构最缺的不是客户，是跟进人手",
    ],
    "coral": [
        "Coral：托管你的开源 AI 助手",
        "Coral：把开源 agent 部署起来，接上业务系统",
        "开源 agent 的部署麻烦，本身就是一门生意",
    ],
    "startclaw": [
        "StartClaw：零配置跑 AI 助手",
        "StartClaw：不买服务器、不配 key 就跑 agent",
        "托管 agent 的零配置版本，卖给不想折腾的人",
    ],
    "bustem": [
        "Bustem：帮电商品牌全网打假",
        "Bustem：找仿品、找窜货、下架链接",
        "电商打假：软件扫一遍，人工再扫一遍",
    ],
    # —— 2026-10-16：公众号 7 条旧草稿「封面第二行 + 摘要清英」配套标题。
    # 这些案例的 cover_desc 早已中文化，但草稿建在那之前，封面第二行还是
    # 英文 one_liner；根目录精修版 HTML 的「摘要」注释也有 5 条是英文
    # （vid-ai/podawaa/publbee/vectosolve/augora-ai，已同步改成中文）。
    # 原标题多带金额（"月收 $1,808…"/"累计 $162 万…"）或超长，统一换成
    # 「品牌：一句话」干净版；nutria 原名带 (9e3eaa) 哈希，一并去掉。
    "nutria-9e3eaa": [
        "Nutria：AI 卡路里追踪应用",
        "Nutria：拍照记录饮食，AI 算卡路里",
        "万人付费的卡路里 App，正在退潮",
    ],
    "vid-ai": [
        "Vid.AI：脚本一键变短视频",
        "Vid.AI：把想法变成能发布的视频",
        "长视频剪短视频，交给 AI 跑完整套",
    ],
    "podawaa": [
        "Podawaa：把 LinkedIn 发布做成订阅",
        "Podawaa：内容排期、运营、看数据一站做",
        "靠平台自己的增长做起的内容工具",
    ],
    "publbee": [
        "Publbee：KDP 作者的 AI 工具箱",
        "Publbee：自助出版的一站式工具集",
        "自助出版作者愿意为工具付钱",
    ],
    "vectosolve": [
        "VectoSolve：位图一键转可编辑 SVG",
        "VectoSolve：PNG/JPG 转干净矢量图",
        "把转矢量做成小工具，卖给设计师",
    ],
    "augora-ai": [
        "Augora AI：Shopify 广告追踪自动化",
        "Augora AI：服务端追踪 Meta 转化",
        "Shopify 商家的 Meta 追踪本来是错的",
    ],
    "quran-unlock": [
        "Quran Unlock：读古兰经解锁其他 App",
        "Quran Unlock：把信仰变成打卡机制",
        "用完即走的信仰工具，靠内购变现",
    ],
    # —— 2026-10-26：新增案例补标题表。jobright / autoshorts 的默认拼接句
    # 里嵌了「¥3,360万」「¥69.2万」，过 sanitizer 时金额被剥掉，拼出
    # 「…月访问 500 万、 约 且已盈利」这种半截病句 ⇒ 必须走 override。
    "jobright": [
        "婉拒 Meta 百万年薪，他把「查不到」的信息做成了生意",
        "Jobright：查「这家公司办不办签证」，做成求职平台",
        "他先把签证数据做成钩子，再去做 AI 求职",
    ],
    "autoshorts": [
        "一个人、每天三小时，他把不露脸短视频做成订阅",
        "AutoShorts：不用出镜、不用剪，也能日更",
        "它卖的不是生成视频，是「不用手动发」",
    ],
    # —— 2026-10-26：2026-10-26 那批入库剩下 5 条的标题表（这批 one_liner
    # 全部内嵌金额，默认拼接句过 sanitizer 后只剩品牌名的退化形态）。
    "serviceform": [
        "Serviceform：把官网那张没人填的表单，做成 AI 销售 Agent",
        "它起手的东西小到不像生意：一张没人填的联系表单",
        "从一张表单做到五个办公室，靠的是一百多家代理商",
    ],
    "salesrobot": [
        "SalesRobot：它卖的不是自动化，是「账号不会死」",
        "把「封号率 0.00001%」做成唯一卖点的外联工具",
        "LinkedIn 外联工具都在比功能，它在比谁会被封号",
    ],
    "sandra-ai": [
        "Sandra AI：给车行接电话，四个人做的生意",
        "它不卖 AI，卖「你漏掉的电话亏了多少钱」",
        "三个麦肯锡出来的人，去做车行的电话前台",
    ],
    "outward": [
        "Outward：三个拒绝 VC 的人，用 AI 重做民调",
        "把一整串人的调研流程，压成一条 AI 流水线",
        "它抄不动的不是 AI，是三个人攒下的方法论",
    ],
    "workhero": [
        "WorkHero：AI 干活、行业老手复核，替暖通小店省下一个人",
        "定价锚不是软件，是「雇一个人的零头」",
        "给暖通小店做后台：AI 开票，真人复核",
    ],
}

# 宣传性词汇 → 直接替换。公众号「赚钱类」内容容易限流，宁可写保守一点。
SENSITIVE_WORDS = [
    ("轻松月入", "持续营收"), ("躺赚", "低维护"), ("稳赚", "收入稳定"),
    ("暴利", "高毛利"), ("月入", "月营收"),
]

# 分析性词汇 → 只提醒、不替换。
# 「赚钱的生意一点都不性感」这种句子是好句子，机械替换反而写坏了；
# 但出现在标题里就有风险，所以交给写的人自己判断。
WARN_WORDS = ["赚钱", "副业", "风口", "红利", "月赚"]

OUTRO = [
    "这条案例的完整核实记录（含全部原始来源）在案例库里。",
    "海外每天都有新跑通的小项目。我每天筛一批发在社群里：哪些值得看、哪些我直接否掉了、为什么。",
]

# 结尾按内容品类分。默认 OUTRO 是「海外小生意」口径（社群招新），
# 非案例类（工具/方法论/观点）照抄会露馅 —— 读者点进来发现是开源项目，
# 却被问「要不要进群看新项目」。与 TITLE_OVERRIDES 同一思路：表里没写的走默认。
OUTRO_OVERRIDES = {}


# ------------------------------------------------------------------ 小工具
def human_num(n):
    """33000000 -> 33M，方便在正文里读。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            v = n / div
            return ("%.1f" % v).rstrip("0").rstrip(".") + unit
    return str(int(n))


def fmt_metric(key, val):
    if key == "arr" and isinstance(val, (int, float)) and not isinstance(val, bool):
        return "$" + human_num(val)
    return str(val)


def strip_quotes(s):
    """去掉文案里已经带的「」《》，避免嵌套成「「xxx」」。"""
    s = (s or "").strip()
    for a, b in (("「", "」"), ("《", "》"), ("“", "”"), ('"', '"')):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a):-len(b)]
    return s.strip()


def _as_quote(s):
    """把一句别人的说法包成引号句；本来就带引号的**原样保留**。

    为什么改用它（2026-09-22）：钩子第一段原来走 strip_quotes()，把数据里
    本来就有的「」剥掉了，于是那句变成一个没有引号、没有来源的光秃陈述，
    读起来像我们自己的结论；为了给它找落点，第二段才加了
    「这句在中文网上被反复引用」——而这句恰恰是全文唯一没有依据的一句
    （corrections 的 source 是我们去核实的页面，不是这句说法的出处）。
    保住引号，指代问题自然消失，那句假话也就不用了。
    """
    s = (s or "").strip()
    if not s:
        return s
    for a, b in (("「", "」"), ("《", "》"), ("“", "”"), ('"', '"')):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            return s          # 已带同款引号，不动
    return "「%s」" % s      # 没有就补上


def sanitize(text):
    """宣传性词汇替换。返回 (新文本, 命中列表)。"""
    hits = []
    for bad, good in SENSITIVE_WORDS:
        if bad in text:
            hits.append(bad)
            text = text.replace(bad, good)
    return text, hits


def scan_warn(text):
    """分析性词汇只提醒不替换，返回 ['赚钱×2', ...]。"""
    out = []
    for w in WARN_WORDS:
        n = text.count(w)
        if n:
            out.append("%s×%d" % (w, n))
    return out


# 「金额量级」只认**当前口径**的收入字段，并统一年化成 run-rate（USD/年）。
#   mrr               月度经常性收入 × 12
#   last_30d_revenue  滚动 30 天 × 12（TrustMRR 对非订阅制只给这个）
#   arr               年化，直接可用
MONEY_TO_ANNUAL = (("mrr", 12.0), ("last_30d_revenue", 12.0), ("arr", 1.0))


def money_scale(case):
    """当前口径的收入量级（年化成 USD/年），供 virality_base 加分。

    ⚠ 2026-10-21 修：此前是 `max(metrics 里所有数字)`，三种口径混在一个
    刻度上，实测 215/255 条的「最大数字」来自 `all_time`（累计）：

      ① 累计当月/年收入 —— 一个两年攒下 $57 万、如今 MRR 为 0 的死项目
         （bakering-app-sl）会被排到活着的 $7K/月 前面；README 与
         DATA_SCHEMA:211 都写明 all_time **不是** ARR，这里却拿它当量级。
      ② 计数当金额 —— `visitors: 37800` 曾按 $37,800 收入计分（macwall）。
      ③ 百分比当金额 —— `growth_30d: 73638.6` 曾按 $73,638 计分
         （the-stealthiest-of-ventures）。

    现在只读上面三个**同轴**字段（都年化到 run-rate），`all_time` /
    `all_time_revenue`（累计）、`subscriptions`/`visitors`/`customers`
    （计数）、`growth_30d` 一类的百分比一律不计入。没有当前口径的数字
    就不给钱的分 —— 「只有累计数」说明这条现在挣多少不知道，人工判断
    走 EDITORIAL_WEIGHT，别用累计数冒充现值。
    """
    m = case.get("metrics") or {}
    vals = []
    for key, factor in MONEY_TO_ANNUAL:
        v = m.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if not math.isfinite(v) or v <= 0:        # NaN / inf / 0 / 负数都不算
            continue
        vals.append(v * factor)
    return max(vals) if vals else 0


def virality_base(case):
    """可计算的基础分。透明、可复现，但它算不出「哪条更值得讲」。

    金额档位的刻度是**年化收入**（money_scale 的返回值就是 USD/年）：
    $10 万/年、$100 万/年、$1000 万/年、$1 亿/年 各一档。
    """
    score = 2.0 * len(case.get("corrections") or [])
    v = money_scale(case)
    if v >= 1e8:
        score += 3.0
    elif v >= 1e7:
        score += 2.0
    elif v >= 1e6:
        score += 1.0
    elif v >= 1e5:
        score += 0.5
    q = (case.get("composite") or {}).get("quadrant")
    if q == "go":
        score += 1.5
    elif q == "export":
        score += 1.0
    return score


def virality_score(case):
    """基础分 + 人工权重。分数只用于排序，不是绝对标准。"""
    return virality_base(case) + EDITORIAL_WEIGHT.get(case.get("id"), 0.0)


def strip_title_money(t):
    """标题红线兜底：去掉金额 / 收入指标词。

    拦住自动路径（one_liner 里若带「月收入 $X」「MRR 约 $46K」之类，
    拼进标题也不该出现金额）与未来的新案例。手工精修标题已在
    TITLE_OVERRIDES 里单独改过、TrustMRR 品牌由调用方排除。

    只删金额 token，保留其余文案；删完顺手清掉残留空格与连续标点。
    """
    if not t:
        return t
    s = t
    # 货币符号 + 数字（+ 可选 K/M/万）
    s = re.sub(r"[¥€£$]\s*\d[\d.,]*\s*(?:[KkMm万])?", "", s)
    # 无符号的金额单位（近1100美元 / 7.5 万欧）
    s = re.sub(r"\d[\d.,]*\s*(?:美元|英镑|欧元|欧)", "", s)
    # 独立 MRR / ARR（品牌 TrustMRR 已在调用方排除）
    s = re.sub(r"(?<![\w])MRR(?!\w)", "", s)
    s = re.sub(r"(?<![\w])ARR(?!\w)", "", s)
    # 收入词
    s = re.sub(r"月收(?:入)?", "", s)
    s = re.sub(r"月入", "", s)
    s = re.sub(r"累计\s*[¥€£$]?\d[\d.,]*\s*(?:[KkMm万])?", "累计", s)
    # 清理残留空格与连续标点
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[，,]\s*[，,]", "，", s)
    s = s.strip(" ，,：: ")
    return s


def build_titles(case):
    """三条备选标题。没手改过的走模板，并在草稿里标出建议手改。"""
    if case["id"] in TITLE_OVERRIDES:
        titles = list(TITLE_OVERRIDES[case["id"]])
        # 2026-10-15：手改过的标题表里实测有 7 条只写了 2 个（nutria、
        # postiz、visualizee-ai…），而下游按「三选一」渲染，拿 titles[2]
        # 会 IndexError 直接崩。这里补齐到 3 个 —— 补的是模板生成的那条，
        # 手写的两条排前面，人的选择权没被稀释。
        if len(titles) < 3:
            name = case.get("name", case["id"])
            titles.append("%s 这条生意，国内能做吗" % name)
        return titles, True
    m = case.get("metrics") or {}
    name = case.get("name", case["id"])
    hook = m.get("headline") or case.get("one_liner") or ""
    # 定位被占位清理清空（或本来就没料）时，别拼出「PROSP：」这种带尾巴冒号的标题
    # ——冒号后面什么都没有，比只写产品名更刺眼。
    def _t(prefix, tail):
        tail = (tail or "").strip()
        return ("%s：%s" % (prefix, strip_quotes(tail))) if tail else prefix
    return [
        _t(name, strip_quotes(hook)),
        "%s 这条生意，国内能做吗" % name,
        _t("拆解 %s" % name, case.get("one_liner", "")),
    ], False


# ------------------------------------------------------------------ 正文分段
ROW_MARK = "@@ROW@@"
# 注：早先键值行靠「······」点线凑对齐，2026-09-21 换成真两端对齐后已删除 DOTS。

# 「不立小标题的段落节」：head 传这个空串。
# 两个渲染器里 `if head:` 不成立 → 不输出 h2；同时它**不是 None**，
# 所以也不会掉进「钩子引文块（blockquote）」那条分支，出的是普通段落。
# 2026-09-23 新增：单句的过渡段落不该立小标题——立了之后标题和它下半句
# 会被排版断成两行，读起来是半句话。
NO_HEAD = ""


def _row(label, value):
    """键值行标记：两个渲染器都会把它渲成两列表格（md 真表格 / html 拼装表格）。"""
    return "%s%s%s%s" % (ROW_MARK, label, ROW_MARK, value)


def _split_row(p):
    parts = p.split(ROW_MARK)
    return parts[1], parts[2]


def _is_row(p):
    return p.startswith(ROW_MARK) and p.count(ROW_MARK) >= 2


def build_sections(case):
    """返回 [(小标题 or None, [段落])]，顺序即发布顺序。

    2026-09-21 改版（用户反馈「正文 AI 味太重」）。诊断结论是问题不在用词、
    在**骨架**，所以这里集中处理三件事：
      · 去掉「一句话：」这类表单式前缀 —— 读起来像填表，不像人说话；
      · 「钱从哪来」只保留正文那一句，structured 字段降级为兜底
        （原来正文 + 「收入模式：」+「定价：」+ 表格「定价」行 = 同一件事四遍）；
      · 开头的引导句加「我」——原本整篇是介绍体，没有作者的在场感。
    编号（一、二、三…）在 render_markdown / render_html 里一并去掉。
    """
    m = case.get("metrics") or {}
    corr = case.get("corrections") or []
    cf = case.get("china_fit") or {}
    sf = case.get("solo_fit") or {}
    comp = case.get("composite") or {}
    secs = []

    # 钩子（不编号）。有纠错就用纠错开场 —— 这是全库转化率最高的钩子。
    # 第一段是「别人说的那句」（保住引号，别剥），第二段是「我核出来的」：
    # 引号就是这句的落点，所以不需要「这句在中文网上被反复引用」这类指代句
    # ——而且那句是全文唯一没有依据的一句（corrections.source 是我们去核实的
    # 页面，不是这句说法的出处），写在一个主打核实的开头最伤可信度。
    if corr:
        secs.append((None, [
            _as_quote(corr[0].get("claim", "")),
            "我去核了一遍：%s" % corr[0].get("truth", ""),
        ]))
    else:
        # 2026-10-23：裸 verdict 升级为三段叙事开头（场景 → 判断 → 路线）。
        # 原来无纠错案例开头就一句 verdict，孤零零没有入戏的台阶；
        # 有纠错案例不经过这里（引号钩子是实测最高转化，保持不动）。
        # 模板库 scripts/lead_templates.py，事实全部取自 case 字段。
        secs.append((None, lead_templates.wechat_lead(case)))

    # 它是干什么的
    what = []
    if case.get("what_it_does"):
        what.append(case["what_it_does"])
    elif case.get("one_liner"):
        # one_liner 正常情况下已用于标题；只在缺正文描述时兜底，避免同一句话写两遍
        what.append(case["one_liner"])
    if what:
        secs.append(("它是干什么的", what))

    # 钱从哪来：正文那句为主，结构化字段只补它没说的
    money = []
    if case.get("how_it_makes_money"):
        money.append(case["how_it_makes_money"])
    elif case.get("models"):
        money.append("收入模式：%s。" % " · ".join(case["models"]))
    pp = m.get("price_point")
    if pp:
        nums = re.findall(r"\d[\d,.]*", str(pp))
        spoke = "".join(money)
        if not money:
            # 「钱从哪来」整节没有正文描述，价格就是唯一线索
            money.append("定价：%s。" % pp)
        elif nums and not any(n in spoke for n in nums):
            # 正文没提过这个价格数字 —— 那是真信息，补一行；
            # 提过就不补（原来不分情况一律补，25/30 篇都在说第二遍）。
            # 没有数字的 price_point（如「B 端年合同 + 支付/采购流水收入」）
            # 基本是 how_it_makes_money 的摘要，一律不补。
            money.append("定价：%s。" % pp)
    if money:
        secs.append(("钱从哪来", money))

    # 它到底做到多大
    size = []
    for key, label in METRIC_LABELS:
        val = m.get(key)
        if val in (None, "", [], {}):
            continue
        size.append(_row(label, fmt_metric(key, val)))
    if size:
        secs.append(("它到底做到多大", size))

    # 第二条被抄错的（可选）
    # 2026-09-23：原来这里挂一个「还有一条被抄错的」小标题，下面再另起一段
    # 「流传的说法：…」。用户反馈这样一断行就成了半句话（「一句话不连贯」）——
    # 「还有一条被抄错的」本来只是下半句的定语，不该单独占一行。
    # 现在不立小标题，把这个短语并进本句，和「流传的说法」连成一行。
    if len(corr) > 1:
        secs.append((NO_HEAD, [
            "**还有一条被抄错的流传的说法**：%s" % corr[1].get("claim", ""),
            "**实际情况**：%s" % corr[1].get("truth", ""),
        ]))

    # 它为什么能成
    if case.get("why_it_works"):
        secs.append(("它为什么能成", ["- %s" % x for x in case["why_it_works"]]))

    # 能搬走的部分
    if case.get("playbook"):
        secs.append(("能搬走的部分", ["- %s" % x for x in case["playbook"]]))

    # 能不能搬回国内
    if cf:
        lines = ["**中国移植分：%.1f / 100**" % cf.get("score", 0)]
        dims = cf.get("dims") or {}
        for key, label, _ in CHINA_DIMS:
            if dims.get(key) is not None:
                lines.append(_row(label, "%d/5" % dims[key]))
        if cf.get("note"):
            lines.append(cf["note"])          # 本身就是完整句子，不必套「判断：」
        if cf.get("blocker"):
            lines.append("**卡点**：%s" % cf["blocker"])   # 短标签，好抓，保留
        secs.append(("能不能搬回国内", lines))

    # 一个人能不能做
    if sf:
        lines = ["**个人可做性：%.1f / 100**" % sf.get("score", 0)]
        dims = sf.get("dims") or {}
        for key, label, _ in SOLO_DIMS:
            if dims.get(key) is not None:
                lines.append(_row(label, "%d/5" % dims[key]))
        if sf.get("delivery_note"):
            lines.append(sf["delivery_note"])   # 同上，去「交付说明：」前缀
        secs.append(("一个人能不能做", lines))

    # 结论
    concl = []
    # 优先用编辑判断（`judgment` 字段）：它回答的是「谁适合抄、坑在哪」，
    # 和开头钩子里的 verdict（结论/买不买的价值）分工不同，不算复读。
    # 没有 judgment 才退回 verdict —— 且仅限有纠错的案例，否则会与钩子重复。
    if case.get("judgment"):
        concl.append(case["judgment"])
    elif case.get("verdict") and corr:
        concl.append(case["verdict"])
    if comp:
        qlabel = comp.get("quadrant_label") or QUADRANTS.get(
            comp.get("quadrant"), ("", ""))[0]
        concl.append("**结论**：%s（综合 %.1f 分）" % (qlabel, comp.get("score", 0)))
    # ⚠⚠ **绝不留空标题**（2026-10-14 修）。此前无 corr、无 comp 的案例
    # concl 为空，正文里就出现一个光秃秃的「我的判断」+ 分隔线（用户截图报障）。
    # 那比复读 verdict 更糟 —— 宁可整段不出现。
    if concl:
        secs.append(("我的判断", concl))

    return secs


# ------------------------------------------------------------------ 渲染
# 注：这里原有 CN_NUM = "一二三四五六七八九十"，给章节加「一、二、三…」编号。
# 2026-09-21 去掉了 —— 九节等长的编号骨架是正文里最强的「AI 味」来源。


def _flush_md_rows(L, rows):
    """把连续的键值行渲成 markdown 真表格（核对稿用）。

    ⚠ 会**清空 rows**（消费掉）。必须的：调用点的逻辑是「遇到非键值行就 flush」，
    如果 flush 完不清空，同一批 rows 会被后面每个普通段落再 flush 一遍 ——
    实测一个 6 行的评分表在正文里连着打印 3 遍（2026-09-21 修）。
    """
    if not rows:
        return
    L.append("| 维度 | 内容 |")
    L.append("| --- | --- |")
    for lab, val in rows:
        L.append("| %s | %s |" % (lab, val.replace("|", "\\|")))
    L.append("")
    rows.clear()


def render_markdown(case, titles, manual, secs, score):
    L = []
    L.append("# %s" % titles[0])
    L.append("")
    # 2026-10-15：原来硬取 titles[1] / titles[2]，而 build_titles() 只保证
    # 至少 1 个 —— 实测有 7 条老案例（nutria、postiz、visualizee-ai…）
    # 只产得出 2 个标题，于是生成文章这一步直接 IndexError 崩掉。
    # 备选标题是给人挑的，有几个写几个；没有就不写，别为了凑数编一个。
    for i, t in enumerate(titles[1:], start=2):
        L.append("<!-- 备选标题 %d：%s -->" % (i, t))
    if not manual:
        L.append("<!-- [!] 标题由模板生成，发布前请手改 -->")
    comp = case.get("composite") or {}
    L.append("<!-- 传播力分 %.1f ｜ 象限 %s ｜ 生成于 %s -->"
             % (score, comp.get("quadrant", "-"), datetime.now().strftime("%Y-%m-%d %H:%M")))
    L.append("")

    for head, paras in secs:
        if head:
            # 不加「一、二、三…」编号：九节等长的编号骨架是最强的「AI 味」来源，
            # 读起来像报告目录，不像有人在这儿跟你说话（2026-09-21 改）。
            L.append("")
            L.append("## %s" % head)
            L.append("")
        rows = []
        for p in paras:
            if not p:
                continue
            if _is_row(p):
                rows.append(_split_row(p))
                continue
            _flush_md_rows(L, rows)
            if head is None:
                L.append("> %s" % p)
                L.append("")
                continue
            # 代码块：``` 包裹的整段原样输出，不当普通段落处理。
            # ⚠ 工具推广文必带命令行示例，走通用段落路径会把缩进和换行吃掉，
            # 读者复制到的是一段乱码 —— 所以必须单独走 fenced 分支。
            if p.startswith("```"):
                L.append(p)
                L.append("")
                continue
            L.append(p)
            if not p.startswith("- "):        # 列表项之间不留空行，紧凑一些
                L.append("")
        _flush_md_rows(L, rows)

    L.append("---")
    L.append("")
    for p in OUTRO_OVERRIDES.get(case.get("id"), OUTRO):
        L.append(p)
        L.append("")
    if case.get("id") not in OUTRO_OVERRIDES:
        L.append("<!-- 公众号正文不能放外链：案例库链接放「阅读原文」和自定义菜单 -->")
    L.append("<!--")
    L.append("核对用来源（发布前逐个点开确认，别进正文）：")
    for s in case.get("sources") or []:
        L.append("  · %s <%s>" % (s.get("label", ""), s.get("url", "")))
    for i, c in enumerate(case.get("corrections") or [], 1):
        if c.get("source"):
            L.append("  · 纠正 %d 依据 <%s>" % (i, c["source"]))
    L.append("-->")
    return sanitize("\n".join(L))


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# 正文统一基线：偷自微信「一键排版」的参数，但不引入它的重排器。
# 三级 <section>/<div> 容器在发布链路会被 pm_safe_body 整块 unwrap（div/section
# 会被 ProseMirror 吞掉），所以基线必须落到每个 <p> 上，不能只挂外层——
# 否则正文会退回微信默认字号（之前就是这么丢的：外层 container 设了
# font-size:16px，粘贴后整段回到微信默认 15px/1.6 灰黑，只有标题和评分表有样式）。
# color 用 rgba(0,0,0,0.9) 而非纯黑，是它给出的原值；font-size/line-height 同理。
BODY_BASE = "font-size:17px;line-height:1.8;color:rgba(0,0,0,0.9)"


def _disp_em(s):
    """估算一段文字占多宽（单位 em，按正文 15px 量）。

    全角/半角宽度比约 1 : 0.55。只用来判断「这一行还放不放得下」，
    不做精确排版，所以不需要很准。
    """
    w = 0.0
    for ch in s:
        w += 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55
    return w


# 「标签 + 值」整行超过这个宽度（em）就换版式。手机正文区约 343px、15px 字，
# 去掉左右内边距后一行约 21em；标签再占 2~4em，留给值的余量约 16~18em。
# 取 19 作整行阈值并**宁可提前分流**：不踩「刚好被挤扁」的边界（见下面长值说明）。
LONG_ROW_EM = 19.0


def _flush_html_rows(P, rows):
    """把连续的键值行渲成「一体化评分表」。

    为什么不直接用 <table>：公众号编辑器（ProseMirror）不认 table 标签，
    pm_safe_body 会把它塌成段落。所以表格是「拼」出来的——
    每行一个 p（浅灰底 + 左侧橙条），行间用 border-top 画细线、
    首末行各自加圆角，合起来才像一整张表。末行用 margin-bottom 收尾，
    不再补空段落（空段落会把行距撑开，实测过）。

    2026-09-21 在真实编辑器里实测（tmp/probe_wxstyle*.py，保存草稿后重载读回）：
      · display:flex / justify-content / width / float / border-radius 微信**全保留**
        ——以前 WX_STYLE_DROP 把它们拉黑是猜的，白白让「值」只能靠「······」凑位；
      · 里面没有文字的空 span 会被整块剔除 → 进度条那类空盒子方案不可行，
        要画刻度只能用字符（●●●○○）。

    ⚠ 两个版式按「行宽」分流（2026-09-21 新增）：
      · 短值行 → 标签左 / 值右，display:flex 两端对齐（一体化表的主体）；
      · 长值行 → 「标签：正文」整段左对齐，**不套 flex**。
    为什么要分流：flex 两端对齐排长文本会两败俱伤。微信编辑器会把我们写的
    white-space 覆盖成 break-spaces（实测草稿 100000117），所以「标签
    white-space:nowrap 防挤压」在公众号里**根本无效** —— 一旦值很长，
    flex-shrink 就会把标签压到一列一个字（「备注」竖排），值还右对齐折行，
    非常难看。短值不会触发挤压，所以照旧用两端对齐好看。
    """
    if not rows:
        return
    last = len(rows) - 1
    for i, (lab, val) in enumerate(rows):
        vhtml = _esc(val).replace("&amp;**", "**")
        # 卡片外壳（底色 + 左橙条 + 行间细线 + 首末圆角）：两种版式共用
        card = ["margin:%s" % ("0 0 16px" if i == last else "0"),
                "background:#f6f8fa",
                "border-left:3px solid #e5b567",
                "padding:8px 12px",
                "font-size:15px",
                "line-height:1.7"]
        if i == 0:
            card += ["border-top-left-radius:8px", "border-top-right-radius:8px"]
        else:
            card.append("border-top:1px solid #eaeef2")
        if i == last:
            card += ["border-bottom-left-radius:8px",
                     "border-bottom-right-radius:8px"]
        if _disp_em(lab) + _disp_em(val) > LONG_ROW_EM:
            # 长值行：不套两端对齐，标签与正文同行左对齐自然换行
            P.append('<p style="%s;">'
                     '<span style="color:#57606a;">%s</span>：'
                     '<span style="color:#24292f;">%s</span></p>'
                     % (";".join(card), _esc(lab), vhtml))
        else:
            # 短值行：标签左、值右，两端对齐
            P.append('<p style="%s;display:flex;justify-content:space-between;">'
                     '<span style="color:#57606a;white-space:nowrap;">%s</span>'
                     '<span style="color:#8a5a00;font-weight:bold;text-align:right;'
                     'margin-left:12px;">%s</span></p>'
                     % (";".join(card), _esc(lab), vhtml))
    rows.clear()   # 同 _flush_md_rows：不清空的话同一张表会被反复输出


def render_html(case, titles, manual, secs, score):
    P = []
    P.append('<section style="%s;'
             'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
             '\'Microsoft YaHei\',sans-serif;">' % BODY_BASE)
    if not manual:
        P.append('<!-- [!] 标题由模板生成，发布前请手改 -->')
    P.append('<h1 style="font-size:22px;font-weight:700;line-height:1.4;margin:0 0 18px;">'
             '%s</h1>' % _esc(titles[0]))

    for head, paras in secs:
        if head:
            # 同 render_markdown：去掉「一、二、三…」编号
            P.append('<h2 style="font-size:17px;font-weight:700;margin:26px 0 12px;'
                     'padding-left:10px;border-left:3px solid #e5b567;">%s</h2>'
                     % _esc(head))
        rows = []
        for p in paras:
            if not p:
                continue
            if _is_row(p):
                rows.append(_split_row(p))
                continue
            _flush_html_rows(P, rows)
            if p.startswith("```"):
                # 代码块 → 真正的 <pre><code>。同 render_markdown 的理由：
                # 命令行示例的缩进/换行必须原样保留，读者要能直接复制。
                code = re.sub(r"^```[a-zA-Z]*\n?", "", p)
                code = re.sub(r"\n?```$", "", code)
                P.append('<pre style="background:#f6f8fa;border:1px solid #e1e4e8;'
                         'border-radius:6px;padding:12px 14px;margin:0 0 14px;'
                         'overflow-x:auto;font-size:13px;line-height:1.6;'
                         'font-family:Consolas,Monaco,\'Courier New\',monospace;'
                         'color:#24292f;">%s</pre>' % _esc(code))
            elif p.startswith("- "):
                # 2026-09-24：去掉 padding-left+text-indent 的悬挂缩进 —— 正文没有
                # 项目符号，单行时看不出效果，多行时「首行顶格、续行缩进」反而
                # 显得错乱。列表项与普通段落同款，全部左缘对齐。
                P.append('<p style="%s;margin:0 0 8px;">'
                         '%s</p>' % (BODY_BASE, _esc(p[2:]).replace("&amp;**", "**")))
            elif head is None:
                # 钩子/导语段：保留灰底灰字的设计，只补字号与行高跟正文对齐
                P.append('<blockquote style="font-size:17px;line-height:1.8;'
                         'margin:0 0 18px;padding:12px 14px;'
                         'background:#f6f8fa;border-left:3px solid #d0d7de;color:#57606a;">%s'
                         '</blockquote>' % _esc(p))
            else:
                P.append('<p style="%s;margin:0 0 14px;">%s</p>'
                         % (BODY_BASE, _esc(p).replace("&amp;**", "**")))
        _flush_html_rows(P, rows)

    P.append('<hr style="border:none;border-top:1px solid #e1e4e8;margin:28px 0 18px;">')
    for p in OUTRO_OVERRIDES.get(case.get("id"), OUTRO):
        P.append('<p style="margin:0 0 10px;color:#57606a;font-size:14px;">%s</p>' % _esc(p))
    P.append('<!-- 核对用来源见同名 .md 草稿 -->')
    P.append('</section>')
    return sanitize("\n".join(P))


# ------------------------------------------------------------------ 主流程
def load_cases(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
    if not isinstance(cases, list):
        print("[!] cases.json 结构异常：拿不到 cases 列表")
        sys.exit(1)
    # 读者不该看到「（Stripe 验证，2026-09-17 快照）」这类**给作者自己看的**元数据标注。
    # 数据里已经清过一遍，这里是第二道闸门：以后往 cases.json 补字段时漏了也不进正文。
    clean_snapshot_marks(cases)
    # 同理，列表项必须收尾：没有句号的半截句并排摆在一起读起来很涩（2026-09-24）。
    end_sentences(cases)
    # 「（具体定位未获取）」这类采集侧兜底占位不能给读者看 —— 它挂在 one_liner 上，
    # 会一路串进标题、摘要、封面和正文首段（2026-09-24 用户反馈）。见 text_clean。
    clean_placeholders(cases)
    return cases


def rank_key(case):
    """排序键：分数降序，并列时按 id 兜底。

    为什么要有第二条：Python 的 sort 是**稳定**的，所以两条同分的案例会保持
    cases.json 里的先后。那等于把「录入顺序」当成了隐藏的排序依据 ——
    同一个库换个顺序输出，公众号排期就跟着变。加 id 兜底后，排期只由内容决定。
    """
    return (-virality_score(case), case.get("id") or "")


def pick(cases, args):
    scored = sorted(((virality_score(c), c) for c in cases),
                    key=lambda t: rank_key(t[1]))
    if args.id:
        hit = [(s, c) for s, c in scored if c.get("id") == args.id]
        if not hit:
            print("[!] 找不到案例 id=%s" % args.id)
            print("    可选：" + "、".join(c.get("id", "?") for _, c in scored))
            sys.exit(1)
        return hit
    if args.min_score is not None:
        scored = [(s, c) for s, c in scored if s >= args.min_score]
    if args.top:
        scored = scored[:args.top]
    return scored


def main():
    ap = argparse.ArgumentParser(
        description="把精写案例转成公众号文章草稿（只读 data/，不改数据）")
    ap.add_argument("--id", help="只生成指定 id 的案例")
    ap.add_argument("--all", action="store_true", help="生成全部精写案例")
    ap.add_argument("--top", type=int, help="按传播力取前 N 条")
    ap.add_argument("--list", action="store_true", help="只打印传播力排名，不写文件")
    ap.add_argument("--min-score", type=float, default=None, help="只要传播力 ≥N 的")
    ap.add_argument("--format", choices=("md", "html", "both"), default="md")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录，默认 out/articles")
    args = ap.parse_args()

    if not (args.id or args.all or args.top or args.list):
        ap.print_help()
        print("\n至少给一个：--list / --id X / --top N / --all")
        return 0

    cases = load_cases(CASES_PATH)
    chosen = pick(cases, args)

    if args.list:
        print("=" * 78)
        print("  传播力排名 = 基础分（纠错/金额/象限，可算） + 人工权重（EDITORIAL_WEIGHT）")
        print("=" * 78)
        print("  %-3s %-20s %6s %6s %6s %-4s %-8s %s"
              % ("#", "id", "基础", "权重", "合计", "纠错", "象限", "headline"))
        for i, (s, c) in enumerate(chosen, 1):
            comp = c.get("composite") or {}
            base = virality_base(c)
            w = EDITORIAL_WEIGHT.get(c.get("id"), 0.0)
            print("  %-3d %-20s %6.1f %+6.1f %6.1f %-4d %-8s %s"
                  % (i, c.get("id", "?"), base, w, s,
                     len(c.get("corrections") or []), comp.get("quadrant", "-"),
                     (c.get("metrics") or {}).get("headline", "")))
        print("-" * 78)
        print("  「权重」这一列就是你的编辑判断，改它 = 改发布顺序。")
        return 0

    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    written, all_hits, all_warns = [], [], []
    for s, c in chosen:
        titles, manual = build_titles(c)
        secs = build_sections(c)
        stem = c.get("id", "case")
        if args.format in ("md", "both"):
            text, hits = render_markdown(c, titles, manual, secs, s)
            p = os.path.join(args.out, stem + ".md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            written.append(p)
            all_hits += hits
            all_warns += ["%s:%s" % (stem, w) for w in scan_warn(text)]
        if args.format in ("html", "both"):
            text, hits = render_html(c, titles, manual, secs, s)
            p = os.path.join(args.out, stem + ".html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            written.append(p)
            all_hits += hits
        print("  [OK] %-20s 传播力 %5.1f  标题%s"
              % (stem, s, "已手改" if manual else "待手改"))

    print("-" * 62)
    # 跨盘符安全版（同 build_static.safe_relpath）：输出目录可能落在别的盘
    # （测试临时目录就在 C:），裸 relpath 会抛 ValueError 把整条流程打断，
    # 而这里只是打印一行日志 —— 不值得为它崩。
    try:
        out_show = os.path.relpath(args.out, ROOT)
    except ValueError:
        out_show = os.path.abspath(args.out)
    print("  生成 %d 个文件 -> %s" % (len(written), out_show))
    if all_hits:
        print("  [!] 宣传性词汇已替换：%s" % "、".join(sorted(set(all_hits))))
    if all_warns:
        print("  [!] 以下词语只在正文里保留、没动，但放进标题有风险，自己看一眼：")
        for item in all_warns:
            print("        %s" % item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
