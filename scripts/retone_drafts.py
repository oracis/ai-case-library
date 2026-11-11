#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量稿批量改写（2026-11-04 用户批准）：14 篇水印稿 + 13 条问题标题。

用法::

    python -X utf8 scripts/retone_drafts.py --check     # 只报计划，不写盘
    python -X utf8 scripts/retone_drafts.py --apply     # 备份后落盘 + 自检
    python -X utf8 scripts/retone_drafts.py --verify    # 只体检

设计约束（都是踩过的坑）：
  · **默认不写盘**、`--apply` 前先把所有受影响文件原样备份到
    `_backups/retone-2026-11-04/`；可重复跑（幂等）。
  · 人工写过的 8 篇长稿（csdn/cod-dex 4876 字、zhihu/shipfast 2748 字…）
    **只做定点手术**：去水印行、去罐头来源句、修标题、修错字，不用
    `gen_platform_md.py --overwrite` 覆盖 —— 那会把人工稿洗回模板。
  · 6 篇机械壳（534–1069 字，五平台共用「一、它到底解决了什么」栏目）
    按 `platform_tone.TONE` 的调性**整篇重写**，事实全部来自 cases.json。
  · `tencentcloud/cod-dex.md` 与 `aliyun/cod-dex.md` 原本逐字相同（只差水印
    平台名），而腾讯云那篇 11-03 已发布 ⇒ 阿里云这篇换成不同命题重写。
  · 不碰 `data/*.json` 台账、不碰 ledger.db、不发布、不推远端。
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import platform_tone as TONE          # noqa: E402
import publish_guard as G            # noqa: E402

BACKUP = os.path.join(ROOT, "_backups", "retone-2026-11-04")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- ① 13 条标题
# 公众号 6 条：旧标题把 metrics.headline 的「TrustMRR 实时核对（非订阅制，
# MRR 恒 0）」整段口径旁注拼了进来，撑到 66–76 字。改成 ≤64 的人话标题。
# ⚠ 2026-11-04 二次改动：这批标题原先还带着金额（「近 30 天收入 $16,120」），
#   全局禁金额（PRD §6.1 #54）后由 `wechat_ai_titles.py --scope money` 用 LLM 重写，
#   下面记的是**重写后的定稿**。若仍写回带金额的旧值，再跑一次 `--apply` 就会把
#   AI 的成果**洗回金额标题**，同时让 `test_platform_tone.test_retone_plan_is_idempotent` 转红
#   —— 所以这张表必须与产物 `<h1>` 逐字一致，改标题后要同步这里。
HTML_TITLES = {
    "out/articles/ai-text-humanizer-netherlands-belgium.html":
        "AI text Humanizer - Netherlands & Belgium 帮创作者把草稿变成可发布的文章",
    "out/articles/altitude-cleaning-crew.html":
        "当住宅清洁变成按次付费，Altitude Cleaning Crew 用透明定价留住客户",
    "out/articles/avenue-ticketing-inc.html":
        "Avenue Ticketing, Inc.：Avenue 卖的不是传统票务，而是科技票务加本地生活入口",
    "out/articles/bloxg-roblox-marketing-agency.html":
        "BLOXG — Roblox Marketing Agency 不是网红市场，而是为开发者提供预订服务",
    "out/articles/cbt-artificial-intelligence-technology-inc-dba-zuuz.html":
        "CBT ARTIFICIAL INTELLIGENCE TECHNOLOGY INC DBA ZUUZ：ZUUZ 将客户邮件转化",
    "out/articles/coaching-marketing-kit.html":
        "Coaching Marketing Kit：不是卖模板，而是帮数字创作者掌握发布节奏",
}

# 头条 2 条：`_truncate_title(t, 30)` 硬切留下的悬空尾巴。
# ⚠ 这两条线上标题 11-02 已用「修改已发布文章」修过（publish_manifest 有记录），
#   这里改的是本地残留产物，不代表平台侧标题变了。
TT_TITLES = {
    "chessigma": "免费下棋平台拿下 100 万用户，只有 576 人付费",
    "augora-ai": "Shopify 商家的 Meta 追踪本来就是错的",
}

# 5 条文本平台 md 标题里，4 条随整篇重写走，1 条单独替换。
# ⚠ 只改 --scan-titles 判红的：tencentcloud/shipfast 原标题合规，不要顺手动它。
MD_TITLES = {
    "drafts/newbatch/zhihu/shipfast.md":
        "一次性买断的产品，为什么从月收入榜单上「消失」了：ShipFast 的三个信号",
}

