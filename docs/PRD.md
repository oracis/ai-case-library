# 拆解海外 · Overseas Teardowns — 产品设计文档（PRD）

> 版本：**v3.0**　日期：**2026-10-18**　状态：**现行**（上一版 v2.0 / 2026-09-25）
> 适用仓库：`oracis/ai-case-library`　线上：https://case.ydtgo.top
> 配套图：`docs/产品流程图.html`（同一份事实的可视化版本）
> 本文描述的是**代码当前实际实现的状态**，不是设想。数据截至文首日期：
> **案例 255 条 / 候选 1 条 / 采集队列 280 条**；四平台台账（2026-10-19 按远端逐条核对）：公众号 **60**（草稿 41 / 已发 19）、头条 **59**（草稿 38 / 已发 21）、B站 **11**、小红书 **52**（草稿 44 / 已发 8）。
> 注意「条数 ≠ 篇数」：头条线上 23 篇 = 台账 21 条（bustem / cometly 各发新旧两篇）；B站线上 13 篇 = 台账 11 条（trustmrr / uplinked 各发新旧两篇）。旧篇标题记在 `note` 的『…』里，巡检认这个别名。

---

## 1. 产品是什么

一句话：**一个本地跑的、已核实的海外软件生意案例库。**

> 别自己想，去看已经跑通的人在干什么。**但看之前，先确认那些数字是真的。**

它把国外已跑通的小项目扒下来 → 核过数字 → 写成人话 → 按「模式」而不是「行业」组织，用来长判断力。零第三方依赖，只用 Python 标准库。

### 1.1 与「案例合集」的差异（产品护城河）

大部分案例合集告诉你「有人赚了多少钱」。本库额外回答三件事：

| 别人不说的 | 本库的答案 | 承载字段 |
|---|---|---|
| 这个数字**是谁说的** | 六档核实等级 + 三档池 | `verification` / `tier` / `source_kinds` |
| 别人**抄错了什么** | 逐条修正记录 | `corrections[]` |
| **一个人**能不能做 | 五维/六维/木桶综合分 | `solo_fit` / `china_fit` / `composite` |

### 1.2 明确不做的事（产品红线）

- **精写层永不自动化。** 只有采集层自动化。`cases.json` 绝不自动写入（`README` 原文：把「写之前自己搜一遍」交给机器，整个库就退化成它本来要反对的那种二手转述聚合站）。
- **采集脚本只写采集队列**，绝不直接写精写库。
- **核实等级不许虚标。** 只有中文二手转述的，任何等级都不成立。
- **不暴露未核实素材。** 对外产物必须加 `--no-inbox`，否则未核实素材会跟着上线。
- **默认不自动群发。** 能力上头条已于 2026-10-18 打通纯 API 发表（`publish --api --send`），但**策略上仍要求人显式加 `--send`**（或传 `--yes`），不设「自动发」默认值。公众号 2026-10-19 打通了 UI「发表」（`wechat_publish.py send`，发布模式不推送、不占每日群发额度，群发模式 `--notify` 走推送）；**两种模式都要管理员/运营者扫码**（2026-10-19 实测纠正：曾据探针里没弹二维码误判发布不扫码，那是探针拦掉了 check 请求、流程没走到扫码那步），且个人订阅号无 `freepublish` API 权限（2026-10-18 复核，见 §4.8.2）——两条路都不改变「默认不自动群发」这条红线。
- **不为了多平台而降低单条质量。** 小红书有独立合规口径（`xhs_note_overrides.json`），宁可少发，不共用一套文案硬塞。

---

## 2. 目标用户与场景

| 角色 | 场景 | 用哪一屏 |
|---|---|---|
| **独立开发者 / 想出海的人**（主） | 找「一个人能做的生意」：看全局 → 按「木桶综合分」排 → 看能不能搬回国内 | 公开站 · 精写案例 / 综合排行 / 移植排行 |
| **内容读者**（次） | 每周多篇长文拆解 | 公众号「万物解释者」/ B站 / 头条 / 小红书 |
| **移动端读者**（新增） | 手机上查案例、买深度报告 | 小程序「远镜拆解」 |
| **运营者（本项目主人）**（内） | 每天：**后台挑候选 → AI 深核 → 人工裁定闸门 → promote → 四平台分发 → 对账** | Admin 后台 5053 |
| **CI / 机器人** | 每天 09:00（北京）采集、校验、推 data 分支；每天 21:00 幂等部署 | GitHub Actions + 本地自动化 |

界线是刻意划开的：**除了运营者本人，没有任何角色能写入 `cases.json`。**

---

## 3. 概念与数据模型

### 3.1 三级漏斗（信息架构的骨架）

```
inbox.json     采集队列  机器写   原始素材   280 条（上限 400，溢出归档不删）
   ↓ 人工点「转入候选池」
candidates.json 候选池   人工挑   1 条
   ↓ 过核实闸门 promote
cases.json     精写库   人工核   255 条 ← 唯一写入口是 /api/candidates/:id/promote
```

素材多、精写少是正常的。漏斗本身就是产品信息的一部分：公开站首页把它画成三根按比例归一的条形。

### 3.2 三套「可信度」分类（不要混）

