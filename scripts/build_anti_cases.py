#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 data/anti_cases.json —— 「反向样本表」（33 条不建议照抄的案例）。

为什么要独立一张表，而不是塞进 cases.json
--------------------------------------------
`data/graveyard.json` 当初独立的理由写在 server.build_payload 的注释里：
「成功案例讲怎么成的，失败案例讲怎么死的，两者的证据要求不同，混在一张表里
会让 case 的入库闸门（content_gate）失去意义」。

反向样本是第三种东西，和前两种都不该混：
- 它们没死（33 条里 20 多条是活得很好的独角兽），进墓碑区是事实错误；
- 它们也不该进 cases.json：那 274 条是「你能抄什么」的推荐位，
  而 china_fit / solo_fit / composite 三套排行是按「值得做」排序的。
  把一批「你搬不动」的东西塞进去打低分，等于在排行榜尾部堆 33 条无信息量的条目，
  还会把 `verify_deploy` 依赖的 curated 计数一起抬高。

所以：独立表，独立证据口径，`role` 决定它写文章时落在哪一栏。

三种 role（一条可以命中多个，`role` 记主因）
--------------------------------------------
- `structural`  结构不可复制：护城河是钱 / 算力 / 人力规模 / 数据资产 / 关系资产
                → 文章落点「门槛的具体数额是多少」
- `license`     资格门槛：不是能不能做，是有没有资格做（牌照 / 执业资质 / 专业责任）
                → 文章落点「缺的是哪一张证，国内对应哪一张」
- `caliber`     数字不成立：页面那个数根本不是收入
                → ⚠ 这一类不许写成「它做不成」。它们多半活得好，只是数字被读错了。

`caliber` 枚举（比 cases.json 多四个，因为这里要记「那个数其实是什么」）
--------------------------------------------------------------------
mrr / arr / annual / funding / valuation / gmv / price / unknown / invalid

数据从哪来
----------
`tmp/neg/<slug>.html`：howtomakemi.com/cases/<slug>/ 的整页快照（2026-10-26 抓，33/33 成功）。
本脚本只负责「人工判断表 + 页面事实」合并成 JSON；页面解析在 tmp/parse_neg_pages.py。
⚠ 原始媒体（TechCrunch / Sacra / Bloomberg 等）**没有逐条打开**，
所以 verification 一律记 `aggregator`，不冒充 official。

用法
----
    python scripts/build_anti_cases.py            # 写 data/anti_cases.json
    python scripts/build_anti_cases.py --check    # 只校验，不写盘
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio                                                          # noqa: E402

PARSED = os.path.join(ROOT, "tmp", "neg", "parsed.json")
CALIBER_TABLE = os.path.join(ROOT, "data", "htm_backlog_caliber.json")
OUT = os.path.join(ROOT, "data", "anti_cases.json")
PAGE = "https://howtomakemi.com/cases/%s/"