# ---------------------------------------------------------------- ② 整篇重写
# 6 篇机械模板壳 + 1 篇与腾讯云逐字相同的阿里云稿。
# 每篇的标题句式 / 开场 / 章节 / 收尾都按 platform_tone.TONE 对应平台写，
# 跨平台不再有共用栏目；事实一律对得上 cases.json，不推断未公开实现。
FULL = {}

FULL["drafts/newbatch/zhihu/chessigma.md"] = """\
# 免费版 Chess.com 拿下 100 万用户，为什么只有 576 人付费？

先说结论：这不是「转化率低」的问题，是免费产品的算术本来就和订阅制不同。100 万量级的用户对应 576 个付费订阅，比例确实极低，但只要免费用户的边际成本主要是服务器，这个结构就能成立。

下面分三层讲：它替代了什么、钱从哪里来、这套打法在什么前提下才成立。

## 它替代了什么

Chessigma 是免费国际象棋对战平台，官网口径「Chess.com but free. 1M+ users.」，创始人 Mehdi Ben Haddou，2025 年 3 月上线。核心对战免费，收入来自可选的增值功能，公开档案记有 576 个付费订阅。

时间点值得留意：国际象棋的线上热度在 2023 年之后被直播和短视频重新带起来，一个 2025 年才上线的产品能吃到这波流量，本身就有外部条件的成分。

## 收入结构怎么成立

公开口径是 MRR $7,306（TrustMRR 从支付网关直读，2026-10-14 同步），近 30 天收入 $19,349，累计 $69,071。

近 30 天流水明显高于 MRR，差额大概率来自年付或一次性内购。所以讲增长只能用 MRR 口径，不能拿流水当月度经常性收入——不少「月入 X」的转述就是在这里串了口径。同一份数据里，MRR 增速 +13.2% 而收入增速 -0.1%，两个方向相反，也不能挑一个讲。

## 哪些做法经得起推敲

- 打订阅制霸主，打在价格上最直接，但必须另有非价格收入兜底，否则免费就是纯支出。
- 免费产品的成本以服务器为主，才可以容忍极低的付费比例；换成人力交付，同样比例立刻不成立。
- 实时对战的对手池越深，玩家越不容易走，这是「免费」能持续的前提，不是结果。

## 什么情况下搬不动

品类大盘是外部条件。换一个没有流量红利的品类，同样的免费策略可能连冷启动都过不去。

还有一层口径边界：100 万用户是累计量，576 个订阅是当期快照，两者不能直接相除得出精确转化率，只能当数量级看。以上是公开信息能支撑的判断；引用时别只截一个数。
"""

FULL["drafts/newbatch/zhihu/cod-dex.md"] = """\
# 把编程课做成闯关地图，为什么能攒下 6,686 个订阅？

先说结论：开发者教育里最难卖的不是课程内容，是「学得下去」。Codédex 把课程组织成冒险旅程，用勋章和进度把长周期目标切成每天能完成的小步，这是免费教程遍地的情况下还有人按月付费的主要原因。

下面分三层讲：它替代了什么、收入结构怎么成立、哪些前提搬不走。

## 它替代了什么

Codédex 做互动式编程学习，课程包装成「冒险旅程」（Start your coding adventure），覆盖 HTML/CSS/JavaScript/Python/React 等，每课配即时运行的练习与勋章体系。创始人 Sonny Li 长期做开发者教育内容，产品 2022 年 11 月上线。

公开口径：250+ 小时内容免费开放，Club 订阅解锁进阶路径、项目与社区功能，6,686 个付费订阅。

## 收入结构怎么成立

MRR $84,214、累计 $1,468,111（TrustMRR 从支付网关直读，2026-10-14 同步）。订阅定价是 $9.99/月——按「一杯咖啡」而不是「一门课」来锚定，用户几乎跳过比价环节，月付流失也不痛。

但这份数据里有两处必须一起看：近 30 天收入 $72,088 低于 MRR $84,214，MRR 近 30 天增速 -2.8%、收入增速 -6.0%，历史峰值留存 94%。也就是说它已经过了高速增长期；把它当成「还在猛涨」的例子讲，是拿旧快照说新故事。

## 哪些做法经得起推敲

- 免费层要给到「足够建立信任」的量，付费层卖的才是进阶与身份。
- 游戏化不是装饰：勋章、连续学习、冒险地图解决的是留存，不是获客。
- 创始人已有的受众是最便宜的冷启动。没有这个存量，获客成本得重新算进定价。

## 什么情况下搬不动

「创始人即流量」这条最难复制，它不是一个可以照抄的功能。另外，低价月付靠规模堆收入，一旦订阅数停止增长，缺少提价空间的产品就会立刻显出天花板——这份数据里 -2.8% 的 MRR 增速已经提示了这一点。

以上是公开信息能支撑的判断；数字都带时点，引用前最好回源核一遍。
"""

