# 拆解海外 · Overseas Teardowns — 产品设计文档（PRD）

> 版本：**v4.0**　日期：**2026-11-05**　状态：**现行**（上一版 v3.9 / 2026-11-05）
> 适用仓库：`oracis/ai-case-library`　线上：https://case.ydtgo.top
> 配套图：`docs/产品流程图.html`（同一份事实的可视化版本）
> **v4.0 是案例库真值从 JSON 迁进 SQLite 版**（2026-11-05）：
> ① **真值换库**：案例库本体从 `data/cases.json` 搬进 `data/ledger.db` 的
> `case_library` 表（与发布台账同一个库），`data/cases.json` 降级为**自动导出的镜像**，
> 每次写库后重导、继续进 git 供 diff 评审。新增 `scripts/case_store.py` 统一存取层
> （`load_cases/load_by_id/get_case/save_cases/export_mirror`）。
> ② **另起一张表而不是改老表**：老 `cases` 表里 501 行（479 条 `type='case'` + 22 条
> graveyard）而 `cases.json` 只有 461 条 —— 它早就是**陈旧镜像**，直接当权威等于把
> 18 条脏数据转正；且老表是「三份 JSON 合并」的设计，写方漏一个 `WHERE type='case'`
> 就写到别人的行上。`platform_posts` 的外键指向 `cases(id)`，SQLite 改不了外键，
> 所以老表只能并列保留（历史归档 + 外键锚点），不能改名复用。
> ③ **差点整批判丢的 14 个字段**：`china_fit`/`solo_fit`/`composite`（两张人工评分表，
> 461 条全有）、`cover_desc`（封面文案链，45 条）、`segment`、`needs_review`、
> `human_read`… 老表结构里根本没有，而 `upsert_case()` 的规则是「未知字段忽略」
> ⇒ 第一次写库就会无声消失。补列之后 `upsert_case()` 现在**对未知字段发警告**。
> ④ **逐字节镜像**：新增 `field_order` 列记住每行原 JSON 的键序，导出按 rowid 序 +
> 原键序还原、剥掉 `type/deleted/created_at`、不补末尾换行 ⇒ 迁移后的
> `data/cases.json` 与迁移前**字节相同**，`git diff` 一个字都不动。

> **v3.9 是「把封面和金额这两件事从注脚变成判据」版**（2026-11-05）：
> ① **封面回读闸**：`⛔ 本地有 PNG ≠ 平台侧封面已设置` 这句在 PLAYBOOK 里挂了很久，
> 但一直没有判据。新增 `scripts/case_store.py、scripts/cover_verify.py` + 台账 `data/cover_readback.json`，
> `publish_guard` 加第 ⑤ 道 `check_cover_remote()`、`publish_one` 九平台全加 `cover_remote` 闸。
> 关键结论：**九平台里只有 4 个真能回读封面**（B站/CSDN/腾讯云/阿里云），
> 公众号脚本压根不上封面、头条与小红书没有独立封面字段、博客园与知乎连封面逻辑都没有 ——
> 所以状态是**五态**（`ok`/`empty`/`unknown`/`local`/`none`），只有 `empty` 能拦，
> 「没查过」永远不算通过。顺带补掉阿里云两个静默洞（`coverUrl` 复核只打印不判定、
> firstImg 上传失败静默跳过）。见 §6.1 第 56 行。
> ② **数据时点**：用户指出金额「不准确、是动态的」，标题已全禁金额，但正文数据卡仍
> 只印数字不印时点 ⇒ 生成器加一行 `数据时点 ｜ 截至 YYYY-MM-DD（抓取日快照）｜口径：…`，
> 发布闸加 `数据缺时点` warn。判据首版把「2026-10-14 同步」全判成缺时点
> （草稿 17 篇全误报、产物 261 篇里 202 篇误报），校准后真缺的只有 shipfast 两篇草稿。见 §6.1 第 57 行。
> ③ 顺手把一条**过期待办销掉**：`--scope all` 的「10 篇同类模板」在 11-04 的 money
> 那轮之后已经做完（`--check --scope all` 待生成 0、`--verify --scope all` 错误 0）。
> v3.8 把内部黑话从数据 214 处 + 产物 39 个文件里清干净，并给发布闸加了 `内部黑话` block（#55）；
> v3.7 把「标题 = 产品说明、九平台禁金额」立成硬闸并清掉 77 条带金额标题（#54）；
> v3.6 用 LLM 彻底去掉 175 篇公众号标题的机械模板（#53）；v3.5 清理 175 篇未发布产物的
> `<h1>` 口径旁注（#52）；v3.4 九平台调性分化 + 存量稿批量改写（#47–51）；v3.3 发布幂等 + 内容质量加固。
> 本文描述的是**代码当前实际实现的状态**，不是设想。数据截至文首日期（2026-11-04 只读核）：
> **案例 461 条 / 候选 0 条 / 采集队列 278 条 / 归档 1404 条**；
> 发布台账真值 = SQLite `data/ledger.db` 的 `platform_posts` **363 条、0 组 `(case_id, platform)` 重复**
> （`python -c "sqlite3 ... group by ... having n>1"` 实测）。
> 注意「条数 ≠ 篇数」：同一案例允许**跨平台**分发，但同一 `(case_id, platform)` 只允许一条记录；
> 头条/B站历史上有同一案例发过新旧两篇的情形，旧篇标题记在 `note` 的『…』里，巡检认这个别名。

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
inbox.json     采集队列  机器写   原始素材   279 条（上限 400，溢出归档不删，归档留底参与去重）
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

**发布幂等与守卫（v3.3，2026-11-04）**：`data/ledger.db` 的 `platform_posts` 是发布记录唯一真值表
（363 条，唯一索引 `ux_posts_case_platform`）；`scripts/publish_guard.py` 是**只读**判据源
（幂等键 / 标题上限 / 内容形态 / 封面），由 `publish_one.py` 的四道新闸调用，也可独立体检：
`python -X utf8 scripts/publish_guard.py --scan-titles` 或 `--platform X --case Y --file <md>`。
⚠ `data/longtail_sync.json` 的键是**建稿批次** `<case>-<日期>-<序号>`，不是幂等键 ⇒
只能当兜底证据、不能当去重真值（2026-11-04 实测 43 条里 12 组重复标题，chessigma 在
tencentcloud 记了 4 个批次）。JSON 台账与 SQLite 双写口径见 §6.1 #32 / #42。

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

保护与取舍：跨**四张表**去重（`inbox / candidates / cases / inbox_archive`，单一入口 `harvest.seen_keys()`，同时比 id、归一化名、`trustmrr_slug`；名称归一化统一走 `text_clean.norm_name`，triage 同名导入 —— **漏了 archive 就会「溢出归档 → 下一轮原样采回」**，实测 19 条 id 回流）；`http_json` 重试 3 次 + `1.5×(n+1)` 退避 + 显式绕过系统代理；IH 1.5s / ARR 2.0s 节流；单页抓取有 16 MB / 150 s 双重兜底；`inbox` 容量 400，溢出进 `inbox_archive.json`（**只进不出、永不删除**）；金额 < $50 当行价丢；MRR 低于 100 的 TrustMRR 条目丢；提问帖/Ask-Tell 类 HN 丢；`--tm-seeds` 可把外部线索站当发现面。

> 组装口径单一化（v3.1）：四路来源的条目字段由 `_entry_skeleton` + 四个 builder 统一产出，`main()` 只负责去重、计数、排序。等价性用前后快照 diff 验证（五源 fixture 逐字段 + 简报全等），不靠肉眼比对。

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

- **标题**：手工表优先（`TITLE_OVERRIDES`）→ LLM 覆盖表（`wechat_title_overrides.json`）→ 模板产物并在 md/html 里插 `[!] 标题由模板生成，发布前请手改`；`sanitize()` 会改敏感词（月入 → 月营收）。**标题是产品说明，不含金额**（`publish_guard.TITLE_MONEY` 硬闸，见 #54）；`|` 拼接、工商简介句、模板腔都是 block 形状。
- **排期**：`virality_score = 纠错数×2 + 金额量级 + 象限 + EDITORIAL_WEIGHT`。
- **占位文案清理**：`text_clean.clean_placeholders` 只动会外显的 `one_liner`/`what_it_does`/`corrections[].claim`；判空用 `[0-9A-Za-z\u4e00-\u9fff]`（真实事故：曾把正常句尾的「。」吃掉）；尾部清理只收连接符 `，,、：；;·-—`。

### 4.8 四平台分发（v3 新增，替代 v2 的「公众号管线」单节）

#### 4.8.1 能力矩阵（2026-10-17/18 实测）

| 平台 | 通道 | 读 | 写 | 删 | 已发台账（另有草稿） |
|---|---|---|---|---|---|
| **B站** | API 读写 + **UI 发布** | ✅ `x/space/article?mid=` 已投稿 | ✅ `draft/add`（带 id 即更新；`submit=1` 也**只存草稿不发布**） | ✅ `draft/delete` | **11**（线上 13 篇；⚠ 草稿无台账文件） |

> B站真发布走编辑器 UI 的「发布」按钮（`bilibili_publish.py send-draft`，
> 2026-10-20）。此前认为「headless 下恒 disabled（封面校验）」是**误诊** ——
> 实测封面设好了按钮照样灰，真正原因是**当日投稿额度用完**（限 5 篇/天）。
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
- **封面第二行不读标题**：`wechat_publish.cover_desc()` 的取值链是 `cover_kv.desc > cover_desc > one_liner > what_it_does 首句`，所以改标题不必重制封面图；两条口径一致之处是**都不含金额**（2026-10-02 起封面已去掉金额）。
- 建稿路径上**任何 `[:N]` 硬切都是疑点**（「——一」残渣事故），截断面落在孤立标点或连接号后要连带清理。

### 4.9 静态站（公开面）

`build_static`（5 步）→ `prerender` 内置；同源不同 flag：

| | `dist/`（默认） | `public/`（`--no-inbox`） |
|---|---|---|
| 定位 | 本地预览，**含未核实素材** | **对外发布版**，上传 OSS 的那一份 |
| `data.json` | 863 KB | **1.23 MB** |
| 文件数 / 体积 | — | **271 个 / 24 MB** |

`data.json`/`data.js` 双写（后者为了 `file://` 双击能看）；每案例一个预渲染静态页 `case/<id>.html`（**共 256 页**：255 案例 + 索引；内联样式、不引 app.js、正文 ≥600 字、CI 独立复检）；`sitemap.xml` 只在配了域名时生成；自检包含「无绝对路径引用」「**案例 id 非空且唯一**」（预渲染按 `<id>.html` 落文件，撞 id 是后者覆盖前者，而「页面数 = 案例数」两边一起少，事后查不出来）。