# ======================================================================
# 人工判断表。字段说明见文件头。
# ⚠ 这张表是本文档的主体：新增反向样本必须在这里登记，否则脚本直接报错，
#   不做「缺了就静默跳过」——静默跳过正是 score_china_fit 那个坑。
# ======================================================================
JUDGE = {
    # ── structural：护城河在钱 / 算力 / 人力 / 数据 / 关系上 ──────────────
    "higgsfield": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥47.04亿」在数据口径段写的是「年化收入」，卡片标签却是「月收入」——"
                     "按标签读会放大 12 倍。",
        role_note="「把模型聚合成可用工作流」这层产品逻辑确实可抄，但托着它的是每月数亿元的推理账单"
                  "和一支能签下 390 家财富 500 强的 BD 队伍。",
        why_not_copy=["30 天生成 2000 万条内容，推理成本按月计；单位成本靠规模摊，你起步就是最贵的那一档",
                      "B 轮 ¥26.88亿（DST 领投），估值 ¥362.88亿；3000 万用户覆盖 238 个国家",
                      "它的「聚合 15+ 视频模型」不是技术活，是采购活——我们连议价资格都没有"],
        article_angle="同一条工作流，你跑一万次和他跑两千万次，单位成本差两个数量级。"
                      "「聚合不是壁垒，账单才是」——这条能写成「为什么模型套壳的生意只有头部能活」。",
    ),
    "lovable": dict(
        role="structural", roles=["structural"],
        # ⚠ 2026-10-26 上站时改：原记 annual，但本条的 caliber_note 原文就是
        # 「¥13.44亿 ARR」——ARR 是年化经常性收入，不是年营收。上站后卡片会把
        # 口径标签和这句说明**并排**显示，两者不一致就是当场自相矛盾。
        # 这一改让 by_caliber 从 arr19/annual4 变成 arr20/annual3（见手册 §1.2）。
        caliber="arr",
        caliber_note="「¥13.44亿」原文是「¥13.44亿 ARR」，卡片标签写「月收入」。"
                     "注意这个数同时等于 2025-07 A 轮融资额 ¥13.44亿——同一个数字被用在两个口径上。",
        role_note="开源前身 GPT Engineer 的社区、两年 ¥36.96亿的融资、以及每个月付给模型厂商的钱，"
                  "三样都不是一个人能凑齐的。",
        why_not_copy=["累计融资约 ¥36.96亿，估值 ¥443.52亿（Google、NVIDIA 参投）",
                      "230 万活跃用户、18 万付费；这个量级的推理开销本身就是一道融资门槛",
                      "同页数据还写「¥121万付费」——那是 18 万付费用户被汇率换算了一次的产物，"
                      "说明连它自己的数字都在不同段落打架"],
        article_angle="「说句话生成全栈应用」是产品判断，可抄；让它成立的是一次性买到 200 万活跃用户的"
                      "分发能力和融资节奏，不可抄。真正的反面教训是：这类案例被读成「两个人做出来的」，"
                      "而两个人只是站在 ¥36.96亿上面。",
    ),
    "cognition": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥4.91亿」原文是 Devin 的 ARR 公开口径，标签写「月收入」。",
        role_note="估值 ¥685.44亿；九个月 ARR 涨 70 倍的曲线里，有一大段是收购 Windsurf 并进来的。",
        why_not_copy=["估值 ¥685.44亿（2025-09），卖的是「AI 软件工程师」这个最贵的岗位",
                      "增长靠并购：Windsurf 并进来，客户和收入一起并进来",
                      "对手是 Anthropic / OpenAI 自己的 agent 产品，这是资本与模型能力的双线作战"],
        article_angle="「ARR 九个月涨 70 倍」这种叙事最该被拆开看：多少是产品长的，多少是买来的。"
                      "这条适合做「增速叙事的成分表」——把并购、口径、时点三件事分离开。",
    ),
    "primeintellect": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥6.72亿」为 ARR 公开口径，标签写「月收入」；同卡的副标题给的是估值 ¥67.2亿。",
        role_note="「让人人能训自己的模型」听着像工具，实际卖的是分布式训练的算力编排——"
                  "客户是研究机构和实验室，不是个人开发者。",
        why_not_copy=["6000+ 客户全是研究侧，采购的是算力与训练编排，不是订阅席位",
                      "A 轮 ¥8.74亿、估值 ¥67.2亿（Radical Ventures 领投）",
                      "卖算力的毛利结构与 SaaS 相反：收入越大，先垫的钱越多"],
        article_angle="「基础设施」这三个字最容易骗人：它看起来是软件，实际是替客户垫算力。"
                      "这条能写「为什么 AI 基础设施不是小团队赛道」。",
    ),
    "appliedcompute": dict(
        role="structural", roles=["structural", "caliber"],
        caliber="arr",
        caliber_note="「¥3.36亿」原文是年化收入；数据口径段只复述了估值基准（新轮未关闭，"
                     "以已披露 ¥87.36亿为准），收入本身没有二次来源。",
        role_note="三个前 OpenAI 研究员（o1 / Codex / RL 基建）。这条案例的产品是人，不是代码。",
        why_not_copy=["创始履历本身就是获客渠道：企业买的是「OpenAI 原班人马帮我训模型」",
                      "估值 ¥87.36亿，在谈 ¥201.6亿新一轮",
                      "「用客户自己的数据训专属模型」= 每个客户一次交付，本质是咨询+算力的混合体"],
        article_angle="「Specific Intelligence」这个故事的可抄部分为零，可读部分很大："
                      "它说明企业真正想买的是「懂我行业的人」，而不是「更好的模型」。",
    ),
    "openrouter": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥3.36亿」数据口径段明写是第三方（Sacra）估算，不是公司披露——"
                     "标签却记成「月收入」。",
        role_note="一个接口调 400+ 模型，位置是「不管谁赢都要经过你这里」。这个位置是先用免费额度"
                  "和开发者口碑抢来的，不是设计出来的。",
        why_not_copy=["估值 ¥87.36亿，报道称 Stripe 出手收购；400+ 模型的接入是逐个谈的",
                      "它的定价能力来自「比价」：要拿到各家模型的批发价，先得有量",
                      "同一条链路上还有 LiteLLM 这类开源替代，护城河是结算与信用，不是代码"],
        article_angle="「管道生意」看着最稳，实际最难起步：没有量就拿不到批发价，拿不到批发价就没有量。"
                      "这条适合写「中间商的冷启动死结」。",
    ),
    "retell": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥5.38亿」为 ARR，标签写「月收入」。",
        role_note="护城河是一个数字：约 600ms 的实时响应。功能一天能抄，延迟要一年。",
        why_not_copy=["语音链路是端到端工程：ASR、打断处理、TTS、抖动缓冲，每一段都要压到毫秒",
                      "按分钟计费、0 重资产，但上游模型与线路成本随通话量线性涨",
                      "三年做到 ¥5.38亿 ARR，前两年基本在调延迟——这段投入没有收入支撑"],
        article_angle="「600ms」这种数字才是真护城河，因为它不是功能列表上的一项。"
                      "这条能写「什么样的指标抄不动」：能写进 PPT 的都能抄，能写进 SLO 的抄不动。",
    ),
    "decagon": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥2.96亿」为 2025 年底 ARR，标签写「月收入」。",
        role_note="按「解决量」计费，意味着要为客户的结果兜底；背后是模型 + 人工复核 + 客户系统集成三层。",
        why_not_copy=["估值 ¥302.4亿，客户是 Eventbrite、Notion 这个量级的企业",
                      "按解决量收费 = 卖结果不卖工具，答错一次要赔的是客户体验",
                      "接一套企业客服系统要动工单、知识库、权限，一个客户三个月起"],
        article_angle="「按结果收费」听起来比订阅高级，实际是把交付风险接了过来。"
                      "这条能写「定价模式决定你能不能一个人干」。",
    ),
    "sanas": dict(
        role="structural", roles=["structural", "caliber"],
        caliber="unknown",
        caliber_note="标签 ¥1.41亿；同卡副标题写「Inc 记 2 年做到 ¥4.17亿、2026 冲 ¥8.74亿」，"
                     "两个数都对不上 ¥1.41亿，数据口径段也只写「Inc. 营收报道」没说年还是月。",
        role_note="实时口音转换+降噪，卖给呼叫中心。一个客户几万个坐席，这是电信级工程的生意。",
        why_not_copy=["累计融资 ¥8.13亿；语音实时处理是重工程活",
                      "客户是客服外包商与大型呼叫中心，销售周期以季度计",
                      "「把口音转成标准音」在部分市场有伦理争议，政策风险随地域变化"],
        article_angle="「卖坐席」和「卖席位」是两种生意：前者一次签几万个，后者一次签一个。"
                      "这条能写「为什么 to-呼叫中心的工具轮不到个人做」。",
    ),
    "opencode": dict(
        role="structural", roles=["structural"],
        caliber="annual",
        caliber_note="「¥1.68亿」原文是「预计年收入」，标签写「月收入」；数据口径段自己也注明"
                     "「为『预计』而非审计数字」。",
        role_note="800 万月活、GitHub 最受欢迎——这个位置是开源社区攒出来的，不是产品做出来的。",
        why_not_copy=["核心工具开源免费，收入来自按 token 收费的 Zen 网关，等于自己开了一家模型分销",
                      "800 万月活的运维成本与社区治理成本，与收入不成比例",
                      "同赛道是 Claude Code / Cursor / Codex，全部有大厂预算在烧"],
        article_angle="开源编程工具的护城河是贡献者数量，而贡献者只在「你已经是标准」之后才来。"
                      "这条能写「开源商业化的顺序问题」。",
    ),
    "n8n": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥2.69亿」原文「ARR 超 ¥2.69亿」，标签写「月收入」。",
        role_note="20.4 万 GitHub 星、23 万活跃用户、估值 ¥168亿——七年攒的社区资产，不是功能堆出来的。",
        why_not_copy=["社区版免费、付费来自企业合规与托管，这是典型 open-core，要先有社区才谈得上付费",
                      "20 万星的积累跨了七个版本周期；同期同类产品大多已经停更",
                      "接 400+ 个第三方系统的连接器，是长期苦力活，不是一次性开发"],
        article_angle="open-core 的付费转化只在「企业必须合规」时才发生。"
                      "这条能写「开源项目什么时候才开始赚钱」。",
    ),
    "hightouch": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥6.72亿」原文「2025 年 ARR 突破 ¥6.72亿」，标签写「月收入」。",
        role_note="「反着做 CDP」这个产品判断很漂亮，但把它卖进 Spotify、DoorDash 的是一支企业销售组织。",
        why_not_copy=["累计融资超 ¥21.64亿，估值 ¥184.8亿（14 个月翻倍）",
                      "同步过 7.3 万亿条记录、覆盖 200 多个下游；每个下游都要单独适配",
                      "客户名单是 Spotify / DoorDash / Domino's——这是靠销售周期换来的信任"],
        article_angle="「不搬数据、只做同步」是可抄的判断；「怎么让 Spotify 签字」不可抄。"
                      "这条能写「产品判断与销售组织哪个才是壁垒」。",
    ),
    "glean": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥6.72亿」原文「ARR 官方 2025 财年破 ¥6.72亿」，标签写「月收入」；"
                     "同段还给了 Sacra 估的 ¥20.16亿（2026-05），两个数相差一倍多。",
        role_note="把 Slack、Drive、Salesforce 上百个系统的知识统一起来——连接器数量是苦力活，"
                  "每个企业系统都要单独谈权限模型。",
        why_not_copy=["估值 ¥483.84亿；创始人 Arvind Jain 是前 Google 杰出工程师，首单靠履历打开",
                      "上百个连接器，每个都涉及权限、增量同步、字段映射",
                      "企业搜索的采购方是 IT 与安全部门，一次上线要过合规评审"],
        article_angle="「企业版 AI 搜索」这个品类只有一种做法：把连接器铺满。"
                      "这条能写「连接器数量本身就是护城河」。",
    ),
    "crustdata": dict(
        role="structural", roles=["structural", "caliber"],
        caliber="invalid",
        caliber_note="标签 ¥336万；同卡副标题写的是公司官网原话「We hit ¥6,720万 ARR! ¥336万 to ¥6,720万」，"
                     "¥336万 是**增长曲线的起点**，不是当前值。取错端点。",
        role_note="6000 万家公司、10 亿人的实时 B2B 数据——数据资产是买来+爬来+清洗出来的。",
        why_not_copy=["数据规模 6000 万公司 / 10 亿人，实时更新；这是持续投入不是一次性采集",
                      "客户是 AI agent 厂商（模型需要实时事实），属于上游供给",
                      "「7 个月营收从 77 万冲到 400 万+」的曲线靠的是数据覆盖度，不是功能"],
        article_angle="这条本身就是一个数据事故的前排座位：同一条案例，标签取曲线起点、正文写终点。"
                      "「同一个数在同一个页面上有两个值」是可以单独写一篇的题材。",
    ),
    "micro1": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥6.72亿」为 ARR，标签写「月收入」；一年内从 ¥4,704万 涨到 ¥6.72亿（约 14 倍）。",
        role_note="它卖的不是软件，是「筛过的领域专家」——给 OpenAI / Anthropic / Google 供人做 RLHF。",
        why_not_copy=["供给端是几万名通过考试的领域专家，这是一张人力网络，不是代码库",
                      "客户是前沿实验室，采购量随它们的训练预算波动，没有留存概念可言",
                      "毛利随专家单价走，规模越大越像人力外包公司"],
        article_angle="「AI 数据」这门生意最容易被误读成 SaaS。"
                      "这条能写「看着像软件的人力公司」——以及为什么它的收入曲线会随客户预算断崖。",
    ),
    "huntress": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥16.8亿」原文「2.5 亿 ARR」（$250M），标签写「月收入」。",
        role_note="报告把它归在「牌照/合规」，但更准确的卡点是**人力规模**：24×7 托管安全 = 三班倒的 SOC。",
        why_not_copy=["800+ 员工、27 万家企业客户、2 万家渠道伙伴——800 人是产品的一部分",
                      "24×7 值守不能靠 AI 完全替代，出事要有人接电话",
                      "2.5 亿美元 ARR 在安全行业里也属于头部，渠道体系是十年铺的"],
        article_angle="托管服务型安全公司的人头就是交付能力。"
                      "这条能写「什么时候人力规模本身就是护城河」——以及为什么它反过来限制了利润率。",
    ),
    "skello": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥3.77亿」原文「ARR 超 ¥3.77亿」，标签写「月收入」。",
        role_note="一线员工排班 SaaS，真正难的不是排班算法，是法国劳动法的合规规则库。",
        why_not_copy=["超 400 名员工、3 万家企业客户、70 万日活；2026-07 拿 ¥15.06亿开始欧洲并购",
                      "排班规则绑定各国劳动法（工时、加班、休息间隔），每进一国重做一遍",
                      "国内这块位置已经被 HR SaaS 占满，且合规口径完全不同"],
        article_angle="排班 SaaS 卖的是「不出劳动纠纷」，这条能写「合规知识怎么变成产品壁垒」——"
                      "以及为什么这类公司最后都去做并购。",
    ),
    "omnidocs": dict(
        role="structural", roles=["structural", "caliber"],
        caliber="annual",
        caliber_note="「¥2.03亿」副标题自己标了「含 Xink」——这是**并购合并口径**，不是内生营收；"
                     "数据口径段只列了融资与 6 次收购，没有给营收来源。",
        role_note="2.5 年 6 次收购，6000+ 客户。这家公司的核心能力是并购整合，不是产品开发。",
        why_not_copy=["2.5 年完成 6 次收购（含 Xink），由 Main Capital 主导——增长是买来的",
                      "6000+ 客户大部分随被收购标的一起进来的",
                      "文档自动化的交付要接客户的模板体系与打印/归档流程，实施环节重"],
        article_angle="「营收涨得快」和「产品卖得好」是两件事。"
                      "这条能写「并购增长的成分表」——合并口径的收入不该和内生的放在一起比。",
    ),
    "picturethis": dict(
        role="structural", roles=["structural"],
        caliber="mrr",
        caliber_note="33 条里**唯一**一条「月收入」标签名副其实的：数据口径段写的就是「FluxBuilder 月收报道」。",
        role_note="1 亿下载 + 40 万物种数据集，两样都是钱堆出来的；而且它是中国产品（形色），"
                  "出现在一个「出海案例库」里本身就是错位。",
        why_not_copy=["1 亿+ 下载靠长期买量与 ASO，获客成本是持续的现金支出",
                      "40 万物种、98% 准确率是标注数据与模型迭代的复利，不是一次训练",
                      "国内同类（形色本体、花伴侣）已经把位置占住，且变现能力普遍弱于海外版"],
        article_angle="这是全批最值得当「口径教材」的一条：只有它标签没写错。"
                      "用它做对照，能反证另外 32 条错在哪。",
    ),
    "doomers": dict(
        role="structural", roles=["structural"],
        caliber="arr",
        caliber_note="「¥6,720万」原文为 ARR，标签写「月收入」；页面自己注明早期有「3 个月」说法、"
                     "2026-09 更新为「6 个月」，本文取最新口径。",
        role_note="零融资是真的，但资产是手里 2500 万粉丝的创作者网络——这个资产不能买，只能攒。",
        why_not_copy=["2500 万粉丝的创作者网络是十年攒的关系资产，不随产品转移",
                      "「病毒式分发即服务」的交付是排期与创作者协调，人工密度高",
                      "客户是 AI 公司的发布预算，属于甲方市场，议价权不在它手上"],
        article_angle="「零融资做到 ¥6,720万 ARR」是最容易被当成「你也能」的一条。"
                      "这条能写「关系资产算不算护城河」——以及它为什么无法继承。",
    ),
    # ── license：资格门槛 ────────────────────────────────────────────
    "medvi": dict(
        role="license", roles=["license"],
        caliber="annual",
        caliber_note="「¥26.95亿」原文是「2025 年营收」，标签写「月收入」——按标签读会放大 12 倍。",
        role_note="两个人、零融资、¥26.95亿营收，看起来是「AI 极致轻公司」的样板；"
                  "但整条命脉挂在「美国处方药远程医疗」的合规边上，而且已经踩线。",
        why_not_copy=["卖的是 GLP-1 处方药（复方司美格鲁肽/替尔泊肽），需要持证医生开方、合规药房配药",
                      "2026-02-20 FDA 警告信：站点把「复方药」暗示成 FDA 批准、并谎称自己是配药方",
                      "Meta 广告库一度 5000+ 条在投，被扒出大量虚构「医生人设」——流量成本与合规成本同源",
                      "国内对应的是「互联网诊疗 + 处方药网售」双资质，比美国更窄"],
        article_angle="这是「2 个人做 26 亿」叙事的完整反面：省下来的人力成本，最后以诉讼、警告信、"
                      "品牌债的形式还回去。落点不是「别做医疗」，而是「合规成本不会消失，只会延后」。",
    ),
    "geviti": dict(
        role="license", roles=["license"],
        caliber="arr",
        caliber_note="「¥2,016万」来自第三方（arr.club）收录；创始人自己的说法只是「开放第一年数百万美金」。",
        role_note="「早筛 + AI 解读」的订阅制长寿医疗，覆盖 47 州，卖的是检测与处方——医疗责任是入场券。",
        why_not_copy=["100 万+ 次实验室检测、1 万+ 会员；检测项目要与实验室、保险、州级执业规则对齐",
                      "47 州覆盖意味着 47 套远程医疗执业与开方规则",
                      "ARR 只有约 ¥2,016万，团队从 4 人扩到 30+ —— 收入不大但合规负担是满额的"],
        article_angle="长寿医疗是最容易被当成「高客单订阅」的品类。"
                      "这条能写「为什么医疗类订阅的规模效应来得特别晚」：合规成本先到，收入后到。",
    ),
    "develop-health": dict(
        role="license", roles=["license", "caliber"],
        caliber="funding",
        caliber_note="「¥1.3亿」是**累计融资**（pre-seed ¥1,209.6万 + A 轮约 ¥9,609.6万~¥1.18亿），"
                     "标签写「月收入」。这个数从头到尾不是收入。",
        role_note="把处方药「预先授权」自动化——它砍的是保险方与药房之间的行政流程，"
                  "接入的是客户的 EHR 与保险规则，责任在医疗链条里。",
        why_not_copy=["客户是 Ro、LifeMD、Calibrate 这类数字医疗与远程诊疗机构，采购要过 HIPAA 与安全评审",
                      "预先授权（prior auth）的规则由各保险公司各自定义，一家一套",
                      "公司自己的成效数据（行政工作量降 83%）需要客户侧配合才能验证"],
        article_angle="医疗里最脏的活往往最赚钱，但也最不能自助。"
                      "这条能写「B 端医疗 SaaS 的销售周期为什么以年计」。",
    ),
    "hello-clever": dict(
        role="license", roles=["license"],
        caliber="arr",
        caliber_note="「¥3.36亿」为 Sacra 估算的年化收入，标签写「月收入」；"
                     "同段另有「累计处理总价值 ¥336亿」——那是支付流水（GMV），不是收入。",
        role_note="把返现做进支付本身，等于自己站到支付机构的位置上：牌照、清算、反洗钱一样不能少。",
        why_not_copy=["澳大利亚支付/金融服务牌照是硬门槛，返现要动清算链路",
                      "累计处理支付 ¥134.4亿~¥336亿（两处口径不一），覆盖 20+ 市场、54 种支付方式",
                      "1000+ 商户、200 万用户；每接一个市场就要一套本地合规"],
        article_angle="「买了就赚」听着像营销玩法，实际是金融牌照生意。"
                      "这条能写「哪些看起来像产品的生意其实是牌照生意」。",
    ),
    "tern-group": dict(
        role="license", roles=["license"],
        caliber="arr",
        caliber_note="「¥2.02亿」为年化收入，标签写「月收入」；同段还给了 ¥1.61亿 A 轮与累计 ¥2.22亿融资。",
        role_note="把印度护士送到英国和海湾——卡的不是 AI，是执业资格互认与签证配额。",
        why_not_copy=["13 个来源国、13 国执业资质；护士执业资格互认是政府间协议，不是产品功能",
                      "100+ 医疗客户、65 万注册专业人士；招聘周期从 6–12 个月压到 10 周仍要人工跟",
                      "留存率 96% 是行业牌照红利，不是产品黏性"],
        article_angle="跨境人力是「AI 只能压缩流程、不能压缩资格」的典型。"
                      "这条能写「哪些环节 AI 永远替不了」——以及为什么这类公司必须重资产落地。",
    ),
    "tjm-labs": dict(
        role="license", roles=["license", "caliber"],
        caliber="invalid",
        caliber_note="标签 ¥672万 与页面任何数字都对不上：官方公告给的是 ¥6.72亿融资、450+ 药房、"
                     "每日 ¥336万+ 任务；正文另称 ¥1.68亿 ARR 为第三方估算。这个 ¥672万 来源不明。",
        role_note="药房机器人「贴在原有系统外面干活」——前提是能进药房，而它是靠收购药房自己当客户的。",
        why_not_copy=["已收购 EncoreRx 与 Pharmesol，自建客户；这不是卖软件，是买渠道",
                      "450+ 药房、每日 ¥336万+ 任务；药房自动化涉及医疗设备与药品管理资质",
                      "创始人干了 15 年药剂师——行业准入靠资历，不靠产品"],
        article_angle="「不做系统、只做外挂」是个聪明的切口，但它落地的方式是收购客户。"
                      "这条能写「垂直 AI 最后为什么都变成行业公司」。",
    ),
    "lexroom": dict(
        role="license", roles=["license"],
        caliber="arr",
        caliber_note="「¥1.57亿」为 Sacra 第三方估算的 ARR，标签写「月收入」；页面自己也标了「属第三方估算」。",
        role_note="法律 AI 的产出要由律师签字负责，责任不能外包给模型——这是它的天花板，也是它的门。",
        why_not_copy=["B 轮 ¥3.36亿（Left Lane 领投）、A 轮 ¥1.28亿；意大利法律体系起家，逐国复制",
                      "输出要专业责任兜底：错一个判例就是执业事故",
                      "法律数据库（判例、法规、注释）按司法辖区授权，进入成本高"],
        article_angle="「AI 法律助手」的门槛不在模型在法律数据库授权与执业责任。"
                      "这条能写「专业服务业为什么愿意为 AI 付高价、却不肯让它签字」。",
    ),
    "fable-security": dict(
        role="structural", roles=["structural", "caliber"],
        caliber="funding",
        caliber_note="「¥2.08亿」是**出 stealth 时的累计融资**（种子 Greylock 领投、A 轮 Redpoint 领投），"
                     "标签写「月收入」。页面从头到尾没有披露过收入。",
        role_note="两个 Abnormal AI 老兵做「人为风险」——出 stealth 就有 Pennymac、DNC 这类客户，"
                  "靠的是创始人上一家公司攒下的企业信任链。",
        why_not_copy=["出 stealth 即拿 ¥2.08亿融资，种子 Greylock、A 轮 Redpoint",
                      "客户是美国民主党全国委员会、Genesys、Pennymac——采购要过安全评审与合规",
                      "「实时干预员工行为」涉及员工隐私，欧洲市场的合法性另算"],
        article_angle="企业安全的销售本质是「你上一家公司叫什么」。"
                      "这条能写「信任链资产」——以及为什么安全创业几乎没有第一次创业者。",
    ),
    # ── caliber：那个数根本不是收入 ──────────────────────────────────
    "morph": dict(
        role="caliber", roles=["caliber", "structural"],
        caliber="funding",
        caliber_note="「¥1.28亿」是 2024-03 的**种子轮**（Dragonfly/Pantera/Polychain）；"
                     "另一源（everydev）记同轮为 ¥3,864万（Khosla 领投）——两源相差 3.3 倍。",
        role_note="自研「Fast Apply」代码合并模型（约 10500 tok/s、98% 准确率），YC S23。"
                  "参数能查，模型训不出来。",
        why_not_copy=["护城河是一个自研模型的吞吐指标，不是功能",
                      "两源融资额冲突，说明这条案例连「拿了多少钱」都不确定",
                      "客户里有 AWS / Binance，属于大厂采购"],
        article_angle="同一条案例的两个数据源，对同一轮融资给出相差 3.3 倍的数字。"
                      "这条能写「聚合站的数字是怎么一层层歪掉的」。",
    ),
    "bland-ai": dict(
        role="caliber", roles=["caliber", "structural"],
        caliber="funding",
        caliber_note="「¥6.72亿」= 「累计融资超一亿美金」；数据口径段列的四个轮次加起来约 ¥7.12亿，"
                     "**整段没有任何收入数字**。标签写「月收入」。",
        role_note="自研全栈语音模型，给企业做能打三十分钟长电话的 AI 接线员；"
                  "112 名员工、每周 350 万通。自研模型的资本消耗与「被 180 家机构拒绝」是同一件事的两面。",
        why_not_copy=["累计融资约 ¥7.12亿（YC 种子→C 轮 Dell Technologies Capital）",
                      "自研全栈语音模型，112 名员工；每周 350 万通电话的推理成本是硬支出",
                      "客户是 Samsara / Better.com / 克利夫兰骑士队这类企业，采购周期长"],
        article_angle="一条案例的卡片写着「月收入 ¥6.72亿」，点进去发现那是融资额，全文没有收入。"
                      "这是全批最干净的一个「标签与事实脱钩」标本。",
    ),
    "paraform": dict(
        role="caliber", roles=["caliber"],
        caliber="gmv",
        caliber_note="「¥3.36亿」原文是「已向平台招聘官支付约 ¥3.36亿」——平台流水（GMV），不是收入。"
                     "标签写「月收入」。",
        role_note="用 AI 武装独立招聘官。它的收入是抽成，而抽成的基数在别人手里。",
        why_not_copy=["累计融资约 ¥4.37亿（Felicis、Scale Venture Partners）",
                      "GMV ¥3.36亿 = 付给招聘官的钱；平台实际抽成比例未披露",
                      "「收入增长 10 倍」与 GMV 增长 10 倍是两句话，页面把它们混着说"],
        article_angle="平台型生意的 GMV 和营收之间隔着一个抽成率，而抽成率往往不公开。"
                      "这条能写「GMV 是最容易被读成收入的一个数」。",
    ),
    "rox": dict(
        role="caliber", roles=["caliber"],
        caliber="arr",
        caliber_note="「¥5,376万」页面原文是「2025 年『预计』ARR 而非审计数字」——所以它确实是收入口径，"
                     "但是**预估**且未审计。⚠ 与旧口径表冲突：旧表把这条记成 invalid（当成融资额）。"
                     "同段另有 TechCrunch 援引知情人士的 ¥80.64亿估值，公司未确认。",
        role_note="骑在 CRM 上面派几百个 AI agent 替销售干杂活。整条案例没有一个可核的数字。",
        why_not_copy=["ARR 是「预计」、估值是「援引知情人士」且公司未确认",
                      "几百个 agent 并发跑在客户 CRM 上，等于替客户承担数据与稳定性风险",
                      "成立不到两年；这类「AI 外壳 + 得罪平台」的模式，平台一次接口调整就归零"],
        article_angle="一条案例里三个关键数字（收入、估值、融资）全部带「预计 / 援引 / 未确认」。"
                      "这条能写「不可核数字的识别方法」——看修饰词，不看数值。",
    ),
    "crowdreply": dict(
        role="caliber", roles=["caliber"],
        caliber="price",
        caliber_note="「¥665」是 Pro 档**定价**（¥665/月），不是收入。数据口径段全篇没有收入数字，"
                     "只有官网定价档位与「5000+ 品牌在用」。标签写「月收入」。",
        role_note="它的形态是 33 条里最可抄的之一（自助订阅 ¥665/月起、两人团队、早期无融资）；"
                  "它被判「不建议」的原因是**数据**，不是产品。",
        why_not_copy=["真实收入未披露；同一页另有一个「¥672万/月增长渠道」的说法，被媒体标为营销声称、存疑",
                      "「铺内容进 Reddit / Quora」的交付方式有 astroturfing 风险，披露边界决定它能不能长期做",
                      "5000+ 品牌在用无法验证（无第三方复核）"],
        article_angle="这是三篇里唯一一篇「产品没问题、数字有问题」的："
                      "把定价当收入写进卡片，会让读者以为它月入 665 元。"
                      "落点是「一个数字的三种读法，会得出三种完全不同的结论」。",
    ),
}