FULL["drafts/newbatch/cnblogs/chessigma.md"] = """\
# 读 Chessigma：一个免费对弈平台的长连接成本账

翻公开资料的时候顺手记一下这个产品。我关心的不是它卖多少钱，是「完全免费」这四个字在工程上到底意味着什么。

Chessigma 是免费国际象棋对战平台，官网口径「Chess.com but free. 1M+ users.」，2025 年 3 月上线，创始人 Mehdi Ben Haddou。

## 先记形态

核心对战免费，增值功能可选订阅。公开档案记有 576 个付费订阅、MRR $7,306（TrustMRR 从支付网关直读，2026-10-14 同步）、近 30 天收入 $19,349、累计 $69,071。

一局棋十分钟量级，双方要持续交换走子、时钟和断线状态。这和普通的「读多写少」站点不是一种负载：连接是长住的，状态是服务端权威的，掉线还得能续上。

## 我留意到的几个约束

- 对局状态得有唯一权威方。两边客户端各自算时钟，一定会对不上，重连时也会争。
- 断线重连不是可选功能。移动端切后台、网络抖动都是常态，没有重连的免费对弈基本留不住人。
- 匹配池冷的时候算法再好也没用。免费策略能成立的前提是有足够深的对手池，这反过来要求前期能扛住不赚钱的连接成本。
- 把能放在浏览器里的算力放浏览器，是「免费」能长期跑下去的成本前提之一；这属于对公开形态的推断，不是它内部实现的记录。

## 公开数字怎么读

近 30 天流水 $19,349 明显高于 MRR $7,306，差额大概率来自年付或一次性内购。同期 MRR 增速 +13.2% 而收入增速 -0.1%，方向相反——讲增长得说清用的是哪个口径。

另外，100 万用户是累计量、576 个订阅是当期快照，两个数相除只能看数量级，不能当精确转化率。

## 成立条件与边界

免费产品的边际成本主要是服务器，才可以容忍极低的付费比例；换成需要人工交付的形态，这套算术立刻失效。国际象棋 2023 年之后的流量回升也是外部条件，不是产品设计能复制的部分。

记到这里。这些都是带时点的快照，不是现值；要引用最好回源核一遍。
"""

FULL["drafts/newbatch/cnblogs/clawlink.md"] = """\
# 读 ClawLink：把 OAuth 配置这一步整个拿掉

翻到这个小工具的时候，我第一反应是「这不就是又一个 MCP 连接器」，看完形态才觉得值得记一笔：它砍掉的不是某个功能，是配置流程本身。

ClawLink 连接 OpenClaw、Hermes Agent 等与 Gmail、Calendar、Slack、Notion、GitHub、Shopify 等 100+ 应用，官方说法是不需要 OAuth 设置、API 密钥或自定义 MCP 配置，安装、配对账户之后就能在聊天里用。2025 年 8 月 19 日成立。

## 先记形态

公开档案：226 个活跃订阅、MRR $2,219、近 30 天收入 $2,453、累计 $5,196，定价每月 $6，客单价 $9.82，走 Polar 收款（TrustMRR 从支付网关直读，2026-10-14 同步）。

顺手做个自洽核算：226 × $9.82 ≈ $2,219，和 MRR 对得上；定价 $6/月 与客单价 $9.82 的差额，通常来自不同套餐或年付折算，不是数据打架。这种「用户数 × 客单价 ≈ 当期收入」的核对值得养成习惯，累计收入和当期收入串了口径是这类档案最常见的错。

## 我留意到的几个约束

- 把应用授权从用户侧收走，意味着凭据与权限边界全部落在服务端。省掉的是用户步骤，增加的是自己要扛的安全面。
- 100+ 应用的连接器，维护成本不在接第一个，在第三方改 API、改授权流程之后的持续回归。
- 「通过聊天使用」看着是个交互选择，实际决定了错误怎么表达：命令行能回 stack trace，聊天里必须回一句人能看懂的话。
- 这些是根据公开形态推出来的工程约束，不是对它内部实现的描述。

## 公开数字怎么读

MRR 近 30 天增速 +253.2%，同期收入增速 +68.7%——两个方向一致但幅度差很多，通常是低基数叠加一次性入账造成的。基数只有 226 个订阅时，百分比本身信息量有限，别拿它外推。

## 成立条件与边界

这类产品的价值随「用户装了多少个应用」增长，也随大厂把连接能力内置而缩水；护城河不在连接数量，在配置成本压得够低之后留下的使用习惯。

记到这里，数字都是带时点的快照。
"""

