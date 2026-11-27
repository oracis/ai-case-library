# 拆解海外 · Overseas Teardowns — 产品设计文档（PRD）

> 版本：**v4.1**　日期：**2026-11-26**　状态：**现行**（上一版 v4.0 / 2026-11-05）
> 适用仓库：`oracis/ai-case-library`　线上：https://case.ydtgo.top
> 配套图：`docs/产品流程图.html`（同一份事实的可视化版本）
> **路径口径（2026-11-11 起）**：文中出现的 `scripts/_oneoff/xxx.py` 是一次性排障
> 脚本，**已全部入库**（此前写在 `tmp/` 或归档被 `.gitignore` 挡住，换机即失，同一
> 修复被迫重写）。已转正常驻工具仍是 `scripts/xxx.py`。`tmp/` 只放产物，不放脚本；
> 由 `scripts/check_tmp_leaks.py` 守住这条线。
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
> firstImg 上传失败静默跳过）。见 `docs/CHANGELOG.md` §6.1 #56。
> ② **数据时点**：用户指出金额「不准确、是动态的」，标题已全禁金额，但正文数据卡仍
> 只印数字不印时点 ⇒ 生成器加一行 `数据时点 ｜ 截至 YYYY-MM-DD（抓取日快照）｜口径：…`，
> 发布闸加 `数据缺时点` warn。判据首版把「2026-10-14 同步」全判成缺时点
> （草稿 17 篇全误报、产物 261 篇里 202 篇误报），校准后真缺的只有 shipfast 两篇草稿。见 `docs/CHANGELOG.md` §6.1 #57。
> ③ 顺手把一条**过期待办销掉**：`--scope all` 的「10 篇同类模板」在 11-04 的 money
> 那轮之后已经做完（`--check --scope all` 待生成 0、`--verify --scope all` 错误 0）。
> v3.8 把内部黑话从数据 214 处 + 产物 39 个文件里清干净，并给发布闸加了 `内部黑话` block（#55）；
> v3.7 把「标题 = 产品说明、九平台禁金额」立成硬闸并清掉 77 条带金额标题（#54）；
> v3.6 用 LLM 彻底去掉 175 篇公众号标题的机械模板（#53）；v3.5 清理 175 篇未发布产物的
> `<h1>` 口径旁注（#52）；v3.4 九平台调性分化 + 存量稿批量改写（#47–51）；v3.3 发布幂等 + 内容质量加固。
> 本文描述的是**代码当前实际实现的状态**，不是设想。数据截至文首日期（2026-11-26 只读核）：
> **案例 599 条 / 候选 3 条 / 采集队列 390 条**（`case_library` 表 / `candidates.json` / `inbox.json` 实测）；
> 发布台账真值 = SQLite `data/ledger.db` 的 `platform_posts` **529 条**（2026-11-26 实测）。
> ⚠ 具体条数会随采集 / 发布滚动，上面是**瘦身当日快照**，不是固定值；刷新时重跑只读计数。
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
tencentcloud 记了 4 个批次）。JSON 台账与 SQLite 双写口径见 `docs/CHANGELOG.md` §6.1 #32 / #42。

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

### 4.8 多平台分发（v3 起为四平台，现为九平台入口；替代 v2 的「公众号管线」单节）