| 维度 | 值 | 谁产生 | 用在哪 |
|---|---|---|---|
| `source_kind` 采集层 | `verified`(支付网关直读) / `self_reported`(创始人自述) / `secondary`(二手转述) / `discovery`(仅发现) | harvest | 素材徽章；认不出时取兜底 `discovery`（宁可保守） |
| `verification` 核实等级 | `stripe` / `official` / `partial` / `founder` / `disputed` | 深核 + 人工 | 六色 badge、筛选下拉 |
| `sources[].kind` 来源构成 | `stripe`(A) / `official`(A) / `press`(B) / `review`(B) / `founder`(C) / `secondary`(D) | 深核 | 定级依据、**自动降级依据** |

派生：`tier` 三档池 —— `premium` 精品池（有一手来源）/ `standard` 实核池（第三方撑住口径）/ `backup` 备选池。
当前分布：**premium 238 / standard 11 / backup 6**。

### 3.3 三套评分（皆 1–5 分，**注意 `replicability` 与另两套方向相反**）

- **`replicability` 可复刻度**：技术 / 获客 / 资金 / 时机，**5 = 最难**（人工判断）。
- **`china_fit` 国内移植性**：付费意愿 ×1.5、支付可达 ×1.2、合规空间 ×1.2、获客迁移 ×1.0、改造成本 ×1.0、竞争空位 ×1.0 → raw 满分 **34.5**，归一到 100。
- **`solo_fit` 个人可做性**：单人交付 ×1.4（`replicability.delivery` 的人工判断）、够得着客户 ×1.3、启动轻 ×1.1、造得出来 ×1.0、窗口还开着 ×1.0 → raw 满分 **29**。后四维由 `6 - replicability.*` 反推，**两套数据永远不会打架**。
- **`composite` 木桶综合分**：`0.6 × min(solo, china) + 0.4 × mean(solo, china)`，及格线 **70**。理由是两维之间有乘性关系（`90×40 → 50`，`60×60 → 60`）。

四象限：`go` 可以开干 / `partner` 有市场但一个人啃不动 / `export` 能做但别在国内卖 / `skip` 别碰。
当前分布：**skip 138 / export 64 / go 27 / partner 26**。

### 3.4 数据文件清单

`inbox.json` `inbox_archive.json` `candidates.json` `cases.json` `verifications.json`（草稿库）`sources.json`（源元数据 + 过滤词表 + 字段契约）`site.json`（外显配置）`ih_slugs.json`（IH 分片游标）`issue_numbers.json`（篇号注册表）`admin.json`（口令 hash/token secret）`last_harvest.json`（采集简报）`secrets.json`（AI key）`arrclub.json`（校对参照，不进候选池）`wechat_broken_drafts.json`（320003 损坏草稿黑名单）。

**四平台台账**：`wechat_published.json`（60）/ `toutiao_published.json`（**21，2026-10-19 三方对账后**；线上 23 篇，另 3 条幽灵记在 `toutiao_ghost.json`）/ `bili_published.json`（**11，2026-10-19 补登 7 条漏登**）/ `xhs_published.json`（**8 条，2026-10-18 按远端接口对账回填**；对账工具 `xhs_sync_ledger.py`，删除工具 `xhs_delete.py`）；另有 `toutiao_drafts.json` / `xhs_drafts.json` / **`bili_drafts.json`（49，2026-10-19 新建，见 `docs/LEDGER_CONSISTENCY.md`）** / `xhs_note_overrides.json` / `xhs_title_overrides.json`（头条与小红书共用）。

---

## 4. 功能规格

### 4.1 采集层（唯一自动化的一段）

`harvest.py` 实现，`--source` 可选 `hn / ph / trustmrr / trustmrr_sitemap / indiehackers / arrclub / all`：

| 源 | 通道实况 | 产出特点 |
|---|---|---|
| **TrustMRR**（主力） | `/api/ai`（主）+ `/api/ai/discovery`（补全未覆盖 slug）+ `startup-sitemap.xml` 广度通道（每轮有界 `.md` 抓取、断点续抓）+ HTML-JSON-LD 回落 | 支付网关验证的收入，信息密度最高 |
| **Hacker News** | Algolia `search_by_date` / `search` + Show HN 关键词 + `items/<id>` 评论树（`--hn-comments`） | 有讨论热度、有创始人现身 |
| **Indie Hackers** | 无公开 API：列表页 + **sitemap 分片轮换**（`?page=2` 返回字节完全相同，无法分页）+ `--ih-breadth` 广度模式 | 创始人自述 + 过程叙事 |
| **Product Hunt** | GraphQL（需 `PH_TOKEN`；**已决策不申请**，维持 Atom feed 雷达定位） | 仅发现，不含收入 |
| **ARR Club** | sitemap + FAQPage JSON-LD | **只写 `arrclub.json`，不进候选池**，仅供校对 |