# 与 data/htm_backlog_caliber.json 的已知分歧（写进记录，不偷偷改旧表）
CALIBER_CONFLICT = {
    "rox": dict(old="invalid", now="arr",
                why="旧表当成「融资轮次金额」，但页面数据口径段原文是「¥5,376万为 2025 年『预计』ARR "
                    "而非审计数字」——它是收入口径（预估），不是融资额。"),
    "crustdata": dict(old="annual", now="invalid",
                      why="旧表取了标签值对应的「营收曲线」，但页面副标题原文是「We hit ¥6,720万 ARR! "
                          "¥336万 to ¥6,720万」——¥336万 是曲线起点，不是当前值。"),
    "tjm-labs": dict(old="annual", now="invalid",
                     why="¥672万 与页面任何数字都对不上（官方公告 ¥6.72亿融资、每日 ¥336万+ 任务、"
                         "¥1.68亿 ARR 第三方估算），来源不明。"),
    "bland-ai": dict(old="unknown", now="funding",
                     why="数据口径段只列了四个融资轮次与运营数据，没有任何收入数字；"
                         "标签值 ¥6.72亿 恰等于「累计融资超一亿美金」，可判为融资口径。"),
    "sanas": dict(old="annual", now="unknown",
                  why="页面只写「Inc. 营收报道」，副标题给的是「2 年 ¥4.17亿、2026 冲 ¥8.74亿」，"
                      "与标签 ¥1.41亿 都对不上，年/月也不明。"),
}