#### 4.8.1 能力矩阵（2026-10-17/18 实测；后续新增平台与冻结状态见 `docs/CHANGELOG.md` 各轮发布条目）

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
| `reconcile_verdict.py` | 多平台共用四态诊断 `PUBLISHED / MISSING / RATE_LIMIT / UNKNOWN`；**判 PUBLISHED 要求双侧都读成功**（草稿箱读失败时「草稿箱无」不是证据） |
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
- **独立匿名 GET 回读工具（2026-11-21 / #216）**：`scripts/verify_deploy_bytes.py` 把 PRD #97 的「独立匿名 GET 全量比 ETag + 字节」从一次性动作固化成常驻工具 —— 无签名、绕本机代理（`ProxyHandler({})`）、8 并发逐对象取回 `public/` 对应产物，校验 HTTP 200 + ETag 是 32-hex 普通 PUT 形态（multipart 的 `<hex>-N` 直接判失败）+ 远端 ETag == 本地 MD5 + 实收字节 MD5 == ETag，任一不符 rc≠0。补的是 `deploy_*` 写入侧自报与 `verify_deploy.py`（语义验收、明确不看字节数）都覆盖不到的「同尺寸不同内容 / 半更新撕裂态」。`test_verify_deploy_bytes` 8 项 mock 单测钉住三层判据各自能抓错。实测本轮部署后 606/606 全量一致。
- **扩展离线套件（2026-11-21 / #214）**：`ci.yml` backend job 末尾新增一步 `python scripts/ci_offline_suite.py`，把 19 个模块 / 398 用例从「只在本地跑」纳入门禁（此前 99 个 `test_*.py` 只有 16 个进 CI）。驱动脚本从 JSON 播种临时 `ledger.db`（CI 无真库文件）、钉住 `LEDGER_DB`、跑后校验 `data/*.json` 指纹零污染。白名单只收纯逻辑/临时库隔离/<5s/无平台依赖的测试；新增 `test_core_guards`（`upsert_post` 状态只前进、`link_hygiene` 自家域名、`compliance_check`）、`test_e2e_platform_readback`（双回读判据）、`test_public_site_e2e`（真 HTTP 公网 E2E，无外网时 skipTest 而非假绿）。
  - ⚠️ **已知红灯（数据债，非测试代码坏）**：真值库案例 564→589 后新增 25 条缺评分/适配表，`test_fit_ready`、`test_make_article`（均已在 CI）、`test_score_value_parity` 因此失败。`test_jargon` 依赖真库可见字段、seed 前后不稳。四者刻意不进扩展套件；修复需补这 25 条的人工表/评分字段，属数据变更，**先确认再动**。全量基线：`python scripts/run_all_tests.py` 当前 95 绿 / 4 红。
  - **数据债已清（2026-11-21 / #215）**：上述红灯在用户确认后修复，走的是既定 11-06 口径而非补人工判断。① 写前备份 `cases.json`/`ledger.db` 到 `tmp/*.pre_rescore_1121`，按顺序跑 `score_china_fit.py` → `score_solo_fit.py`（顺序不能反，solo 的 composite 依赖 china）：25 条新案例自动走**全库中位数兜底 + `provisional`**，不写进 `SCORES`/`DELIVERY` 人工表。② `provisional` 99→124、`table_scored` 仍 465，465 条人工 note 逐条回读**零改动**，589/589 四条评分全覆盖，库/镜像 id 集合一致。③ 内容债另算：`scrub_jargon.py --check` 报 4 处（cozymac 的「候选资料」、mecartoo/chadme/redstring 的 `last_30d` 口径词），`--apply` 只改措辞、数字序列逐字段核过零改动，回归 0 处。④ `test_score_value_parity` 的 `cases=564`/`兜底=99` 是写死的旧真值，**只更新事实计数**到 589/124，判据结构（表↔库逐键一致、兜底等于当前中位数、两轴集合一致）一条不放宽。复现：`score_value_parity.py --check` → `[OK]`；5 个曾失败测试全绿（fit_ready 26/26、make_article 87/87、score_value_parity 19/19、jargon 12/12、build_static 56/56）；ci_offline_suite 398/398 + 读真库 205/205 全绿。`cases.json` diff 4662+/3062− 系评分脚本重导镜像的键序规范化，逐案例键集合/标量字段零删改。
  - 手动端到端工具 `scripts/e2e_platform_readback.py`：复用 `verify_page.py`（公开页 CDP 回读）+ `title_match.py`（已发表列表唯一命中），把散在发布脚本尾部的「独立双回读」收口成可事后复核的入口；不进 CI（需登录态/浏览器），零新增依赖、不引入 Playwright。
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

## 6. 变更记录

逐次发布 / 修复 / 排障的**完整记录已迁至 [`docs/CHANGELOG.md`](CHANGELOG.md)**（2026-11-26 瘦身，共 240 条，编号 `#N` 全仓通用）。
PRD 只维护**现行设计**：新增设计决策就地更新对应章节，并在 CHANGELOG 留一条「为何改」。
⛔ 不要再往本文追加运行日志——那会让设计文档退化成流水账。


## 8. 下一步 Roadmap

1. **小程序上线**（备案已完成：苏ICP备20260791212号-1X）：剩余「个体户执照 → 主体变更 → 开虚拟支付 → 认证」；个人主体开不了虚拟支付，付费墙 `get-cases` 双层白名单 + `canReadFull()` 已就绪，待真机支付闭环。
2. **把透明度字段放到前台**：详情页展示「本条等级曾由 X 降为 Y，原因…」、`source_kind` 徽章、`tier_reason`，把后台规则变成读者可见的信任凭证。
3. **台账自动化**：四平台发布后自动回写 `status/group_sent`，把 `reconcile_verdict` 从「事后诊断」变成「发布后自检」。
4. **公众号窗口修复**：`appmsgid` 全量回填 + 草稿列表分页拉取，消除 `refresh`/`fix-cover` 的静默跳过。
5. **主体升级后再谈 API**：若将来有企业/个体工商户主体，优先认证**服务号**——这才是 `draft/add + freepublish` 全链路 API 化的唯一路径；届时先跑 `scripts/probe_wechat_openapi.py` 自检。
6. ~~README 与 PRD 同步~~ **本轮 v4.1 已做**：文首快照刷新到 599 案例 / 529 台账，§4.8 平台数改为九入口；README 条形数字另行同步（本次未动 README）。
7. **消化采集积压**（持续）：2026-11-26 只读核 inbox **390 条**、候选 **3 条**；按分数降序批量晋升，继续确认 `pick_targets` 的「只挑 trustmrr」硬条件是安全阀。
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