保护与取舍：跨 `inbox/candidates/cases` 三层去重（id + 归一化名）；`http_json` 重试 3 次 + `1.5×(n+1)` 退避 + 显式绕过系统代理；IH 1.5s / ARR 2.0s 节流；单页抓取有 16 MB / 150 s 双重兜底；`inbox` 容量 400，溢出进 `inbox_archive.json`（**只进不出、永不删除**）；金额 < $50 当行价丢；MRR 低于 100 的 TrustMRR 条目丢；提问帖/Ask-Tell 类 HN 丢；`--tm-seeds` 可把外部线索站当发现面。

### 4.2 零成本初筛（`triage.py`）

纯函数，不联网、不调 LLM、不写盘。给每条素材打分：证据起点（一手 40 / 自称 20 / 第三方 16 / 验证源 18）→ 数字可得性（有数字 +16，无数字 −20，<$1000 −12，文本-only 字段不算数字）→ 关注度 → 可核性（有官网 +6），clamp 0–100。

判级优先序：**duplicate → ready → backfill → placeholder/reference → 可深核 → 分数线（DEEP 56 / LATER 28）→ 兜底**。

两条刻意写死的规则：**候选池不由分数判 drop**（只有重复才 drop）；**高分 ≠ 可深核**。

### 4.3 候选池 enricher

- **`backfill_website.py`** —— 只补「官网」这一个字段：从 TrustMRR `/api/ai` + discovery 铸 `{slug: {name, website}}`，三层定位（slug → id → 归一化名），**只补空不覆盖**，幂等，默认 dry-run。
- **`resolve_slug.py`** —— TrustMRR slug 反查：用**全站 `startup-sitemap.xml`（必须显式 gzip，实测 10,691 条）+ `/api/ai`（≈90）**建索引，六层证据强度 `已登记 → 来源页 → 官网匹配 → 官网子域 → 名称匹配 → 站点索引`；最弱一层必须调 slug `.md` 核对标题（**身份确认**，因 `private-venture-1` 误配事故）。默认 dry-run，`--write` 才落盘。
- **`promote_to_candidates.py`** —— 晋升门槛 `MIN_MRR = 300` 与 `triage.TINY_REVENUE = 300` **必须同值**；`PEAK_RETENTION = 0.40`、`COLLAPSE = -0.50`；留存 <40% 仍拦，且一律写进 `metric_note`「峰值 X → 最近完整月 Y，留存 Z%」。

### 4.4 AI 深核（`ai_verify.py`）

四阶段：**拼素材**（官网 → 来源页 → TrustMRR `.md` → slug 反查 → HN Algolia/Bing/DDG，最多 5 页，每页 6000 字，<200 字回落 meta 摘要，抓不动才用本地 Chrome 无头渲染）**→ 调 LLM**（OpenAI 兼容端点，默认智谱 `glm-4-flash` 免费档，`temperature=0.2`，JSON 模式，300 s 超时；`_cfg()` 三级链 环境变量 > secrets > 内置默认，判据是 `in os.environ`）**→ 映射**（三态门槛 `yes/no/unknown`，**布尔 `False` 不当反证**；musts 与旧草稿取**并集，勾是粘的**；`human_read` 任何模式都不由 AI 勾）**→ 落草稿**（`verifications.json`）。

失败语义：一页原文都没抓到 → 直接跳过不写草稿；判定不可发布 → 草稿照存，区块清单给人；`--publish` 但分不够 → `held`，只存草稿。
⚠ 智谱怪癖：`response_format=json_object` + 长生成会 500/超时；`verification` 可能回多值（按「不在枚举」记缺失）；小批量会把 id 编成 `"1"/"2"/"3"`。

### 4.5 核实规则引擎（`verify_rules.py`）+ 入库闸门

**门槛三态**：`confirmed = gates ∩ {仍在运营, 是真实生意, 有个人路径}`，只要**一道成立**就放行（denied 且未 confirmed 才 block）；AI 没核到的 `unknown` 不拦人。

**必填**：只有 `caliber_decided`（口径定了没）拦发布；`caliber_consistent`、`human_read` **不拦**，只挂「不拦发布」的提醒标签。另有三项实际 block：核实等级非法、口径值非法、**`source_kinds` 为空**。

**质量分**：6 项 bonus（20/20/15/15/15/15）+ 「三道全 yes 且无 denied」自动 +10，满分 100，**≥60 进精品池**。

**Promote 闸门（`server.py`）顺序**：候选存在 → `evaluate`，未过返 **409** → `evidence_gap` 把高于证据的等级**自动降级**并写 `verification_downgraded{from,to,reason,at,tool}` → `default_case_tier` 定档 → 写 case（`cases.insert(0)`、候选出池、草稿用完即清）。`human_read` 时间戳**只取服务端已存草稿**。

> 这是全项目唯一不可绕过的写入口。任何 AI / 脚本想入库，都必须从这里过。

### 4.6 内容包 & 入库后 Enrichment

| 脚本 | 服务时点 | 校验 |
|---|---|---|
| `contentpack_ready` | **promote 之前**（promote 只搬候选已有字段，裸发会造空壳） | `why_it_works`/`playbook` ≥3 条且每条 ≥20 字、`signals` ≥2、`verdict`/`what_it_does`/`how_it_makes_money` 非空、`metrics.headline` 必须带 `metric_note` |
| `publish_ready` | 发布动作本身 | 五字段 + 两条 ≥3；**`--id` 指定不合格条目也不放行** |
| `fit_ready` + `score_*` | promote 之后 | 检查缺哪套人工判断；只写 `replicability`，`solo/china` 交给各自脚本算 |