# 「这些别照着做」是独立栏目，不占「拆解海外 · 第 N 篇」的篇号。
# 篇号是栏目身份（见 wechat_publish.build_one 的注释），反面案例挂到那套序号下
# 会让读者以为这是第 260 个「可以做」的案例。所以按 role 分篇，不编序号。
SERIES = {
    "structural": "这些别照着做 · 结构篇",
    "license": "这些别照着做 · 资格篇",
    "caliber": "这些别照着做 · 数字篇",
}
SERIES_FOOTER = "— 万物解释者 · 这些别照着做 —"

# ======================================================================
# 分篇：33 条按「门槛类型」合并成 7 篇
# ----------------------------------------------------------------------
# 为什么还要一层「篇」，而不是就按 role 三个栏目发三篇：
#   role 只有三个值（structural / license / caliber），但 structural 有 21 条。
#   21 条塞一篇 = 一万五千字，读者看完只记得「他们都很有钱」——这正是
#   手册 §2.2 点名的那个陷阱（「写成他们有钱所以你不行」）。
#   所以 structural 必须再按**跨不过去的东西是什么**拆开：
#     钱/算力（bill）· 人头（headcount）· 逐个适配的苦力（grunt）
#     · 攒出来的社区（community）· 履历与信任链（trust）
#   拆开之后每篇只讲一种门槛，落点才是「这个数额/这个人数量级你差多远」。
#
# ⚠ 与 series_tag 是**两个轴**，不是重复字段：
#   series_tag = 栏目身份（三个：结构 / 资格 / 数字），用于公众号栏目标签；
#   volume     = 这一批要写成几篇、每篇叫什么，用于成稿排期与站点分组。
#   栏目不会因为篇数变化而变化，篇会。
#
# ⚠ 每条必须归篇，且每篇的条数要和下面 counts 一致（main() 硬校验）。
#   不做「没归篇就跳过」——静默跳过正是 score_china_fit 那个坑。
VOLUMES = [
    dict(
        key="bill", no="Ⅰ", short="账单", title="用钱买来的规模",
        judge="边际成本随用量线性涨，规模是融资垫出来的",
        lead="这几条的产品逻辑大多能抄。抄不动的是一句话：他们先花掉了几亿。"
             "要读的是「钱具体花在哪一格」——推理账单、算力垫款、买公司、买下载量，"
             "四种花法对应四种完全不同的入场价。",
    ),
    dict(
        key="headcount", no="Ⅱ", short="人头", title="人力就是交付能力",
        judge="交付要有人接电话、有人复核、有人上门，产能不由代码决定",
        lead="有些生意的产能不是代码写的，是人头写的。"
             "看这类案例只需要问一句：它敢不敢在合同里承诺响应时间？"
             "敢，就说明背后有一排工位。",
    ),
    dict(
        key="grunt", no="Ⅲ", short="苦力", title="每个系统都要重做一遍",
        judge="覆盖面靠逐个适配（连接器 / 国别 / 毫秒），只能拿时间换",
        lead="这类门槛最不性感，也最劝退：它没有技巧，只有数量。"
             "400 个连接器、几百个下游、每进一国重写一遍规则——"
             "这些活没有捷径，只能一年一年堆。",
    ),
    dict(
        key="community", no="Ⅳ", short="社区", title="买不到、只能攒的分发",
        judge="星数 / 粉丝 / 月活是时间攒出来的关系资产，不随产品转移",
        lead="「零融资做到几千万」的案例几乎都属于这一篇。"
             "它们确实没拿钱，但手里握着一个用钱买不到的东西："
             "二十年攒下的 2500 万粉丝，或者 20 万颗 GitHub 星。",
    ),
    dict(
        key="trust", no="Ⅴ", short="关系", title="首单靠上一家公司",
        judge="客户信任与数据资产来自创始人履历和长期采购，不是产品功能",
        lead="这一篇最容易被误读成「技术很强」。技术是真的强，"
             "但让客户签字的不是技术：是「他上一家公司叫什么」、"
             "「这三个人的名字在哪个圈子里」。",
    ),
    dict(
        key="license", no="Ⅵ", short="资格", title="缺的不是能力，是那张证",
        judge="门槛是牌照 / 执业资质 / 专业责任兜底，不是技术难度",
        lead="读者常以为自己做不了是因为技术不够。这一篇要说明的是另一件事："
             "有些生意是**资格**问题——处方权、支付清算、护士执业互认、"
             "律师签字。能力到了，证没到，一样进不去。",
    ),
    dict(
        key="caliber", no="Ⅶ", short="数字", title="那个数根本不是收入",
        judge="页面那个数是融资额 / GMV / 定价，不是收入（⚠ 不许写成「它做不成」）",
        lead="这一篇不评价生意，只评价数字。五条案例被聚合成「月入几亿」，"
             "逐条回源之后，这五个数字分别是：一条预估 ARR、一条平台流水、"
             "一条定价档位、两条融资额。⚠ 它们大多活得很好，写法见手册 §3。",
    ),
]