FULL["drafts/newbatch/cnblogs/cod-dex.md"] = """\
# 读 Codédex：把课程做成数据，把进度存成事件

翻 Codédex 的公开资料时，我想记的不是「游戏化教学」这个词，而是它给工程留下的两个形状：课程是内容数据，进度是行为事件。这两件事分开看，很多设计选择就顺了。

Codédex 做互动式编程学习，课程包装成「冒险旅程」，覆盖 HTML/CSS/JavaScript/Python/React 等，每课配即时运行的练习与勋章体系。2022 年 11 月上线，250+ 小时内容免费开放，Club 订阅解锁进阶路径与项目。

## 先记形态

公开口径：6,686 个付费订阅、MRR $84,214、累计 $1,468,111、Club 定价 $9.99/月（TrustMRR 从支付网关直读，2026-10-14 同步）。

前端要同时装下三样东西：可读的课件、能跑代码的编辑器、以及对「跑得对不对」的判定。这三样各自都不难，难的是在同一个页面里让它们稳定配合，还要跨设备同步进度。

## 我留意到的几个约束

- 题目不该写死在模板里。把课程当数据，内容团队才能不改代码就上新一课；这也是「250+ 小时」这种体量能维护的前提。
- 进度别只存一个「当前在第几课」的布尔/整数字段。多端提交、离线补交、勋章规则调整都会让它互相覆盖；记事件再推导当前状态更稳。
- 判题如果只比字符串输出，报错对学生就没有价值。可读的失败原因属于产品功能，不是附属品。
- 权限门控必须在服务端。免费层和订阅层的边界只在前端隐藏入口，等于内容已经发出去了。
- 以上是对这类形态的通用工程判断，不是 Codédex 内部实现的记录；公开资料没有披露它的架构。

## 公开数字怎么读

近 30 天收入 $72,088 低于 MRR $84,214， MRR 近 30 天 -2.8%、收入 -6.0%，历史峰值留存 94%。数字带时点，而且已经过了增长高峰；拿它讲「高增长教育产品」是错配。

## 成立条件与边界

低价月付靠规模堆收入，创始人既有的开发者受众又是最便宜的冷启动——这两条凑齐才跑得动，缺一条获客成本就得重新算进定价。

记到这里。数字都是快照，引用前回源核一遍。
"""

FULL["drafts/newbatch/aliyun/chessigma.md"] = """\
# Chessigma：百万用户级实时对弈的负载形状与算力成本

换个角度，从资源与弹性的账本看这个形态：Chessigma 是免费国际象棋对战平台，官网口径「Chess.com but free. 1M+ users.」，2025 年 3 月上线。这里不谈定价策略，只谈负载形状会怎么反过来约束架构。

## 负载长什么样

对局类服务和内容站点的流量曲线完全不同。一局棋持续十分钟量级，双方要不断交换走子与时钟，连接长时间保持，状态还得由服务端裁决；玩家切后台、网络抖动都要求能重连续上。

这意味着容量不能按「日活 × 页面数」估，要按「同时在线对局数 × 单局连接时长」估。后者决定了长连接网关与内存里那份对局状态的规模，也是免费产品最主要的边际成本。

公开资料没有披露 Chessigma 的架构，下面是按公开形态推的通用取舍，不是对它内部实现的描述。

## 资源与成本的取舍

- 静态资源（棋盘、棋子、页面）适合整体推到边缘缓存，成本几乎不随对局数增长；真正需要弹性的是连接与状态服务。
- 棋局状态是有会话亲缘的小对象，适合放在内存型存储并设过期时间，落库只保留结果；把每一步都写进主库，是这类系统最常见的成本失控点。
- 复盘/分析这类纯算力任务，放在浏览器端由用户设备承担，是「免费且无限次」能成立的成本前提；放在服务端，用户量一上来就直接变成账单。
- 匹配池需要全局视图，但不必与对局连接共用同一组实例。把「找对手」和「下棋」拆开，扩容时才不会被彼此的峰值牵着走。

## 公开数字的时点

官网口径是「1M+ users」；公开订阅数 576 来自 2026-10-14 同步的快照，是当时值不是现值。收入类数字与本文的工程结论无关，这里不引用，也不据快照推算现值或年化；不同口径之间不互相折算。

累计用户量不是并发数；这两个量一旦混用，容量估算会差出几个量级。

## 判断与适用边界

结论：免费对弈平台的成本结构由「长连接 + 会话状态」决定，不由内容量决定。把可缓存的推到边缘、把状态收进有过期策略的内存层、把算力尽量挪到客户端，这三件事决定同样的用户规模能不能用同样的资源扛住。

以上是公开形态能支撑的架构分析；数字是带时点的快照，不是现值。
"""