### 4.7 成文（`make_article.py`）

`--id/--all/--top N/--format md|html|both`，产物 `out/articles/<id>.{md,html}`，**只读 data、不写数据**。

九节骨架：钩子 → 干什么的 → 钱从哪来 → 做到多大（六指标，**故意不含 price_point**）→ 第二条纠错 → 为什么能成 / 能搬走什么 → 能不能搬回国内 → 一个人能不能做 → 我的判断 + 结论象限。

- **标题**：手工表优先（`TITLE_OVERRIDES`），否则模板产物并在 md/html 里插 `[!] 标题由模板生成，发布前请手改`；`sanitize()` 会改敏感词（月入 → 月营收）。
- **排期**：`virality_score = 纠错数×2 + 金额量级 + 象限 + EDITORIAL_WEIGHT`。
- **占位文案清理**：`text_clean.clean_placeholders` 只动会外显的 `one_liner`/`what_it_does`/`corrections[].claim`；判空用 `[0-9A-Za-z\u4e00-\u9fff]`（真实事故：曾把正常句尾的「。」吃掉）；尾部清理只收连接符 `，,、：；;·-—`。

### 4.8 四平台分发（v3 新增，替代 v2 的「公众号管线」单节）

#### 4.8.1 能力矩阵（2026-10-17/18 实测）

| 平台 | 通道 | 读 | 写 | 删 | 已发台账（另有草稿） |
|---|---|---|---|---|---|
| **B站** | **纯 API** | ✅ `x/space/article?mid=` 已投稿 | ✅ `draft/add`（带 id 即更新） | ✅ `draft/delete` | **11**（线上 13 篇；⚠ 草稿无台账文件） |
| **头条** | **纯 API**（写操作需活着的编辑器页 + **有头** Chrome 供 `tt-anti-token`） | ✅ `draft_list` + `mp_provider` | ✅ `article/publish`（`--api`，21 字段 form；**`--send` = save=1 群发**）+ 封面 `/spice/image` 两级 | ✅ `delete_draft` / `unpublish`（删已发布） | **21**（另有草稿 38） |
| **公众号** | **CDP**（9222 有头 Chrome） | ✅ `app_msg_list` + `appmsgpublish?sub=list` | ✅ `mp_editor_set_content` / `mp_editor_change_cover` | ✅ `operate_appmsg?sub=del` | **19**（另有草稿 41） |
| **小红书** | **CDP**（9223）；**读侧已接口化** | ✅ `note/user/posted` 页面内 fetch（秒级，`scripts/xhs_webapi.py`） | ⚠ 编辑器 UI（`POST /web_api/sns/v2/note` 返回 **461 风控 + verifytype**，创建发不出去）；`send-draft` 可开草稿箱→挑最久的→开编辑器→图片体检，发布按钮 DOM 已知 = `button.ce-btn bg-red` 但**在抽屉编辑器里没实测到** | ✅ `capa/postgw/note/delete`（纯 API） | **8**（另有草稿 44；台账已回填、重复已清理） |

> 2026-10-18 修正：小红书「无服务端接口」只说对了**草稿**那一半（页面原文：
> 「草稿存储于当前使用的浏览器本地」）。已发布列表 / 用户信息有服务端接口，
> 且能在已登录页面内直接 fetch（签名走全局 `window._webmsxyw`）。
> 当天对账发现线上已发 **9 条**（台账只记 2 条），其中「月收$128K：销售挖客户线索的工具」
> 09-29 重复发了两次（12:09 / 22:14）⇒ 已用 `xhs_delete.py --apply` 删掉 22:14 那篇，
> 线上与服务端对账均为 **8 条**。
> **删除可行、创建不可行**：删除走 `capa/postgw` 网关，实测 200 `code:0`；
> 创建走 `web_api/sns/v2/note`，稳定 461 —— 两者不是同一道闸，别互相外推。

口径说明：**「读/写/删」指该平台后台能做的操作**，不等于服务端开放 API。头条「只能 UI」是旧结论，已作废；公众号「删除 `action=del` 返 200009」也是猜错接口身。

#### 4.8.2 公众号为什么还是 CDP（2026-10-18 复核）

官方文档（`developers.weixin.qq.com/doc/subscription/`，订阅号独立文档区）今日实测适用范围：

| 接口 | 公众号（订阅号） | 服务号 |
|---|---|---|
| `draft/add` `draft/batchget` `draft/delete` | ✔ | ✔ |
| `material/add_material`（封面）`material/uploadimg`（正文图） | ✔ | ✔ |
| **`freepublish/submit`（发布）** | **仅认证（企业主体已认证）** | ✔ |
| **`message/mass/sendall`（群发）** | **仅认证** | **仅认证** |

