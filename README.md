# 拆解海外 · Overseas Teardowns

[![CI](https://github.com/oracis/ai-case-library/actions/workflows/ci.yml/badge.svg)](https://github.com/oracis/ai-case-library/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3fb950.svg)](LICENSE)
[![Python 3 · stdlib only](https://img.shields.io/badge/Python%203-stdlib%20only-3776ab.svg)](#启动)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-8b949e.svg)](#贡献)

**仓库**：<https://github.com/oracis/ai-case-library> · **协议**：MIT（可商用、可改、可分发）
· **克隆**：`git clone https://github.com/oracis/ai-case-library.git`

**English**：[README.en.md](README.en.md) · **数据结构**（给第三方程序消费的字段字典）：
[docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md)

> 界面文案与案例正文不做英译，这是刻意的：正文字段的语言是 `zh-CN`，
> 而分数、枚举、数值、日期这些**结构化字段与语言无关**，第三方程序可以直接用。
> 面向海外只需要英文接口，不需要英文界面——理由见
> [DATA_SCHEMA.md 的 Language boundary 一节](docs/DATA_SCHEMA.md#language-boundary)。

一个本地跑的**已核实案例库**：把国外已经跑通的小项目扒下来、核过数字、写成人话，
按「模式」而不是「行业」组织，用来长判断力。

零依赖——只用 Python 标准库，不需要 `pip install` 任何东西。

---

## 启动

```bash
# 方式一：Python 直接跑
python server.py

# 方式二：Windows 双击
start.bat
```

浏览器会自动打开 <http://127.0.0.1:5052/>。

**一次启动会开两个站**，它们是两个端口、两套界面：

| 端口 | 目录 | 是什么 |
|---|---|---|
| <http://127.0.0.1:5052/> | `static/` | **公开站**：只读的阅读站，谁都能看。要部署到 OSS 的就是这份。 |
| <http://127.0.0.1:5053/> | `admin/` | **管理后台**：要登录，核实与入库只在这里。 |

换端口：`set CASE_LIB_PORT=5099 && python server.py`（后台默认跟着 +1，
也可以单独设 `CASE_LIB_ADMIN_PORT`）。

也可以不起服务，直接看静态版本（部署到线上的就是这份，只读）：

```bash
python scripts/build_static.py     # 构建到 dist/
# 然后双击 dist/index.html
```

详见下面的「部署到阿里云 OSS」。

---

## 这个库和「案例合集」有什么不一样

大部分案例合集告诉你「有人赚了多少钱」。这个库额外告诉三件事：

**1. 这个数字是谁说的（核实等级）**

每条案例都标注来源等级，从高到低：

| 等级 | 含义 |
|---|---|
| `stripe` | 支付网关直连验证（TrustMRR / Stripe 客户案例） |
| `official` | 公司官方通稿或财报 |
| `partial` | 数据真实，但口径待核（分不清是 MRR 还是累计） |
| `founder` | 只有创始人自报，没有第三方验证 |
| `disputed` | 数字有出入，不同来源对不上 |
| `unverified` | 还没核 |

当前 24 条案例里，16 条已核实，2 条标了「数字有出入」，2 条标了「口径待核」。

**2. 别人抄错了什么（修正记录）**

这是最有价值的部分。已收录 14 处数字出入，例如：

- **Nitra 客户数**：中文转述写「7000 多家诊所」，官方披露是 **700+ 家**。差一个数量级。
- **Chatbase 收入**：「年入 $20M」是误读，$20M 是**累计**收入，当前 ARR 约 $10M。
- **ShipFast 现状**：旧报道说「月收入 $50K+」，TrustMRR 直连支付显示近 30 天约 **$2,989**，已进入维护期。
- **Viktor 客户数**：同一批报道里「12,000 团队」和「2,000 组织」两个口径混用。
- **Speel.co**：$65K MRR 是真的，但 MoM 增长为 **0%** —— 收入真实 ≠ 还在增长。

**3. 一个人能不能做（可复刻度）**

每条案例有四个分数（1＝最容易，5＝最难）：技术门槛 / 获客门槛 / 资金门槛 / 时机依赖。
四项里只要有一个是 5 分，一个人基本做不了。

---

## 三级漏斗

```
data/inbox.json        采集队列     400 条   机器捞的，全没看过
data/candidates.json   候选池        28 条   人工挑的，还没核数字
data/cases.json        精写案例      30 条   核过数字，能拿出去说
```

侧栏会把这三层画成漏斗。**素材多、精写少是正常的**——一天能精写 2–3 条，一周十几条。
这个库不是一次做完的，是边看边长。

界面上每一层都能往上推：采集队列 →「转入候选池」，候选池 →「提升为精写案例」。
推进精写库时会带一个 `needs_review` 标记，提醒你还没核实。

漏斗还有个横切的口径：`scripts/triage.py` 给每层的每一条打一个「值得核」分，
首次跑时把 474 条收敛成 11 条真正该花 AI 的（见「核实之前先做零成本初筛」）。
它不改变这三层的归属，只决定**先看哪一条**。

---

## 国内移植可行性排行

库里的案例都是国外的。但**「一个人能不能做」和「能不能搬回国内做」是两个不同的问题**，
结论经常相反：

- `CheckVibe` 一个人完全能做（本地化成本几乎是零），但在国内卖不动——开发者不为安全工具付费。
- `Nitra` 产品也不难做，但医疗数据的合规与资质拿不到，等于做不了。

所以另设一套六维评分，只回答「搬回国内」这一件事。每维 1–5 分：

| 维度 | 权重 | 问的是什么 |
|---|---|---|
| 付费意愿 | ×1.5 | 国内目标客户会不会真掏钱 |
| 支付可达 | ×1.2 | 国内能不能顺畅收款（Stripe 用不了） |
| 合规空间 | ×1.2 | 越不碰监管红线分越高 |
| 获客迁移 | ×1.0 | 海外获客渠道在国内有没有等价物 |
| 改造成本 | ×1.0 | 本地化改造量，越小分越高 |
| 竞争空位 | ×1.0 | 国内是否已有强势免费替代 |

满分 34.5，归一化到 100。

```bash
python scripts/score_china_fit.py            # 打分并写回 data/cases.json
python scripts/score_china_fit.py --top 8    # 只看前 8 名
python scripts/score_china_fit.py --dry-run  # 只看结果，不写入
```

左侧「移植排行」页是完整榜单。当前金银铜：

| 奖牌 | 项目 | 分数 | 为什么是它 |
|---|---|---|---|
| 🥇 金 | **AEO Engine** | 86.4 | 把监测对象从 ChatGPT / Perplexity 换成豆包、DeepSeek、文心就行，产品骨架不用动；国内这块几乎真空，而营销预算本来就存在 |
| 🥈 银 | **Visualizee.ai** | 79.1 | 付费方明确（装修公司、设计院、软装工作室），习惯为「出图效率」掏钱；小红书就是现成的获客池 |
| 🥉 铜 | **Speel.co** | 75.7 | 电商素材是每日刚需、能算 ROI，国内盘子全球最大；代价是正面红海，且原产品增长已停滞（MoM 0%） |

**另外 8 条打了「硬伤」标记，不建议碰**，原因分三类：

- **合规**：`Nitra`（医疗数据）、`Meerkats.ai`（自动化外呼撞《个人信息保护法》）
- **支付与土壤**：`TrustMRR`（它的整个价值前提是国内不存在的支付验证生态）、`Marc Lou 矩阵`
  （国内没有「5 分钟接好收款」的基础设施，快速试错的成本高一个量级）
- **需求端薄**：`Rezi`、`CheckVibe`（国内用户不为这类工具付费）

分数是主观判断，但每一条都写了依据——鼠标悬停卡片上的分数，或点进详情看
「能不能搬回国内做」整段。要调权重或改某条的分数，直接改
`scripts/score_china_fit.py` 里的 `SCORES` 再重跑。

---

## 个人可做性排行 + 双轴综合排行

「一个人能不能做」和「能不能搬回国内做」是两个问题，结论经常相反。
所以这里**不是把两个分数相加**，而是做成三件东西。

**① 个人可做性 `solo_fit`（0-100）**

原始的 `replicability` 四维有两个毛病：方向反着（1 分＝最容易）、没有依据说明。
所以先把四维翻成正向，再补一个原数据没有、但对「一个人能不能做」最致命的维度：

| 维度 | 权重 | 来源 | 问的是什么 |
|---|---|---|---|
| **单人交付** | ×1.4 | **人工判断（新增）** | 要不要团队 / 资质 / 7×24 值守 |
| 够得着客户 | ×1.3 | `replicability.distribution` 反推 | 不靠销售团队就能触达 |
| 启动轻 | ×1.1 | `replicability.capital` 反推 | 不需要先烧钱就能开张 |
| 造得出来 | ×1.0 | `replicability.tech` 反推 | 技术栈在不在一个人射程内 |
| 窗口还开着 | ×1.0 | `replicability.timing` 反推 | 现在进场还有没有位置 |

四个维度直接反推（`build = 6 - tech`，以此类推），**所以两套数据永远不会打架**；
只有「单人交付」是新的人工判断。满分 29 归一化到 100。

**② 综合分 `composite`：木桶式聚合，不是平均**

```
composite = 0.6 × min(solo, china) + 0.4 × mean(solo, china)
```

短板占 6 成权重。因为这两个是**乘性关系**：一个人能做但国内没人买 = 0，
国内有需求但我做不了 = 0。

```
90 × 40 → 50            60 × 60 → 60
双及格  好于  单点突出
```

**③ 四象限（及格线 70）**

| 象限 | 含义 | 当前 |
|---|---|---|
| `go` 可以开干 | 两边都过线 | 2 条 |
| `partner` 有市场，但一个人啃不动 | 门槛在资质 / 大客户销售 / 团队交付 | 4 条 |
| `export` 能做，但别在国内卖 | 技术在手，卡在国内需求或支付土壤 | 9 条 |
| `skip` 别碰 | 两个方向都不过线 | 9 条 |

综合金银铜：

| 奖牌 | 项目 | 综合分 | 个人可做 | 国内移植 | 象限 |
|---|---|---|---|---|---|
| 🥇 金 | **Visualizee.ai** | 79.3 | 80.3 | 79.1 | 可以开干 |
| 🥈 银 | **Bustem** | 71.1 | 71.0 | 71.3 | 可以开干 |
| 🥉 铜 | **AEO Engine** | 70.6 | 66.6 | 86.4 | 有市场，但一个人啃不动 |

注意这个排序**和移植排行不一样**，而且差得有价值：

- `AEO Engine` 移植分第 1（86.4），综合掉到第 3 —— 它是大客户销售驱动，一个人啃不动
- `Bustem` 移植分第 4，综合升到第 2 —— 两边都在 70 上下，**没有短板**
- `Rezi` 个人可做性第 1（80.3，纯自助工具、零交付），综合只排中游 —— 国内收不到钱

```bash
python scripts/score_china_fit.py                 # 先跑（综合分依赖它）
python scripts/score_solo_fit.py                  # 再跑：个人可做性 + 综合 + 象限
python scripts/score_solo_fit.py --threshold 75   # 换及格线
python scripts/score_solo_fit.py --dry-run --top 10
```

**界面**：左侧「综合排行」页 = 四象限 + 综合榜 + 个人可做性榜并排。

---

## 筛选

案例页顶部多了一条筛选栏，三个条件可以叠加（都在「精写案例」视图生效）：

| 控件 | 作用 |
|---|---|
| 按分筛 · 维度 | 综合分 / 个人可做性 / 国内移植性 |
| 按分筛 · 分数 | ≥ 80 / 70 / 60 / 55 |
| 象限 | 可以开干 / 有市场但啃不动 / 能做但别在国内卖 / 别碰 |

选了维度但没选分数时会自动补 ≥70，免得点了没反应。右侧实时显示命中数，
有「清空筛选」按钮。配合排序下拉用最有效：

```
筛「个人可做性 ≥70」＋ 排「综合分优先」        → 我能做的那批里，哪几个综合最好
筛「可以开干」                                → 只剩 2 条，直接看
筛「能做，但别在国内卖」＋ 排「个人可做性优先」 → 出口方向的候选
```

排序下拉新增 `综合分优先（木桶）` 和 `个人可做性优先`。卡片上现在有三个徽章：
`综 79.3`（带象限配色）、`个 80.3`、`国 79.1`。

---

## 采集

```bash
python scripts/harvest.py --source hn             # Hacker News，免 key，可直接跑
python scripts/harvest.py --source trustmrr       # 走 TrustMRR 官方公开数据端点
python scripts/harvest.py --source indiehackers   # Indie Hackers（自报数字 + 过程自述）
python scripts/harvest.py --source arrclub        # ARR Club（只做校对，不进候选池）
python scripts/harvest.py --source all            # 能跑的都跑
python scripts/harvest.py --source hn --dry-run       # 只看结果，不写入
python scripts/harvest.py --source hn --hn-comments 5 # 给新增条目补 HN 评论正文
python scripts/harvest.py --max-inbox 400         # 队列上限，超额自动归档（不删）
python scripts/harvest.py --max-inbox 0           # 0 = 不限，队列无限增长
```

`--source ph`（Product Hunt）需要 token：`--token <PH_TOKEN>` 或设环境变量 `PH_TOKEN`。

### 每条素材都带 source_kind（可信度钉在数据层）

五个信息源的可信度差别很大，混在一起看会出事。所以每条采集结果都带一个
`source_kind`，前端据此显示徽章：

| source_kind | 含义 | 对应源 |
|---|---|---|
| `verified` | 收入由支付网关 API 直读，不接受截图自报 | TrustMRR（A） |
| `self_reported` | 创始人自述，未经第三方验证 | Indie Hackers、Hacker News（B） |
| `secondary` | 二手转述，只能当线索或校对参照 | ARR Club（B-） |
| `discovery` | 只用于发现新项目，本身不含收入数据 | Product Hunt（B） |

这条规则是硬的：**Indie Hackers 的收入一律标 `self_reported` 并在 note 里写明
「进精写库前必须交叉核对」；Product Hunt 的条目不许写任何收入字段**（它根本
没有收入数据，只有榜单热度）；ARR Club 的结果只写 `data/arrclub.json` 当校对
参照，**不进候选池**。`scripts/test_harvest_sources.py` 把这些都钉成了断言。

### 各源的真实通道与限制（都实测过）

| 源 | API | 详情页 | 实测结论 |
|---|---|---|---|
| TrustMRR | ✅ `/api/ai` 免鉴权，单条 40 字段 | ✅ HTML + `.md` | 通道全通 |
| Hacker News | ✅ Algolia 免 key，连打 6 次无限流 | ✅ `items/<id>` 拿完整评论树 | 「怎么做到的」在评论里 |
| Indie Hackers | ❌ 无 API（`.json`/`/api/product/` 均 404） | ✅ 服务端渲染可解析 | 只能靠 sitemap 枚举 |
| ARR Club | ❌ 仅企业版 | ✅ 但**只能解 FAQPage JSON-LD** | 二手转述，只做校对 |
| Product Hunt | ⚠️ 只有 Atom feed（官方 GraphQL 需 token） | ❌ **www 全站 403（Cloudflare）** | 只能当雷达；`api.` 子域不挡，见下 |

几个必须知道的坑：

- **Product Hunt 的详情页抓不到。** `/products/<slug>`、`/products`、`/leaderboard/*`、
  `/rss`、`/topics/*`、`/frontend/graphql` 实测**全部 403**。唯一可达的是
  `https://www.producthunt.com/feed`（Atom，50 条/页，支持 `?category=` 过滤），
  而且**feed 里没有 upvote 数**。所以 PH 在本库只能当发现雷达，想要投票数只能
  申请官方 API token。
- **但 API 子域没有被 Cloudflare 挡，token 一到就能跑。** 2026-09-24 实测：
  `api.producthunt.com/v2/api/graphql` 的 **GET 返回 404**（只是不认 GET），
  带假 token 的 POST 返回 **401 `invalid_oauth_token`**（应用层鉴权，不是风控拦截）。
  也就是说：网络通、域名通，**唯一缺的只是一个真 token**。token 从
  `https://www.producthunt.com/v2/oauth/applications` 建应用拿（  该页面在 www 上，
  脚本抓是 403，必须用真人浏览器登录）。
  **决策（2026-09-24）：不申请 token。** PH 没有收入数据，官方 API 对本项目
  只多出「upvote 数 + 按名反查」，性价比不如把力气放在 TrustMRR 两个端点上
  （后者带支付网关验过的收入）。PH 维持「仅 Atom feed 雷达」定位，以后别再提。
- **Indie Hackers 的分页是假分页。** `?page=2` 与 `?page=1` 返回**完全相同字节**
  （排序翻页都在前端 JS 里），所以不能靠翻页枚举，只能走 sitemap。sitemap 里
  `/product/<slug>` 是干净产品页（133KB，metrics 齐全），
  `/product/<slug>/<milestoneId>` 是单条里程碑（42KB，没有 metrics）——后者要丢掉。
- **Indie Hackers 的 sitemap 分片很大。** 每个分片约 **10.6MB**（5 片合计 ~53MB），
  而且干净产品 slug 均匀散在分片里（前 2MB 一个都没有），所以「下一半就停」省不了
  时间。对策是**一次只抓一个分片**并用游标轮换，结果存进 `data/ih_slugs.json`
  缓存；日常采集直接读缓存，零下载。想看它慢慢覆盖全部产品，跑几天就行；
  想立刻重抓一片加 `--refresh-ih-slugs`。
- **ARR Club 的 `arr` 字段不可靠。** 实测 `/notion` 上取不到（`/linear` 才有），
  所以只解 `FAQPage` JSON-LD——那里是**带年份**的权威值。年份必须和数字成对存储：
  `<title>` 写 2026 而 FAQ 写 2025，只存数字会串年份。另外它覆盖不全（`/stripe`
  实测 404），而且页面自贴「Certificate」徽章、FAQ 里自引
  「according to ARR Club's verified data」——自己说自己是 verified，这正是
  二手转述的典型特征。

### TrustMRR 走官方 AI 端点

TrustMRR 提供了面向 AI 的公开数据端点，**一次请求拿到 40 字段**，包括官网、
排名、MRR、累计收入、客户数、增速、分类、国家、创始人 X 账号：

```
https://trustmrr.com/api/ai              公开 JSON（recentlyListedStartups + bestDeals）
https://trustmrr.com/startup/<slug>.md   官方 AI 读本（出处分层、刷新时间戳、X 粉丝数）
https://trustmrr.com/startup/<slug>      详情页（JSON-LD 里是官网明文）
https://trustmrr.com/llms.txt            站点抓取说明
```

抓取顺序：**先走 `/api/ai`**，拿不到才回落解析首页 HTML。

早先只读首页 JSON-LD 的 `ItemList`，而那里 `url` 恰好是榜单自身页，于是
「官网」这个概念根本没进采集器——候选池一度 36 条**没有一条**带官网。
现在主通道换成官方端点，这部分字段直接落盘。

`.md` 读本比 JSON 多两样东西，所以采集时会给新增条目顺手补上（`--trustmrr-md`）：
**出处分层**（哪些字段由外部 provider 验证、哪些是用户生成内容）和
**refresh 时间戳**（用来判断这份数据到底有多新，落进 `data_freshness`）。

已采过的旧数据可以回填：

```bash
python scripts/backfill_trustmrr.py --dry-run      # 先看会改什么
python scripts/backfill_trustmrr.py                # 实际写入（幂等，可重复跑）
python scripts/backfill_trustmrr.py --no-detail    # 只用批量端点，不逐个查详情页
```

回填只补空字段、不覆盖人工写过的内容；只处理能确认来自 TrustMRR 的记录
（候选池里混着的公众号二手转述不会被误改）。

采集脚本会按 `data/sources.json` 里的 `filter_rules` **去掉广告与新闻稿**，
只留「有人在为自己的东西说话」的素材。TrustMRR 侧另外跳过两类噪音：
**隐身公司**（名字是「Anonymous startup」假名、域名不公开）和
**月收入低于 $100 的条目**（刚上线或已停摆，没有案例价值）。

采集脚本一律只写 **采集队列**，绝不直接写精写库。

### 过滤规则的一次修正

`keep_signals` 最初是裸子串匹配，结果 `bootstrapped` 命中了 `source-bootstrapped`——
「Nix has a source-bootstrapped OpenJDK」这种编译器帖子被当成了创业项目，还排在简报第一位。
现在改成**词边界匹配**，并且刻意不收裸的 `bootstrapped`（只收 `bootstrapped startup`）。

### AI 核实时会剔除无关来源

产品名恰好是常见英文词时会出问题：`Eloquent` 那次，AI 去检索找回来的全是
**剑桥词典、金山词霸的单词释义**；中文二手转述里还会夹带 `music.163.com`、
`y.qq.com` 这类音乐平台。这些来源一旦进了 `sources`，会虚增「来源条数」、
干扰一手来源判定，让一条本该被否的条目看起来「有多个来源互相印证」。

现在 `scripts/ai_verify.py` 有一份域名黑名单（词典 / 音乐影视 / 电商下载站），
**先过滤再取前 6 条**（顺序很重要：先截断的话垃圾会白占名额），被剔除的条数
记进草稿的 `irrelevant_sources_dropped` 并在 note 里透明标注。


---

## 更新节奏

三层刻意用不同的更新方式：

| 层 | 谁在更新 | 节奏 |
|---|---|---|
| `inbox.json` 采集队列 | **机器**，脚本自动 | 每天 |
| `candidates.json` 候选池 | 人工挑 | 看到好的就推 |
| `cases.json` 精写案例 | 人工核 | 慢工，一条十分钟起 |

**只有采集层自动化。** 精写层永远手动——核心价值就是「写之前先自己搜一遍」，
这一步交给机器，整个库就退化成它本来要反对的那种二手转述聚合站。

每日采集做的事：

```bash
python scripts/harvest.py --source all --limit 40
```

1. 只写 `inbox.json`，**绝不碰** `cases.json`
2. 队列超过 `--max-inbox`（默认 400）时，最旧的移入 `data/inbox_archive.json`——**留底，不删除**
3. 跑完写一份 `data/last_harvest.json`：新增条数、来源分布、队列总量、最值得先看的几条

简报的排序不按热度，按**商业信号强度**：挂牌金额 > 文本里的金额 > 收入词
（MRR / paying customers）> HN 热度。否则「This up votes itself」这类高赞娱乐帖
会盖过真在收钱的项目。

### 在 GitHub 上更新（主力方式）

`.github/workflows/harvest.yml` 每天 09:00（北京时间）在 GitHub 的机器上跑一遍，
全流程无人值守：

```
采集 → 校验 → 提交 → 推送 →（配了 OSS 密钥的话）构建静态站 → 部署上线
```

**不用开着本机**，也不依赖你本地的网络和代理；Runner 在境外，访问
Hacker News / TrustMRR 反而更顺。也可以去仓库 Actions 页面点「Run workflow」
手动触发，能选采集源和条数。

两个刻意的设计：

1. **先校验，再推送。** 采集完先验 `data/*.json` 是不是合法 JSON → 跑一遍
   `selftest.py` → 断言自测没留下额外改动，全过才允许 push。
   任何一步挂掉，远程保持原样。
2. **校验必须内联在这个 workflow 里。** GitHub 有个设计：用 `GITHUB_TOKEN`
   推出去的提交**不会**再触发 `ci.yml`（防止递归）。所以不能指望 CI 兜底，
   数据校验得自己带着。

> 定时任务在 GitHub 上不精确：高峰期可能延迟几十分钟，极端情况会跳过某天——
> 采集场景无所谓，漏一天下次补上就是。另外仓库连续 60 天没有任何提交活动时，
> GitHub 会自动暂停定时任务；这个库每天都有提交，碰不到这个问题。

### 本地也能跑（同一套脚本）

云端工作流调的就是 `scripts/daily_harvest.py`，本地手动跑也用它：

```bash
python scripts/daily_harvest.py                # 采集 → 提交 → 推送
python scripts/daily_harvest.py --dry-run      # 只采集，不提交（改动留在工作区）
python scripts/daily_harvest.py --no-push      # 提交到本地，不推送
python scripts/daily_harvest.py --limit 30     # 参数透传给 harvest.py
```

它的行为边界写得很死，这几条是刻意的：

| 情况 | 行为 |
|---|---|
| `data/` 无变化（没捞到新素材） | **直接退出，不制造空提交** |
| 有变化 | `git add -- data/` → 提交 → 推送 |
| `data/` 之外的文件 | **一律不动**，不会被卷进提交 |
| `cases.json` | **绝不被自动修改**，只在人工核实后手动提交 |
| 采集失败 | 不提交，退出码 1 |
| 推送失败 | 本地提交保留，提示手动 `git push` |

提交信息按简报自动生成，长这样：

```
采集：新增 4 条素材（2026-09-11）

来源分布：hn 4
采集队列 131 条
只写采集队列；精写案例仍需人工核实。
```

无人值守会遇到的坑也处理了：没有提交身份就明确报错并给出配置命令；
`.git/index.lock` 残留时，超过 10 分钟的陈旧锁自动清理、太新的锁则跳过本次（说明真有
一次 git 在跑）；推送后顺手补齐本地 `origin/<branch>` 引用。

---

## 核实

```bash
python scripts/verify.py "Nitra" --domain nitra.com          # 打印核查清单与搜索入口
python scripts/verify.py "Nitra" --domain nitra.com --open   # 顺手开浏览器
python scripts/verify.py "Nitra" --log --verification partial --note "客户数是 700+ 不是 7000"
python scripts/verify.py --checklist                         # 只看通用清单
```

它会做四件事：

1. 把**六个收入口径**摊在你面前（ARR / MRR / run-rate / 累计 / 流水 / 毛利）——混一个就差十倍
2. 列出一手证据的优先级（支付网关 > 官方通稿 > 媒体 > 创始人自述 > 中文二手转述）
3. 提醒六个最容易出错的对照点（客户数、累计 vs 年、流水 vs 收入、时间点、口径范围、是否还在增长）
4. 生成搜索入口；`--log` 会记到 `data/verification_log.json`

**这一步不能交给工具。** 工具不会因为「数字看起来合理」而警觉，但你会。

### 核实之前先做零成本初筛

深核一条要联网检索、抓多页原文、再调一次 LLM —— 这是整条流水线上唯一真正花钱的环节。
所以花钱之前先跑 `scripts/triage.py`：**不联网、不调 LLM、纯确定性**地给每条打一个
「值得核」分，并给出下一步该干什么。

```bash
python scripts/triage.py                     # 候选池 + 采集队列一起看，按级汇总
python scripts/triage.py --grade deep --top 20   # 只看值得深核的前 20 条
python scripts/triage.py --scope inbox       # 只看采集队列
python scripts/triage.py --json              # 结构化输出（给工具消费）
python scripts/ai_verify.py --triage         # 看「本次会按什么顺序核、为什么」
```

五级，每级对应一个动作，而不是一个形容词：

| 级 | 含义 | 下一步 |
|---|---|---|
| `ready` | 草稿已判定可发布 | 去后台点发布 —— **别再花 AI** |
| `deep` | 有数字 + 有能追的来源 | 送 `ai_verify` 深核 |
| `backfill` | 有现成的零成本脚本能补 | 跑 `backfill_trustmrr.py` |
| `later` | 缺关键材料 | 等下一轮采集，或人工扫一眼 |
| `drop` | 没有数字、没有关注度、没有来源 | 归档（后台操作，不删文件） |

打分只用记录里已有的字段：证据起点（一手 / 第三方 / 自报）、数字可得性与口径、
公开关注度（HN 的分与评论数）、可核性（有没有自家官网）、以及收入量级
（门槛 $1,000/月 —— 已发布案例里最小的月收入是 $1,619，再低撑不起一篇拆解）。

**`ready` 这一级是被真实数据教出来的**：写这个脚本时，6 份草稿**全部**已被规则引擎
判成「可发布」，其中 5 条还挂在候选池里。它们缺的不是 AI，是人点一下发布 ——
不拦掉，`--limit 5` 就会把预算正好花在这 5 条上。

两条刻意写死的判断：

- **候选池不由分数判死**。候选池的条目是人工从队列里一条条挑出来的，机器只能排前后，
  不能推翻人的决定 —— 唯一能判 `drop` 的理由是硬标记（已发布重复）。
- **高分不等于深核**。深核的条件是「有数字 **且** 有能追的来源」：没有数字就没有
  可核的东西。`疑似金额 $99` 这种从标题正则抽出来的数**不算数字** —— 采集器自己
  在 note 里写着「不是收入」，把它当数字就会让一批论坛帖冒充「有数据的项目」。
  同理，300 分的 Show HN 帖也不等于有人在付钱：高关注度只够让它免于被当噪音丢掉。

首次跑的真实分布（候选 33 + 队列 441）：

```
ready 5 条 · deep 11 条 · backfill 49 条 · later 47 条 · drop 362 条
```

也就是说 **474 条里真正需要花 AI 的只有 11 条**，另有 49 条一条命令零成本补齐、
5 条只差人点发布。这个脚本只读不写 —— 归档动作仍然要人在后台点。

### AI 自动核实（找卡点 → 补材料 → 判定 → 发布）

「人工一个一个对」里的大部分活是确定性劳动：检索原文、抓页面、对口径、
判断「够了没有」。这些交给 `scripts/ai_verify.py`，人只保留最后背书。

**推荐用法：后台「AI 核实」面板**。登录管理后台（默认 5053），
侧栏最后一项就是它：

1. **LLM 接口配置**：填 API Key（DeepSeek / Kimi / GLM 等 OpenAI 兼容接口都行）
   点「保存」。Key 存在 `data/secrets.json`（.gitignore 已排除，绝不入库），
   保存后立刻生效、重启也在；也可以用下面的环境变量，环境变量优先。
2. **先看卡点**：离线列出每条候选卡在哪（门槛几项 / 必填缺几项），不联网不动数据。
3. **开始跑**：设数量（默认 5）和发布阈值（60 = 只发精品档），勾不勾
   「够格直接发布」。任务在**服务端后台线程**跑，关掉页面不影响，
   回来接着看实时日志；跑完候选池的草稿进度自动刷新。

命令行等价用法（CI / 定时批量时更顺手）：

```bash
python scripts/ai_verify.py --plan                # 离线：列出每条候选的卡点，不动任何数据
python scripts/ai_verify.py --triage              # 离线：本次的核实顺序与理由（初筛排序）
python scripts/ai_verify.py --limit 5             # AI 预核 5 条：检索→抓原文→填草稿（不发布）
python scripts/ai_verify.py --id gojiberry-ai     # 单条
python scripts/ai_verify.py --limit 5 --publish   # 够格的直接发布成案例（精品/实核/备选由规则定）
python scripts/ai_verify.py --all --publish --min-score 60   # 全部处理，只发精品档
```

它怎么工作：

1. 用规则引擎算出每条候选**当前卡在哪**（占位条目、「未获取」、「体量太小」自动跳过，
   **材料已齐的也跳过**——那种缺的是人点发布）；**核实顺序由初筛分决定**，不再按录入顺序：
   人工显式标了「高优先级复核」的仍置顶（人的判断不被机器分覆盖），其余按
   `scripts/triage.py` 的分数从高到低
2. 生成检索词 → Bing（国内可达）+ DuckDuckGo 兜底 → 抓正文
3. 把候选资料 + 卡点 + 原文摘录交给 LLM（OpenAI 兼容接口），**只许依据原文判断，禁止编造 URL**
4. AI 回答映射成核实草稿——**每个勾都要求结构性证据**：AI 的布尔值不许裸信，
   bonus 必须与来源构成对得上。**门槛是三态**（`yes`/`no`/`unknown`），判定口径是
   **一道成立就进库**：三道里只要有一道被确认成立就放行，AI 找到的反证降级为
   「待人工复核」提醒；只有**一道都没成立、且 AI 拿到了明确反证**才判不进库。
   **没有一手来源也不再一票否决**，降级为「待人工复核」提醒——AI 找不到官网不代表
   数字是假的，那是人该判的事；但提醒必须留着，本库所有错误都出在中转述这一层。
   **三道门槛全部成立时质量分自动 +10**（由 `gates` 推导，不是第七个勾选项）——
   核得越实诚离精品线越近；差一道就不给，这是它唯一的触发条件
5. 草稿写回后台（工作台里能看到），规则引擎判定；`--publish` 时把够格的走
   `/promote` 发布——服务端会**再过一遍规则引擎**，这是第二道闸门

环境变量（也可以在后台「AI 核实」面板里保存，文件优先级低于环境变量）：

```bash
export CASE_LIB_AI_KEY=sk-...                  # 必填（--plan 除外）；或存进 data/secrets.json
export CASE_LIB_AI_BASE=https://api.deepseek.com   # 默认，DeepSeek/Kimi/GLM 均可
export CASE_LIB_AI_MODEL=deepseek-chat         # 默认
export CASE_LIB_ADMIN_PASSWORD=...             # 后台密码（写草稿/发布要登录 5053）
```

**人保留什么**：`human_read`（我亲自看过原文）**任何模式都不自动勾**，包括
`--publish`。它**不拦发布**（2026-09-17 起）—— 不勾照样能入库，区别在于：

- 不勾 → 草稿挂一条 `pending_human_read` 提醒，案例上落 `human_read: false`
- 勾了 → 案例上落 `human_read: true` + `human_read_at`（核读时刻），
  后台案例卡片显示「已核读 2026-09-17」

这个时间戳由服务端在第一次勾上时铸，**请求体里带什么都会被忽略** —— 否则前端
能自己伪造一个背书时间；通用 `PATCH /api/cases/<id>` 也显式挡掉了这两个字段。

改成不拦发布的原因：原来它一拦，每条候选都卡在「还差 1 项必填」，而勾完之后案例
上什么都不留（案例字段里既没有 `human_read` 也没有 `musts`）—— 每次提升都要求人
点一下，换来的却是一份阅后即焚的承诺。现在承诺变成一条可查的记录。

没抓到原文的条目会被明确拒绝而不是硬编（实测 getdavid 一条：检索不到一手
披露，脚本直接跳过、维持卡点）。

### 发布是一条六步链路，用 release.py 串起来

发布**不是**一个原子动作。它由六步组成，而**少一步不报错，会静默出问题**：

| # | 步 | 漏了会怎样 |
|---|---|---|
| 1 | `contentpack` 补内容包 | 发布出「有数字没内容」的空壳卡片 |
| 2 | `publish` 走 `/promote` | 数据还在候选池，站上什么都没有 |
| 3 | `fit` 补三套人工判断 | 公众号草稿从 5 段掉到 3 段；首页适配度空白 |
| 4 | `score` 算派生分 | `solo_fit` / `china_fit` 是空的，四象限图缺这条 |
| 5 | `build` 建静态站 | 部署的还是上一版 |
| 6 | `deploy` 上传 OSS | 本地对了，线上还是旧的 |

每一步单独看都有脚本，也各自有测试。但它们之间的**顺序和依赖只存在于人的记忆里**，
而顺序错了同样不报错：`fit` 跑在 `publish` 前补的是上一批案例，`build` 跑在 `score` 前
打进去的是空的派生分。所以有一条命令把它们串起来：

```bash
python scripts/release.py --dry-run                        # 全链路预演，不写盘不上传
python scripts/release.py --bucket ai-case-library \
       --region cn-hongkong                                # 完整发布
python scripts/release.py --skip-publish --skip-deploy     # 只重算分并重建本地产物
python scripts/release.py --only fit,score,build           # 只跑某几步（调试用）
python scripts/release.py --selfcheck                      # 只校验步骤顺序，不碰数据
```

几条设计上的取舍：

- **失败即停**。任何一步非 0 退出，后面都不跑 —— 后面的步骤依赖前面的产物，
  `fit` 挂了还继续 `score`，等于把破坏再固化一层。退出码就是停下的步骤序号，
  便于在 CI 里区分（`6` = 停在第 6 步；`2` = 参数错；`3` = 步骤表自相矛盾）。
- **顺序是声明式的，不是注释**。步骤表里每步声明 `after`，自检和测试拿它校验
  实际顺序，并做拓扑排序比对 + 环检测。所以「改了顺序忘了改声明」和
  「改了声明忘了改顺序」都会红 —— 手写断言只能抓后者。
- **产物目录写死 `public`，不暴露 `dist`**。`dist` 是带 `inbox` 未核实素材的
  本地预览版，2026-09-20 曾用默认参数把 441 条推上公网。想发预览版得自己去跑
  `deploy_oss.py` 并显式 `--allow-inbox`，`release.py` 不给你这个口子。
- **有候选要发但 server 没起时，在第一步之前就停**。以前是等到 `publish`
  那步连不上才报错，那时 `contentpack` 已经写盘了，等于白改一遍数据。
- **`--dry-run` 里配置缺失只警告不失败**，真跑才失败 —— 预演的目的就是来看
  「会怎样」的，报出缺什么比直接退出有用。

### 核实等级不能被虚标

核实等级不是装饰，是给读者的信任凭证：标「支付网关验证」，读者就以为我们
看过 Stripe 后台。所以 **`stripe` / `official` 必须有 A 档来源撑着**，
否则一路降级：`partial`（只有第三方报道）、`founder`（只有创始人自述）；
只有中文二手转述的，任何等级都不成立 —— 那需要的是人工重核，不是换个标签。

```bash
python scripts/audit_evidence.py            # 只报告，不改数据
python scripts/audit_evidence.py --apply    # 降级写回（先备份，并留降级记录）
```

规则只写一遍，在 `scripts/verify_rules.py` 的 `best_supported_level()` /
`evidence_gap()`；上面那个脚本只是遍历 `cases.json` 而已。

第一次跑 `--apply` 时，库里有 **11 条**案例标着 stripe / official，登记的来源
却只有第三方拆解站（SaaSXtra、Steal What Works、NeoDrop），全部降成了
「口径待核」。降级不留哑账：原等级和理由写进每条案例的
`verification_downgraded` 字段，想改回去删掉它就行。

### 核实工作台（在独立的管理后台里）

核实与入库**不在公开站上**。`python server.py` 会同时开第二个端口跑
`admin/`，那是一个独立的站点：有自己的登录页、自己的侧栏（候选池 /
采集队列 / 已发布）、自己的样式和脚本。

```bash
python server.py
# 公开站  http://127.0.0.1:5052/
# 后台    http://127.0.0.1:5053/   ← 核实在这里
```

- 第一次启动会在控制台打印一个**随机管理员密码**，只显示这一次，
  之后沿用。想改随时重设：

  ```bash
  python scripts/auth.py --reset      # 重新生成随机密码并打印
  python scripts/auth.py --set 你的密码
  ```

- 打开 5053 → 输密码 → 候选池卡片上就有「核实 →」，点开是完整的工作台。
- 登录状态存 HttpOnly cookie，**12 小时**有效；点「退出」立刻失效。
- 登出后再拿旧 cookie 也进不去（服务端会吊销，不只是清浏览器 cookie）。

凭据存在 `data/admin.json`（已 gitignore）：里面是密码的 PBKDF2 摘要和
一个随机签名密钥，**没有明文密码**，也绝不能提交。

#### 为什么不放在同一个站的 `/admin` 路径下

因为「同一个站上的 `/admin`」永远挡不住好奇的人去试：它会响应、会返回
401，等于明晃晃告诉别人这儿有个后台，还顺手给了个撞库的入口。

分成两个端口之后，**公开站上根本没有这些接口** —— 请求过来就是 404，
和请求一张不存在的图片没有任何区别：

| 请求 | 公开站 5052 | 后台 5053 |
|---|---|---|
| `GET /api/verify-schema` | 404 | 401（未登录）/ 200 |
| `POST /api/login` | **404（连门都没有）** | 401 / 200 |
| `POST /api/cases`（入库） | 404 | 401 / 201 |
| `GET /verify.js`（工作台脚本） | 404 | 200 |
| `GET /api/data` | 只有公开数据 | 管理员还能拿到核实草稿 |

注意这个隔离是**结构性**的，不是靠鉴权撑着的：就算把鉴权整个关掉
（`CASE_LIB_NO_AUTH=1`），公开站照样一个字都改不了。`selftest.py` 的
第 9 节就是拿关掉鉴权的服务来验这件事的。

### 仓库是公开的，这样安全吗

安全，前提是**服务只在本机跑**。把边界说清楚：

| 会被公开 | 不会公开 |
|---|---|
| `scripts/auth.py` 的全部算法 | `data/admin.json`（密码摘要 + secret）|
| `server.py` 的接口与鉴权逻辑 | 你实例的真实密码 |
| 迭代次数、token 格式、cookie 名 | `data/verifications.json`（核实草稿）|
| 生成密码的字母表和长度 | |

代码全公开没关系——**密码学不靠算法保密**。别人 clone 之后启动，会在他
自己机器上生成**另一个**密码和**另一套** secret，那是他本地实例的管理员；
你的 secret 只在你 `data/admin.json` 里，他拿不到，所以：

- 他生成的密码，在你这里**校验不通过**；
- 他用自己 secret 签的 token，你的服务**拒绝**（HMAC 对不上）。

反过来也一样：你的密码不能用来登他的。两边是两台互不相干的机器。

**但有一个前提不能破：两个端口都绑在 `127.0.0.1`。** 代码里是这么写的，
别改成 `0.0.0.0` 或做端口转发——那等于把这个登录框直接挂到互联网上，那时
「密码会不会被猜出来」就变成真问题了。同理，**别把 `data/` 提交上去**。

登录做了两道减速：PBKDF2 本身每次约 0.8 秒，加上**连续错 5 次锁 5 分钟**
（锁定期内正确密码也进不来）。公开仓库意味着攻击者知道怎么比对密码，
唯一挡着他的就是「试不快」和「试不多」，这两条正好守住了。

线上静态站（`case.ydtgo.top`）**没有后台**——它只有一个 `data.json`，
没有后端，所以是只读快照。构建产物里也不会带上后台的任何一行代码
（`scripts/build_static.py` 只吃 `static/`，`admin/` 根本不在输入里）。
核实请在本地跑 `server.py`，然后开 5053。

---

## 自测

```bash
python selftest.py                    # 后端：起临时服务，121 项接口测试，自动备份+还原数据
node uitest.js                        # 公开站：DOM 桩跑一遍所有渲染函数，104 项检查
node uitest.js --static               # 公开站静态产物：只读模式，105 项检查
node uitest-admin.js                  # 管理后台：登录 / 三块视图 / 工作台 / AI 面板，120 项检查
python scripts/test_verify_rules.py   # 核实规则引擎，70 项
python scripts/test_ai_verify.py      # AI 自动核实的纯函数：挑选/映射/JSON 提取，47 项（不联网）
python scripts/test_triage.py         # 零成本初筛：打分/判级/去重/与 ai_verify 的排序契约，52 项（不联网）
python scripts/test_harvest_sources.py # 五源解析 + source_kind 可信度，188 项（不联网）
python scripts/test_prerender.py      # 预渲染与 noscript 兜底，54 项（不联网）
python scripts/test_daily_harvest.py  # 自动提交脚本：git 机制与失败路径，25 项（不联网）
python scripts/test_build_static.py   # 静态构建：防误删护栏 + 构建自检，38 项（不联网）
python scripts/check_ci.py            # 校验 workflow 的 YAML 结构与其中 shell 脚本语法，44 项
python scripts/e2e_verify_write.py    # 手动跑：核实写入端到端（需先起 server.py），27 项
```

`test_harvest_sources.py` 用的是**真抓下来的响应**（`scripts/fixtures/`，含
IndieHackers 产品页、ARR Club 公司页与 sitemap、HN 搜索、PH Atom feed、
TrustMRR 的 `.md`），全部离线跑，不联网。它守的几条线：

- 每条采集结果**必须带 `source_kind`**，且值在四枚举内；
- IndieHackers 的自报数字必须标 `self_reported`，Product Hunt **不许带任何收入字段**；
- ARR Club 的 ARR **必须与年份成对存储**（防止 `<title>` 2026 与 FAQ 2025 串年份）；
- sitemap 里 `/product/<slug>/<milestoneId>` 这种里程碑 URL 要丢掉；
- `sources.json` 与 `harvest.py` 的 `source_kind` **不能漂移**
  （一个是给人看的说明，一个是真跑的逻辑，对不上不会报错，只会静默不一致）；
- 抓取要有**总耗时上限**（`urlopen` 的 timeout 只管 socket 空闲，慢速滴数据时不会触发）。

`selftest.py`、`uitest.js`、`uitest-admin.js`、`build_static.py` 都会被 CI
在每次 push 时自动跑（见下）；
`test_daily_harvest.py`、`test_build_static.py`、`check_ci.py` 是本地工具。

**`scripts/e2e_verify_write.py`** 是手动跑的工具，验证「核实写入路径」是否真的落盘：
`PUT /verification` → `promote` → 案例字段。给核实草稿加字段时**必须先起服务再跑它** ——
单测绕过 HTTP 层，字段漏进 `server.py` 的白名单时存盘即丢、而所有单测仍是绿的
（这个坑踩过一次，那次丢的是 `gates_denied`）。它用合成候选跑、跑完从备份还原。

```bash
python server.py                              # 另开一个终端
python scripts/e2e_verify_write.py            # 27 项断言
```

**`selftest.py`** 起一个临时服务（默认 5087 公开 / 5088 后台，不影响正在跑的 5052），
**在开始前备份 data/、结束后无论成败都还原**。

覆盖：公开站静态资源、后台静态资源、接口、数据结构、三级漏斗推进、
新增/修改/异常处理、目录穿越防护、后台鉴权（[8]）、**双站点隔离（[9]）**。

第 [9] 节值得单独说一句：它故意在**关掉鉴权**的服务上验证「公开站仍然只读」。
如果公开站上还能写，就说明「两个站分开」只是靠鉴权撑着，而不是结构上真分开。

**`uitest.js`** 不需要浏览器——用最小 DOM 桩加载 `static/app.js`，跑完全部渲染函数，检查：

- 15 个 UI 区块是否产出 HTML（含移植评分维度、排行榜、金银铜领奖台）
- 卡片数量是否与 `data/*.json` 一致
- 24 条案例的详情抽屉能否逐条渲染
- 已读进度是否正确写入 localStorage
- **特殊字符转义**：注入 `<script>` / `<img onerror>` / `&` / 单双引号，确认没有 XSS 漏洞
- 缺 `metrics`、全空字段是否抛异常
- **综合排行**：四象限 4 个格子、分组不重不漏、两个榜的行数、综合分公式逐条验算、
  两套金牌顺序、五维完整性（区分于原来的四维）
- **排序与筛选**：三个排序分支的首位是否正确、象限筛选命中数与卡片数一致、
  分数下限筛选后是否全部达标、**叠加筛选**、命中提示文案、清空筛选能恢复全量
- 抽屉里是否渲染出「个人可做性」「综合分」区块、象限徽章、双轴对比条、五列网格

也可以对着真实接口跑：`node uitest.js http://127.0.0.1:5052`

### 静态产物也要测

`node uitest.js --static` 测的是 `scripts/build_static.py` 的产物，不是源文件——
否则测的就不是要部署的那份东西了。它会自己切到只读模式、从 `dist/data.js` 取数，
并把 `fetch` 换成「一调就炸」的桩：万一哪天静态模式里误用了 `/api`，测试会直接暴露，
而不是静默拿到 `undefined`。

多验四件事：不渲染写按钮、侧栏标注只读快照、数据确实来自 `data.js`、
**app.js 里一个后台函数都不剩**（`openVerify` / `verifyHTML` / `IS_ADMIN` /
`/api/login` …全都不存在 —— 这是「两个站真的分开了」的前端侧证据）。

**`uitest-admin.js`** 是后台站自己的那套，加载 `admin/verify.js` + `admin/app.js`：

- 默认停在登录框、错密码给出提示、对密码进入后台
- 三块视图（候选池 / 采集队列 / 已发布）与视图切换
- 核实工作台六段齐全、发布按钮默认禁用、判定条区分过与不过
- 点「核实 →」能打开抽屉并渲染出工作台
- 登出后清掉内存里的数据、退回登录框
- 反过来确认两个站没串：`static/app.js` 里没有 `loadAdmin`，`admin/app.js` 里没有公开站的阅读视图

### 持续集成

`.github/workflows/ci.yml` 在每次 push / PR 时自动跑，三个 job：

| job | 矩阵 | 内容 |
|---|---|---|
| 后端 | Python 3.9 / 3.11 / 3.13 | 校验 `data/*.json` 是合法 JSON → 跑 `selftest.py` → 断言数据没被测试改脏 |
| 前端 | Node 18 / 20 / 22 | `node --check` 四个 JS（公开站 + 后台 + 两个测试）→ 跑 `uitest.js` 与 `uitest-admin.js` → 断言数据没被写脏 |
| 静态产物 | — | 真跑一遍 `build_static.py` → `uitest.js --static` 测只读模式 → 断言构建只产出 `dist/` |

前后端两个 job 都是 `fail-fast: false`，某个版本挂掉时能看到全貌，而不是只看到第一个。

第三个 job 守的是「能部署出去的那份东西确实可用」——它挂掉意味着线上会白屏或
按钮失灵，在推上去之前就知道，而不是等你打开浏览器才发现。

**「数据没被改脏」这条断言是这个库特有的**：`selftest.py` 会在开始时备份 `data/`、
结束时还原，那条断言就是验证这个备份/还原真的有效——如果哪天还原逻辑坏了，
CI 会先于你发现。手改 JSON 少个逗号也会在这一步被拦住。

`scripts/check_ci.py` 是本地版的 workflow 校验（YAML 结构 + 其中每段 shell 脚本的
`bash -n`），需要 `pip install pyyaml`；它属于本地工具，不在 CI 里跑。

> Windows 上有个坑：PATH 里的 `bash` 往往解析到 `C:\WINDOWS\system32\bash.EXE`，
> 那是 WSL 的启动桩——它不解析脚本，对任何输入都返回 1。`check_ci.py` 因此
> 硬编码优先用 `C:\Program Files\Git\bin\bash.exe`，并且在正式校验前先拿一个
> 故意写错的脚本自检一次，确认这个 bash 真的会报语法错。

---

## 部署到阿里云 OSS

`scripts/build_static.py` 把整个库打成纯静态站点（`dist/`，6 个文件、约 580 KB）——
不需要服务器、不需要 Python 运行环境，扔到任何静态托管都能跑。

```bash
python scripts/build_static.py              # 构建到 dist/
python scripts/build_static.py --no-inbox   # 精简版：不含采集队列
python scripts/build_static.py --out public # 换输出目录
```

| 产物 | 说明 |
|---|---|
| `index.html` | 注入了 `window.__STATIC__ = true`，前端据此切只读模式 |
| `data.js` | `window.__CASE_LIB_DATA__ = {...}`，页面实际加载这个 |
| `data.json` | 同一份数据，给第三方程序抓取。字段字典：[docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md) |
| `style.css`、`app.js` | 前端本体 |
| `404.html` | 可以在 OSS 里配成错误页 |

**为什么有 `data.js` 还要 `data.json`：** 直接双击 `index.html` 打开时协议是
`file://`，`fetch` 本地文件会被浏览器 CORS 拦掉，而 `<script>` 不受这个限制。
所以页面走 `data.js`，这样整个 `dist/` 拷到哪儿都能双击打开；`data.json` 给程序消费。
构建脚本会自检这两件事：静态标记注进去了没有、有没有退回根路径引用
（根路径在子目录部署时会 404）。

### 线上是只读快照

静态站点没有后端，所以「提升为精写案例」「转入候选池」这类写操作在线上不出现——
前端会把按钮位置换成「只读快照」标签，而不是让你点了没反应，侧栏也会标出来。
要做核实和写操作，克隆仓库跑 `python server.py`。

已读进度走 `localStorage`，线上照常工作——它记的是「你这个人读过哪些案例」，与后端无关。

### 上传（零依赖）

`scripts/deploy_oss.py` 自己实现了 OSS 的 V1 签名（HMAC-SHA1），
**不需要 `pip install oss2`，也不要求本机装 ossutil**。

```bash
# 1 验证凭证（只读：列一下 Bucket，不写任何东西）
python scripts/deploy_oss.py --check --env-file <你的.env>

# 2 看会传哪些文件
python scripts/deploy_oss.py --bucket my-bucket --dry-run --env-file <你的.env>

# 3 真传
python scripts/deploy_oss.py --bucket my-bucket --setup-website --env-file <你的.env>
```

凭证按优先级取：`--env-file` → `OSS_ACCESS_KEY_ID`/`OSS_ACCESS_KEY_SECRET` →
`ALIYUN_AK_ID`/`ALIYUN_AK_SECRET`。**脚本里不存任何密钥**，`.env` 已在 `.gitignore`。

上传时按扩展名给正确的 `Content-Type`（OSS 不会自己猜，猜错浏览器行为会很怪），
并按文件设缓存策略：

| 文件 | Cache-Control | 理由 |
|---|---|---|
| `index.html`、`404.html`、`case/*.html`、`data.js`、`data.json` | `no-cache` | 内容会变，改完立刻生效 |
| `app.js`、`style.css` | `public, max-age=300` | 基本不变，省点回源 |

`--setup-website` 会配好静态网站托管（首页 `index.html`、错误页 `404.html`）。
不配这个，访问域名根路径会返回一个 XML 文件列表而不是首页。

### 对外发布要传「--no-inbox」的那一份

`dist/` 是完整版，带着还没核实的采集队列，别直接传出去。
对外发布的流程是先建一份干净的，再传它：

```bash
# 1 构建不含采集队列的版本
python scripts/build_static.py --site-url https://case.ydtgo.top --no-inbox --out public

# 2 传这份
python scripts/deploy_oss.py --bucket ai-case-library --region cn-hongkong \
  --dir public --setup-website --verify-public
```

### 部署完怎么验

```bash
python scripts/verify_deploy.py                    # 默认验 https://case.ydtgo.top
python scripts/verify_deploy.py --expect-cases 30  # 案例数变了就改期望值
```

它会挨个访问首页、24 个案例页、404 页、`data.json`、sitemap、静态资源，
一共三十来项断言。**验的是语义标记，不是字节数**——数据每天都在涨，
`wc -c` 对不上根本分不清是「没部署」还是「部署了但数据变了」。
所以验的是 `site.title`、`generated_at`、cases 条数、`inbox` 是否为 0 这类东西。

一个容易误判为失效的点：`case/index.html` 里的链接是相对路径（`./nitra.html`），
这样根目录部署和子目录部署都能直接用，校验时别按绝对路径去匹配。

### 部署只有一个入口：本地（Actions 不部署）

**`harvest.yml` 只采集，不部署。** 它以前会构建 + 部署到 OSS，那是错的：

- 采集只写 `data/inbox.json`（采集队列），而对外产物是
  `build_static.py --no-inbox --out public` —— **产物里根本不含 inbox**。
  部署 public 等于把同样的站点重传一遍，白跑。
  更糟的是它当时连 `--no-inbox` 都没加，用的是默认 `dist`，
  于是**每天把 400+ 条未核实素材的全文推上公网**，跑了至少 4 天没人发现
  （`dist/data.json` 525 KB vs `public/data.json` 174 KB）。
- 案例内容（`data/cases.json`）只会由人工核实后改动，而那条路走的是
  `scripts/release.py`，它自己就包含 build + deploy。

所以「什么时候上线」只有一个入口：

```bash
python scripts/release.py --bucket ai-case-library --region cn-hongkong
```

构建能力仍受 CI 保护 —— `ci.yml` 的 static job 每次 push 都会真跑一遍构建，
并断言"只产出 `dist/`、没碰别的地方"。所以 workflow 改动不会悄悄弄坏构建。

`ci.yml` 与 `harvest.yml` 的分工：

| | `ci.yml` | `harvest.yml` |
|---|---|---|
| 触发 | push / PR | 每天 09:00 + 手动 |
| 做什么 | 跑测试 + 构建验证 | 采集 → 校验 → 提交 → 推送 |
| 权限 | `contents: read` | `contents: write` |
| 部署 | 不部署 | 不部署 |

### 本地参数固化：`.env`

项目根目录有个 `.env`（已在 `.gitignore` 里，不会入库）：

```
OSS_BUCKET=ai-case-library
OSS_REGION=cn-hongkong
```

有了它就不必每次带那两个参数：

```bash
python scripts/release.py --env-file .env                       # 完整发布
python scripts/release.py --only build,deploy --env-file .env    # 只重建上线
python scripts/auto_deploy.py --env-file .env                    # 兜底检查
```

优先级：**命令行 > 环境变量 > `--env-file`**。实现上靠 `load_env_file`
「不覆盖已存在的变量」这条性质，所以不需要额外的优先级代码。

注意 `--env-file` 必须在 preflight **之前**就被 release.py 自己读掉 ——
不能只转给 `deploy_oss`。那边要到第六步才载入，而 preflight 在第一步之前
就要用这两个值判断「这趟会不会部署」，否则 deploy 会被误判成「没给地域」跳过。
（这个坑踩过：同一个 `.env`，两个模块给出不同结论。）

凭证不放这里 —— 用 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
环境变量（或 `--env-file` 里写，但别提交）。

### 忘了部署怎么办：兜底检查

发布案例不会忘 —— 部署是 `release.py` 的第六步，而且失败即停。
但**只改文案 / 前端 / 站点配置**时没有那条链路兜着：改完忘了跑部署，
线上就一直停在旧版本，而且没有任何提示。

所以有一个幂等的兜底检查：

```bash
python scripts/auto_deploy.py --env-file .env             # 检查，需要就部署
python scripts/auto_deploy.py --env-file .env --dry-run    # 只报告差异
```

它构建一份对外产物，和线上**逐文件比内容**，一致就什么都不做。
两处细节值得知道：

- **时间戳不算差异**：`generated_at` 与 `stats.inbox` 会被归一化掉。
  不这么做的话每次构建都「有差异」，这脚本就退化成每天空转部署一次。
  实测依据：同一份数据连续构建两次，39 个产物里只有 `data.json` / `data.js`
  的哈希会变 —— 这两条都写成了测试断言，改动一旦破坏它就会红。
- **线上多出来的文件不触发部署**，只报告。因为部署本来就不会删它们，
  拿它触发等于白跑；要清得去 OSS 控制台手动删。

已注册一个本地定时任务（每天 21:00）跑它兜底，所以忘了也不至于长期不更新。
但它**不能替代人工判断上线时机** —— 唯一的依据是「本地和线上不一样」，
它不知道你是不是还在改。所以只当兜底，不当发布主路径。

### OSS 部署的配置

凭证只从环境变量或 `--env-file` 读，**不进仓库**。本地跑之前：

```bash
export OSS_ACCESS_KEY_ID=...
export OSS_ACCESS_KEY_SECRET=...
export OSS_BUCKET=ai-case-library
export OSS_REGION=cn-hongkong
```

两条和阿里云有关的实际经验：

- **用香港地域的 Bucket 不需要大陆备案。** 大陆地域绑自定义域名要备案，香港不用。
  本项目没备案，所以 Bucket 全在 `cn-hongkong`，直绑 OSS（代价是没有 CDN 加速）。
- **地域必须显式给，脚本不猜。** `deploy_oss.py` 曾经兜底 `cn-hangzhou`，但 Bucket 在
  香港，于是「什么都不填」必然指向错 endpoint，OSS 只回一句
  `must be addressed using the specified endpoint` 403 —— 看不出是地域错了。
  现在不给地域就当场停下：`--region cn-hongkong` / `OSS_REGION=cn-hongkong` /
  `--env-file`（三种任选）。
- **绑了 CDN 自定义域名的话，传完记得刷缓存**，否则可能还是旧的：
  控制台 → CDN → 域名管理 → 刷新预热 → 刷新缓存 → 类型选「目录」填 `/`

---

## 目录结构

```
ai-case-library/
├─ server.py               零依赖 HTTP 服务 + JSON API（标准库）
├─ selftest.py             后端自测（备份 → 测试 → 还原）
├─ uitest.js               前端渲染冒烟测试（DOM 桩，支持 --static）
├─ start.bat               Windows 一键启动
├─ LICENSE                 MIT
├─ .github/workflows/
│  ├─ ci.yml               push / PR 跑后端 + 前端 + 静态产物
│  └─ harvest.yml          每天 09:00 云端采集 → 校验 → 提交 → 部署
├─ data/
│  ├─ sources.json         5 个信息源的配置、可信度分级、坑，以及采集过滤规则
│  ├─ cases.json           精写案例（25 条）
│  ├─ candidates.json      候选池（33 条）
│  ├─ inbox.json           采集队列（脚本产出，347 条）
│  ├─ inbox_archive.json   队列超额后的归档（留底，不删除）
│  └─ last_harvest.json    最近一次采集的简报
├─ scripts/
│  ├─ harvest.py           采集：HN / TrustMRR / Product Hunt
│  ├─ daily_harvest.py     采集 + 自动提交推送（本地与云端共用同一套）
│  ├─ build_static.py      构建静态站点到 dist/（部署用）
│  ├─ deploy_oss.py        上传 dist/ 到阿里云 OSS（零依赖，自己实现 OSS 签名）
│  ├─ verify.py            核实助手
│  ├─ verify_rules.py      核实规则引擎（门槛 / 必填 / 质量分 / 定档 / 等级与证据）
│  ├─ triage.py            零成本初筛（给候选与队列打「值得核」分，定核实顺序；不联网不写数据）
│  ├─ ai_verify.py         AI 自动核实流水线（找卡点→检索→填草稿→判定→可选发布）
│  ├─ test_triage.py       初筛的单测（打分 / 判级 / 去重 / 排序契约，52 项）
│  ├─ verify_case.py       对**已发布案例**重跑一次 AI 自核（候选池外也能核）
│  ├─ audit_evidence.py    审计并修正「等级高于来源证据」的案例
│  ├─ peek_ai.py           只读探针：打印发给 AI 的提示词与它的原始回答
│  ├─ backfill_tier.py     给存量案例补 tier + tier_reason
│  ├─ prerender.py         把案例预渲染成 case/<id>.html（给爬虫与分享用）
│  ├─ make_article.py      生成公众号拆解稿（排序口径 + 发布红线检查）
│  ├─ score_china_fit.py   国内移植可行性评分（六维加权 + 金银铜）
│  ├─ score_solo_fit.py    个人可做性评分 + 双轴综合分 + 四象限
│  ├─ test_daily_harvest.py 自动提交脚本的测试（25 项，不联网）
│  ├─ test_build_static.py 静态构建的测试（38 项，含防误删护栏，不联网）
│  └─ check_ci.py          校验 workflow 的 YAML 与 shell 语法（本地工具）
├─ admin/                  管理后台（独立站点，5053 端口，要登录）
│  ├─ index.html           登录页 + 后台主体（侧栏三视图 + 工作台抽屉）
│  ├─ style.css            复用公开站的设计变量 + 后台自己的布局
│  ├─ app.js               登录态、三块视图、转入候选池
│  └─ verify.js            核实工作台（只在这里，公开站一行都没有）
├─ uitest-admin.js         后台渲染冒烟测试（DOM 桩，120 项）
└─ static/                 公开阅读站（5052 端口，纯只读）
   ├─ index.html
   ├─ style.css            深色主题
   └─ app.js               无框架前端
```

## 数据接口

公开站（5052）只提供读接口；下面标了「后台」的只在 5053 上存在，
在公开站上请求一律 404。

| 方法 | 路径 | 谁有 | 说明 |
|---|---|---|---|
| GET | `/api/data` | 两边 | 全部数据（cases / candidates / inbox / sources / stats）。后台登录后会多带 `verifications`（核实草稿） |
| GET | `/api/stats` | 两边 | 只要统计 |
| GET | `/api/session` | 两边 | 登录状态；公开站恒为 `admin: false` |
| GET | `/api/ping` | 两边 | 探活，回 `site` 表明自己是哪个站 |
| POST | `/api/login` | **后台** | 登录，下发 HttpOnly cookie |
| POST | `/api/logout` | **后台** | 登出，服务端吊销 token |
| GET | `/api/verify-schema` | **后台** | 核实规则表（门槛 / 必填 / 加分） |
| GET | `/api/candidates/<id>/verification` | **后台** | 读某条候选的核实草稿 |
| PUT | `/api/candidates/<id>/verification` | **后台** | 存草稿（可中断，下次接着填） |
| PATCH | `/api/cases/<id>` | **后台** | 改一条案例 |
| PATCH | `/api/cases/<id>/tier` | **后台** | 重定档位（备选 → 实核 → 精品，见 verify_rules.default_case_tier） |
| POST | `/api/cases` | **后台** | 新增案例（需 `name`） |
| POST | `/api/candidates` | **后台** | 新增候选 |
| POST | `/api/inbox/<id>/to-candidates` | **后台** | 采集队列 → 候选池 |
| POST | `/api/candidates/<id>/promote` | **后台** | 候选池 → 精写案例（过不了核实闸门会 409） |

直接改 `data/*.json` 刷新页面即可生效，不需要重启服务。

---

## 快捷键

- `/` 聚焦搜索
- `Esc` 关闭详情抽屉

---

## 信息源可信度

| 源 | 等级 | 说明 |
|---|---|---|
| TrustMRR | A | 连只读 API key，收入由支付数据自动验证，不接受截图自报 |
| IndieHackers | B | 唯一能拿到「他们怎么做到的」的地方，但全是自报数字 |
| Product Hunt | B | 发现新项目的雷达；榜单热度 ≠ 有人在付钱 |
| Hacker News | B | 技术圈最诚实的自曝渠道，API 免 key |
| ARR Club | B- | 查某个时间点的 ARR 方便，但大量二手转述 |

详细的接入方式、能拿到的字段、以及每个源的坑，见应用里的「信息源」页。

---

## 贡献

欢迎 PR。两类东西最有用：

- **新案例**：按 `data/cases.json` 现有字段加。`verification` 必须标真实等级——
  找不到公开来源就老老实实标 `unverified`，别为了好看往上写。
  带 `corrections`（记录「别人抄错了什么」）的尤其欢迎，那是这个库最有价值的部分。
- **改正数字**：发现库里某条数字有问题，开 issue 附上来源链接即可，不用改代码。

改完跑一遍自测再提：

```bash
python selftest.py && node uitest.js
```

## 协议

[MIT](LICENSE)，版权归 oracis。可商用、可修改、可分发，保留版权声明即可。

---

## 一句话

别自己想，去看已经跑通的人在干什么。但看之前，先确认那些数字是真的。