FULL["drafts/newbatch/aliyun/cod-dex.md"] = """\
# 在线编程课的弹性账：突发执行、沙箱隔离与内容静态化 —— Codédex

把 Codédex 这类互动编程课程放到资源视角看，它和普通的在线教育站点不是一种负载：课程页面是静态内容，学生点「运行」的瞬间却会突然产生一次计算请求。前者的成本几乎不随人数变化，后者是一次真实的进程启动。

公开资料能核实的形态是：课程包装成「冒险旅程」，覆盖 HTML/CSS/JavaScript/Python/React 等，每课配即时运行的练习与勋章体系；250+ 小时内容免费开放，Club 层解锁进阶路径；2022 年 11 月上线。下面只讨论「这类形态」的工程账，不推断任何未公开的内部实现。

## 负载长什么样

一次「运行」和一次「浏览」不是同一种请求。浏览可以完全交给 CDN 与对象存储，横向扩展几乎零成本；运行请求有三个特征：突发（用户改一行就点一次）、短时（多数秒级结束）、不可信（输入是用户写的代码）。

容量规划因此不能按「日活 × 平均页面数」估算，而要按「并发运行数 × 单次执行时长」估算。这两个数通常差一个量级，是这类产品最容易算错的地方。

## 资源与成本的取舍

公开形态里，前端能跑的语言与必须后端跑的语言要分开处理：JavaScript 可以在浏览器的 Worker 或带 sandbox 的 iframe 里执行，HTML/CSS 直接渲染，这类请求不占服务端算力；需要完整运行时或文件系统的语言，才落到隔离容器里。

隔离不只是安全要求，也是成本要求。用户代码可能死循环、可能申请大内存，所以必须有超时、内存上限与并发配额；否则一个学生的失控脚本就足以拖垮整台机器。这类限制要放在执行层，靠业务层兜底太晚。

第三个取舍是内容静态化。课件、题目、勋章规则都是读多写少的资产，适合编译成静态产物分发；只有「运行」和「进度写入」需要动态服务。把两类流量拆开，弹性伸缩才只需要覆盖后者。

## 进度与状态怎么算

「学到哪一课」如果用一行 current_state 字段存，多端同时提交、离线补交、规则回溯都会互相覆盖。更稳的做法是记事件：每次完成练习追加一条，当前进度由事件推导。代价是读取要重放或依赖物化视图，换来的是可追溯与可重算——勋章规则改了，不必清洗用户表。

权限门控必须放在服务端。免费层与进阶层的边界如果只在前端隐藏入口，静态课件本身就已经发出去了；正确形状是内容请求经过鉴权，前端只负责展示。

## 判断与适用边界

公开数字是带时点的快照：6,686 个付费订阅来自 2026-10-14 同步的那次快照，是当时值不是现值。收入类数字与本文的工程结论无关，这里不引用，不同口径之间也不互相折算或年化。这个规模描述的是累计量，不是同一时刻并发点「运行」的人数；拿它估容量，会把隔离层的配额算小几个量级。

结论落在工程上：这类产品的成本结构由「突发执行」决定，不由「内容量」决定。把静态内容尽量推到边缘、把不可信执行收进有配额的隔离层、把进度记成事件，这三件事决定了同样的课程规模能不能用同样的资源扛住。以上是公开形态能支撑的架构分析，Codédex 未公开的内部实现不作推断。
"""