# slug -> 篇 key。⚠ 33 条一条都不能漏（main() 会拦）。
VOLUME_OF = {
    # Ⅰ 用钱买来的规模：推理账单 / 垫算力 / 买公司 / 买下载量
    "higgsfield": "bill",        # 每月数亿推理账单 + 390 家财富 500 强 BD
    "primeintellect": "bill",    # 卖算力 = 替客户垫钱，收入越大垫越多
    "cognition": "bill",         # 估值 ¥685亿，增长一大段是收购 Windsurf 并来的
    "omnidocs": "bill",          # 2.5 年 6 次收购，营收是买来的
    "picturethis": "bill",       # 1 亿下载靠长期买量 + 40 万物种标注
    "lovable": "bill",           # 累计 ¥36.96亿融资 + 18 万付费背后的推理开销（已单写）
    # Ⅱ 人力就是交付能力
    "huntress": "headcount",     # 800 人三班倒的 SOC
    "micro1": "headcount",       # 几万名通过考试的领域专家
    "sanas": "headcount",        # 一个客户几万个坐席，电信级工程
    "decagon": "headcount",      # 按解决量计费 = 为结果兜底，人工复核在链路里
    # Ⅲ 每个系统都要重做一遍
    "glean": "grunt",            # 上百个连接器，每个都要单独谈权限模型
    "hightouch": "grunt",        # 200+ 下游，每个单独适配
    "skello": "grunt",           # 每进一国重写一遍劳动法规则库
    "openrouter": "grunt",       # 400+ 模型逐个谈，才拿得到批发价
    "retell": "grunt",           # 600ms 延迟是一年一年压出来的
    # Ⅳ 买不到、只能攒的分发
    "opencode": "community",     # 800 万月活 / GitHub 最受欢迎，开源攒的
    "n8n": "community",          # 20.4 万星跨七个版本周期
    "doomers": "community",      # 2500 万粉丝的创作者网络
    # Ⅴ 首单靠上一家公司
    "appliedcompute": "trust",   # 三个前 OpenAI 研究员，「产品是人」
    "fable-security": "trust",   # 出 stealth 即拿融资，客户靠上一家公司的信任链
    "crustdata": "trust",        # 6000 万公司 / 10 亿人的数据资产，买来+爬来+清洗
    # Ⅵ 缺的不是能力，是那张证
    "medvi": "license",          # 处方药远程医疗（已单写）
    "geviti": "license",         # 47 州远程医疗执业与开方规则
    "develop-health": "license", # HIPAA + 保险规则 + 客户安全评审
    "hello-clever": "license",   # 支付/金融服务牌照 + 清算 + 反洗钱
    "tern-group": "license",     # 护士执业资格互认 + 签证配额
    "lexroom": "license",        # 律师签字负责，法律数据库按辖区授权
    "tjm-labs": "license",       # 药房准入靠资历，落地方式是收购药房
    # Ⅶ 那个数根本不是收入
    "crowdreply": "caliber",     # ¥665 是 Pro 定价（已单写）
    "rox": "caliber",            # 预估 ARR，未审计
    "paraform": "caliber",       # ¥3.36亿 是付给招聘官的 GMV
    "morph": "caliber",          # ¥1.28亿 是种子轮，两源还差 3.3 倍
    "bland-ai": "caliber",       # ¥6.72亿 是累计融资，全文没有收入
}