但**文档的 ✔ 不等于本账号有权限**：后台首页明示本号是「万物解释者 · 公众号 · **未认证**」，且社区 2026 年多份实测一致——**未认证个人订阅号调 `draft/add` 直接 48001**。个人订阅号可以申请「个人认证」（¥300/次、注册满 30 天、实名、近 30 天无违规），但官方文档没有区分「个人认证 / 企业认证」逐条列权限，社区口径也不一致（有说「已认证公众号（个人/企业）即可」，有说需企业/个体户主体）——**只有认证后跑一次 `probe_wechat_openapi.py` 才知道**。`freepublish` 则明确限定「企业主体已认证」，个人认证大概率仍不可用。
⇒ **结论：API 这条路对当前账号是堵死的，CDP 仍是唯一通道**。「草稿箱」这一段还有一丝机会（个人认证后实测，成本 ¥300/年）；但要把**发布**也 API 化，唯一路径是**换主体 + 认证服务号**（服务号发布列是 ✔）。认证后先跑 `scripts/probe_wechat_openapi.py`，别再从零摸。

#### 4.8.3 公众号 CDP 管线（现状）

`build → queue → plan → publish → fix-cover → refresh → inspect → discover → sync → delete`。

- **登录态**：token 从**已登录页面动态抠**（URL / `wx.cgiData` / 整页 HTML），不能写死；使用独立 profile（`%LOCALAPPDATA%\Google\ChromeCDP`），登录态过期只能人工扫码。
- **篇号**：持久注册表 `data/issue_numbers.json`（篇号是**历史编号**不是排序位置，因为新案例往 `cases.json` 头部插入）。
- **封面**：HTML data URL → Chrome 截图 1080×460@2x（**必须带 doctype + charset**）；**CDP 截屏前必须 `Emulation.setDeviceMetricsOverride` 到画布大小**，否则 headless 视口 ~780px 会把 clip 裁掉、右侧补白边（产物尺寸仍 2160×920 正常，肉眼与尺寸都发现不了）；先上传插入正文 → 再从「正文选择」设为封面（顺序不能反）；已有封面草稿走 `_click_text_anywhere` + `--cover-api`（`mp_editor_change_cover`，单篇省约 3 分钟）。
- **稳定性**：逐篇 `save_published`；单篇异常 `continue` 并断 ws 重连；收尾判定：封面与原创都到位才算 `ok`，否则 `partial`。
- **320003 三步定位法**：错误页没有 `#title`，后续 `NO_EL/NO_API/NO_FILE_INPUT` 全是后果；区分「限流」与「记录损坏」只能**交替试开**（`wechat_verify_refresh.py --probe-missing`）；记录损坏进 `wechat_broken_drafts.json` 黑名单（**当前为空**）。
- **保存 ≠ 落库**：`保存: OK` 只代表编辑器跳转出 `appmsgid=`，必须**另起进程**回读（`wechat_verify_refresh.py`）。

#### 4.8.4 头条纯 API 三件套

- **建稿**：`POST /mp/agw/article/publish`，body **必须 form-urlencoded**（JSON 发 HTTP 200 但不生效），`save=0` 就是存草稿。
- **封面**：`/spice/image` 两级——① multipart 字段 `image` 返回 `image_uri/image_url/image_width/image_height`；② `need_cover_url=1` 拿 `cover_url`；publish body 里字段名是 **`pgc_feed_covers`**（不是 `cover_tos_url`）。
- **token**：`tt-anti-token` **只需新鲜、不必逆向**（同会话四段对照证明不绑 body、取一次可连用 >26 s）；但必须**有头 Chrome + 同一个编辑器 tab**（首页 tab 被 argus 挡）。🚨 **headless 抓到的 token 服务端不认**：页面自己的自动保存就返 `code:7050`（2026-10-18 用增强 hook 录到响应体），根因是缺 WebGL 导致反滥用凭证不合格。旧版 `verify_draft_cover` 只判「封面 uri 非空」、`cover_one_api` 又丢了 `save` 的 msg ⇒ 曾把 7050 读成「换封面成功」，现已三重把关（msg / 同 gid / expect_uri 逐字符比对）。
- 三个故障症状不同：缺 token → `7050`；缺 `Accept` → argus HTML；**body 裸塞中文** → argus HTML（必须 `json.dumps` 转义）。

#### 4.8.5 跨平台共用工具

| 工具 | 作用 |
|---|---|
| `reconcile_verdict.py` | 四平台共用四态诊断 `PUBLISHED / MISSING / RATE_LIMIT / UNKNOWN`；**判 PUBLISHED 要求双侧都读成功**（草稿箱读失败时「草稿箱无」不是证据） |
| `scan_pub_dupes.py` | 已发布 vs 草稿箱残留扫描（标题去空白精确相等；公众号附产品名级提示只列不删） |
| `verify_cover_edge.py` | 封面白边体检：右缘 4.5% 平均亮度 **>200 ⇒ 脏封面**（47 张实测：脏=255，净=47~75） |
| `verify_paywall.py` / `verify_miniprogram.js` / `verify_wxml.py` | 小程序专项守卫（见 §4.12） |

#### 4.8.6 标题与封面的三处来源（别打错）