# ---------------------------------------------------------------- ③ 定点手术
# 14 篇带水印的长稿里，除上面 7 篇整篇重写外，其余 7 篇只清管线痕迹、修错字。
# 复用守卫的正则，不在改写工具里另立一套判据（11-04 就吃过「两处口径不一致」
# 的亏：守卫只认 2 种罐头句变体，存量稿实际有 4 种）。
WM_LINE = G.WATERMARK
CANNED_LINE = G.CANNED_SOURCE

TEXT_FIX = [
    # 金额换算错乱：$1.27M 被机械替换成「1.27 美元M」
    ("drafts/newbatch/tencentcloud/shipfast.md", "累计 1.27 美元M", "累计 127 万美元"),
]


# ---------------------------------------------------------------- 执行

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(rel, text):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def _clean(text):
    """只删「一定不该出现」的行，不改写内容、不编事实。"""
    text = WM_LINE.sub("", text)
    text = CANNED_LINE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _retitle_h1(text, title):
    return re.sub(r"^#\s+.*$", "# " + title, text, count=1, flags=re.M)


def _html_h1(text, title):
    import html as _h
    new = "<h1 style=\"font-size:22px;font-weight:700;line-height:1.4;margin:0 0 18px;\">%s</h1>" % _h.escape(title)
    return re.sub(r"<h1[^>]*>.*?</h1>", lambda m: new, text, count=1, flags=re.S | re.I)


def affected():
    files = set(HTML_TITLES) | set(MD_TITLES) | set(FULL) | {r for r, _, _ in TEXT_FIX}
    for cid in TT_TITLES:
        files.add("out/toutiao/%s/meta.json" % cid)
        files.add("out/toutiao/%s/article.html" % cid)
    # 14 篇水印稿里没进 FULL / MD_TITLES 的，也要做定点清理
    for plat in TONE.TEXT_PLATFORMS:
        d = os.path.join(ROOT, "drafts", "newbatch", plat)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".md"):
                continue
            rel = "drafts/newbatch/%s/%s" % (plat, f)
            if WM_LINE.search(_read(rel)) or CANNED_LINE.search(_read(rel)):
                files.add(rel)
    return sorted(files)


def plan():
    """返回 [(rel, new_text, reason)]，不写盘。"""
    out = []
    for rel, title in HTML_TITLES.items():
        out.append((rel, _html_h1(_read(rel), title), "公众号标题去口径旁注 + ≤64 字"))
    for cid, title in TT_TITLES.items():
        mrel = "out/toutiao/%s/meta.json" % cid
        d = json.loads(_read(mrel))
        d["title"] = title
        out.append((mrel, json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                    "头条 t[:30] 硬截断 → 完整分句"))
        hrel = "out/toutiao/%s/article.html" % cid
        out.append((hrel, re.sub(r"<h1[^>]*>.*?</h1>",
                                 "<h1>%s</h1>" % title, _read(hrel),
                                 count=1, flags=re.S | re.I),
                    "头条 article.html 与 meta.json 对齐"))
    for rel, title in MD_TITLES.items():
        out.append((rel, _clean(_retitle_h1(_read(rel), title)), "标题去金额 / 去商用词"))
    for rel, text in FULL.items():
        out.append((rel, text, "按 %s 调性整篇重写" % TONE.profile(rel.split("/")[2])["label"]))
    done = {r for r, _, _ in out}
    for rel in affected():
        if rel in done:
            continue
        txt = _read(rel)
        new = _clean(txt)
        for r, old, rep in TEXT_FIX:
            if r == rel:
                new = new.replace(old, rep)
        if new != txt:
            out.append((rel, new, "定点清理：去水印 / 罐头来源句 / 修错字"))
    # 错字修复对所有产物生效（含同时被改标题 / 整篇重写的那些），
    # 不能只放在上面的定点分支里，否则 MD_TITLES 那份会带着错字落盘。
    fixed = []
    for rel, text, why in out:
        for r, old, rep in TEXT_FIX:
            if r == rel and old in text:
                text = text.replace(old, rep)
        fixed.append((rel, text, why))
    return fixed