# 每篇的条数（含已单独成文的）。改 VOLUME_OF 忘了改这里 → main() 直接报错。
VOLUME_COUNTS = {"bill": 6, "headcount": 4, "grunt": 5, "community": 3,
                 "trust": 3, "license": 7, "caliber": 5}

# 已经单独成文的 3 条（各 role 一条试点）。占着篇里的位置，但不用再写一次，
# 成稿时引用即可。文章落在 out/articles/<id>.html（不入库前由 make_article 生成）。
STANDALONE = {
    "lovable": "公众号-lovable拆解.html",
    "medvi": "公众号-medvi拆解.html",
    "crowdreply": "公众号-crowdreply拆解.html",
}

# 公众号摘要（≤120 字）。只给要成稿的几条写，其余留空回退 one_liner。
SUMMARY = {
    "lovable": "「两个人做出来的」是产出端的事实。投入端的三个事实常被省掉："
               "累计融资、每次生成都要付的推理成本、以及一场免费的分发红利。",
    "medvi": "两个人、零融资、两亿多营收、16% 净利，完全符合「AI 轻公司」的样板。"
             "代价是：把医疗链条全外包出去之后，监管责任并没有跟着外包出去。",
    "crowdreply": "同一行数字，读成 665 元、665 万、还是每个席位 665 元，会得出三种完全"
                  "相反的结论。这条案例的问题不在产品，在它的数字被怎么读。",
}