- 标题：`xhs_title_overrides.json`（**头条/小红书共用**）→ 平台模板（`make_toutiao_title` / `make_xhs_title`）→ 台账。**override 一律带产品名**，否则 `find_case_for_title` 匹配不回 case，`refresh` 会静默跳过。
- 封面：公众号 1080×460（2.35:1）、头条 3840×2160（16:9）、小红书 3:4 卡片、B站 16:9；**不许跨平台借图**。
- 建稿路径上**任何 `[:N]` 硬切都是疑点**（「——一」残渣事故），截断面落在孤立标点或连接号后要连带清理。

### 4.9 静态站（公开面）

`build_static`（5 步）→ `prerender` 内置；同源不同 flag：

| | `dist/`（默认） | `public/`（`--no-inbox`） |
|---|---|---|
| 定位 | 本地预览，**含未核实素材** | **对外发布版**，上传 OSS 的那一份 |
| `data.json` | 863 KB | **1.23 MB** |
| 文件数 / 体积 | — | **271 个 / 24 MB** |

`data.json`/`data.js` 双写（后者为了 `file://` 双击能看）；每案例一个预渲染静态页 `case/<id>.html`（**共 256 页**：255 案例 + 索引；内联样式、不引 app.js、正文 ≥600 字、CI 独立复检）；`sitemap.xml` 只在配了域名时生成；自检包含「无绝对路径引用」。

### 4.10 部署

`release.py` 六步流水线：**contentpack → publish → fit → score → build → deploy**，`after` 字段是**有约束力的依赖声明**。

- **零依赖自签名 OSS 上传**（hmac-sha1，不用 oss2/ossutil）；Region **必填且刻意不兜底**。
- **inbox 硬闸门三态**：0 放行 / 正数拒绝 / **None（读不到）也拒绝**（真实事故：2026-09-20 把 441 条未核实素材推上公网）。
- 每个 PUT 都带 `x-oss-acl: public-read`；上线后**不带签名 GET 一次首页**自检。
- 缓存：`data.json/index/case/*` → `no-cache`；`app.js/style.css` → `max-age=300`。
- **全量覆盖、无 `?v=` 版本化、远端孤儿文件需手动删**；失败即停，返回步骤序号作为退出码。
- **日常部署走 `auto_deploy.py`**（每日 21:00 自动任务）：SHA256 逐文件比对 `public/` 与线上，`generated_at`/`stats.inbox` 归一化后再比；有差异才构建+部署，之后跑 `verify_deploy.py` 语义校验。**它不是发布主路径，但它是唯一带观测的路径。**

### 4.11 CI 与分支拓扑

- `ci.yml`（`contents: read`）：PR + push main，多版本 Python/Node 矩阵，`selftest`（**当前 122 项全绿**）→ 单测 → `release --selfcheck` → 构建静态站 → `prerender --check` → 断言 `data/` 干净。不部署。
- `harvest.yml`（`contents: write`）：每天 UTC 01:00（北京 09:00）+ 手动，「采集 → 内联校验 → 推 `refs/heads/data` → `git reset origin/main`」。不部署。
- **为什么独立的 data 分支**：机器提交直接进 main 会让本地每次 push 都要 rebase；分分支后 main 只承载人工改动。

### 4.12 小程序「远镜拆解」（v3 新增）

- **形态**：内容收费。深度报告 **¥6.60/篇**、全站导出 **¥19.90**、月会员 **¥12**。
- **结构**：5 个页面（`index / case / rank / report / profile`）+ 6 个云函数（`get-cases`、`export-cases`、`pay-order`、`pay-notify`、`query-order`、`sync-data`；`pay-notify`/`sync-data` 为 HTTP 触发型）。
- **数据链路**：`data/cases.json` →（`sync_cases_cloudbase.js`，`mapCase()` 是唯一契约）→ CloudBase `cases` 集合 → `get-cases`。改案例必须重跑同步。
- **付费墙**：`get-cases` 做 FREE/PAID 双层白名单，服务端 `canReadFull()` 校验，PAID 未购不返回（实测教训：免费接口曾把导出 17 列全给，导出/报告门形同虚设）。
- **虚拟支付**：`paySig = HMAC(AppKey, uri+'&'+body)`、`signature = HMAC(sessionKey, body)`、`post_body` 逐字节一致；`order.status = 2` 已支付待发货。**个人主体开不了虚拟支付**（后台判定「企业/事业单位」），必须换**个体工商户**；主体变更后 AppID 不变，备案跟随 AppID。
- **时序红线**：备案审核期间绝不做主体变更。顺序：备案通过 → 办执照 → 变更 → 开虚拟支付 → 认证。
- **成本**：执照免费 + 变更 ¥300 + 认证 ¥30/年 + 公证 0~500 ⇒ **¥330~830**。
- **守卫**：`verify_miniprogram.js`（37 断言，数据契约对齐）、`verify_paywall.py`（白名单边界）、`verify_wxml.py`（编译期语法，曾因 `wx:elif` 缺值没被任何工具编译过）。
- **命名**：注册名「**远镜拆解**」（单名 2 字有驳回风险）；简称同填；简介 ≤120 字（中文/中文标点/`·` 各算 2 字符）。
- **CloudBase 免费环境**发布后第 15 天销毁，只能开发测试；真上线升个人版 ¥19.9/月。

### 4.13 公开站前台（7 视图）