def backup(rels):
    os.makedirs(BACKUP, exist_ok=True)
    n = 0
    for rel in rels:
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(BACKUP, rel.replace("/", "__").replace("\\", "__"))
        if not os.path.isfile(dst):          # 已备份过就不覆盖，保住最初的原件
            shutil.copy2(src, dst)
            n += 1
    return n


def verify():
    """只读验收：标题闸、水印、罐头句、跨平台同稿、调性禁忌。"""
    import glob
    bad = []
    # ① 全仓标题 block
    rows = []
    for plat in G.TITLE_MAX:
        pats = {"wechat": "out/articles/*.html",
                "toutiao": "out/toutiao/*/meta.json",
                "xhs": "out/xhs/*/meta.json",
                "bilibili": "out/bili/*/meta.json"}.get(
                    plat, "drafts/newbatch/%s/*.md" % plat)
        for p in glob.glob(os.path.join(ROOT, pats)):
            src = os.path.dirname(p) if p.endswith("meta.json") else p
            t = G.title_of(src)
            if t:
                rows.append((plat, os.path.relpath(p, ROOT), t))
    blocks = [(p, r, t, f) for p, r, t in rows
              for f in G.scan_title(p, t) if f["level"] == "block"]
    print("标题：扫到 %d 个，block %d 条" % (len(rows), len(blocks)))
    for p, r, t, f in blocks[:20]:
        print("   ✗ [%s] %s → %s" % (p, r, f["rule"]))
        bad.append("title:" + r)
    # ② 水印 / 罐头句：扫整个 drafts/newbatch，不能用 affected()
    #    （改完后 affected() 里只剩「还有痕迹的文件」，会自证清白）
    wm = []
    for p in glob.glob(os.path.join(ROOT, "drafts", "newbatch", "*", "*.md")):
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        s = _read(rel)
        if WM_LINE.search(s):
            wm.append((rel, "水印"))
        if CANNED_LINE.search(s):
            wm.append((rel, "罐头来源句"))
    print("管线痕迹：%d 处" % len(wm))
    for r, k in wm[:20]:
        print("   ✗ %s %s" % (r, k))
        bad.append("mark:" + r)
    # ③ 跨平台逐字同稿
    fps = {}
    for plat in TONE.TEXT_PLATFORMS:
        for p in glob.glob(os.path.join(ROOT, "drafts", "newbatch", plat, "*.md")):
            fp = TONE.body_fingerprint(_read(os.path.relpath(p, ROOT).replace("\\", "/")))
            fps.setdefault(fp, []).append(os.path.relpath(p, ROOT))
    dup = [v for v in fps.values() if len(v) > 1]
    print("跨平台逐字同稿：%d 组" % len(dup))
    for v in dup[:10]:
        print("   ✗ %s" % " == ".join(v))
        bad.append("dup:" + v[0])
    # ④ 调性禁忌
    ti = []
    for plat in TONE.TEXT_PLATFORMS:
        for p in glob.glob(os.path.join(ROOT, "drafts", "newbatch", plat, "*.md")):
            s = _read(os.path.relpath(p, ROOT).replace("\\", "/"))
            t = G.title_of(p) or ""
            for issue in TONE.tone_issues(plat, t, s):
                ti.append((os.path.relpath(p, ROOT), issue))
    print("调性禁忌：%d 处" % len(ti))
    for r, issue in ti[:20]:
        print("   ✗ %s %s" % (r, issue))
        bad.append("tone:" + r)
    print("\n总计 %d 项待处理 ⇒ %s" % (len(bad), "还有问题" if bad else "全部通过"))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只报计划")
    g.add_argument("--apply", action="store_true", help="备份后落盘")
    g.add_argument("--verify", action="store_true", help="只读验收")
    a = ap.parse_args(argv)
    if a.verify:
        return verify()
    todo = plan()
    changed = [(r, t, why) for r, t, why in todo if t != _read(r)]
    print("计划改动 %d 个文件（已一致的 %d 个跳过）：" % (len(changed), len(todo) - len(changed)))
    for r, _, why in changed:
        print("   %-62s %s" % (r, why))
    if a.check:
        return 0
    n = backup([r for r, _, _ in changed])
    print("\n已备份 %d 份原件 → %s" % (n, os.path.relpath(BACKUP, ROOT)))
    for r, t, _ in changed:
        _write(r, t)
    print("已写入 %d 个文件\n" % len(changed))
    return verify()


if __name__ == "__main__":
    sys.exit(main())