**两道路径护栏（v3.1）**：`cases` 为空时 `build` **直接 return 1**（首次搭架子显式加 `--allow-empty`）。原因是覆盖写 + `auto_deploy` 的判据就是「本地与线上不一致」——空站会被当成「本地有新版本」推上线，把 255 条案例的线上站点盖成空壳，而整轮没人报错。`data/*.json` 半截时 `build_payload` 抛出的 `ValueError` 也转成人话（「多半是某个 data/*.json 写坏了」）并同样返回 1。

### 4.10 部署

`release.py` 六步流水线：**contentpack → publish → fit → score → build → deploy**，`after` 字段是**有约束力的依赖声明**。

- **零依赖自签名 OSS 上传**（hmac-sha1，不用 oss2/ossutil）；Region **必填且刻意不兜底**。
- **inbox 硬闸门三态**：0 放行 / 正数拒绝 / **None（读不到）也拒绝**（真实事故：2026-09-20 把 441 条未核实素材推上公网）。
- 每个 PUT 都带 `x-oss-acl: public-read`；上线后**不带签名 GET 一次首页**自检。
- 缓存：`data.json/index/case/*` → `no-cache`；`app.js/style.css` → `max-age=300`。
- **全量覆盖、无 `?v=` 版本化、远端孤儿文件需手动删**；失败即停，返回步骤序号作为退出码。
- **日常部署走 `auto_deploy.py`**（每日 21:00 自动任务）：SHA256 逐文件比对 `public/` 与线上，`generated_at`/`stats.inbox` 归一化后再比；有差异才构建+部署，之后跑 `verify_deploy.py` 语义校验。**它不是发布主路径，但它是唯一带观测的路径。**
- **比对与校验并发 8（v3.1）**：实测线上单文件 GET **3.7~8.6 s** ⇒ 271 个产物串行要 20~40 min、255 个案例页串行十几分钟。**慢到没人愿意跑，这道关等于没有。** 并发后 auto_deploy 全链路（构建+比对）**2 m 28 s**、verify_deploy **2 m 59 s**。三态语义（`ok / missing / error`）不变：取不到**不当作差异**（判不了就别猜），线上多出来的 `extra` 只报不部署。
- ⚠ 并发**必须配 1 次重试**（404/500 不重试）：首跑有 3 个页面 `status=0`，单独重取全部 200 —— 并发把瞬时抖动暴露成「内容坏了」，**假警报比不报更坏**。`fetch_one` 另需捕获非 HTTPError 异常（原先超时会带栈崩掉，看不出哪项没过）。

### 4.11 CI 与分支拓扑

- `ci.yml`（`contents: read`）：PR + push main，多版本 Python/Node 矩阵，`selftest`（**当前 122 项全绿**）→ 单测（**1333 项**）→ `release --selfcheck` → 构建静态站 → `prerender --check` → 断言 `data/` 干净。不部署。
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
| 幂等 | harvest / build / deploy 可重复跑；**发布自 v3.3 起有 case 级幂等**：`ux_posts_case_platform` 唯一约束 + `publish_guard.check_dupe()` 的 `dedupe` 闸（SQLite 真值 + `longtail_sync.json` 批次兜底）+ 两朵云 `remote` 闸的 published 判定（修了阿里云恒读 `tc_state` 的漏洞）。显式 `--force` 只绕 dedupe，标题/形态/封面/remote 仍硬拦。Promote 是唯一非幂等写入，失败在写盘前返 409 |
| 性能 | 公众号全量发布数小时（每篇封面开销，`--cover-api` 后单篇省约 3 分钟）；255 案例 → 256 页预渲染，`public/` 271 文件 / 24 MB；**线上比对/校验 8 并发后 2.5~3 min**（串行 20~40 min） |
| 运维 | 每日 21:00 自动部署检查；255 案例时 auto_deploy **≈2.5 min**、verify_deploy **≈3 min**（2026-10-21 并发化前为 25 min / 12 min）。⚠ 仍需走受管后台任务：前台 120 s 会被 SIGTERM 砍，且 stdout 缓冲看起来像卡死。2026-11-04 落地为激活的受管定时任务（id `b2e189f7`、rrule `FREQ=DAILY;BYHOUR=21;BYMINUTE=0`、`validFrom=2026-11-04`、状态 `ACTIVE`）：先跑 `auto_deploy.py --env-file .env`（比线上新才传），再跑 `verify_deploy.py --base https://case.ydtgo.top`（30+ 项断言）。**2026-11-04 实测三份日志**：15:11 全量部署 478 文件 / 0 失败 / 匿名 GET 首页 200（`tmp/_auto_deploy_1104.log`）；15:19 `verify_deploy.py` 全部通过、461 页 / 461 条（`tmp/_verify_deploy_1104.log`）；16:19 复跑兜底报「线上与本地一致 —— 不需要部署」（`tmp/_auto_deploy_test_1104.log`，印证幂等）。凭证来自环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET`（`.env` 只放 bucket/region，且被 gitignore） |
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
| 10 | 案例 39 → **255**，候选 19 → 1，采集队列 400 → 280（v3.1 时 279）；`tier` 分布 premium 238 / standard 11 / backup 6 | 采集层五源改造 + 批量晋升 |
| 11 | **公众号 API 结论复核**（2026-10-18）：订阅号文档区实测，草稿箱接口文档标 ✔ 但未认证个人号实测 48001；发布/群发仅认证企业主体 | 用户要求「再探测一次」；结论：CDP 仍是唯一通道 |

### 6.1 v3.0 → v3.9（2026-10-21 ~ 11-06，工程加固 + 口径/来源补证 + 发布幂等 + 调性分化 + 标题清理 + LLM 去模板化 + 全局禁金额 + 内部黑话清理 + 封面回读 + 数据时点 + 候选池存取收口 + 晋升后评分补齐）

v3.1 的八条落在「拿数据 → 核 → 入库 → 建站 → 上线」这条线的正确性与速度上；v3.2 的四条落在**数字本身可不可引用**上。每一条都在真实数据上复现过才动手。

| # | 变更 | 复现证据 / 动机 |
|---|---|---|
| 1 | **采集去重补上 `inbox_archive`**（`seen_keys()` 单点） | 19 条 id 同时在 inbox 与 archive（`archived_at` 09-17、`added_at` 10-13），另 35 条只是大小写/标点差异。队列 279/400，再采几轮就进「溢出 → 重采 → 再溢出」，新素材被老条目挤出去，`last_harvest.added` 还虚高 |
| 2 | **采集组装抽成 `build_entry`** | 四路来源各抄一遍 14 个公共字段共 200 行；顺手消掉隐性依赖（`metrics` 是分支内局部变量，「榜单」分支根本没定义） |
| 3 | **线上比对 / 案例页校验并发 8**（+1 次重试） | 单文件 GET 3.7~8.6 s ⇒ 20~40 min / 十几分钟，慢到没人跑等于没有这道关。并发后 2 m 28 s / 2 m 59 s |
| 4 | **`build_static` 空 cases 硬拦** + 案例 id 唯一性自检 | 空站会被 auto_deploy 当「本地更新」推上线，把 255 条案例全盖掉 |
| 5 | **自检与真实数据隔离**（四条连环坑） | ① `rmtree` 撞沙箱批量删除门禁 + 抛在结果汇总前 ⇒ 每轮 `exit=1` 且吞掉结论；② 前台超时 SIGTERM 让 `finally` 不执行 ⇒ 固件留进真实 `data/`，下一轮把脏状态当基线备份，污染永久固化；③ 假 key（`sk-selftest-*` / `test-model`）同路径顶掉真实智谱 key，全量红在 `test_ai_verify`；④ `[4]` 节直接拿 `inbox[0]` 转候选 ⇒ **实测丢过一条真素材且救不回**（`inbox.json` 不进 git）。修法：常驻备份目录 + `.running` 标记 + 信号转 SystemExit + `ORIG_SECRETS` 原字节还原 + 队列固件 |
| 6 | **写盘统一 `scripts/atomicio.py`** | 此前 4 处各写各的 tmp+replace 且无 fsync；`fetch_tm_md` 缓存干脆没做原子写，抓取被打断就留半截档案当有效缓存（判据改为「长度 + 官方页脚 `## Related TrustMRR Pages`」） |
| 7 | **队列顺序统一**（`multiplatform/queue.py` 的 `newest_first`） | 头条/小红书 `pending_cases()` 用 `[::-1]` 而注释写「最新在前」，实际语义是反的；抽成单函数后两平台共用一套口径 |
| 8 | **`make_article.money_scale` 口径修正** | 原取「所有 metrics 数字的最大值」，实测 215/255 条的最大数来自 `all_time`（累计），影响 `--top` 排序。改为只读 `mrr / last_30d_revenue / arr` 并统一年化 |
| 9 | **`promote_to_candidates` 按分数选稿** | 空索引让 `duplicate_of` 三条判据成死代码（潜在洞，当前数据无行为差异）；`pick_targets()` 三个硬条件后按 score 降序、同分按 id 兜底 |
| 10 | **`fill_caliber.py`：口径按证据补，不按猜** | 24 条缺 `caliber` 一直挂着 must。判据四级（note 明写 run-rate → headline 口径词 → 数字与 metrics/TrustMRR 档案互证 → note 措辞），否定句排除（checkvibe 的「不宜换算成 ARR」曾被误读成 arr）。写回 22 条：mrr +13、arr +4、last_30d +2、lifetime +2、run_rate +1；`test_fill_caliber.py` 19 项 |
| 11 | **新增第八个口径 `annual`**（数据契约变更） | `marc-lou-portfolio` 门面数字是「2025 全年 $1,032,000」—— 按自然年合计的已发生收入，装不进 arr（前瞻）/ lifetime（跨年累计）/ run_rate（某月×12 的估计），硬塞等于把「已发生」写成「预计」。补齐后 `caliber` 空缺 24 → **1**（仅剩 bustem：榜单快照 + 档案 404，两解都成立）。同步改 `verify_rules.CALIBERS`、`verify.py` 清单、AI prompt 枚举、`miniprogram/utils/fmt.js`、`DATA_SCHEMA.md` |
| 12 | **`independent_source.py`：36 条单来源案例补第二来源审计** | 全库 188/255 只有 2 条来源、其中 36 条只有 TrustMRR 一条。判据只用**实体锚点**（App Store `sellerUrl` / GitHub 仓库 `homepage` 与官网同域），关键词匹配不可用（实测 `mort`→真人快打、`angel-match`→猫咪消消乐）。结果：第三方确认 **9** / 仅自述 16 / 连不上 11，写回 10 条 `sources` + 36 条 `independent_check` |
| 13 | **CSE 检索通道可配**（secrets.json + 独立代理） | cn.bing 对这批目标（海外小体量 SaaS）是负收益：`gtm.help` → 知乎/MC 百科/搜狐，`AEO Checker` → 百度百科「AEO 认证」+ 海关总署。改为 HN Algolia → Google CSE → DDG → cn.bing（默认关）。⚠ 2026-10-21 实测本机出口：`www.googleapis.com` 直连 000、走 1792 代理 000、走 Shadowsocks 10808 也 000 —— **key 齐了还需能出 Google 的代理**，故另设 `google_proxy` 单通道代理，不动全局 `HTTPS_PROXY`（那层一改，cn.bing 与官网抓取反而断）。**⚠ 2026-10-29 用户决定取消该通道**：CSE 要求 Google Cloud 项目开通结算 + 给 key 单独授权，实测恒返 403「This project does not have the access to Custom Search JSON API」，且出口成功率仅 2/5；补源改回 HN Algolia / DuckDuckGo 两路，`search_google`、`probe_google`、`check_google_cse.py` 一并移除 |
| 14 | **云函数双身份（openid/uid），服务订阅站 Web 壳**（M2，配合新仓 `yuanjing-web`） | Web SDK callFunction 语境没有 OPENID，`getWXContext().OPENID` 为空 ⇒ 云函数无法识别 Web 用户。方案：`deliver.js`（四处同步，`verify_paywall.py` md5 守卫）新增 `resolveCaller()`——OPENID 优先，Web 语境走 `@cloudbase/node-sdk` 的 `getEndUserInfo` 拿 uid（**懒加载**，小程序路径永不 require，pay-order/pay-notify 不加该依赖）；`getEntitlements`/`deliver` 用户查询改 `_openid OR uid` 双查；`get-cases` 新增 `{bind_uid}`（仅小程序语境，把 Web 匿名 uid 绑进 users 文档，两端权益互通）。pay-order/pay-notify **不改**（下单身份来自 code2Session、发货身份来自订单记录）。回归 verify_paywall 13 项全过 |
| 15 | **get-cases 新增 `{subscribe:{email}}` 周报订阅动作**（M3，配合 `yuanjing-web`） | 落地页表单需要持久化订阅者。**不走集合直写**（要给匿名用户开 subscribers 集合写权限，等于放垃圾注入进来）：云函数侧做格式校验（`^[^@\s]+@[^@\s]+\.[^@\s]+$`，≤200 字符）+ email 去重（where count）后写 `subscribers` 集合，字段 `{email, source, created_at}`。不要求登录态。`subscribers` 集合权限保持默认全关（仅管理端读写），云函数是唯一写入口 |
| 16 | **export-cases 新增 `{filter:{tier,verification,quadrant}}` 服务端筛选**（M4，配合 `yuanjing-web`） | 订阅站导出页要筛选导出。为避免「下载全量再本地筛」造成筛选口径漂移，筛选放服务端：仅白名单三键，`'all'`/空 = 不过滤，非法值静默忽略；与 Web 端 `yuanjing-web/site/js/csv.js` 的 `applyFilter` 同语义（键变更两边同步）。权益硬校验不变（canReadFull 先于筛选执行，无权益直接 40301） |
| 17 | **新增反向样本表 `data/anti_cases.json` + 选案例手册 `docs/SELECTION_PLAYBOOK.md`**（数据契约变更） | 45 条 backlog 的四道筛子只活在一次对话和一份临时报告里，下一批会推出不同结论；同时 33 条 T3 被措辞成「剔除」，把「剔除出可抄清单」与「剔除出选题池」混成一件事。逐条核完 33 条页面的「💰 月收入」后：**只有 1 条（picturethis）名副其实**，其余 19 条是 ARR、4 条年营收（读成月收入放大 12 倍）、4 条融资额、1 条 GMV、1 条定价、2 条口径对不上、1 条年/月不明。**不进 `cases.json`** 的三个理由：①33 条里 20 多条是活得很好的公司（lovable 估值 ¥443.52亿），进推荐位是事实错误；②`china_fit`/`solo_fit`/`composite` 按「值得做」排序，给它们打低分只是在排行尾部堆 33 条无信息量条目；③`verify_deploy` 拿 `curated` 计数与线上比对、`score_*` 缺表会静默清空评分。落地：`scripts/build_anti_cases.py`（`JUDGE` 表驱动，缺表直接报错不静默跳过）、`scripts/test_anti_cases.py` 17 项（含 id 不许与 cases 相撞、anti 不许进篇号台账）、`wechat_publish.py build --anti --case`（摘要走 `summary` 回退链、不写 `issue_numbers.json`）。顺带复核出 `htm_backlog_caliber.json` 5 条与页面原文不符（rox / crustdata / tjm-labs / bland-ai / sanas），已在新表 `caliber_conflict` 留痕，旧表不回改 |
| 18 | **反向样本区：按门槛类型分 7 篇 + 上站**（数据契约变更 `anti_cases/1`→`/2`） | 33 条按 `role` 只有三类、`structural` 独占 21 条 —— 一篇一万五千字，读者只记得「他们都很有钱」，正是手册 §2.2 点名的陷阱。改为按**门槛类型**切 7 篇（账单 6 / 人头 4 / 苦力 5 / 社区 3 / 关系 3 / 资格 7 / 数字 5），每条必归篇、篇条数与 `VOLUME_COUNTS` 不符则脚本直接退出（旧的 `report_group` ①②③④ 下线，两份分组并存必然漂移）。上站方式：`/api/data` 加 **`anti` 独立键**（`{cases,volumes,built_at}`）+ `stats.anti`；前端 `#anti` 视图（chips 只放短名，全名留篇头）；预渲染 `anti/index.html` **一页**（进 sitemap、不引 app.js、不落 `case/` —— `verify_deploy` 断言「case 链接数 = curated 数」，塞进去会让「没少案例」这条唯一判据失效）；`build_static` 自检加三道锁（anti id 不混进 cases / 页面缺条目 / 篇条数不符）。顺带改正 `lovable` 的 `caliber`（annual→arr，其 `caliber_note` 原文就是 ARR）与移动端底部 tab 的 `width:100%` 老 bug（每项占满整条栏，9 个 tab 只能一次看到一个）。测试：`test_anti_cases` 17→20、`test_prerender` 54→68、`test_build_static` 49→56 |
| 19 | **反向样本 30 条正文按门槛类型落成 7 篇** | 33 条里已单写 3 条（lovable / medvi / crowdreply），剩下 30 条按篇合并：Ⅰ账单 6 / Ⅱ人头 4 / Ⅲ苦力 5 / Ⅳ社区 3 / Ⅴ关系 3 / Ⅵ资格 7 / Ⅶ数字 5。每篇一条论证线（不是逐条罗列），共 18337 字，落 `out/articles/anti-{i..vii}-*.html`；正文源在 `scripts/gen_anti_articles.py`（段落元组 → HTML，改稿改元组，样式串只有一处定义，避免漏闭合标签）。三条纪律：**标题禁金额**；**每篇必写「不是失败案例」段**（这 30 条大多活得很好，写成「做不成」是事实错误）；**所有 ¥ 数字必须能在 `data/anti_cases.json` 原文里找到**（一次性校验：7 篇 50 个金额，0 处无出处）。Ⅶ 篇给出全局口径分布作为后续六篇的读法前提：33 条里只有 1 条（picturethis）标签没写错，23 条是单位错（arr/annual，改个除法就对）、6 条是口径错（funding/gmv/price，页面上没有收入），两者混为一谈会做出两种相反的错误动作 |
| 20 | **五平台草稿：补掉 21 处「数据待补充」占位符，并把 drafts/ 纳入版本控制** | `gen_platform_drafts.py` 批量生成时，模型常在「为什么能成/方法论」「可复制性」「我的判断」三节交白卷，留下「虽然具体的数据待补充，但我们可以……」——流水线照常写文件、台账照常记 pending，一路绿灯直到推到平台上才露馅。50 条草稿里 15 个文件共 21 处。新增 `scripts/fill_draft_placeholders.py`：按行替换含占位符的行，内容全部取自 `candidates.json` 原文（不编事实），按平台调性分别改写（wechat 正式 / toutiao 资讯 / xhs 口语 / bilibili 解说），**替换段数与占位符行数不符直接抛错**——宁可不做，不做一半；幂等，重复跑是 no-op。顺带修掉一处口径冒充：`xhs/headshotpro` 把源数据明标「第三方估算、无官方确认、不作案例数字」的月流水 $280-300K 写成了「**官方数据显示**」，还把它放进了标题（同时违反「标题禁金额」）；已按 juejin 同案例的正确口径改写。另外 `drafts/` 此前**压根没入库**（不是被 ignore，是从未 add）——50 条 220KB 是 LLM 生成 + 手工补写的产物，与 `out/articles/` 同属不可重建资产，本次一并纳入；行尾按 `.gitattributes` 统一为 LF（22 个文件原本是 CRLF）。新增 `scripts/test_draft_placeholders.py` 6 项回归锁：全目录不许再出现占位符、替换内容本身不许含占位符、首行 `# ` 不许被抹（`gen_platform_drafts.py` 靠它做断点续跑，一丢就整批重生成）、所有 $/€/¥ 金额必须能在 `candidates.json` 原文找到同形数字（已做变异验证：塞回占位符测试转红）|
| 21 | **草稿标题全面去金额（40 条）+ 收入口径纠错（7 处正文）** | 承接 #20：占位符补完后做全目录体检，发现 50 条草稿里 **43 条标题带金额**，违反 #19 立的「标题禁金额」铁律（公众号金额清洗器会把标题里的数字剥掉，「从 0 到 40K MRR，这个营销工具……」被剥成病句）。新增 `scripts/fix_draft_titles.py`：只替换首行、**只对含金额的标题动手**（已合规的跳过，可反复跑，不覆盖人工改过的标题），非收入数字保留（「15 万粉受众」「三周搭建」「六年」是产品形状，禁掉会让标题失去信息量）。其中 7 条不是「带金额」而是**口径硬伤**：klar 四条写「30 天赚 130K」（€130K 是 ARR 年化）、3ak 三条写「月入 $70K」（$70K 是六个月累计，MRR 是 $17-19K）——与反向样本 33 条里 23 条「ARR 当 MRR」是同一个错。正文另修 7 处（3ak 的「六个月后月收入高达 70K」→「六个月内累计约 70K」；「月入数万」→「月入近两万美元」——无单位在人民币语境下会被读成几万，差 6 倍；klar 的「创造出价值 130K」/「30 天内赚了 130K 欧元」→ ARR 口径）。⚠ **踩坑：金额正则第一版只认货币符号，漏掉了裸写金额**（「30天赚130K」既无符号也无「万」），klar 三条全部漏网；第二版补 `赚\s*\d` 与 `\d+\s*[KM]` 后又发现中文表述「年入百万」仍未覆盖。**写这类检测正则必须拿实际语料回扫，不能凭空列模式。**新增 `scripts/test_draft_titles.py` 5 项锁（禁金额 / 新标题本身合规 / 纠错表不空不重 / 「月入·月收入·月流水」三词不许出现在标题），已做变异验证 |
| 22 | **两份未入库工作产物纳入版本控制 + 台账 title 同步锁** | `_howtomakemi采集调研.md`（4.3KB，33 条反向样本的采集依据：1446 个案例页的只读扫描分类、TrustMRR 88 条分档）与 `data/drafts_pending.json`（14KB 待推送台账，50 条草稿）此前都未跟踪，换机器即失。入库时顺带发现一处**静默隐患**：台账的 `title` 只在 `gen_platform_drafts.py` 生成时被写入一次，之后对草稿首行的任何改动都不回写——#21 改了 41 条首行，台账里 46 条 title 仍留着**带金额的旧版**。发布脚本一旦取台账 title，就是绕过「标题禁金额」铁律且不报错。新增 `scripts/sync_draft_ledger.py`（幂等，`--check` 只体检）：把 title 回写成草稿首行，并把 `file` 字段从 Windows 反斜杠统一为正斜杠（入库后要能在 Linux 上打开）。`test_draft_titles.py` 加 4 项锁（台账非空 / title 与首行一致 / title 不带金额 / file 无反斜杠），已做变异验证：注入带金额 title + 反斜杠 file，3 项断言同时转红 |
| 23 | **第六平台 CSDN 接入：纯 REST + 阿里云网关签名**（`scripts/csdn_publish.py` + `scripts/test_csdn_publish.py` 4 项） | CSDN 无官方 API；编辑器私有端点 `saveArticle` 走 bizapi.csdn.net，鉴权 = Cookie + 网关 HMAC 签名（secret 固定值，无登录探针实锤）。实测五坑：① **saveArticle 无草稿语义**，`pubStatus:"draft"` 被无视直接发表（两篇测试稿因此上了公开博客，已用删除 API 清干净）；② **日配额 2 篇按发表计**（超限 400300011；50 条积压≈25 天，须限速排期）；③ 同 payload 有时 401 有时 400300011——**见 401 先查配额别重登**；④ 探活只能空 body saveArticle（200 且不建稿），`/v1/user/info` 恒 401；⑤ 缺字段也回 200「成功」但不建稿——判成功必须看 `data.id`。回读 = `blog.csdn.net/community/home-api/v1/get-business-list`（免签名），删除 = `blog/phoenix/console/v1/article/del`（连删 3 篇实测），均已入脚本 `list`/`delete` 命令；blog 域请求过密被 WAF 521。`draft` 子命令故意报错不可用，真草稿端点待 hook（合成 click 触发不了 Vue handler）。`mp.csdn.net` 老接口与 MetaWeblog 均实锤死亡 |
| 24 | **第七平台 博客园接入：官方 MetaWeblog XML-RPC——全链路实测打通**（scripts/cnblogs_publish.py + test 5 项） | 博客园官方支持 MetaWeblog 协议，stdlib xmlrpc.client 直发——八平台里唯一零逆向零签名零风控对抗的。端点 rpc.cnblogs.com/metaweblog/<博客名>，鉴权 = 用户名 + MetaWeblog 访问令牌。publish=False 为真草稿（与 CSDN 假草稿相反）；categories 必含 [Markdown] 且 description 传 MD 原文，否则被当 HTML 解析。2026-10-28 全链路实测：login（token 验证 + 自动补 blogid=881058）→ draft 首篇 flodesk 建 post_id=23468183 → getPost 回读命中、分类正确。坑：① MetaWeblog 访问令牌与 Open API PAT 是两把独立 token（用 PAT 打 MetaWeblog 回 Fault 401，错误信息自带正确入口 i.cnblogs.com/settings#enableServiceAccess）；② 博客未开通时打端点回 ProtocolError 500，不是「token 无效」。config 落盘 data/cnblogs_config.json，台账 data/cnblogs_drafts.json |
| 25 | **第八平台 知乎草稿全链路打通（免签名！）**（scripts/zhihu_sign.py + zhihu_publish.py + test 6 项） | 🏆 关键发现：真实发文 API 走 zhuanlan.zhihu.com/api/articles/*（zhuanlan 域自有 API），鉴权只要 cookie + x-xsrftoken（_xsrf cookie），**x-zse-96 完全不需要**；www.zhihu.com/api/v4/articles POST 是 404（老资料全是旧公开 API）。两段式由编辑器 autosave hook 实测钉死（探针只填标题正文等自动保存、绝不点发布按钮）：POST /api/articles/drafts 建稿 → PATCH /api/articles/{id}/draft 写 HTML 正文；回读 = GET /api/articles/{id}/draft（裸 id 404）；删除 = DELETE /api/articles/{id}（实测 200）。2026-10-28 全链路：login（z_c0）→ draft 建 flodesk id=2098799999773570136 → 回读 title/content 一字不差。zhihu_sign.py 签名移植保留（www 域 api/v4 数据接口仍需要；自研分组加密常量表经 grep.app 8 实现交叉印证，base_str 65 字符末位死字符别修成 64）。publish 仍故意禁用，发表人工或后续评估 |
| 26 | **腾讯云/阿里云打通：CSDN 同步助手 MCP 一键双草稿（实测）**（_probe_csdn_sync.py 探针 + mcp.json 配置） | 🏆 CSDN 官方「同步助手」Chrome 扩展（sync.csdn.net，v1.2.0）+ csdn-article-sync-mcp（npx stdio v1.0.1）实测打通腾讯云+阿里云：sync_article 一键把 flodesk 同步成腾讯云(draftId=337067)+阿里云(draftId=1697817)双草稿，get_sync_task 回读双 success。7 平台登录态在线（掘金/博客园/小红书/头条/B站/腾讯云/阿里云）。实测四坑：① WebSocket 方向是**扩展为 client、MCP server 监听 3721**；② 每次新起 MCP server 进程扩展需 ~10-20s 重连，立刻调用报「浏览器扩展未连接」——initialize 后 sleep 20s；③ Windows spawn 用 shutil.which('npx')（裸 npx 是 WinError 2）；④ 当前标签页须停留在普通 HTTP/HTTPS 网页。工具 5 个（list_platforms/check_auth/sync_article/get_sync_task/upload_image_file），幂等键防重发。腾讯云同步曝光计划已提交审核（5 个工作日），过审后绑公众号自动同步。~16 平台长尾（思否/开源中国/51CTO/简书/微博/百家号/抖音/小红书图文）今后全部走此通道，不再逐个 CDP 逆向 |
| 27 | **两朵云 CDP 收尾自动化：摘要/封面/社区全填好 + 发表双门禁**（scripts/yun_cdp.py 探针 11 子命令 + yun_finalize.py 固化模块 + sync_longtail --finalize/--publish） | 🏆 实测钉死：腾讯云编辑器直达 URL `write-new?draftId=`，发布抽屉「一键提取」8s 自动填摘要、点正文图缩略图（.img-box-bd-item）synthetic click 一次设封面；阿里云 putDraft API 直写（p_csrf=c_csrf cookie，payload {title,abstractContent,content,contentRender,format,aid,coverUrl,productTags,freeTierVOS}）比 UI 稳，coverUrl 外链落库实测成功。🏆 静态挖包定案发表 API：`POST /developer/api/article/publishArticle?<csrf>&groupCode=`，payload 含 relationshipVOS:[{exId,exDomainId}]（发布子社区）——**草稿阶段平台不存社区**（putDraft/draftDetail 无字段，实验证实选中社区后 payload 依旧不含），故 finalize 阶段 UI 真实鼠标选中+台账 data/yun_finalize.json 留档，发表时复用。发表走双门禁：`--publish --i-said-publish i-said-publish` 缺一拒绝（CLI 三条拒绝路径实测）。upvoty 1696290 现状：摘要 114 字+coverUrl+社区云原生（UI 选中），发表等人工口令。红线保持：不发口令谁也点不了确认发布 |
| 28 | **`--tm-seeds` 种子必须先与 sitemap 求交：「slug 未收录」≠「档案下架」**（harvest.py `tm_pick_batch` 加 sitemap 过滤 + 新增 `unlisted` 表 + `test_harvest_sources` 4 项回归锁） | 2026-10-28 howtomakemi 增量实测：254 个种子里 **187 个在 TrustMRR sitemap 里没有同名 slug**（`cursor` / `ollama` / `openrouter` / `bitwarden` 这类明显还在的产品也在内 —— 线索站用自己的命名，跨站不一定对齐）。旧逻辑不做求交就排队抓 `.md`，服务端对不存在的 slug 同样回 404，`tm_fetch_profiles` 于是按「404/410 = 真下架」全记进 `gone`。攒下来的 **233 条 gone 与 `profiles`、`sitemap` 零交集** —— 真下架必然留有旧档案，零交集说明一条真下架都没有，全是「名字没对上」被记成了「产品下架」。后果不止脏数据：① 这些 slug 从此永不重试，发现面被静默削掉一块；② 任何「这条是不是下架了」的诊断（`audit_delisted`、人工核查）都会被这张表带偏，正是红线「判下架必须带对照组」被绕过的形态。修法：种子只在 `st["sitemap"]` 内排队；未收录的落新键 `unlisted`（语义：线索源有、本站没有），与 `gone` 分开记账。历史数据已迁移（gone 233→0、unlisted 233，备份 `data/trustmrr_profiles.json.bak-20261028-gonefix`），重跑后输出「种子中 187 个 slug 未收录（非下架，跳过）」，管线退出码 0 |
| 29 | **GUI 增量管线的 subprocess 替身必须「像 subprocess 模块」**（`gui/proc_shim.py` 独立成模块 + `htm_increment._REAL_SUBPROCESS` 私有引用 + `scripts/test_htm_increment.py` 32 项回归锁） | 🚨 2026-11-01 实测：GUI 点「采集」**100% 崩**，而命令行手动跑同一套代码没事。根因是 GUI 用 in-process 包裹（把 `htm_increment` 的 `subprocess` 整个换成自己的流式壳，好让子进程输出进日志窗口、停止键能杀它），而 10-29 那次提交给 `_python_ok()` 新增了 `subprocess.DEVNULL` / `subprocess.SubprocessError` 这类**模块级属性**访问 —— 替身只实现了 `run()` ⇒ `AttributeError: '_ProcShim' object has no attribute 'DEVNULL'`。这类故障最阴的地方在于**只有 GUI 这条路崩**，CLI 路径永远绿，没人跑 GUI 就没人发现。修法双保险：① 依赖方（`htm_increment`）的内部探测改走私有引用 `_REAL_SUBPROCESS`，不再依赖任何可被替换的模块级名字；② 替身（`gui/proc_shim.py`）用 `__getattr__` 把所有未定义属性**转发给真 subprocess**，并把「指定了 stdout/stderr/capture_output/timeout」的调用交回真 `subprocess.run`（那些调用要的是精确语义：输出丢弃、超时抛异常），只有 harvest 主流程那种「只给 cwd」的调用才走流式接管。替身从 GUI 主文件里拆成独立纯 stdlib 模块（`gui/htm_increment_gui.py` 顶层 `import tkinter`，CI 的 ubuntu-24.04 没有 tkinter，替身跟着 GUI 就没法单测）。回归锁含**变异验证**：把修复回退到崩溃版，测试精确复现同一条 `AttributeError` 且 3 条断言同时转红 —— 另有反向用例证明「旧式替身确实缺 DEVNULL」，防止测试因替身写错而假绿。冻结态补 `--selftest --probe-only`（只验解释器探测就退出；dry-run 走不到采集那步，而崩溃点正在那儿），实测 exe 正确选出 `C:\Python315\python.exe` 并实跑 rc=0。顺带修 `_python_exe()` 的候选顺序（真实解释器优先、`py` 启动器垫底；扫 `C:\Python3*` 与 `%LOCALAPPDATA%\Programs\Python` 兜底） |

| 30 | **种子模式必须刷新陈旧档案：收入快照不能「首抓定终身」**（harvest.py `tm_pick_batch` 加 `TM_SEED_REFRESH_DAYS=7` 刷新窗口 + `test_harvest_sources` 4 项回归锁） | 2026-11-03 howtomakemi 增量连跑第三次零新增，排查定案三层原因：① 站点确实零新 slug（索引 1268 与 11-01 持平，273 种子 100% 重叠）；② 206/273 种子 TrustMRR 无同名 slug（其中 high-revenue 的全是 cursor/lovable/huntress 这类融资公司，本来就不是 TrustMRR 支付验证覆盖对象；模糊归一化只救回 1 个 `dappsentry→dapp-sentry`），命中交集仅 67 个；③ **67 个全部在 10-14 首抓过，旧逻辑种子模式 `return []` 不再抓，档案池选拔拿 20 天前的快照判「低于 $100」** —— 实时重抓 63 个（4 个 429/404）后 5 个涨过门槛（bibby 缓存 $96→实时 $344、swisscohousing-ch $98→$207、citohr $89→$138、chatbackup $54→$108、clearcut $74→$100），全被旧快照拦死。修法：种子模式下「已在 profiles 但 `fetched` 超过 7 天」的进刷新队列（受 budget 有界），窗口内的才直接用缓存选拔；空批日志同步改语义（「本轮不抓取：种子档案均在刷新窗口内」替代误导性的「本轮抓 0 个」）。修复后重跑：**inbox 364→375（11 条入队：5 条实时收入过线 + 6 条当前归零但 all-time 非零），gone 仅 +2（aislide/subfox，sitemap 在列但 .md 真 404）**。回归锁 `test_tm_seed_refresh`：窗口外进刷新队列/窗口内不重抓/全在窗口内走选拔/budget 有界，250 项全过 |

| 31 | **triage `dead_revenue` 闸：当前收入归零的条目不许进深核**（triage.py `_zero_now` + PENALTIES/HINT + harvest.py `_entry_from_api` 显式落 0 + `test_triage` 3 项回归锁） | 🚨 2026-11-03 分级 11 条新入队条目时发现：6 条「当前 MRR 与近 30 天均为 0、只剩 all_time 残值」的条目（upworkscribe 累计仅 **$28**、hachigo $133、1milliontaps $558、ai-selfie-studio $3,030、bidway $5,026、aprende $5,900）全部被判 **deep 送 AI 深核**。成因是 10-21「累计跳过」修复的半截工程：`revenue_of` 刻意对「累计收入 $X」返回 None（口径对了），但 rev=None 让 `tiny_revenue` 旗标永不成立；headline「累计收入 $X（当前 MRR 与近 30 天均为 0…）」含数字又让 `has_numbers` 为真（+16 分）；`_grade` 的分数兜底（score≥56）也不看量级旗标 —— 三条路全通，10-10/10-21 注释里写过的「墓碑洗成 deep 白花深核预算」第三次露头。修法三处：① `_zero_now()`（认显式 0 或 headline 机器声明 `ZERO_NOW_MARKER`）→ `dead_revenue` 旗标（罚 12 分，与 tiny_revenue 同档「病因不同：量不够 vs 已不赚」）；② `_can_deep` 与分数兜底都拦该旗标；③ harvest 落盘契约改为「缺字段=没抓到、0=抓到且为零」（旧写法 `if api.get("mrr")` 把实测 0 静默丢掉，下游分不清 backfill 和归零）。效果：**全队列 deep 206→97**（存量 103 条同类条目一并出列，不只今天的 6 条），11 条新条目 0 deep（5 条 tiny_revenue later + 6 条 dead_revenue later）；抽查 deep 97 条零误杀，`test_triage` 67 项 / `test_harvest_sources` 250 项全过。⚠ bibby（MRR $173、近 30 天实收 $344、增长 +493%）判 later 是 `revenue_of` mrr-优先策略 + TINY_REVENUE=300 门槛的正确行为不是 bug —— last30 含一次性收入别当经常性收入引用，改取数顺序须先拍板 |
| 32 | **JSON 台账 → SQLite 统一台账（`data/ledger.db`）+ 全量测试 db 隔离**（`scripts/db.py` 三表：cases / platform_posts / verifications；`import_to_db.py` 幂等导入 495/360/171；`db_cli.py` 查询 CLI；`backup_ledger.py` 每日 2 点备份留 10 份） | 🚨 2026-11-03 落地：散落 `data/*.json` 台账并发互踩（`score_*` 整份重写曾静默覆盖晋升）换 SQLite WAL 单文件。读写两端全迁：**读端** = db 优先 + JSON fallback（发布/对账/管理脚本 20+ 个）；**写端** = db 真值 + JSON 快照双写（`state.Manifest._db_write`、`ledger._db_sync`、各平台 mark_*）。守卫：① `upsert_post` 状态前进序（draft 不覆盖 published，实测曾 58 条写反）；② 测试注入临时路径时不碰真库（`_db_enabled` / `path != MANIFEST` 先例）；③ `LEDGER_DB` 环境变量可把整条链钉到测试库（`db.get_conn` 动态解析，线程本地连接记路径自动重开）。**全量测试一键跑法**：`python scripts/run_all_tests.py`（自动播种 tmp/test_ledger.db → 逐文件跑 59 个 test_*.py → sha256 指纹断言真库 + 58 个 data/*.json 零污染）。复现证据：2026-11-03 全量 59 文件全绿（首轮 2 个内容类失败已修：① `MONEY_RE` 的 MRR/ARR 改独立词匹配，TrustMRR 品牌名子串误伤，加 4 断言回归锁；② `test_bilibili_publish` 键值块断言从「每篇必须有 blockquote」改为「有 flex 输入必须消费、≥2 行组必须转块」——game_rebrand 重写后 38 篇本就是纯段落无 flex），跑前跑后真库指纹一致 |
| 33 | **阿里云发表四个连环坑一次修完：滑块可见性判据 / 标题取 `split_md` 第 0 项 / 摘要写 `textarea#abstractContent` / 异步回填先等内容稳定**（`yun_finalize.py`：`_CAPTCHA_VISIBLE_JS`、`publish_aliyun(abstract=…)`、注入后回读重试 3 次、空稿与空摘要硬拦） | 🚨 2026-11-04 发 `ext-ep-001-super-mario-bros` 连卡四次，每层表现都像另一个 bug：① `.aliyunCaptcha-top` **常驻 DOM 但 0×0**（`display:flex`、`visibility:visible`），旧代码只判「元素存在」⇒ 认定滑块在挡路，原地等 300s/240s 全超时，而用户看到的是**没有滑块的空白编辑器**；② `split_md()` 返回的 body 已摘掉 `# 标题` 行，旧代码再用 `^#\s+` 正则找标题恒为空 ⇒ 注入后标题 0 字；③ 摘要真身是 **`textarea#abstractContent`**（占位「请填写摘要」、右下 `0/300`），`.ai-summary-box` 只是标签盒子、内部**没有任何 input**，摘要为空时点「发布文章」**无弹窗无报错无请求不跳转**（`window.__net` 里连一条 draft/article 请求都没有，只闪「消息」提示）；④ 草稿回填是**异步**的，页面刚开就注入会被回填响应覆盖（`ok len=1905` 三秒后复读 0 字）。修后实发成功：**草稿 `1703041` → 文章 `https://developer.aliyun.com/article/1778894`**（再次印证「草稿 aid ≠ 文章 id」）。⚠ 旧草稿 `draftDetail` 三字段全 0、`putDraft` 返回 `{}` 也写不进 ⇒ 空壳草稿只能走 UI 重填，别再对着它调 API |

| 34 | **`publish_one.py --upload` 改走 CDP 通道（`--via cdp`），不再依赖 CSDN 同步助手扩展**（`upload_remote` 显式传参 + `test_publish_one.UploadRemote` 2 项回归锁） | 🚨 2026-11-04 排查阿里云发布时发现：`upload_remote()` 拼的命令没带 `--via`，而 `sync_longtail.py` 默认 `mcp` ⇒ MCP 探针在浏览器没开时报「浏览器扩展未连接」，`remote` 闸永远补不上，失败提示还把人往「八成是浏览器/扩展没开」上带。实际上 10-30 已落地 `yun_draft_cdp.py`（9228 常驻 Chrome + `chrome-yun-profile` 磁盘持久登录态），`sync_longtail._run_via_cdp` 也已接好，help 文本自己标着「推荐」却没人调用。修法：两朵云 `--upload` 显式带 `--via cdp`，失败提示改 CDP 语境，加离线测试钉住命令形态与干跑不落子进程。复现证据：修前 `_probe_csdn_sync.py list` 工具 5 个全在但 `list_platforms` 返回「浏览器扩展未连接」（Chrome 无进程、3721 无监听）；修后 `test_publish_one` 全绿、`publish_one --platform aliyun` 干跑正确显示「今日已发 1 篇，达到上限 1」。⚠ `sync_longtail.py` **全局默认仍是 mcp**（思否等长尾平台还只能走扩展），只在 publish_one 这条两朵云路径显式指定，别顺手改全局默认 |

| 35 | **腾讯云「建了草稿却永远过不了 remote 闸」+ `tc_article` 写成文章 URL 两个缺陷一次修完**（`sync_longtail._run_via_cdp` 建稿后显式 `ledger_set(tc_draft/al_draft)`；`yun_finalize.publish_tencent` 回传 `articleId=`，CLI 用正则取 7 位 id） | 🚨 2026-11-04 发 `ext-ep-002-2048`（游戏厅：2048）复盘：① `sync_longtail --via cdp` 建稿成功（`addArticleDraft` 返 `draftId=340253`），却只写 `data/longtail_sync.json`；而 `publish_one` 的 `remote` 闸读的是 **SQLite `platform_posts.draft_id`**（`_yun_row`）⇒ 连跑两次都报「台账没有远端草稿 id」，只能手工补一条 `ledger_set` 才放行。已让 CDP 建稿路径建完即登记（JSON + db，`_db_sync` 代劳）。② `publish_tencent` 成功后返回 `"<文章URL>\n    封面已落库：<封面URL>"`，而 CLI 用 `str(info).split()[0]` 取「文章 id」⇒ `tc_article` 恒空、`platform_posts.remote_id` 也空，「已发表列表按标题命中」这条红线失去可比对的 id。改为回传 `articleId=<7位>` 并正则提取。复现证据：本次实发 `草稿 340253 → 文章 https://cloud.tencent.com/developer/article/2775867`（`tc_manage.py status` 列表命中标题、state=published，封面落库 `developer.qcloudimg.com/http-save/yehe-12743977/e5884c3e7822cb7a927fac7b`），回填后 `yun_finalize.json.tc_article=2775867` / db `remote_id=2775867`。⚠ 建稿阶段 `save-http-image` 报「接口超时」**不影响发布**：发表时 `inject_cover()` 用本地 `out/toutiao/<case>/cover.png` 重新注入，判据是提交后卡片缩略图（CSS background-image） |

| 36 | **技术类平台标题口径重写：从「<名> 技术拆解：一个海外独立产品的工程与增长」换成「名字 + 做什么 + 一个可核验数字」**（`gen_platform_md.make_title()`；金额去 `$`：`18,380 美元`/`4 万美元`/`100 万美元`；`_strip_meta()` 清「TrustMRR 实时核对」旁注；腾讯云 `money=False` 不带金额） | 🗣 2026-11-04 用户驳回「标题太笼统，没有给出必要合理的信息」，病根是 `build_zhihu/build_cnblogs/build_aliyun` 三处硬编码模板只有 `name`、没有任何事实。`make_title()` 取 `one_liner` 首分句（≤26 字）+ `metrics.growth`→`headline`→`customers` 第一个带数字的语句（≤30 字），总长 ≤62 字。复现：`trustmrr` → `TrustMRR：把验证收入本身做成了产品：连 Stripe 才能上榜，上线 5 天做到 18,380 美元 MRR`；`cod-dex` → `Codédex：把学编程包装成冒险旅程的互动课程平台，MRR 84,214 美元`；腾讯云同案例 → 去掉金额尾巴。⚠ KaTeX 平台标题里不能出现 `$`（一段两个 `$` 会被吃成公式）。已发布的 3 篇旧标题待 CSDN 额度恢复后按新口径重发 |

| 37 | **CSDN 合规闸 + 封面随发表一次提交**（新增 `scripts/csdn_rules.py`；`publish_one` 的 csdn 闸加 `rules`/`cover`；`csdn_publish publish --cover` 走 `saveArticle.cover_images`；`csdn_set_cover.py --upload` 只取 URL 不发表） | 🗣 2026-11-04 用户四问：封面补齐没 / 驳回原因是什么 / 看过规则没 / 以后按规则发吗。实测真账：`已发布(1)`（Codédex 169850647，有封面 198 阅读）；`全部(3)` 另两篇 st=32 非公开（Chessigma 169742331 无封面、TrustMRR 169654106 有封面 `85dfeef64e…`）；`回收站(2)`、`草稿箱(3)`。驳回理由是 **广告-营销推广**，对照《社区内容创作规范》落在 一.4 纯广告 / 一.10 风险投资 / 一.3 批量低质 / 一.7 标题。闸的 block 判据：标题 夸张·悬念·强迫·带金额·空壳；正文 站外联系方式·交易引导·网赚口吻·第三方引流；金额 >3 处或商业化词 >6 处。`clean()` 剥 `> 平台：` 管线水印（自曝批量的元凶）。复现：`publish_one.py --platform csdn --case chessigma` → `✗ rules 2 条会驳回（金额密度超限）`；`csdn_set_cover.py --upload out/toutiao/cod-dex/cover.png` → `✓ https://i-blog.csdnimg.cn/direct/a43d215c8bad40cea17c6d59a4019566.png`（64s，未产生任何文章）。⚠ status 32 = 非公开态（不是「审核中」）；「已发布(N)」chip 才是真公开数 |
| 38 | **CSDN 草稿可编程改写 + 草稿补封面**（新增 `scripts/csdn_save_draft.py`、`scripts/csdn_draft_cover.py`；`csdn_set_cover._feed_and_confirm` 选择器换成 `.el-upload__input`；`csdn_publish._BROWSER_JS` 补 GET 签名规则） | 🗣 2026-11-04 用户「草稿箱里的草稿没有封面，然后按规则改写」。实测草稿箱 3 条（Chessigma `170045008` / 2048 `170041126` / 马力欧 `169938945`）封面字段**全为空**。三条关键技术结论：① **保存草稿 = `saveArticle` + `id` + `status:2`**（页面点「保存草稿」抓包所得），而 `save_article()` 写死 `pubStatus:"publish"`+`status:0` 调用即发表 ⇒ 另起 `save_draft()`，封面同在 `cover_images` 里一次提交；② 封面上传控件是 **`input.el-upload__input`**，老选择器 `.cover-upload-box input[type=file]` **恒为空**（症状＝永远报「抽屉没开」）；③ ⛔ **浏览器内 GET 的 HMAC 签名至今没打通**（只发 POST 时永远不暴露）：`article/list?status=draft` 试过三版（① 整串含 query 去签 ② 只签 pathname ③ 再叠「GET 时 Content-Type 取空」）服务端一律回 `HMAC signature does not match`，还差条件没摸到 ⇒ **别再对着它调参**；读草稿箱/已发布/回收站改走 `scripts/csdn_list_drafts.py`（打开创作中心→点 tab→读注入 hook 抓到的响应，等于照抓包抄，不碰签名）。另：**>2MB 的封面传不上去**（5.6MB 图 40s 无回图），缩到 ≤960 宽（700 最稳）后秒过。复现证据：三条草稿回读均为 `st=2` 且 `coverImage` 非空 —— `94b53f6af8a4…` / `f9811f07fbd0…` / `eb599d3e8a58…`，标题同步换成「做一个免费国际象棋对战平台，工程上要过哪几道关」「4×4 网格怎么写出「合并」手感：读开源 2048 的代码结构」「用 Backbone 游戏引擎复刻超级马力欧兄弟：Sprite、Engine 与关卡数据怎么分层」，正文 3334/2369/2058 字，三篇 `csdn_rules` 全部 rc=0（chessigma 剩 2 处金额 warn） |
| 39 | **CSDN 结论固化：命令级 runbook + 三道代码闸 + 8 项回归**（`PLAYBOOK-platforms.md` 加「CSDN 操作速查」表；`csdn_set_cover.ensure_cover()` 开抽屉前缩图 / `COVER_MAX_BYTES=2MB`；`csdn_draft_cover` 加 `location.href` 守卫；`test_csdn_publish.py` 5→8 项，锁草稿 payload、封面主选择器、封面尺寸闸） | 🗣 2026-11-04 用户「把 csdn 的相关书写草稿、发布等最新结论存入记忆或代码，不要下次犯同样的错误」。查出 **`PRD §6.1 #38` 有一条与事实相反的记录**（声称 GET 签名「pathname + 空 Content-Type」已打通并钉死，实际三版全 `HMAC signature does not match`）—— 这类错误结论比没记录更坏事，已就地改成「至今没打通，别再调参」。新增三道机械闸替代口头约定：① `ensure_cover()` 在**开抽屉前**判 `COVER_MAX_BYTES=2MB`，超了自动缩到 700 宽（找不到缩图脚本则抛错并给可复制命令，**不静默硬喂**）；② `csdn_draft_cover` 读到 `location.href` 不含 `articleId=` 立刻中止并提示去回收站恢复（此前是等 24s 后报「正文为空」，会误导成稿子坏了）；③ 测试锁 `status:2`/`id` 键名、`.el-upload__input` 排序、封面尺寸常量与 PNG/JPEG 宽高解析。复现：`python -X utf8 scripts/test_csdn_publish.py` → `8/8 PASS`；`ensure_cover('tmp/big-cover-test.png')` → `封面 5.57MB 3845x2157 超限 → 缩到 700 宽` → `0.38MB 700x393` |
| 40 | **CSDN 收敛到只留 Codédex 一篇**（新增 `scripts/csdn_keep_only.py --dry/--trash/--deep`；`csdn_publish.delete_article(deep=True)` 支持彻底删除；台账 `data/csdn_drafts.json` 标 `deep_deleted`） | 🗣 2026-11-04 用户「先只留 Codédex 那一篇」，即放弃重发 Chessigma / TrustMRR 与三篇游戏稿。**先审计后动手**：`tmp/csdn_audit.py` 一个会话读齐四 tab，实测 `draft=3` / `all_v3=3`（去重后）/ `deleted=2`，并确认「全部」把回收站条目也算进来 —— `all_v3` 里那两条 st=32 与 `deleted` 的两条**是同一批 ID**，所以实际只需清 5 条而非 7 条。删除分两步：`--trash`（移入回收站，可恢复）→ 回读 → `--deep`。防误删靠**双白名单**（`KEEP` 与 `TARGETS` 都写死 ID，绝不做「非 KEEP 即删」差集，差集会连以后新建的一起删）。复现：`--trash` → 3 成功/2 跳过（已在回收站）；`--deep` → **5 成功 0 失败**；回读 `draft=0`、`deleted=0`、`all_v3` 去重后仅 `169850647 st=1 views=198 cover=有` |
| 41 | **Codédex 稿改技术向（更新已发布文章，不占额度）**（`drafts/newbatch/csdn/cod-dex.md` 重写 973→4845 字；走 `save_article(..., article_id="169850647", cover_images=[原图], cover_type="1")` 原地更新） | 🗣 2026-11-04 用户「写成技术向再发」。原稿跑 `csdn_rules` 是 **3 项 block 全中**（金额 9 处、商业化词 11 处、`折扣` 交易引导）—— 它能过审属漏网，不是合规。改写路径：把「商业模型/为什么能跑起来」全删，换成六个工程决策（代码在哪跑 / 判题三级与四个坑 / 课程即数据 / 进度存事件不存布尔 / 权限边界不能只在前端 / 单次运行成本），带可运行的最小判题器与课程 schema 代码块，并**明确写「它没有公开技术实现，本文不猜内部架构」**。新结论（重要）：`remaining_publish_count=0` 的情况下，`saveArticle` + `id` + `status:0` **更新已发布文章仍成功**，**不进审核队列、当场生效**，阅读数 198 与封面全部保留 ⇒ 额度耗尽时仍可修正已发布稿。回读证据：`all_v3` 仅 1 条、`st=1`、标题已换、`cover=有`；公开页显示「已于 2026-11-04 17:16:30 修改」，六节小标题全部命中、「商业模型」已消失。旧稿备份 `tmp/cod-dex.old-1715.md` |
| 42 | **腾讯云发游戏稿：建稿渠道路径不同 + 台账双写修复**（`yun_draft_cdp.py` 新增 `_remember_db()` 落 SQLite `platform_posts`；`scripts/test_yun_finalize.py` 6→8 项） | 🗣 2026-11-04 用户「继续其他平台」。档期实测：九平台里**只有腾讯云还有空位**（2/3），其余全部到上限，CSDN 记着剩 1 但真实 `remaining_publish_count=0`、头条仍冻结到 11-10。**两条硬发现**：① `sync_longtail.py --via cdp` 只认 `drafts/wechat/<case>.md`，**游戏稿必失败**（报 `草稿不存在`）⇒ 游戏稿走 `yun_draft_cdp.py --file <游戏仓>/platform/tencentcloud.md --case <cid>`；② `yun_draft_cdp` 原来**只写 `longtail_sync.json`**，而 `publish_one` 的 remote 闸只读 SQLite ⇒ 建完草稿仍报「台账没有远端草稿 id」，照提示再跑一次 `--upload` 会**建第二份草稿**（重复稿源头），已补 `_remember_db()`（`state=draft`，靠 `_STATE_RANK` 不覆盖已 published）。内容侧：游戏稿原是五段模板（先说清楚/它是什么/零成本能拿到什么/仓库长什么样/上手成本）+ `> 平台：` 水印，腾讯云有过「只收技术向」驳回单 ⇒ 按工程向重写为《给 Doom 引擎造一套合法素材：Freedoom 要对的那些资源契约》（WAD 目录表结构 / PLAYPAL·COLORMAP 绑定 / PNAMES·TEXTURE1·SIDEDEFS 三层贴图 / S_START·F_START 哨兵区间 / 精灵 4 字符+帧字母+视角数字 / DEMO1-3 崩溃性 / BEX 不重编译改行为 / 构建可重复与清单校验 / 换引擎+叠 PWAD+demo 回放三重验收），并注明「仓库脚本以 README 为准，本文只讲公开规格」，不猜它内部实现。复现：`publish_one.py --platform tencentcloud` 六闸全绿（draft/link/katex/genre/cover/remote=draftId=340532）→ `--yes` → `articleId=2776285`，封面落 `developer.qcloudimg.com/.../ad4df40cfd`；公开页回读 3937 字、七个关键段落全命中、`发布于 2026-11-04 19:26:11` |
| 43 | **腾讯云补齐 AI case 档期：新稿 mort → 2776290**（`drafts/newbatch/tencentcloud/mort.md` 新建，约 2900 字；`yun_draft_cdp.py --file` 建稿 340537 → `publish_one --case mort --yes`） | 🗣 2026-11-04 用户「腾讯云发一篇 ai 稿」。档期逻辑：腾讯云今日已发游戏 1 篇、上限 3，「补另一类」正好是 AI case，但 `publish_slot` 报「AI case 无稿可发」——查实是**没有产物文件**，不是没有案例（未发腾讯云的 AI 案例有 453 条）。选稿判据改成三条：**不在腾讯云已发 17 条内 + `out/toutiao/<cid>/cover.png` 已存在（免生图）+ 工程素材够厚**，命中 `mort`（AI 求职代理：5 万+ career page、14 家 ATS、每月 20 万+ 职位、0-100 分项打分、Auto Apply）。成稿按腾讯云口径写成纯工程题：《盯住 5 万个招聘页：招聘聚合系统的采集调度、去重与投递副作用》——两条数据链路（平台 vs 官网 career page）、五万页面的三层调度（按变更率分频 / 条件请求 / 失败退避）、解析稳定性（结构化数据→DOM→正则 + 字段命中率告警）、去重（复合键归一化，去重键须比 URL 稳）、打分是产品形态不是模型指标（规则召回 + 模型精排，可解释优先）、**自动投递是「代人有后果地操作」**（幂等键 / 投递前二次校验 / 非幂等操作不许盲重试 / 人工兜底判据「重复执行会怎样」）、改写不虚构（可追溯到输入条目 + ATS 闭环）、成本落在维护面（四条健康度曲线）。商业数据只留一句背景（截至 2026-09-30 活跃订阅 2），标题无金额。复现：六闸全绿（remote=draftId=340537）→ `articleId=2776290`，封面 `developer.qcloudimg.com/.../8ad423f670`；公开页回读 4710 字、六处关键段落全命中、`发布于 2026-11-04 19:33:10`；SQLite = published/2776290。**顺带验证 11-04 补的 `_remember_db()` 已生效**：建稿即自动写 `platform_posts(state=draft)`，本轮没有再手工回填 |
| 44 | **今日踩坑总账 24 条 + 公开页回读转正**（新增 `scripts/verify_page.py`；`PLAYBOOK-quality` 假绿 6→10 连；`PLAYBOOK-publish` 顶部加「唯一证据」段；`PLAYBOOK-platforms` 补 4 条；`MEMORY.md` 加三条自检问句） | 🗣 2026-11-04 用户「记忆今天碰到的问题，以防下次不再犯」。**先查覆盖再写**（grep 分册），发现真缺口 4 条：① 假绿第 7 条——**把「打算这么修」写成「已修好」**（CSDN GET 签名三版全 `HMAC signature does not match`，却写进 §6.1 #38「已打通并钉死」，还配了字符串断言；错误结论比没记录更坏，已就地改回事实版）；② 「平台审核通过 ≠ 内容合规」（Codédex 唯一在架稿跑 `csdn_rules` 仍 3 项 block）；③ 列表 `全部(N)` 含回收站条目（差点按 7 条删、实际 5 条）；④ 腾讯云编辑器「正文载入 N 字」**只统计汉字**（2662 字报 1003，差点当截断）。已覆盖的（`el-upload__input` / `status:2` / `COVER_MAX_BYTES` / `_remember_db` / 双白名单）未重复写。**把一次性脚本转正**：今天 5 个 tmp 验证脚本会被清 → `verify_page.py`（`--url/--expect/--title/--chrome/--wait`，退出码 0/1/2，自清 stale LOCK），补上「发布成功唯一判据」一直缺的可执行入口。复现：`verify_page.py --url https://cloud.tencent.com/developer/article/2776290 --expect "盯住 5 万个招聘页,ATS,幂等,去重,匹配分" --title 招聘` → `✓ 回读通过：5 个关键词全部命中` rc=0（20s / 9067 字 / `cleared stale LOCK`） |
| 45 | **C 档 22 条评分补表，两表覆盖全库 461/461**（`score_solo_fit.DELIVERY` / `score_china_fit.SCORES` 各 +22；`data/cases.json` 同步；提案状态写回 `tmp/fit_proposal_2026-11-04.md`） | 🗣 2026-11-04 用户「c类处理」＝评分提案里「别搬（综合 < 50）」那 22 条，此前刻意不入表；理由「别搬」也该对读者可见 ⇒ 按原提案分数/六维**原值**补齐、不重打分（源 `tmp/fit_proposal_2026-11-04.json`），合并走 `tmp/merge_c_tables.py`（集合核对 / 分数自洽 / 标点不起行 / `ast.literal_eval` 逐字还原四道锁 + 已合并保护；⚠ 不是 B 档那只 `merge_b_tables.py`）。**关键判断：入表后不是全落 `skip`** —— `quadrant_of` 按 solo/china 双轴而非总分，实测 12 条 `export`（个人能做、国内没市场）/ 10 条 `skip`（双低），分数区间 35.1–49.7；全库前三未被低分档挤掉（`visualizee-ai` 79.3 / `downconvert-sketchup-files` 78.0 / `clawlink` 73.5）。复现：`build_static.py --out tmp/_dist_check --no-inbox` → 461 页 / 44.11 MB、`composite` 461/461 全非空；`python -X utf8 -m unittest scripts.test_build_static scripts.test_prerender scripts.test_auto_deploy scripts.test_deploy_oss -q` → 59 项全绿。核对：`DELIVERY` 462 条（多出的 `private-venture-1` 是示例占位、不在库）、`SCORES` 461 条，库内 461 条两表均无缺口。提交 `20bb74d` 已推 `origin/main` |
| 46 | **「近期没有部署记录」从口头建议落地为受管定时任务**（任务 `b2e189f7-ed81-4184-bca9-226ad2efe950`，`FREQ=DAILY;BYHOUR=21;BYMINUTE=0`，`validFrom=2026-11-04`，状态 `ACTIVE`；PRD 运维行 + README 部署段同步） | 🗣 2026-11-04 用户指出上一轮回答里「近期没有部署记录 → 持续挂在每日 21:00（auto_deploy.py）…如果不配，每回手动」只是建议、没真跑 ⇒ 要求「修复近期没有部署记录」。落地：每天 21:00（北京）先 `auto_deploy.py --env-file .env`（比线上新才传），再 `verify_deploy.py --base https://case.ydtgo.top`（30+ 项语义断言），结果看任务记录。前置排查链：截图「阿里云 OSS 没更新」→ 用户质疑「为什么要重建，我记得上次已经改成了动态渲染」→ **查 `build_static.py`/预渲染确认站点仍是静态预渲染**（`case/<id>.html` 461 页 + `data.json`，浏览器端动态只是筛选），改数据必须重建 `public/` 再传；「做一次 CDN 缓存刷新」核为**域名直连 OSS、无 CDN 层**（DNS/CNAME + 响应头），故未调刷新接口，改为强刷浏览器缓存。⚠ 顺手修掉自己写进 PRD/README 的三处不实表述：`DFS` → `rrule`、拼错的 `env _u http_proxy` 凭证说明、「失败写 tmp/ 日志」非实测。**实证三份日志**：15:11 全量部署 478 文件 / 0 失败 / 匿名 GET 首页 200（`tmp/_auto_deploy_1104.log`）；15:19 语义校验全部通过、461 页 / 461 条（`tmp/_verify_deploy_1104.log`）；16:19 复跑兜底报「线上与本地一致 —— 不需要部署」（`tmp/_auto_deploy_test_1104.log`，印证幂等） |

| 47 | **发布幂等：case 级去重闸 + 数据库唯一约束**（新增 `scripts/publish_guard.py::check_dupe()`；`publish_one.GATES` 九平台全加 `dedupe`；`db._init_schema` 加 `ux_posts_case_platform`；`db.upsert_post` 改为 UPDATE、不再 DELETE+INSERT；修 `_gate_remote` 恒读 `tc_state` 的漏洞；B站/小红书 FIFO 分支补档期配额） | 🗣 2026-11-04 用户「每个平台会发重复草稿和存重复草稿」。三条实因：① `platform_posts` 只有普通索引，去重全靠应用层 `SELECT→DELETE→INSERT`，并发建稿无兜底；② `longtail_sync.json` 的键是**建稿批次** `<case>-<日期>-<序号>` 而非幂等键 —— 只读统计 43 条里 **12 组重复标题**（chessigma 在 tencentcloud 4 个批次、cod-dex 在 aliyun 2 个），重复建稿无痕；③ `publish_one` 九平台 **0 条 case 级去重闸**，B站/小红书 FIFO 分支连档期都不看直接 `send-draft`；另 `_gate_remote` 写死 `row.get("tc_state")`，阿里云永远拿不到状态 ⇒ 已 published 也放行。**实测**：`cd scripts && python -X utf8 -m unittest test_publish_guard test_publish_one -q` → `Ran 52 tests ... OK`；只读核真库 → `platform_posts rows: 363` / `dup (case,platform) groups: 0` / `UNIQUE INDEX: CREATE UNIQUE INDEX ux_posts_case_platform ON platform_posts(case_id, platform)`；`python -X utf8 scripts/publish_guard.py --platform aliyun --case chessigma --file drafts/newbatch/aliyun/chessigma.md` → `✗ 重复发布/重复建稿 台账已是 published ⇒ 再发就是第二篇`；`python -X utf8 scripts/run_all_tests.py` → `60 个文件，2 失败`（`test_agnes_cover` 缺 `crypto web3` / `utilities` 两个封面主题、`test_make_article` 79 过 1 失败＝5 条 `corrections` 内部黑话泄漏），**两个文件都不 import 本次改动的任何模块，属既有红**；`ledger.db + 57 个 data/*.json` 指纹零污染。显式 `--force` 只绕 dedupe，不许绕标题/形态/封面 |
| 48 | **封面闸从「本地有 PNG」升级为「本地有资源 + 发布命令真的带 `--cover`」**（`publish_guard.check_cover()` / `cover_of()` 查找链扩到 `out/xhs/<cid>/card-1.png` 等四处；`cover` 闸补到九平台；`_gate_cover` 复核 `build_cmd` 是否含 `--cover`） | 🗣 2026-11-04 用户「封面有可能没设置」。旧 `_gate_cover` 只查 `out/toutiao/<case>/cover.png` **在不在**，而 9 个平台里只有 4 个配了 cover 闸；CSDN 三篇草稿「本地有图、平台侧 `coverImage` 全空」就是封面没随 `saveArticle` 一次提交（§6.1 #37/#38）。实测：`test_publish_guard.CoverAndCmd` 3 项全绿（缺封面拦下 / 三个 `--file` 平台命令必带 `--cover` / 闸拒空封面）；`publish_one.py --platform csdn --case chessigma --json` 干跑 → `✓ cover 本地封面存在；发布命令必须带 --cover（随请求一次提交）；平台侧是否落库仍需回读 ｜ 命令已带 --cover`。⚠ 静态闸**不证明平台侧已落库**，仍按 `VERIFY_HINT` 回读（`tc_manage.py status` / `csdn_list_drafts.py`）；只读统计 `drafts/newbatch/*` 19 篇里 2 篇没有 `out/toutiao/<id>/cover.png` |
| 49 | **标题质检闸 + 生成器去硬切**（新增 `publish_guard.scan_title()` 与 `TITLE_MAX` 单一真理；`gen_platform_md.make_title()` 把 `return t[:100]` 改成 `_cut(t, 上限)` 按标点裁；`title` 闸补到九平台） | 🗣 2026-11-04 用户「仔细检查标题有没有截断、AI 味太重、套机械模板」。`make_title` 注释写「≤62 字」末尾却 `t[:100]` 硬切，而 `audit_cover_title.py` 的平台上限（头条 30 / 小红书 20 / 公众号 64 / B站 80）发布链根本不读。**实测** `python -X utf8 scripts/publish_guard.py --scan-titles` → `扫到 401 个标题，命中 block 13 条`：6 条公众号 66–76 字超 64（都带「TrustMRR 实时核对（非订阅制…）」旁注）、**2 条头条 30 字硬截断实锤**（`Shopify 商家的 Meta 追踪本来就是错的，于是有了` 尾字「了」、`Chessigma：「免费版 Chess.com」拿下 10` 尾字「0」，正是 11-02 `data/title_rewrite_2026-11-02.json` 记的 `t[:30]` 坑）、3 条空壳模板（知乎 2 条「一个海外小产品怎么赚钱：X」、阿里云 1 条「云原生视角下的独立产品增长：Chessigma」）、2 条技术平台标题带金额（博客园 cod-dex 9.99 美元 / 知乎 ShipFast 127 万美元）。⚠ 判据校准两处：HTML 实体 `&amp;` 要反转（否则把读者看到的 61 字误报成 65 字）、`meta.json` 要按目录形态读。`test_publish_guard.TitleScan` 8 项 + `Generator` 5 项全绿。**当时存量产物只出清单、未批量改文案**（须用户确认）⇒ 11-04 晚用户批准后见 #51 |
| 50 | **内容形态闸 + 五平台草稿生成器去模板**（新增 `publish_guard.scan_content()` / `strip_pipeline_marks()`；`template` 闸补到九平台；`gen_platform_md.py` 重写五个 builder：删 `> 平台：X ｜ 状态：本地稿` 水印与罐头来源句、五平台各用不同骨架，CSDN 不再等于博客园 `webfix()`、腾讯云不再等于阿里云换标题） | 🗣 2026-11-04 用户「AI 味太重、套机械模板」。旧生成器五平台共用一串栏目（`## 一、它到底解决了什么` … `## 五、给想做类似产品的你`），14/35 篇 `drafts/newbatch/*.md` 带水印、5 篇带固定「TrustMRR 直连支付网关 / 官网+公开访谈」来源句 —— 后者既模板化又可能失实（抓到官网 ≠ 数字来自官方，见 PLAYBOOK-quality 28）；这正是 CSDN 一.3「批量低质」与腾讯云「技术无关内容」驳回单的形状。**实测** `python -X utf8 scripts/publish_guard.py --platform zhihu --case chessigma --file drafts/newbatch/zhihu/chessigma.md` → `block 5 条 ⇒ 不许发`（dedupe 已有草稿 1 / 空壳标题 1 / 管线水印 / 罐头来源句 / 机械栏目骨架 5 个固定小标题）；同稿 csdn → `block 0 条 ⇒ 可发`。技术平台把水印判 block，社交平台判 warn。**合规复核（2026-11-04 查证）**：知乎 2026-04-13《关于规范 AI 辅助创作行为的公告》呼吁声明「包含 AI 辅助创作」且明确「主动声明不影响正常推荐」，同时重点治理滥用 AI 批量发布纯生成/拼凑内容（公告称已折叠 25 万条低质 AIGC、处罚 1.1 万账号）；《人工智能生成合成内容标识办法》2025-09-01 施行，禁删改/伪造标识；知乎《社区规范》禁导流与联系方式变体（含同音/谐音/符号），CSDN/博客园/掘金等技术社区对参考链接相对宽容但仍查营销导流 ⇒ 闸按平台分档：`ZHIHU_CTA` 只硬拦知乎，`csdn_rules` 仍只服务 CSDN。**当时存量 14 篇带水印的 md 未批量改写**（生成器已修，重建需 `--overwrite`，属批量改文案须先确认）⇒ 11-04 晚用户批准后见 #51，且未用 `--overwrite`（会洗掉人工长稿） |

| 51 | **九平台调性单一源 + 存量稿批量改写**（新增 `scripts/platform_tone.py`（九平台 TONE 表 / `tone_issues()` / `body_fingerprint()`）、`scripts/retone_drafts.py`（`--check`/`--apply`/`--verify`，默认只读、先备份）、`scripts/test_platform_tone.py`（17 项）；`gen_platform_md.py` 由五个 builder 收敛为**单一渲染器 + 调性表**；`make_article._clean_title_hook()` / `_cut_title()`、`toutiao_publish._DANGLING_TAIL` 修两个标题根因；`publish_guard.CANNED_SOURCE` 收紧到「行首 + 无时点标记」） | 🗣 2026-11-04 用户两条指令：「#1 按照每个平台的调性编写文稿 / #2 存量稿批量改文案：14 篇水印稿和 13 条问题标题」—— 即批准上一轮挂起的批量覆盖。**取证**：14 篇水印稿里 **6 篇是机械模板壳**（534–1069 字，五平台共用「一、它到底解决了什么…」栏目）、7 篇是人工写过的长稿、**1 篇与腾讯云逐字相同**（`aliyun/cod-dex.md` 3712 字 vs `tencentcloud/cod-dex.md` 3718 字，只差水印里的平台名，而腾讯云那篇 11-03 已发布）；罐头来源句实有 **4 种变体**，旧守卫只认 2 种，「数据来源：产品官网公开信息、公开支付验证页。」整类漏网。**改动**：6 篇壳按各平台调性整篇重写（知乎论证体 / 博客园第一人称随笔 / 阿里云弹性成本视角），阿里云 cod-dex 换命题重写拆开同稿，7 篇长稿**只做定点手术**（去水印行、去罐头句、修 `1.27 美元M` 换算错乱），13 条标题逐条改写；24 个文件落盘、原件备份在 `_backups/retone-2026-11-04/`。**实测**：`python -X utf8 scripts/retone_drafts.py --apply` → `已备份 24 份原件 / 已写入 24 个文件 / 标题：扫到 401 个，block 0 条 / 管线痕迹 0 处 / 跨平台逐字同稿 0 组 / 调性禁忌 0 处 / 总计 0 项 ⇒ 全部通过`；再跑 `--check` → `计划改动 0 个文件（幂等）`；`python -X utf8 scripts/publish_guard.py --scan-titles` → `扫到 401 个标题，命中 block 0 条`；`cd scripts && python -X utf8 -m unittest test_platform_tone -v` → `Ran 17 tests ... OK`；7 篇长稿字数 `-56 ~ -90`（只删水印/罐头句，人工正文保留）；`data/*` 与 `ledger.db` 按 mtime 核查 21:15 后**零写入**；`python -X utf8 scripts/run_all_tests.py` → `61 个文件，2 失败`（仍是 `test_agnes_cover` / `test_make_article` 两个既有红，后者 79 过 1 失败＝`corrections` 内部黑话泄漏，与标题无关）、`ledger.db + 57 个 data/*.json 指纹全部一致，零污染`。⚠ 判据精度：掘金归档稿「数据来源：产品公开定价页…（2026-10 快照）」是**带时点的合法出处**，不是罐头句（初版 4 处误报已修）；`retone_drafts` 直接复用 `publish_guard` 的正则，不许另立一套。⚠ 本地改标题**不等于平台侧标题已变**：头条两条 11-02 已走「修改已发布文章」修过（见 `data/publish_manifest.json`），公众号 6 篇是静态产物里的 `<h1>`；要改线上需另走发布/更新流程（本次未发布、未推远端、未提交） |

| 52 | **公众号存量标题旁注清理（用户批准方案 ①）+ 尾字判据校准**（新增 `scripts/clean_title_caliber.py`（`--check`/`--apply`/`--verify`，默认只读、先备份、幂等）、`scripts/test_clean_title_caliber.py`（15 项）；`publish_guard._CUT_TAIL` 拆成 block 用（含裸数字）与 `_DANGLING_TAIL` warn 用（不含数字）） | 🗣 2026-11-04 用户批准上轮挂起的选项 ①「只清 175 篇未发布的旁注后缀，13 篇已发布保持不动」。**取证**：`out/articles/*.html` 293 篇里 **188 篇 `<h1>`** 形如「Name：MRR $835，TrustMRR 实时核对」（159 篇）或「…（非订阅制，MRR 恒 0）」（29 篇）；每篇旁注出现 **2 次**（标题 + 正文「官方口径」数据卡），只能清标题那一处；`data/cases.json` 的 `metrics.headline` 是数据层口径，不动。**保护集合五源运行时求交**（不写死 ID）：`wechat_published.json status=published` / `publish_manifest.json wechat.state=published` / `platform_posts(wechat,published)` / 任一 `data/*.json` 的 title 字段与 h1 逐字相同 / `wechat_standalone.json` 提到该 case ⇒ 恰好 **13 篇**，可清理恰好 **175 篇**。**实测**：`python -X utf8 scripts/clean_title_caliber.py --check` → `带旁注产物 188 篇 ｜ 已发布保护 13 篇 ｜ 可清理 175 篇`；`--apply` → `已备份 175 份原件 → _backups/title-caliber-2026-11-04` / `已清理 175 篇 <h1> 旁注；13 篇已发布产物未动`；`--verify` → `验收：baseline 188 ｜ 保护 13 ｜ 已清理 175 ｜ 错误 0` + `除 <h1> 外零字节改动；13 篇已发布产物逐字未动；正文数据卡旁注保留`（逐篇用备份把 h1 换回期望值后比对全文）；再 `--check` → `可清理 0 篇（幂等）`；`publish_guard.py --scan-titles` → `扫到 401 个标题，命中 block 0 条`；`cd scripts && python -X utf8 -m unittest test_clean_title_caliber test_publish_guard test_platform_tone test_publish_one test_toutiao_publish -q` → `Ran 188 tests ... OK`；`git status --short -- data/` → 空（台账零污染）。⚠ **判据校准**：清理后标题普遍以 `$835` 数字收尾，旧 `_CUT_TAIL` 把 `\d` 算「半个词」⇒ 尾字可疑 warn 从 172 **涨到 200**（187 条误报）；拆成 `_DANGLING_TAIL`（warn，不含数字）后降到 **15**（13 篇保护对象的「核对→对」+ 2 条真实口语化尾字），而「长度恰好等于平台上限 + 尾字数字」仍判 block（11-02 头条「拿下 10」硬切实锤）。⚠ **0 block ≠ 标题全好**：清完主体仍是 `Name：MRR $X` 模板（有名字 + 可核验数字，守卫不判 block），13 篇已发布产物仍带旁注，均按批准未动；此处当时写的「要彻底去模板化须逐条人工改写」是**表述错误** —— 用户指出项目已接 AI，改写动作应交给模型，见 #53（本轮已用 LLM 改完 175 篇）。⚠ 口径旁注仍保持 `warn` 不升 `block`：13 篇已发布产物按批准保留旧标题，升级会把它们硬拦；生成器 `_clean_title_hook()` 已修，新产物不会再带旁注。本次未发布、未推远端、未提交 |
| 53 | **用 LLM 彻底去掉 175 篇公众号标题的机械模板**（新增 `scripts/wechat_ai_titles.py`（`--check`/`--generate`/`--apply`/`--verify`/`--dry`/`--limit`/`--only`/`--scope`）、`scripts/test_wechat_ai_titles.py`（26 项）、`data/wechat_title_overrides.json`（175 条只读覆盖表）；`make_article.ai_title_overrides()` 接入 `build_titles()`，人工 `TITLE_OVERRIDES` 优先） | 🗣 2026-11-04 用户质疑 #52 的结论「彻底去模板化得逐条人工改写 175 个标题？为什么一定要人工？我们已经接了 AI」——「人工」表述不准确，收回：不能交给模型的是**事实裁定**，不是改写动作。项目已有 `ai_verify.llm()` 三级链与 `xhs_ai_titles.py` 的「AI 只出短语 + 本地拼装 + 硬校验」范式。**取证**：`out/articles/*.html` 里 `^.+：(MRR\|近 30 天收入)` 模板标题共 203 篇，其中上一轮按批准清过旁注的 175 篇与已发布三源求交 **0 篇**（`wechat_published.status=published` / `publish_manifest.wechat.state=published` / `platform_posts(wechat,published)` 并集 39 篇）；另有 15 篇同类模板（5 篇已发布）做成 `--scope all` 可选项、默认不跑。**分工**：每批 10 条只喂 `cases.json` 事实原文，模型只输出 `{"titles":{cid:标题}}`；代码硬校验 ① 数字集合必须是素材原文子集 ② 中文数字串与「美元/万元」等约数单位一律拒（3 条试跑实锤模型把 `$835` 写成「八三一美元」、`$4,666` 约成「四千多美元」，绕过阿拉伯数字子集守卫）③ 产品名原样保留（`normalize_name()` 归一大小写、漏名时 `inject_name()` 三种形态本地补名）④ 12–64 字、禁 AI 套话 / 口径旁注 / 外链 ⑤ `publish_guard.scan_title()` 无 block 与关键 warn ⑥ 全表标题不撞车；不合格带「原因 + 上一版答案」重试一次，仍不合格**保留原题**。**实测**：`--generate --limit 3 --dry` → `合计接受 3 ｜ 拒收 0 ｜ 1.9s ｜ 主/Sage-A1.5`（换端点后必抽真实候选验正文，不是只 ping）；`--generate` → 18 批 `合计接受 160 ｜ 拒 15 ｜ 52.4s`；**判据校准**：15 条拒收里 13 条是「叙述与金额口径粘连」误报（旧正则把「月收入 $375」「AgentsKit 近 30 天收入 $1,139 卖的是开发效率」当断面），收成只认「近N天…$」零分隔糊块后重跑 → 172 条，再两轮定向 → `175/175`；`--apply` → `已备份 175 份原件 → _backups/wechat-ai-titles-2026-11-04` / `已写入 175 篇新标题（只改 <h1>）`；`--verify` → `守卫：block 0` / `验收：覆盖表 175 条 ｜ 逐篇核对 175 ｜ 保护 48 ｜ 错误 0` / `除 <h1> 外零字节改动；数字全部来自素材原文；已发布与 13 篇保护对象未动`（逐篇用备份把 h1 换回期望值后比对全文）；`--check` → `模板标题 175 篇 ｜ 已生成 175 ｜ 待生成 0`（幂等）；句式体检 `175 唯一 / 0 仍匹配模板 / 仅 1 条含中文冒号 / 0 中文数字串 / 平均 32.2 字 / 61 条带可核验金额`；`publish_guard.py --scan-titles` → `401 标题 / 0 block`；验收发现 `angel-match` 收尾「有 317 个」是断面，用新增 `--only` 定向重做为「Angel Match 把融资信息不对称做成了 12.4 万条线索的订阅」，warn 从 16 回落到 15；`cd scripts && python -X utf8 -m unittest test_wechat_ai_titles test_make_article test_clean_title_caliber test_publish_guard test_platform_tone test_publish_one test_toutiao_publish -q` → `Ran 218 tests ... OK`，`test_wechat_ai_titles` 单跑 `Ran 26 tests ... OK`。⚠ **残留**：13 篇已发布产物仍带口径旁注（按批准不动）、口径旁注仍 `warn` 未升 `block`；175 条全为陈述句（0 问句），句式分化靠具体名词与动作而非疑问句式；`--scope all` 未跑；全量 2 个既有红未收（`test_agnes_cover` 缺封面主题、`test_make_article` 的 `corrections` 黑话，均不引用本轮模块）。本次未发布、未推远端、未提交 |
| 54 | **标题口径升到「标题 = 产品说明」，金额全平台硬禁**（`publish_guard.TITLE_MONEY` 作为唯一判据、`wechat_ai_titles.MONEY` 降为别名；`scan_title()` 金额闸由「技术平台专属」扩到**九平台全量**、`published=True` 降 `warn`；`wechat_ai_titles` 新增 `--scope money` / `local_no_money()` 本地降级 / `_BIZ_GATE` 工商简介闸 / 英文词白名单 / `inject_name()` 改自然形态；`wechat_publish._MONEY_ANY` 补半角 `¥`、全角逗号、中文约数；`test_publish_guard` 4 项 + `test_wechat_ai_titles` 新增 `MoneyGate` 类） | 🗣 2026-11-04 用户给出禁金额的**理由**而不只是指令：「标题是产品的简要介绍或产品说明。这个金额是不准确的，金额并且是动态的」。此前只有 #19/#21 在草稿与 #36 在腾讯云立了这条规矩，**正文标题从未被约束** —— 全仓 293 篇 `<h1>` 里 77 条带金额（`$835` / `4 万美元` / `¥1.01亿` 三种写法都有）。**判据三处漏洞一次修完**（都是先拿实际语料回扫才发现的，凭空列模式必漏）：① `wechat_publish._MONEY_ANY` 的字符类只有全角 `￥`，实测 10 条封面文案里 **8 条用半角 `¥`（U+00A5）** 全部漏判；② `[\d,.]` 不认全角逗号，`¥1，234` 只截到 `1`；③ 中文约数 `[百万千亿]{0,2}` 漏掉「几百万美金」（「几百万」＝「几」+「百万」的两字组合）。**「金额」与「规模」必须分开**：用户数/条数/篇数/粉丝数不是金额，禁掉会让标题失去信息量（`12.4 万条线索`、`5 万个招聘页` 全部保留）。**执行**：77 条 → 70 条 AI 改写（沿用 #53 的「改写归模型、事实裁定归代码」，数字子集/中文数字/产品名/撞车四道硬校验）+ 7 条本地降级与定向重做（`local_no_money()` 只在素材原文里摘子句，不生成新事实）；另定向重做 9 条（2 条草稿金额、6 条把标题写成了工商简介、`cartboss` 改稿后新冒出「一条短信能带回几十美元」）。**新增三道内容闸**：`_BIZ_GATE` 拦「<产品名> 由创始人 X 于 Y 年创立，官网…」这类把工商信息当标题的形状（`what_it_does` 里带工商信息，降级素材必须限 `cover_desc`/`one_liner`）；英文词白名单要求标题里每个英文词都能在素材原文 ∪ 产品名里找到（模型实锤编出 `claudethings`、`Calzy`，后者真名是 `Calsy`）；`inject_name()` 不再用 `｜` 拼接（断面判据误报），改「名字：说明」/「句子，名字」。**判据校准两处**：`local_no_money()` 首版把 `$12,775` 的千分位逗号当子句分隔符，降级后只剩 `775` ⇒ `re.split(r"[，。；;、]|,(?!\d)")`；`--only` 重跑时把自身旧题也加入 `seen` 导致整轮「撞车」⇒ 加 `exempt`。**改标题还有个连带陷阱（跑全量才暴露）**：`retone_drafts.HTML_TITLES` 记着这 6 篇公众号的定稿 `<h1>`，AI 把金额从这里去掉后该表仍留着带金额的旧值 ⇒ 再跑一次 `retone_drafts --apply` 就会把 AI 的成果**洗回金额标题**，同时 `test_platform_tone.test_retone_plan_is_idempotent` 转红（全量从「2 个既有红」变 3 红，很容易被当成「又一个既有红」放过去）。已把 6 条同步为定稿逐字，并在表头写明「改标题后必须同步这里」；另 `ai-text-humanizer-netherlands-belgium` 首版被模型写成「当地下创作者把草稿交给 X 时」（状语从句无主句，`_DANGLING_TAIL` 的虚字表不含「时」所以没报）⇒ `--only` 定向重做为「…帮创作者把草稿变成可发布的文章」。**实测终态**：`publish_guard.py --scan-titles` → `401 标题 / 0 block`；`wechat_ai_titles.py --check --scope money` → `待生成 0`（幂等）；`--verify --scope money` → `覆盖表 192 条 ｜ 逐篇核对 192 ｜ 保护 48 ｜ 错误 0`，`除 <h1> 外零字节改动`；全仓 `<h1>` 293 篇仍带金额 **32 条 = 已发布 23 + 13 篇保护对象里的 9 条**，**未发布/未保护的残留 0**；覆盖表 192 条（175 上轮 + 15 历史 + 2 草稿）唯一 192、平均 31.4 字、**写成工商简介的 0 条**；`python -X utf8 -m unittest test_platform_tone test_wechat_ai_titles test_publish_guard test_clean_title_caliber -q` → `Ran 100 tests ... OK`；`retone_drafts.py --check` → `计划改动 0 个文件`。**封面不用跟着改**（用户追问「标题改了封面上的介绍文字要不要改」）：`tt_cover_html()` 自 2026-10-02 起只渲染「项目名 + 一句话介绍」，第二行取值链 `cover_kv.desc > cover_desc > one_liner > what_it_does 首句`，**整条链不读标题**；带金额的 10 条封面文案本轮已清空（现 0 条），且这 10 个案例**都没有生成 `out/toutiao/<id>/cover.png`**，无需重制图片。⚠ **残留**：32 条带金额全在已发布/保护集合内（不重发即无影响，按批准不动）；`published=True` 降 `warn` 是为保住已发表列表的标题认亲，不是判据放松。本次未发布、未推远端、未提交 |
| 55 | **内部流水线黑话从数据与产物里清干净**（新增 `scripts/scrub_jargon.py`（`--check`/`--apply`/`--verify`/`--artifacts`）、`scripts/test_jargon.py`（12 项）；`publish_guard.scan_content()` 新增 `内部黑话` block 规则（判据复用 `scrub_jargon.JARGON`）；`ai_verify.SYSTEM_PROMPT` 加第 7 条铁律；清 `data/cases.json` 214 处 + 39 个产物文件） | 🗣 承接 #54 收掉两个既有红的第二件：`test_make_article` 的 [10d] 报 `corrections` 里有 5 处内部黑话（`pageturner.truth`、`aera-browser.truth`、`arc-raiders-map.claim`×3）。**那不是 5 处**：全库按 9 类可见字段回扫是 **168 处、145 条案例**（`corrections[].claim` 73 / `.truth` 47 / `metrics.metric_note` 43 / `signals` 2 / `how_it_makes_money`·`founder`·`corrections[].source`·`sources[].label` 各 1），全部落在 11-03 指标刷新的那批**还没成文**的案例上 —— 当时产物里确实没有，但下一批写稿就会印出去（判据只看 `corrections` 两个字段，是这套漏网的直接原因）。**读法**：`候选 / 入库时 / 抓取原文` 是流水线自用词，`headline / last_30d_revenue / price_point / monthly_timeline` 是我们自己的 json key（页面上印的是 `Last 30 days`），读者看到只会以为页面坏了。**改法（只换说法，不动数字）**：`data/cases.json` 214 处 → 「候选资料 headline 字段」→「（据 TrustMRR 档案）」、「早期入库时收入未公开」→「早期收录时收入未公开」、「见 corrections」→「见纠错记录」；产物侧 39 个文件（`out/articles/*.html` 34 个带「入库时」、`magicslides-app`/`vectosolve`/`1lookup` 等带字段名）定点替换。**四条硬保险**：① 每个字段比对金额/百分比/日期序列，动一个就整批中止；② 引号内是页面原文，一律不改（改了就是伪造引文），但引号里装的是 json key（`'last_30d_revenue':`）时改；③ `metrics.caliber="last_30d"` 这类**枚举值是判据输入不是文案**，可见字段用白名单列，绝不遍历整个 dict；④ 产物写入前校验 `<h1>` 未变 + 标签数不变 + 金额集合不变。**正当术语必须保护**（三个真踩）：`候选人`（jobright 讲「候选人质量」）、`候选集`（腾讯云 mort 讲检索粗筛）、`候选表`（反向样本方法论里定义过）—— 裸 `候选→档案` 会造出「档案人」「档案集」这种错句，所以判定与改写两头都先掩掉 `PROTECTED`。⚠ **判据两次假绿**：`residual()` 一开始拿「洗过的文本」判残留，数据一个字节没写进去也报 `残留 0 处`；`--artifacts --verify` 只扫「这轮改过的文件」，被保护的文件根本没进判据。两处都改成「只读盘上的原文 + 与改动范围对齐」。**实测**：`--check` → `待清洗 214 处 ｜ 145 条` → `--apply` → `已写入 214 处（数字逐字段核过）` → `--verify` → `残留 0 / 数字漂移 0 / 结构差异 0`；`--artifacts --apply` → `39 个文件` → `--verify` → `0`；再跑 `--check` → `待清洗 0 处`（幂等）；`ai_verify.SYSTEM_PROMPT` 加第 7 条（把这次的两个反例直接写进 prompt）；`publish_guard.scan_content` 加 `内部黑话` block ⇒ 以后草稿/产物带这些词过不了闸。⚠ **残留**：「候选表 / 候选集 / 候选人」作为正当术语保留（判定掩掉）；`anti-vii-caliber` 方法论文章里的「候选表」不动；本次未发布、未推远端、未提交 |

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
| **P1** | ~~存量草稿形态未重建~~ **已于 11-04 解决（#51）**：6 篇机械壳按平台调性重写、1 篇与腾讯云同稿拆开、7 篇人工长稿定点清理；13 条问题标题全部改写 | `retone_drafts.py --verify` → 401 标题 0 block / 水印罐头句 0 / 跨平台同稿 0 / 调性禁忌 0；原件在 `_backups/retone-2026-11-04/`。⚠ 只改本地产物，平台侧已发布标题未动（要改需另走发布/更新流程） |
| ~~P2~~ | ~~175 篇清理后主体仍是 `Name：MRR $X` 模板~~ **已于 11-04 解决（#53 + #54）**：LLM 批量改写为 175 条唯一、具体句式标题，事实裁定全部交给代码硬校验；再把「标题 = 产品说明、禁金额」升为**九平台硬闸**，77 条带金额标题清到残留 0（见 #54）。`wechat_ai_titles.py --verify --scope money` → 错误 0 / `publish_guard --scan-titles` → 401 标题 0 block | 仅剩 **13 篇已发布产物 `<h1>` 仍带口径旁注 + 其中 9 条带金额**（按批准方案 ① 不动，避免与已发表列表认亲错位；不重发即无影响）；`--scope all` 另有 10 篇同类模板未跑，须另行确认。**封面无需同步**：封面第二行取值链不读标题（见 #54 末段） |
| ~~P1~~ | ~~全量测试 2 个既有红~~ **已于 11-05 收掉**：`test_agnes_cover` 补 `crypto web3` + `cat_key()` 大小写归一（`utilities` 现在和 `Utilities` 同一套风格）；`test_make_article` 的 5 处内部黑话按全库口径扩到 168 处一并清完，并加 `内部黑话` block 闸防回流（见 #55） | `python -X utf8 scripts/run_all_tests.py` → 全绿；`scrub_jargon --verify` → 残留 0 / 数字漂移 0；`--artifacts --verify` → 0 个文件 |
| **P1** | 案例视图不渲染 `verification_downgraded` / `source_kind` / `tier_reason` / `quality_score`（`caliber` 已在反向样本区渲染，见 §6.1 #18；案例卡仍不显示） | 「曾被机器降级」等诚实度卖点对读者不可见，透明度未打满 |
| **P1** | 公众号草稿列表窗口只拉最近 50/60 篇，`refresh` 按标题反查 | 老草稿既不能补封面也不能刷新；`appmsgid` 缺失条目只能手工定位 |
| P2 | 公众号编辑器 DOM 选择器是硬编码实测值（`cover_input`/`save_draft` 仍为 `None`） | 微信改版会全线失效 |
| P2 | `deploy_oss` 全量覆盖、无版本化、孤儿文件不清理 | 删文件需手动 |
| P2 | 台账靠半自动回写：群发后 `status=published` 需跑 `sync` | 四平台都存在「已发但台账未更新」的窗口 |
| ✅ 已解决 | 头条「群发」与「给已有草稿换封面」仍 UI | 2026-10-18 打通：群发=`save=1`+`entrance=main`（同一端点）；换封面=`cover --api`（带 `pgc_id` 更新）；另补 `unpublish` 删已发布文章 ⇒ **六项全通** |
| P2 | 小红书**草稿**只在浏览器 IndexedDB，换机器即丢；**创建笔记**被风控拦死（461/verifytype=124） | 草稿只能同 profile；创建只能走 UI；读列表已改用 `xhs_webapi.py`（秒级）；**删除已可纯 API**（`xhs_delete.py`） |
| ✅ 已解决 | 小红书台账滞后（线上 9 vs 本地 2） | 2026-10-18 `xhs_sync_ledger.py --apply` 回填，重复发布已标注 |
| ✅ 已解决 | 小红书 prosp 重复发布两篇（12:09 / 22:14） | 2026-10-18 `xhs_delete.py --apply` 删 22:14 一篇，留原发；线上 9→8，台账同步 |
| P3 | `README.md` / `公众号-发布计划.md` 数字过期（2026-10-21 复核：仍写「当前 24 条案例」） | 文档误导 |
| P2 | **采集队列积压：135 条 `deep` 级素材长期卡在 inbox**（候选池只剩 1 条） | 不是代码问题，是运营节奏。`promote_to_candidates.py --write` 已按 triage 分数降序选稿、2 并发防风控；队列 279/400，不推进就会撞上限开始溢出 |

---
| 56 | **封面回读从注脚变成可执行闸**（新增 `scripts/cover_verify.py`（`--case/--platform/--live/--clear/--report/--matrix`）、`scripts/test_cover_verify.py`（21 项）、台账 `data/cover_readback.json`；`publish_guard` 新增第 ⑤ 道 `check_cover_remote()` 并入 `audit()`；`publish_one.GATES` 九平台全加 `cover_remote` 闸；`yun_finalize` 新增 `LAST_COVER`/`_set_cover()`/`_record_cover()` 并把腾讯云、阿里云发表结果写进台账） | 🗣 承接 §8 第 8 项剩下的那件：`⛔ 本地有 PNG ≠ 平台侧封面已设置` 这条一直只有注脚 —— `publish_guard.check_cover()` 只查本地 PNG 在不在、命令带不带 `--cover`，**平台侧字段是空的它看不见**。2026-11-04 CSDN 三篇草稿「本地有图、coverImage 全空」就是这条洞第一次付账。**先核能力再写代码**（9 个平台逐个读实现，不照文档抄）：真能回读的只有 **4 个** —— B站（草稿接口 `origin_image_urls`/`image_urls`；🚨 `banner_url` 在该接口里**恒为空串**，拿它判会永远误判成没封面，`_cover_of()` 里有定论）、CSDN（`csdn_list_drafts.list_status()` 的 `coverImage[0]`，走点 tab 抓页面自己的请求，不是那个至今没打通的签名 GET）、腾讯云（发表后 `verify_cover_online()` 读线上卡片缩略图的 CSS `background-image`；`og:image` 是平台默认分享图不能用）、阿里云（`draftDetail.coverUrl`）；另外 5 个**没有可读字段**：公众号 `SELECTORS['cover_input'] is None`（脚本压根不上封面，群发前必须人工选首图）、头条（题图随发布请求一次提交）、小红书（首图即封面）、博客园/知乎（两个脚本里 `cover`/`封面` 一个都不出现）。**五态而不是两态**是这模块存在的理由：`ok`（真读了、有 URL）/ `empty`（真读了、是空的，**唯一 block**）/ `unknown`（没读、台账无记录、记录超 30 天，只 warn）/ `local`（平台侧无可读字段，warn）/ `none`（脚本不设封面，warn）——把「没查过」写成「通过」是本仓库最贵的一类假绿，所以读不到一律 `unknown`。**三道保险**：① `record()` 收到无证据的 `ok` 直接降级 `unknown`（「结果打印了但没人读」那类假绿）；② `empty` 是 block，但给人留了逃生口 `--clear`/`--live`（否则人工已在后台补好封面仍会被旧记录永久拦死）；③ TTL 30 天，三个月前的 `ok` 不替今天作证。**顺手补掉两个真洞**：阿里云 `finalize_aliyun()` 的 `coverUrl` 复核**只打印不判定**（`LAST_COVER` 之前它连一行结论都没有）；阿里云发表时 firstImg 上传失败**静默跳过**（`if u:` 没有 else）⇒ 现在「本地有图、文章里没图」发不出去也不知道。**实测**：`--matrix` → `九平台里只有 4 个有可回读的封面字段（bilibili、csdn、tencentcloud、aliyun）；其余 5 个不能回读，闸只出 warn`；`--case chessigma --platform csdn` → `状态 unknown ｜ 台账没有回读记录…未回读，不能当通过`；`publish_guard --platform csdn --case chessigma` → 新增 `cover_remote ⚠ 封面未回读`（不进 block，`block 1 条` 仍是原来的标题项）；`publish_one --platform csdn --case chessigma`（干跑）→ `✓ cover_remote ⚠ 台账没有回读记录…`；`test_cover_verify` → `Ran 21 tests OK`；`test_publish_guard` 39 → **49 项 OK**；`test_publish_one` 16 项 OK。⚠ **未跑通的**：`--live` 的 CSDN 探针在本机返回 `未回读：CSDN 列表读取失败（✗ Chrome CDP 没起来（端口 9224））`（`netstat` 有 8 条到 9224 的 `TIME_WAIT`、没有 `LISTENING`，15 个 chrome 进程在跑 ⇒ 环境问题不是判据问题），**只证明了失败路径正确（读不到绝不写 ok），没证明成功路径**；B站探针同样未实跑。存量已发布记录尚未回填，闸对它们一律 `unknown`（诚实但无用），需跑一轮 `--live` 或发表链路补。本次未发布、未推远端、未提交 |
| 57 | **正文数据卡加「数据时点 + 口径」一行，并把「缺时点」做成发布 warn**（`make_article.timepoint_row()` + `CALIBER_TEXT` 口径字典、`publish_guard.TIMEPOINT`/`MONEY_METRIC` 与 `数据缺时点` warn；`ai_verify.SYSTEM_PROMPT` 加第 8 条；`test_make_article` 80 → **86 项**、`test_publish_guard` 46 → **49 项**） | 🗣 用户给出的是**理由**不是指令：「标题是产品的简要介绍…这个金额是不准确的，金额并且是动态的」——#54 只把金额从**标题**里清掉了，正文数据卡仍然只印数字、不印时点，读者看到「MRR $244,029」会当成今天的数（`1lookup` 的 `verified_at` 是 **2026-09-20**，正文里一个日期都没有）。**只印日期也不够**：`last_30d` 那种滚动 30 天流水常比 MRR 高一个量级（实测有佣金型业务高出三十余倍），不标口径就会被当经常性收入引用。所以生成器在数据卡末尾压一行 `数据时点 ｜ 截至 YYYY-MM-DD（抓取日快照）｜口径：…`，**时点取自 `verified_at`、口径取自 `caliber`，两者都不许编**（没有 `verified_at` 就只印口径，两样都没有就不出这一行）。`CALIBER_TEXT` 必须覆盖库存实际用到的全部口径（mrr 345 / last_30d 82 / arr 20 / lifetime 6 / annual 5 / run_rate 2），缺一个就会把枚举值直接印给读者 —— 这是 `test_make_article` 里新锁的一条。**判据校准（一次真误报）**：`TIMEPOINT` 第一版只认「数据时点」和「截至 YYYY-MM-DD」，把散稿里最常见的「（TrustMRR 从支付网关直读，**2026-10-14 同步**）」全判成缺时点 ⇒ 草稿 17 篇全误报、产物 261 篇里 **202 篇是误报（真缺的只有 59 篇）**；补上「日期 + 同步/抓取/核对/更新/核实」「抓取/同步/时点/截点 + 日期」两种写法后回落到草稿 8 篇 / 产物 59 篇。一条天天误报的 warn 等于没有 warn。**存量处置分了两种，没有一刀切**：① `out/articles/*.html` 这 293 篇是**本地产物**，读者看的是平台上的版本，改本地 HTML 不改远端 ⇒ **不回落**（回填对读者零收益，还要动 261 个已发布文件）；② 真正值得补的是还没发出去的散文草稿，实测只有 shipfast 一家两篇（`cnblogs/shipfast`、`csdn/shipfast`）真缺，已各补一句「以上是 2026-09-11 抓取 TrustMRR 时的快照」/「快照时点 2026-09-11，之后仍会变化」；另 3 篇（clawlink / chessigma / cod-dex）本就有「2026-10-14 同步」，是误报。**实测**：`make_article --id sotie-ai-best-friend --out tmp/_tp` → md 表格末行`| 数据时点 | 截至 2026-11-04（抓取日快照）｜口径：近 30 天滚动流水，含一次性收入，不等于经常性收入 |`、HTML 同一行落在 `<p>` 卡末；`test_make_article` → `结果：全部通过（86 项）`（含「时点行压在数据卡最末」「没有 verified_at 不编日期」「既无日期又无口径不出这一行」）；`publish_guard --platform cnblogs --case clawlink --file …` → `⚠ 数据缺时点`（校准后该篇不再报）；两篇 shipfast 补完后 `template ✓ / block 0 条`。⚠ **残留**：仍报缺时点的 6 篇全是 juejin（已永久停发）或已发布（按约束不动）；已于 11-04 发布的 13 篇带口径旁注产物按批准不动。另：`--scope all` 的「10 篇同类模板」这一待办**前提已不成立** —— `wechat_ai_titles --check --scope all` → `模板标题 192 篇 ｜ 已生成 192 ｜ 待生成 0`、`--verify --scope all` → `错误 0`，该题在 11-04 的 money 那轮之后已经做完。本次未发布、未推远端、未提交 |
| 58 | **案例库真值迁进 SQLite（`case_library` 表），`data/cases.json` 降为逐字节镜像**（新增 `scripts/case_store.py`（`load_cases`/`load_by_id`/`load_ids`/`get_case`/`count`/`save_cases`/`save_case`/`export_mirror`，CLI `--count/--export/--check`）、`scripts/test_case_store.py`（11 项）；`db.py` 新增 `CASE_TABLE`/`CASE_JSON_FIELDS`（24 个结构化字段）/`_CASE_LATE_COLUMNS`（14 列幂等 ALTER）/`field_order` 列/未知字段警告；`server.load_json`·`save_json` 与 `harvest.load_json`·`save_json` 收口；17 个读取点、10 个写入点改走 case_store；`preflight` 新增「库/镜像同源」检查） | 🗣 用户问「现在还用 cases.json 吗？可以换成 sqlite 来处理吗」，并在给出三个方案后选了**全量迁 SQLite**。**先核事实再动手**：`data/ledger.db` 早就是 SQLite（`platform_posts` 发布台账 + `verifications`，20 个脚本在用），但案例库本体一直在 2.86MB 的 `cases.json` 里（77 个脚本引用：77 读、8 写；`build_static`/`prerender`/`make_article` 全从它取数）。**为什么另起 `case_library` 而不是改老 `cases` 表**（三条实测）：① 老表 501 行 = 479 条 `type='case'` + 22 条 graveyard，而 `cases.json` 只有 **461** 条 ⇒ 它是陈旧镜像，改它等于给 18 条脏数据转正；② 老表是「cases/graveyard/anti_case 三份 JSON 合并」的设计，迁移后真值只对应一个文件，混在一张表里每个写方都得记得加 `WHERE type='case'`；③ `platform_posts` 有 `FOREIGN KEY (case_id) REFERENCES cases(id)` 且 `PRAGMA foreign_keys=ON`，SQLite 改不了外键 ⇒ 不能「改名旧表→复用 cases 这个名字」，只能并列。**开工前先查 schema 差异**，查出 cases.json 有 **14 个字段表里根本没有**（`china_fit`/`solo_fit`/`composite` 两张评分表 461 条全有、`cover_desc` 45 条、`segment`/`segment_reason`/`needs_review`/`human_read`/`human_read_at`/`judgment`/`independent_check`/`verification_downgraded`/`sources_from_candidate`/`exclude_from_solo_cases`），而 `upsert_case()` 的规则是「只写表中存在的列，未知字段忽略」⇒ **第一次写库就会静默丢掉这 14 个字段**。补列 + 幂等 `ALTER TABLE`（`CREATE TABLE IF NOT EXISTS` 对已存在的表不生效，老库必须显式补列）+ `upsert_case()` 对未知键**发一次警告**（每表每键一次，不刷屏）。**逐字节镜像**是这次设计的关键：SQLite 存不了有序对象，若按表列顺序导出，461 条键序全部错位 ⇒ 迁移那一次提交变成 2.8MB 整文件 diff，评审等于失效；所以加 `field_order` 列记住每行原 JSON 键序，导出按 `rowid` 序（= 原数组顺序）+ 原键序还原、剥掉 `type`/`deleted`/`created_at`、值为 `None` 的键不写、不补末尾换行（实测原文件末字节是 `]`）⇒ **迁移前后 `data/cases.json` 字节完全相同**（`git diff --stat` 为空）、`git status --short data/` 干净。**迁移面**：读取点 17 个（15 个本地 `load_cases`/`all_cases` + `e2e_verify_write` 两处直读；保留各自返回类型 —— 12 个返回 list、2 个返回 `{id: case}`、1 个返回 id 列表；`make_article.load_cases(path)` 保留 path 语义，传非默认路径时仍读那个文件，测试才好喂临时 JSON），写入点 10 个（`audit_evidence`/`backfill_tier`/`cover_desc_ai`/`fill_caliber`/`fill_missing_replicability`/`fix_replicability_dims`/`independent_source`/`scrub_jargon`/`fit_ready`/`harvest.save_json`）全改成 `CS.save_cases()`（写库 + 重导镜像）；`server.load_json/save_json` 与 `harvest.load_json/save_json` 两处收口一次覆盖 `build_static`/线下 API/`gen_case_drafts` 等一批调用方。**判据（不是「跑通了」）**：① 往返 `461 条 → 导出` 与原文件**逐字节相同**（`语义一致 ✅ / 14 字段覆盖一致 ✅ / 字节一致 ✅`）；② 站点 payload 走库 vs 走文件 **`cases` 列表完全一致、12 个顶层键零差异**（`server.build_payload()` 两次调用，中间只把 `load_json("cases")` 换成读文件）；③ `case_store --check` → `镜像与库一致：是（2860451 bytes）`；④ `run_all_tests.py` → **66 个文件 0 失败**、`ledger.db + 59 个 data/*.json` 指纹零污染；⑤ `test_case_store` 11 项（往返逐字节 / 14 字段不丢 / 键序还原 / 内部列不入镜像 / save 后镜像自动更新 / prune 是软删 / LEDGER_DB 隔离 / 空库回落 / 非默认路径读文件）。⚠ **踩到一个真洞并修掉**：测试把 `LEDGER_DB` 指到临时库、但**镜像路径还指着真 `data/cases.json`** ⇒ `test_harvest_sources` 的 `save_json("cases", [{"id":"a"},{"id":"b"}])` 顺手把真镜像写脏（多两条空壳案例），连带 `test_verify_rules` 报「存量被拦」、`test_make_article`/`test_prerender` 变红。修法两条：`mirror_path()` **镜像跟着库走**（库不是默认库时镜像落到库旁边；读回落另外走 `_readable_mirror()` 找进 git 的那份），以及 `_guard_real_write()` 在 unittest 里没有 `LEDGER_DB` 时**直接抛异常**，把静默污染变成一声巨响。同时把 `test_atomicio` 的断言从「fit_ready 里直接调 atomicio」改成「必须调 `CS.save_cases` + `case_store` 导出必须走 atomicio」。⚠ **新克隆行为**：`data/ledger.db` 在 `.gitignore` 里 ⇒ 版本库里的真值是 `data/cases.json` 镜像，工作库是 SQLite；新克隆没有库时 `case_store` 回落读镜像并**明确打印**提示，`import_to_db.py --cases` 可重建（实测 `cases.json → case_library 461 条`、`graveyard.json → cases 22 条`）。本次未发布、未推远端、未提交 |
| 59 | **头条「预览并发布」误标已发布的根治：回读后才记账，顺手修掉 `upsert_post` 静默丢写**（`toutiao_publish.py` 新增 `_settle_ui_publish()`（点完发布后回读已发表列表 + 草稿箱双查再定案）/`mark_auditing()`（state=auditing 占号）/`_extract_article_gid()`/`_OCCUPIED_STATES`（与 `publish_guard.PUBLISHED_STATES` 同值，测试锁相等）；删除无条件 `mark_published()`；`--yes` 与 `--api --send` 退回 UI 路线两条路都改走回读；`db.upsert_post()` 补 `conn.commit()`；新增 `scripts/test_toutiao_settle.py`（14 项）） | 🗣 用户先要求「彻底修改 42 和 45」两条互相矛盾的记忆条目（42 旧标题写「`--yes` 是真发布」、正文却写「发布并未完成」；45 写「不真发布但误写台账」），以代码定案时发现两边都把后果写轻了：`mark_published()` 写 `state=published` 但 `remote_id` 全空，而 `publish_guard` 的 dedupe **只认 state** ⇒ 真实后果不是「台账脏」而是**这篇从此被 dedupe 拦掉、再也不会发出去**（漏发，不是重发）。用户随后拍板「要改」。**定案的事实**（逐行读 `toutiao_publish.py`/`publish_guard.py`）：`publish_one()` 不传 `--yes` ⇒ `_wait_autosave()` + `mark_drafted()`（这才是存草稿，头条发文页没有「存草稿」按钮）；传 `--yes`（CLI 里 `yes=(args.yes or args.send)`）⇒ 点「预览并发布」→ `sleep(4)` → **无条件 `mark_published()`**；`--api --send` 取不到 anti_token 时 `print("[api] 退回 UI 路线")` **静默退化**成上述误标路径——这就是当初 42 写成「是真发布」的由来：一次观察落在 API 路线、一次落在 UI 路线，各自都对，合起来矛盾。**修法是「记账必须带回读证据」，不是改状态名**：`_settle_ui_publish()` 点完发布后 ① 已发表列表命中 → `mark_published_remote(cid, gid, evidence)`（带 gid，新增 `_extract_article_gid` 从已发表接口条目里提取）；② 只在草稿箱 → `mark_drafted`（仍是草稿，如实记）；③ 双读均未命中 → `mark_auditing`（多半在审核队列；**必须占号**否则会当没发过而重发，且不写 `toutiao_published.json` 快照——那是「已发表」口径，塞进去把篇数算多）。`_ids()` 的已占号判定改用 `_OCCUPIED_STATES`（`("published","auditing","blocked","审核中")`，与 `publish_guard.PUBLISHED_STATES` 逐值相等，`test_toutiao_settle` 有相等断言防单边漂移）。**顺手挖出并修掉一个更老的洞**：`db.upsert_post()` 从不 `commit()`，而调用方普遍 `get_conn() → upsert_post() → close_conn()`，`close_conn()` 只是 `close()` —— sqlite3 对未提交事务**静默回滚**，实测写完立刻另开连接查、行不存在；受害面是所有「单行写」调用方（`toutiao_publish.mark_*`、`wechat_publish.save_published`…），JSON 快照看着有、SQLite 里没有，而 dedupe 只认 SQLite ⇒ 这层台账一直等于没写；只有显式 `conn.commit()` 的 `ledger.py` 是通的，所以从未暴露。⚠ 不改成在 `close_conn()` 里统一提交：那里区分不了「正常结束」和「异常中途」，异常时提交半截数据比丢写更糟。**实测**：`test_toutiao_settle` → 14 项 OK（已发表命中记 published+gid / 仅草稿命中记 draft / 双未命中记 auditing / auditing 不写已发表快照 / `_OCCUPIED_STATES == publish_guard.PUBLISHED_STATES` / 干跑不写台账 / 隔离不碰真库）；`run_all_tests` → **67 个文件 0 失败**，`ledger.db + 59 个 data/*.json` 指纹零污染；真库 `platform_posts` 363 行、toutiao 状态分布 `draft 40 / published 46 / deleted 1` 不变。⚠ 时点声明：「UI 点完预览并发布停在预览步」是 2026-10-14 实测、本次只复核代码未复验 UI；真发表路径（真点一次发布按钮）未端到端跑过，下一次真发布时按 `_settle_ui_publish` 的三分支核对 |
| 60 | **`verification` 同名三义写进数据契约，两边互不回填**（`docs/DATA_SCHEMA.md` 新增「Same key, three stores, three meanings」；`scripts/test_promote_templates.py` 补第 9 项回归；`PLAYBOOK-data.md` #37 / `MEMORY.md` 索引一行；`template_fix_20261105.md` §4 由「未处理」改判为设计约定） | 🗣 2026-11-05 用户复核：「候选池 verification 硬编码 stripe 与 verifications.json 的 partial 是两套语义（支付侧直读 vs 证据等级），不互相覆盖」。**取证**：`data/candidates.json` 4 条全为 `verification="stripe"`（`promote_to_candidates.to_candidate():444` 硬编码，作支付网关 API 直读的来源标记），`data/verifications.json` 里 `bubblav` 是 `partial` / `source_kinds=["homepage","review"]`（Polar 支付源只撑得起这一档）；两者写入者不同（`to_candidate()` vs `PUT /api/candidates/:id/verification` 即 `server.py:883`），`import_to_db.py` 也分表，**没有任何脚本把一边的值写进另一边**；唯一交汇点是 `server.py:1087-1134` 晋升时新建 `cases.json` 对象，取 `draft.verification or hit.verification` 再过 `evidence_gap()`，刻意以证据等级为准。⛔ 反向两个错：把 `partial` 回写候选池＝抹掉支付侧直读标记；把 `stripe` 回填台账＝正是红线 28 / `audit_evidence.py` 打击的 A 级虚标。**实测** `python -X utf8 scripts/test_promote_templates.py` → `Ran 9 tests ... OK`；`git status --short` 只有 `docs/DATA_SCHEMA.md` / `docs/PRD.md` / `scripts/test_promote_templates.py` 三个 M，`data/` 零改动。本次未改数据、未发布、未推远端 |
| 61 | **补时点：候选池记「快照日」，晋升时不再被 `now` 覆盖**（`data/candidates.json` 4 条补顶层 `verified_at='2026-11-05'` / `caliber='mrr'`；新增 `verify_rules.resolve_case_timepoint(candidate, draft, now)`，`server.py` 晋升端点 `new_case` 的 `verified_at` / `caliber` 改由它解析；`test_verify_rules.py` 补 `ResolveTimepoint` 5 项；`PLAYBOOK-data.md` #40 / `MEMORY.md` 索引） | 🗣 2026-11-05 用户：「补时点」。**取证**：`server.py:1163` 原为 `"verified_at": now`（晋升当天），而 `make_article.timepoint_row()` 印「截至 X（抓取日快照）」⇒ 今天回源、明天晋升就虚报一天时效。写入依据为 `data/verifications.json` 四条 `caliber_reason` 的「Revenue last synced 2026-11-05」+ 候选 `refreshed_at` + `data_freshness[:10]` 三方对账，口径与 `verifications.json.caliber` / `metrics.caliber` 逐条对照，任一不一致即拒写。**实测**：模拟服务端 `now=2026-11-06` 晋升，4 条仍解析出 `verified_at=2026-11-05`，时点行「截至 2026-11-05（抓取日快照）｜口径：MRR，当前月经常性收入」，`PG.scan_content('wechat')` 的「数据缺时点」warn 草稿正文 1 → 生成正文 0；旧逻辑同条件印 11-06。`test_verify_rules.py` → `Ran 100 tests ... OK`，全量 `run_all_tests.py` → 68 文件 0 失败、`ledger.db` + 59 份 `data/*.json` 零污染。**更正**：上一轮把「草稿正文缺时点行」误记为 `timepoint_row()` 空行（`timepoint_row(case)` 读晋升后的 case，不是候选）⇒ 这次补的是快照日血缘，不是让半成品过闸。备份 `tmp/htm_screen/candidates.bak-20261105-2330.json`；详见 `tmp/htm_screen/timepoint_fill_20261105.md` / `timepoint_verify_20261105.md`。未晋升、未发布、未推远端 |
| 62 | **存量 129 条措辞清洗：先补规则再全量洗，判据刻意不扩**（复现证据：`tmp/htm_screen/scrub_stock_wording_20261105.py` 以 `tmp/draft_text.bak-20261105-wording.json` 为基准只读预演 → 首版 `SCRUB_RULES` 跑 129 条后 **38 条仍违规、11 条命中却零改动**；补齐 7 类旧稿真实形态 + 并合除法规则后实跑 **129→0 命中、131 条改动、294 次替换、金额集合 0 变化**，4 条新稿与无关键逐键零变化；`test_draft_case_text.py` 51 tests OK、`StockWording` 7 项回归。判据外形态混有正当叙述，`wording_residual_20261105.md` 单列清单；只写 `tmp/draft_text.json`，`data/` 七份台账指纹前后一致、未 apply / 未发布 / 未 push） |
| 63 | **候选池约 50 处读写收口到 `scripts/candidate_store.py`**（`load_candidates` / `save_candidates` / `upsert_candidate` 三接口 + 格式探测 + `atomicio` 原子写 + `file_lock`），顺带根治三处潜伏写盘 bug；契约写进 `docs/DATA_SCHEMA.md`「Candidate pool: single I/O entry」 | 收口前同一份 `candidates.json` 被写成三种形态：`apply_draft_text.py` 硬 `.replace("\n","\r\n")`（磁盘实为 **LF + indent=2 + 无尾换行**，且它的「字节级自检」比的是自己刚写的输出 ⇒ 永远通过）、`verify_case.py` 文本模式 `open(...,"w")` + `f.write("\n")` ⇒ 换行被翻译且凭空补尾换行、`sync_headline_numbers.py` / `audit_delisted.py` 原地截断写（无 fsync / `os.replace`）。通用层照 `cases` 先例加分支（`server` / `harvest` / `ai_verify` / `triage` / `htm_increment` / `promote_ready` / `audit_delisted`），专用脚本直接调 CSt；`contentpack_ready` 留三个薄封装 + `CAND_PATH` 兼容 `test_atomicio` / `test_publish_ready`。**实测命令与响应**：`python scripts/candidate_store.py --check` → `OK：格式一致（indent=2 / LF / 末尾换行=无，4 条，7976 bytes）`；真文件副本无改动写回 SHA `6da5952808489a…` 前后逐字节一致、upsert 往返可还原、无 `.tmp`/`.lock` 残留；`python scripts/run_all_tests.py -v` → **69 个文件 0 失败**，`ledger.db + 59 个 data/*.json` 指纹全部一致、零污染。新增 `test_candidate_store.py` **21 项**（含 CRLF+indent1+尾换行、坏 JSON 不当空池、静态闸防退化）。⚠️ 收口中踩到「丢了路径解析」：通用层不传 `path=` 时 `test_fetch_tm_md` 的临时替身失效、打了 3 个 FAIL 并联网抓真候选 ⇒ 所有通用层改为显式传 `path=`，并写进数据契约红线。刻意不收口：测试固件 / `e2e_verify_write` 写脏恢复 / `verify_case` 备份逐字节还原 / `tmp` 一次性历史脚本。未改任何 `data/` 数据、未 apply、未发布、未 push |
| 64 | **晋升后补齐两张人工评分表，派生分仍由脚本单一源算出**（2026-11-06 新增 4 条：`score_china_fit.SCORES` 461→465、`score_solo_fit.DELIVERY` 462→466；草拟 `tmp/htm_screen/fit_scores_draft_20261106.md` 逐条列出 `.md` 依据并用真实评分函数预演派生分，用户逐条确认后由 `tmp/htm_screen/apply_fit_scores_20261106.py` 幂等插入 + 逐字回读） | **实测命令与响应**：`python scripts/score_china_fit.py --top 3` → `已写入 case_library（465 条带 china_fit），镜像 data/cases.json 已重导`；`python scripts/score_solo_fit.py --top 3` → `已写入 case_library（solo_fit 465 条，composite 465 条）`；4 条 china / solo / composite / quadrant 与预演逐值一致（51.3/62.4/53.5/skip、63.5/75.9/66.0/export、51.3/75.9/56.2/export、68.4/67.2/67.4/skip），note 与 delivery_note 与草拟逐字一致；`preflight` → `[PASS] 库/镜像同源 库里 465 条 镜像 465 条` + `[PASS] cases 数据完整度 465 条；四项齐备`；`run_all_tests.py -v` → **69 个文件 0 失败**，`ledger.db + 59 个 data/*.json` 指纹全部一致、零污染。⚠️ 两个评分脚本会**全量重排**并把 465 条 `scored_at` 从 2026-11-04 刷到 2026-11-06（`cases.json` +2787/-2539 行，实测仅 `china_fit`/`solo_fit`/`composite` 三字段变化）——这是新增案例后排名重算的既定行为，不是数据损坏；判据单一源，绝不手写归一化公式（`fit_ready.cmd_apply` 已注明）。⚠️ 4 条 `replicability` 沿用晋升模板默认值、未人工裁定，它经 `6-原值` 反推四维并直接决定 solo 分。未发布、未部署、未 push |

## 8. 下一步 Roadmap

1. **小程序上线**：按「备案通过 → 个体户执照 → 主体变更 → 开虚拟支付 → 认证」时序推进；先跑通真机支付闭环。
2. **把透明度字段放到前台**：详情页展示「本条等级曾由 X 降为 Y，原因…」、`source_kind` 徽章、`tier_reason`，把后台规则变成读者可见的信任凭证。
3. **台账自动化**：四平台发布后自动回写 `status/group_sent`，把 `reconcile_verdict` 从「事后诊断」变成「发布后自检」。
4. **公众号窗口修复**：`appmsgid` 全量回填 + 草稿列表分页拉取，消除 `refresh`/`fix-cover` 的静默跳过。
5. **主体升级后再谈 API**：若将来有企业/个体工商户主体，优先认证**服务号**——这才是 `draft/add + freepublish` 全链路 API 化的唯一路径；届时先跑 `scripts/probe_wechat_openapi.py` 自检。
6. **README 与 PRD 同步**：以本文数据为准刷新 README 条形数字与发布计划模板（2026-10-21 复核：README 仍写 24 条案例）。
7. **消化采集积压**：135 条 `deep` 卡在 inbox，按分数降序批量晋升；顺带确认 `pick_targets` 的「只挑 trustmrr」硬条件是安全阀（实测 deep 135 条全部来自 trustmrr，HN/IH 的 41 条全是 drop/later，当前 0 漏判）。
8. ~~存量稿形态清洗~~ **已做（#51 + #52 + #53 + #54 + #55，2026-11-04/05）**：13 条问题标题、14 篇水印稿（含 6 篇机械壳重写、1 篇与腾讯云同稿拆开）全部改完；按方案 ① 清掉 175 篇未发布公众号产物的口径旁注（13 篇已发布按批准保留）；再用 LLM 把这 175 篇的 `Name：MRR $X` 模板标题彻底改写（#53）；把「标题 = 产品说明、禁金额」升为九平台硬闸并清掉 77 条带金额标题（#54）；最后把内部流水线黑话从数据 214 处 + 产物 39 个文件里清干净，并给发布闸加了 `内部黑话` block（#55）。**两件已在 v3.9 收掉**：封面回读做成 `cover_remote` 闸（#56）；`--scope all` 的「10 篇同类模板」经复核**前提已不成立** —— `--check --scope all` → `模板标题 192 篇 ｜ 已生成 192 ｜ 待生成 0`、`--verify --scope all` → `错误 0`，11-04 money 那轮之后已做完。剩余仅 **13 篇已发布产物的 `<h1>` 仍带口径旁注**（按批准方案 ① 不动，避免与已发表列表认亲错位；不重发即无影响）。
9. ~~给「动态数字」定一个时点口径~~ **已做（#57，2026-11-05）**：`make_article.timepoint_row()` 在数据卡末尾压一行 `数据时点 ｜ 截至 YYYY-MM-DD（抓取日快照）｜口径：…`，时点取 `verified_at`、口径取 `caliber`（`CALIBER_TEXT` 覆盖库存全部 6 种取值），两者都不许编；发布闸加 `数据缺时点` warn。**剩余**：需要写一句话的是**散文草稿**而不是表格，实测只有 shipfast 两篇真缺（已补），另 6 篇是 juejin（已永久停发）或已发布（不动）；`out/articles/*.html` 293 篇是本地产物、改它不改远端，故不回填。

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
| `seen_keys()` | 采集去重的**单一入口**：ids / 归一化名 / slug 三套键，覆盖 inbox、archive、candidates、cases 四张表 |
| 固件（fixture） | 自检/单测专用的合成条目（`_selftest_*`）。真实队列与候选池**不许被测试消费**，否则还原失败即永久少数据 |
| `atomicio` | 统一原子写（tmp + `os.replace` + fsync，含换行探测）。半截 JSON = 整本台账失效 |
| 远镜拆解 | 小程序注册名；内容收费形态 |
| 幂等键 | 发布记录的唯一键 `(case_id, platform)`；跨平台分发合法，同键第二条即重复稿 |
| `dedupe` 闸 | `publish_guard.check_dupe()`：SQLite 真值（published / draft / draft_id）+ `longtail_sync.json` 批次兜底；`--force` 只绕这一道 |
| `cover` 闸 | 只保证「本地有封面资源 + 发布命令带 `--cover`」；**平台侧落库仍须回读**，本地有 PNG ≠ 封面已设置 |
| 管线水印 | 草稿正文里的 `> 平台：X ｜ 状态：本地稿，未推送`，等于向审核自曝批量（CSDN 一.3） |
| 口径旁注 | `metrics.headline` 里给编辑看的「，TrustMRR 实时核对（非订阅制…）」，**不是标题文案**；生成器 `_clean_title_hook()` 已清，存量产物的定点清理见 #52 |
| AI 标题覆盖表 | `data/wechat_title_overrides.json`：`wechat_ai_titles.py` 用 LLM 生成的 192 条标题，`make_article.ai_title_overrides()` 只读加载 ⇒ 重建产物不回退到模板；优先级低于人工 `TITLE_OVERRIDES`。**改写动作归模型、事实裁定归代码**，见 #53 / #54 |
| 标题禁金额 | **标题是产品说明，不是业绩快照**：金额是抓取时点值、会过期且常被换算错，读者在标题里读到的是一个不可复核的数字 ⇒ 九平台标题一律不许出现金额（`publish_guard.TITLE_MONEY`，**唯一判据**）。「规模」不算金额（用户数/条数/篇数/粉丝数照留），已发布标题降 `warn` 只为保住台账认亲。见 #54 |
| 封面第二行 | `tt_cover_html()` 渲染的一句话介绍，取值链 `cover_kv.desc > cover_desc > one_liner > what_it_does 首句`，**不读标题** ⇒ 改标题不必重制封面图（2026-10-02 起封面已不含金额） |
| 封面回读 | `cover_verify.py` 的五态判据：`ok`（真读了、有 URL）/ `empty`（真读了、是空的，**唯一 block**）/ `unknown`（没读、无记录、记录超 30 天，只 warn）/ `local`（平台侧无可读字段）/ `none`（脚本不设封面）。九平台里只有 B站/CSDN/腾讯云/阿里云 能回读；**「没查过」永远不算通过**。见 #56 |
| 数据时点 | 数据卡末尾那一行 `截至 YYYY-MM-DD（抓取日快照）｜口径：…`。时点取 `verified_at`、口径取 `caliber`，都不许编；发布闸 `TIMEPOINT` 认「数据时点 / 截至 YYYY-MM-DD / YYYY-MM-DD 同步 / 抓取时点 YYYY-MM-DD」四种写法，缺了只 warn。见 #57 |
| 案例库镜像 | `data/cases.json`。2026-11-05 起它不再是人改的文件，而是 `case_library` 表的**自动导出**；逐字节还原（键序/缩进/行尾/末尾换行全一致），所以 `git diff` 仍然可用于评审 |
| 候选池存取层 | `scripts/candidate_store.py`，2026-11-06 起 `data/candidates.json` 的**唯一**读写入口（`load_candidates` / `save_candidates` / `upsert_candidate`）。格式按磁盘字节探测、写盘走 `atomicio`、单条增改持 `file_lock`；通用层收口时必须显式传 `path=`，否则测试的路径隔离会失效。详见 `docs/DATA_SCHEMA.md` 与 #63 |
| 人工评分表 | `score_china_fit.SCORES`（国内移植六维 + blocker + 理由）与 `score_solo_fit.DELIVERY`（单人交付分 + 理由），**人工裁定长文本，不许自动编**。新增案例缺表会被脚本静默清空 `china_fit`/`solo_fit`，连带 `make_article` 少「能不能搬回国内 / 一个人能不能做 / 我的判断」三段。派生分只由两个评分脚本算，别在别处抄归一化公式。见 #45 / #64 |
| 内部黑话 | 流水线自用的词与字段名跑到读者面前：`候选/候选资料`（candidates 池）、`入库时`（晋升那步）、`抓取原文`、`headline`/`last_30d_revenue`/`price_point` 这些 json key。判据 `scrub_jargon.JARGON`，清洗 `scrub_jargon.py`，发布闸 `scan_content` 判 block。**`候选人`/`候选集`/`候选表` 是正当术语，判据先掩掉它们**（见 #55） |

---

*本文描述的规则以代码为准：`scripts/verify_rules.py`（判定）、`server.py`（闸门）、`scripts/harvest.py`（采集）、`scripts/publish_guard.py`（发布幂等 / 标题 / 形态 / 封面判据 / `TITLE_MONEY` 全局禁金额）、`scripts/publish_one.py`（九平台发布编排）、`scripts/clean_title_caliber.py`（存量标题旁注清理，含已发布保护集合）、`scripts/wechat_ai_titles.py`（LLM 标题改写 + 事实硬校验 / 覆盖表 / `--scope money` 禁金额清理）、`scripts/scrub_jargon.py`（内部黑话清洗：数据 `--check/--apply/--verify` + 产物 `--artifacts`）、`scripts/cover_verify.py`（封面回读：`--matrix` 能力矩阵 / `--live` 现场读 / `--report` 台账 / `--clear` 逃生口，台账 `data/cover_readback.json`）、`scripts/make_article.py`（`build_titles()` 读覆盖表防重建回退）、`scripts/platform_tone.py`（九平台调性单一源）、`scripts/wechat_publish.py` / `toutiao_publish.py` / `bilibili_publish.py` / `xhs_publish.py`（分发；前者同时是封面第二行 `cover_desc()` 的出处）、`scripts/reconcile_verdict.py`（对账）。*