精写案例（三档分块）/ 综合排行（四象限 + 木桶榜 + 双轴条 + 五维）/ 移植排行（六维小条 + 硬伤）/ 候选池 / 采集队列 / 信息源 / 方法论。

- 卡片：核实等级 badge + 主金额 + 右上角三件套 `个/国/综` + ⚠ 硬伤 + `修正 N`。
- 详情抽屉：14 个信息块，含口径说明警戒条、**「核实修正 · 别抄错」**、可复刻度四维条、来源可点回原文。
- 排序 8 项；筛选＝搜索 + 核实等级 + 模式芯片 + 按分筛选（只选维度自动补 ≥70）。
- **数据健康度横幅**：公开交代三档条数与「待核 N 条」，并劝读者引用前自己再搜一遍。
- 引流只出现在顶部条 / 抽屉底 / 页脚三处，不插进卡片流。

---

## 5. 非功能需求

| 项 | 约束 |
|---|---|
| 依赖 | **零第三方库**。Python 标准库 + 原生 JS；CDP 客户端手写 WebSocket；OSS 签名手写 hmac-sha1 |
| 可达性 | 静态站可用 `file://` 双击打开（故需 `data.js` 兜底） |
| 端口 | 公开站 127.0.0.1:5052；后台 127.0.0.1:5053（**只绑回环**）；公众号 CDP 9222；头条/小红书 CDP 9223 |
| 安全 | 后台口令 PBKDF2 + HttpOnly cookie + HMAC token + 连错 5 次锁 5 分钟；公开站 API 用 **404 而非 401**；`human_read` 时间戳由服务端铸；**没有 do_DELETE** |
| 幂等 | harvest / build / deploy / publish 皆可重复跑；Promote 是唯一非幂等写入，失败在写盘前返 409 |
| 性能 | 公众号全量发布数小时（每篇封面开销，`--cover-api` 后单篇省约 3 分钟）；255 案例 → 256 页预渲染，`public/` 271 文件 / 24 MB |
| 运维 | 每日 21:00 自动部署检查；255 案例时 auto_deploy ≈25 min、verify_deploy ≈12 min，**必须走受管后台任务**（前台 120 s 会被 SIGTERM） |
| 浏览器 | 自动化 Chrome 必须有头（头条 token 靠编辑器自动保存触发；公众号封面裁剪弹窗无头点不着）；**必须用「启动器 + 长驻」保活**，否则父进程退出即被回收 |

---

## 6. 本期更新（v2 → v3，2026-09-25 ~ 10-18）

| # | 变更 | 动机 |
|---|---|---|
| 1 | **四平台分发打通**（B站 / 头条 / 公众号 / 小红书），各自台账与命令 | v2 只有公众号一条线 |
| 2 | **头条全面 API 化**：建稿（21 字段 form）+ 封面两级 `/spice/image` + 删除 + `refresh` 存量刷新 17 篇 | 「只能 UI」是旧结论；API 后单篇秒级 |
| 3 | **B站 API 化**：`draft/add` 带 id 即更新、`x/space/article` 读已投稿 | 秒级完成，不用开编辑器 |
| 4 | **公众号删除接口纠错**：`operate_appmsg?sub=del`（`action=del` 恒 200009 是猜错接口身） | 页面 hook 抓真实流量 |
| 5 | **封面白边事故定位**：CDP 截屏未设 device metrics ⇒ 右侧白边 + tag 被裁；新增 `verify_cover_edge.py` 量化体检，47 张线上封面全量修复 | 产物尺寸正常，肉眼与尺寸都发现不了 |
| 6 | **跨平台四态对账** `reconcile_verdict.py`（PUBLISHED/MISSING/RATE_LIMIT/UNKNOWN），判 PUBLISHED 要求双侧读成功 | 误判比不判更坏 |
| 7 | **已发布残留草稿扫描** `scan_pub_dupes.py`，四平台一键清 6 条 | 群发后稿件离箱，草稿箱残留没人管 |
| 8 | **小程序「远镜拆解」**：内容收费 + 付费墙双层白名单 + 虚拟支付签名 + 三项守卫 | 新增移动端收入面 |
| 9 | **标题截断残渣修复**（`——一` 事故）+ override 一律带产品名 | 服务端不会帮你收拾截断残渣 |
| 10 | 案例 39 → **255**，候选 19 → 1，采集队列 400 → 280；`tier` 分布 premium 238 / standard 11 / backup 6 | 采集层五源改造 + 批量晋升 |
| 11 | **公众号 API 结论复核**（2026-10-18）：订阅号文档区实测，草稿箱接口文档标 ✔ 但未认证个人号实测 48001；发布/群发仅认证企业主体 | 用户要求「再探测一次」；结论：CDP 仍是唯一通道 |

---

## 7. 已知问题与技术债

**已解决（v2 清单里划掉）**