def load_parsed():
    if not os.path.exists(PARSED):
        raise SystemExit(
            "缺 %s\n先跑：python tmp/parse_neg_pages.py（需要 tmp/neg/*.html 页面快照）" % PARSED)
    with open(PARSED, encoding="utf-8") as f:
        return json.load(f)


def build():
    parsed = load_parsed()
    missing = [k for k in parsed if k not in JUDGE]
    extra = [k for k in JUDGE if k not in parsed]
    if missing or extra:
        raise SystemExit("判断表与页面不匹配：\n  页面有但表里没有 %s\n  表里有但页面没有 %s"
                         % (missing, extra))

    cases = []
    vol_by_key = {v["key"]: v for v in VOLUMES}
    for slug, p in parsed.items():
        j = JUDGE[slug]
        cal = j["caliber"]
        vk = VOLUME_OF[slug]
        rec = {
            "id": slug,
            "name": p["name"],
            "category": p.get("category") or "",
            "origin": re.sub(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF]", "",
                             p.get("country") or "").strip(),
            "one_liner": p["one_liner"],
            "site": p.get("site") or "",
            "role": j["role"],
            "roles": j["roles"],
            # 篇：这一条归到哪一篇里写。VOLUMES 只是篇的元信息，逐条只存 key，
            # 免得到时候改篇名要改 33 处（一处没改就是同一篇两个名字）。
            "volume": vk,
            "series_tag": SERIES[j["role"]],
            "series_footer": SERIES_FOOTER,
            "role_note": j["role_note"],
            "why_not_copy": j["why_not_copy"],
            "article_angle": j["article_angle"],
            "page_label": {
                "key": p.get("page_label_key") or "月收入",
                "value": p.get("page_label_value") or "",
                "sub": p.get("page_label_sub") or "",
            },
            "caliber": cal,
            "caliber_note": j["caliber_note"],
            "verification": "aggregator",
            "verified_at": "2026-10-26",
            "sources": [
                {"label": "howtomakemi.com 案例页（含其自列来源）",
                 "url": PAGE % slug},
            ],
            "source_check": {
                "page_read": "2026-10-26",
                "originals_opened": False,
                "note": "只读了聚合站整页，页面自列媒体的原文未逐条打开",
            },
        }
        if rec["site"]:
            rec["sources"].append({"label": "产品官网", "url": rec["site"]})
        if slug in SUMMARY:
            rec["summary"] = SUMMARY[slug]
        # 已单独成文的 3 条：篇里占位，但不用再写一遍。给站点的用处是
        # 卡片上挂一个「已单独成文」标记 —— 否则读者会以为这 33 条都还是空头支票。
        if slug in STANDALONE:
            rec["standalone"] = True
            rec["standalone_article"] = STANDALONE[slug]
        if slug in CALIBER_CONFLICT:
            c = CALIBER_CONFLICT[slug]
            rec["caliber_conflict"] = {
                "table": "data/htm_backlog_caliber.json",
                "old": c["old"], "now": c["now"], "why": c["why"],
            }
        # 口径不是收入 / 与旧表冲突的，标「口径待定」，不许当已定口径用。
        # ⚠ 它与 graveyard 的 needs_verify 同名字段，但含义更窄：
        #   这里只表示「这个数字到底是什么，还没定」，不表示「整个案例没核」。
        #   33 条的 originals_opened 全是 False，那件事记在 source_check 里。
        if cal in ("invalid", "unknown"):
            rec["needs_verify"] = True
            rec["needs_verify_reason"] = "caliber_undefined"
        elif slug in CALIBER_CONFLICT:
            rec["needs_verify"] = True
            rec["needs_verify_reason"] = "caliber_conflict"
        else:
            rec["needs_verify"] = False
            rec["needs_verify_reason"] = None
        cases.append(rec)

    # 排序 = 篇的顺序 × role × id。JSON 的数组顺序就是站点与成稿的阅读顺序，
    # 所以这里不用「按 role 排」（那样 Ⅳ/Ⅴ 两篇会和 Ⅰ 混在一起）。
    vol_order = {v["key"]: i for i, v in enumerate(VOLUMES)}
    order = {"structural": 0, "license": 1, "caliber": 2}
    cases.sort(key=lambda r: (vol_order[r["volume"]], order[r["role"]], r["id"]))

    by_role, by_cal, by_vol = Counter(), Counter(), Counter()
    for r in cases:
        by_role[r["role"]] += 1
        by_cal[r["caliber"]] += 1
        by_vol[r["volume"]] += 1

    # 篇的元信息 + 成员 id。前端直接拿它渲染分组标题，不用自己按 key 聚合
    # （聚合逻辑一旦前后端各写一份，迟早出现「某条不在任何组里」的空白卡）。
    volumes = []
    for v in VOLUMES:
        ids = [r["id"] for r in cases if r["volume"] == v["key"]]
        volumes.append({
            "key": v["key"], "no": v["no"], "short": v["short"], "title": v["title"],
            "judge": v["judge"], "lead": v["lead"],
            "count": len(ids), "ids": ids,
            "role": next((r["role"] for r in cases if r["volume"] == v["key"]), ""),
        })

    return {
        "_schema": "anti_cases/2",
        "_note": "反向样本表：可以读、不建议照抄的案例。与 cases.json（推荐位）和 "
                 "graveyard.json（已停运）并列，三者证据口径不同，不要混。"
                 "role=structural 护城河在钱/算力/人力/数据/关系；role=license 缺资格而不是缺能力；"
                 "role=caliber 页面那个数根本不是收入——这一类不许写成「它做不成」。"
                 "volume=这一批按「门槛类型」合并成 7 篇时它归哪一篇（无 volume 即旧版本数据）。",
        "_source": "howtomakemi.com/cases/<slug>/ 整页快照（2026-10-26 抓，33/33 成功），"
                   "页面自列媒体的原文未逐条打开，故 verification 一律 aggregator。",
        "_built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "_summary": {
            "total": len(cases),
            "by_role": dict(by_role),
            "by_caliber": dict(by_cal),
            "by_volume": dict(by_vol),
            "volumes": len(volumes),
            "standalone_written": len(STANDALONE),
            "caliber_conflicts": len(CALIBER_CONFLICT),
            "caliber_pending": sum(1 for r in cases if r["needs_verify"]),
            "originals_opened": 0,
        },
        "volumes": volumes,
        "cases": cases,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()

    d = build()
    s = d["_summary"]
    print("反向样本 %d 条" % s["total"])
    print("  按 role  :", s["by_role"])
    print("  按 caliber:", s["by_caliber"])
    print("  口径分歧 :", s["caliber_conflicts"], "| 口径待定:", s["caliber_pending"],
          "| 原始媒体已打开:", s["originals_opened"])
    print("  分篇 %d 篇：" % s["volumes"], " · ".join(
        "%s%s %d" % (v["no"], v["title"], v["count"]) for v in d["volumes"]))
    print("  已单独成文:", s["standalone_written"], "条")

    # 硬校验：三类都得有，且不许出现「标签写月收入、正文却按年读」这种没记 note 的情况
    for r in d["cases"]:
        assert r["role"] in ("structural", "license", "caliber"), r["id"]
        assert r["roles"], r["id"]
        assert r["role"] in r["roles"], r["id"]
        assert r["why_not_copy"], r["id"]
        if r["caliber"] != "mrr":
            assert r["caliber_note"], "%s 不是月收入却没写口径说明" % r["id"]
        if r.get("summary"):
            assert len(r["summary"]) <= 120, "%s 摘要 %d 字，超过公众号 120" % (
                r["id"], len(r["summary"]))
    for k in ("structural", "license", "caliber"):
        assert s["by_role"].get(k), "role=%s 一条都没有" % k

    # ── 分篇校验（2026-10-26 加）──────────────────────────────────────
    # 为什么这些是 assert 而不是 print：分篇是**成稿排期**的依据。
    # 一条没归篇 = 这篇永远写不到它；一条归错篇 = 读者看到的落点不对。
    # 两者都不会报错，只会静默产出错误的排期。
    # ⚠ 用 SystemExit 而不是 assert：python -O 会关掉 assert，而 build 脚本
    #   恰恰是「跑起来就好」的那种，没人会去看有没有被优化掉。
    miss = [r["id"] for r in d["cases"] if not r.get("volume")]
    if miss:
        raise SystemExit("这些条目没归篇（补 VOLUME_OF）：%s" % miss)
    stray = [k for k in VOLUME_OF if k not in JUDGE]
    if stray:
        raise SystemExit("VOLUME_OF 里有判断表不认识的 slug：%s" % stray)
    if set(VOLUME_COUNTS) != {v["key"] for v in VOLUMES}:
        raise SystemExit("VOLUME_COUNTS 的键和 VOLUMES 的 key 对不上")
    for v in d["volumes"]:
        if v["count"] != VOLUME_COUNTS[v["key"]]:
            raise SystemExit("篇「%s」实际 %d 条，声明 %d 条 —— 改了 VOLUME_OF 就要改 VOLUME_COUNTS"
                             % (v["key"], v["count"], VOLUME_COUNTS[v["key"]]))
    if sum(v["count"] for v in d["volumes"]) != len(d["cases"]):
        raise SystemExit("各篇条数加起来 %d ≠ 总条数 %d"
                         % (sum(v["count"] for v in d["volumes"]), len(d["cases"])))
    # 篇 key 必须唯一：前端按 key 分组，重名会把两篇并成一篇且吞掉一篇的标题
    if len({v["key"] for v in VOLUMES}) != len(VOLUMES):
        raise SystemExit("VOLUMES 里有重复的 key")
    for slug in STANDALONE:
        if slug not in JUDGE:
            raise SystemExit("STANDALONE 里的 %s 不在判断表里" % slug)

    if args.check:
        print("--check：不写盘")
        return 0
    atomicio.write_json(OUT, d, indent=2)
    print("已写 → %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