| 原优先级 | 问题 | 现状 |
|---|---|---|
| ~~P0~~ | 9 条新案例缺 `china_fit`/`solo_fit`/`why_it_works`/`playbook` | ✅ 255 条全部齐备（脚本核查 0 缺失） |
| ~~P1~~ | 封面变小 / tag 丢失 | ✅ 根因（截屏视口）已修，47 张封面复检全净 |
| ~~P1~~ | 6 条 320003 损坏草稿 | ✅ 黑名单机制 + 当前名单为空 |
| ~~P3~~ | 标题截断残渣 | ✅ `_truncate_title()` + 回归锁 |

**仍存在**

| 优先级 | 问题 | 影响 |
|---|---|---|
| **P1** | 前端不渲染 `verification_downgraded` / `source_kind` / `tier_reason` / `quality_score` / `caliber`（`static/app.js` 中 0 处引用） | 「曾被机器降级」等诚实度卖点对读者不可见，透明度未打满 |
| **P1** | 公众号草稿列表窗口只拉最近 50/60 篇，`refresh` 按标题反查 | 老草稿既不能补封面也不能刷新；`appmsgid` 缺失条目只能手工定位 |
| P2 | 公众号编辑器 DOM 选择器是硬编码实测值（`cover_input`/`save_draft` 仍为 `None`） | 微信改版会全线失效 |
| P2 | `deploy_oss` 全量覆盖、无版本化、孤儿文件不清理 | 删文件需手动 |
| P2 | 台账靠半自动回写：群发后 `status=published` 需跑 `sync` | 四平台都存在「已发但台账未更新」的窗口 |
| ✅ 已解决 | 头条「群发」与「给已有草稿换封面」仍 UI | 2026-10-18 打通：群发=`save=1`+`entrance=main`（同一端点）；换封面=`cover --api`（带 `pgc_id` 更新）；另补 `unpublish` 删已发布文章 ⇒ **六项全通** |
| P2 | 小红书**草稿**只在浏览器 IndexedDB，换机器即丢；**创建笔记**被风控拦死（461/verifytype=124） | 草稿只能同 profile；创建只能走 UI；读列表已改用 `xhs_webapi.py`（秒级）；**删除已可纯 API**（`xhs_delete.py`） |
| ✅ 已解决 | 小红书台账滞后（线上 9 vs 本地 2） | 2026-10-18 `xhs_sync_ledger.py --apply` 回填，重复发布已标注 |
| ✅ 已解决 | 小红书 prosp 重复发布两篇（12:09 / 22:14） | 2026-10-18 `xhs_delete.py --apply` 删 22:14 一篇，留原发；线上 9→8，台账同步 |
| P3 | `README.md` / `公众号-发布计划.md` 数字过期（写 24 条案例） | 文档误导 |

---

## 8. 下一步 Roadmap

1. **小程序上线**：按「备案通过 → 个体户执照 → 主体变更 → 开虚拟支付 → 认证」时序推进；先跑通真机支付闭环。
2. **把透明度字段放到前台**：详情页展示「本条等级曾由 X 降为 Y，原因…」、`source_kind` 徽章、`tier_reason`，把后台规则变成读者可见的信任凭证。
3. **台账自动化**：四平台发布后自动回写 `status/group_sent`，把 `reconcile_verdict` 从「事后诊断」变成「发布后自检」。
4. **公众号窗口修复**：`appmsgid` 全量回填 + 草稿列表分页拉取，消除 `refresh`/`fix-cover` 的静默跳过。
5. **主体升级后再谈 API**：若将来有企业/个体工商户主体，优先认证**服务号**——这才是 `draft/add + freepublish` 全链路 API 化的唯一路径；届时先跑 `scripts/probe_wechat_openapi.py` 自检。
6. **README 与 PRD 同步**：以本文数据为准刷新 README 条形数字与发布计划模板。

---

## 9. 术语表

| 术语 | 含义 |
|---|---|
| 三级漏斗 | inbox → candidates → cases |
| 门槛三态 | yes / no / unknown，一道成立就放行 |
| 三道门槛 gates | 仍在运营 / 是真实生意 / 有个人路径 |
| 精品池 / 实核池 / 备选池 | premium / standard / backup，由是否有一手来源决定 |
| 内容包 | 入库前必须补齐的人工写作字段（否则可能造成空壳案例） |
| 篇号注册表 | `issue_numbers.json`，一次性分配的历史编号 |
| `--no-inbox` | 对外产物必须加的 flag，防止未核实素材跟着上线 |
| 四态对账 | PUBLISHED / MISSING / RATE_LIMIT / UNKNOWN；判 PUBLISHED 要求双侧读成功 |
| 白边脏封面 | 右缘 4.5% 平均亮度 >200 的封面（截屏视口被裁的产物） |
| `tt-anti-token` | 头条写操作的 header 级凭证；只需新鲜、不绑 body |
| 48001 | 微信「api unauthorized」；未认证账号调草稿箱/发布接口的返回 |
| partial | 公众号草稿已建但收尾（封面/原创）没完成 |
| 远镜拆解 | 小程序注册名；内容收费形态 |

---

*本文描述的规则以代码为准：`scripts/verify_rules.py`（判定）、`server.py`（闸门）、`scripts/harvest.py`（采集）、`scripts/wechat_publish.py` / `toutiao_publish.py` / `bilibili_publish.py` / `xhs_publish.py`（分发）、`scripts/reconcile_verdict.py`（对账）。*
