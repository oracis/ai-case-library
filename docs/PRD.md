# 拆解海外 · Overseas Teardowns — 产品设计文档（PRD）

> 版本：**v2.0**　日期：**2026-09-25**　状态：**现行**
> 适用仓库：`oracis/ai-case-library`　线上：https://case.ydtgo.top
> 配套图：`docs/产品流程图.html`（同一份事实的可视化版本）
> 本文描述的是**代码当前实际实现的状态**，不是设想中的所有数据（cases 39 条、候选 19 条、采集队列 400 条）截至文首日期。

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
- **不自动群发。** 个人订阅号无 `freepublish` 权限，最后一击永远由人点。

---

## 2. 目标用户与场景

| 角色 | 场景 | 用哪一屏 |
|---|---|---|
| **独立开发者 / 想出海的人**（主） | 找「一个人能做的生意」：看全局 → 按「木桶综合分」排 → 看能不能搬回国内 | 公开站 · 精写案例 / 综合排行 / 移植排行 |
| **内容读者**（次） | 每周 2–3 篇长文拆解 | 公众号「万物解释者」 |
| **运营者（本项目主人）**（内） | 每天：**后台挑候选 → AI 深核 → 人工裁定闸门 → 发布** | Admin 后台 5053 |
| **CI / 机器人** | 每天 09:00（北京）采集、校验、推 data 分支 | GitHub Actions |

界线是刻意划开的：**除了运营者本人，没有任何角色能写入 `cases.json`。**

---

## 3. 概念与数据模型

### 3.1 三级漏斗（信息架构的骨架）

```
inbox.json     采集队列  机器写   原始素材   400 条上限，溢出归档不删
   ↓ 人工点「转入候选池」
candidates.json 候选池   人工挑   19 条
   ↓ 过核实闸门 promote
cases.json     精写库   人工核   39 条 ← 唯一写入口是 /api/candidates/:id/promote
```

素材多、精写少是正常的。漏斗本身就是产品信息的一部分：公开站首页把它画成三根按比例归一的条形。

### 3.2 三套「可信度」分类（不要混）

| 维度 | 值 | 谁产生 | 用在哪 |
|---|---|---|---|
| `source_kind` 采集层 | `verified`(支付网关直读) / `self_reported`(创始人自述) / `secondary`(二手转述) / `discovery`(仅发现) | harvest | 素材徽章；认不出时取兜底 `discovery`（宁可保守） |
| `verification` 核实等级 | `stripe` / `official` / `partial` / `founder` / `disputed` | 深核 + 人工 | 六色 badge、筛选下拉 |
| `sources[].kind` 来源构成 | `stripe`(A) / `official`(A) / `press`(B) / `review`(B) / `founder`(C) / `secondary`(D) | 深核 | 定级依据、**自动降级依据** |

派生：`tier` 三档池 —— `premium` 精品池（有一手来源）/ `standard` 实核池（第三方撑住口径）/ `backup` 备选池。

### 3.3 三套评分（皆 1–5 分，**注意 `replicability` 与另两套方向相反**）

- **`replicability` 可复刻度**：技术 / 获客 / 资金 / 时机，**5 = 最难**（人工判断）。
- **`china_fit` 国内移植性**：付费意愿 ×1.5、支付可达 ×1.2、合规空间 ×1.2、获客迁移 ×1.0、改造成本 ×1.0、竞争空位 ×1.0 → raw 满分 **34.5**，归一到 100。
- **`solo_fit` 个人可做性**：单人交付 ×1.4（`replicability.delivery` 的人工判断）、够得着客户 ×1.3、启动轻 ×1.1、造得出来 ×1.0、窗口还开着 ×1.0 → raw 满分 **29**。后四维由 `6 - replicability.*` 反推，**两套数据永远不会打架**。
- **`composite` 木桶综合分**：`0.6 × min(solo, china) + 0.4 × mean(solo, china)`，及格线 **70**。理由是两维之间有乘性关系（`90×40 → 50`，`60×60 → 60`）。

四象限：`go` 可以开干 / `partner` 有市场但一个人啃不动 / `export` 能做但别在国内卖 / `skip` 别碰。

### 3.4 数据文件清单

`inbox.json` `inbox_archive.json` `candidates.json` `cases.json` `verifications.json`（草稿库）`sources.json`（源元数据 + 过滤词表 + 字段契约）`site.json`（外显配置）`ih_slugs.json`（IH 分片游标）`issue_numbers.json`（篇号注册表）`wechat_published.json`（发布台账）`admin.json`（口令 hash/token secret）`last_harvest.json`（采集简报）`secrets.json`（AI key）`arrclub.json`（校对参照，不进候选池）。

---

## 4. 功能规格

### 4.1 采集层（唯一自动化的一段）

六通道，`harvest.py` 实现：

| 源 | 通道实况 | 产出特点 |
|---|---|---|
| **TrustMRR**（主力） | `/api/ai`（主）+ `/api/ai/discovery`（只补全未覆盖 slug）→ HTML-JSON-LD 回落 | 支付网关验证的收入，信息密度最高 |
| **Hacker News** | Algolia `search_by_date` / `search` + Show HN 关键词 | 有讨论热度、有创始人现身 |
| **Indie Hackers** | 无公开 API：列表页 + **sitemap 分片轮换**（`?page=2` 返回字节完全相同，无法分页） | 创始人自述 + 过程叙事 |
| **Product Hunt** | GraphQL（需 `PH_TOKEN`；**已决策不申请**，维持 Atom feed 雷达定位） | 仅发现，不含收入 |
| **ARR Club** | sitemap + FAQPage JSON-LD | **只写 `arrclub.json`，不进候选池**，仅供校对 |

保护与取舍：跨 `inbox/candidates/cases` 三层去重（id + 归一化名）；`http_json` 重试 3 次 + `1.5×(n+1)` 退避 + 显式绕过系统代理；IH 1.5s / ARR 2.0s 节流；单页抓取有 16 MB / 150 s 双重兜底；`inbox` 容量 400，溢出进 `inbox_archive.json`（**只进不出、永不删除**）；金额 < $50 当行价丢；MRR 低于 100 的 TrustMRR 条目丢；提问帖/Ask-Tell 类 HN 丢。

### 4.2 零成本初筛（`triage.py`）

纯函数，不联网、不调 LLM、不写盘。给每条素材打分：证据起点（一手 40 / 自称 20 / 第三方 16 / 验证源 18）→ 数字可得性（有数字 +16，无数字 −20，<$1000 −12，文本-only 字段不算数字）→ 关注度 → 可核性（有官网 +6），clamp 0–100。

判级优先序：**duplicate → ready → backfill → placeholder/reference → 可深核 → 分数线（DEEP 56 / LATER 28）→ 兜底**。

两条刻意写死的规则：**候选池不由分数判 drop**（只有重复才 drop）；**高分 ≠ 可深核**。

### 4.3 候选池 enricher（两个新能力，2026-09-24 新增）

- **`backfill_website.py`** —— 只补「官网」这一个字段：从 TrustMRR `/api/ai` + discovery 铸 `{slug: {name, website}}`，三层定位（slug → id → 归一化名），**只补空不覆盖**，幂等，默认 dry-run。
- **`resolve_slug.py`** —— TrustMRR slug 反查：用**全站 `startup-sitemap.xml`（必须显式 gzip，实测 10,691 条）+ `/api/ai`（≈90）**建索引，六层证据强度 `已登记 → 来源页 → 官网匹配 → 官网子域 → 名称匹配 → 站点索引`；最弱一层必须调 slug `.md` 核对标题（**身份确认**，因 `private-venture-1` 误配事故）。**缺 slug 不是死局**，反查无果会提示改走官网/创始人等一手来源。默认 dry-run，`--write` 才落盘；默认只跑 cases/candidates（inbox 需显式 `--only inbox`）。

### 4.4 AI 深核（`ai_verify.py`）

四阶段：**拼素材**（官网 → 来源页 → TrustMRR `.md` → slug 反查 → HN Algolia/Bing/DDG，最多 5 页，每页 6000 字，<200 字回落 meta 摘要，抓不动才用本地 Chrome 无头渲染）**→ 调 LLM**（OpenAI 兼容端点，默认 DeepSeek，`temperature=0.2`，JSON 模式，300 s 超时）**→ 映射**（三态门槛 `yes/no/unknown`，**布尔 `False` 不当反证**；musts 与旧草稿取**并集，勾是粘的**；`human_read` 任何模式都不由 AI 勾）**→ 落草稿**（`verifications.json`，后台再跑一次规则引擎判定回显）。

失败语义：一页原文都没抓到 → 直接跳过不写草稿；判定不可发布 → 草稿照存，区块清单给人；`--publish` 但分不够 → `held`，只存草稿。

### 4.5 核实规则引擎（`verify_rules.py`）+ 入库闸门

**门槛三态**：`confirmed = gates ∩ {仍在运营, 是真实生意, 有个人路径}`，只要**一道成立**就放行（denied 且未 confirmed 才 block）；AI 没核到的 `unknown` 不拦人。

**必填**：只有 `caliber_decided`（口径定了没）拦发布；`caliber_consistent`、`human_read` **不拦**，只挂「不拦发布」的提醒标签。另有三项实际 block：核实等级非法、口径值非法、**`source_kinds` 为空**（这一项是 2026-09-24 踩出来的：没来源不许入库）。

**质量分**：6 项 bonus（20/20/15/15/15/15）+ 「三道全 yes 且无 denied」自动 +10（注明不是人勾的），满分 100，**≥60 进精品池**。

**Promote 闸门（`server.py`）顺序**：候选存在 → `evaluate`，未过返 **409** → `evidence_gap` 把高于证据的等级**自动降级**并写 `verification_downgraded{from,to,reason,at,tool}` → `default_case_tier` 定档 → 写 case（`cases.insert(0)`、候选出池、草稿用完即清）。`human_read` 时间戳**只取服务端已存草稿**，请求体里的概不采信。

> 这是全项目唯一不可绕过的写入口。任何 AI / 脚本想入库，都必须从这里过。

### 4.6 内容包 & 入库后 Enrichment

| 脚本 | 服务时点 | 校验 |
|---|---|---|
| `contentpack_ready` | **promote 之前**（promote 只搬候选已有字段，裸发会造空壳） | `why_it_works`/`playbook` ≥3 条且每条 ≥20 字、`signals` ≥2、`verdict`/`what_it_does`/`how_it_makes_money` 非空、`metrics.headline` 必须带 `metric_note` |
| `publish_ready` | 发布动作本身 | 五字段 + 两条 ≥3；**`--id` 指定不合格条目也不放行** |
| `fit_ready` + `score_*` | promote 之后 | 检查缺哪套人工判断；只写 `replicability`，`solo/china` 交给各自脚本算（避免多套口径） |

### 4.7 成文（`make_article.py`）

`--id/--all/--top N/--format md|html|both`，产物 `out/articles/<id>.{md,html}`，**只读 data、不写数据**。

九节骨架：钩子（有纠错就用「别人说的 vs 我去核了一遍」，没有就用 verdict）→ 干什么的 → 钱从哪来 → 做到多大（六指标，**故意不含 price_point**，它已在正文出现过）→ 第二条纠错（不立小标题，避免断句）→ 为什么能成 / 能搬走什么 → 能不能搬回国内（六维 + 硬伤）→ 一个人能不能做 → 我的判断 + 结论象限。

- **标题**：手工表优先（`TITLE_OVERRIDES`），否则模板产物并在 md/html 里插 `[!] 标题由模板生成，发布前请手改`；`sanitize()` 会改敏感词（月入 → 月营收）。
- **排期**：`virality_score = 纠错数×2 + 金额量级 + 象限 + EDITORIAL_WEIGHT`（人工表，顺序＝发布优先级）。
- **占位文案清理**：`text_clean.clean_placeholders` 只动会外显的 `one_liner`/`what_it_does`/`corrections[].claim`；占位有两大家族（定位未获取 / 无公开信息）；**判空用 `[0-9A-Za-z\u4e00-\u9fff]` 而不是 strip 句号**（真实事故：曾把正常句尾的「。」吃掉，导致 rezi 的 `what_it_does` 被改、30 条老案例 diff 全红）；**尾部清理只收连接符 `，,、：；;·-—`，刻意不收「。！？」**。

### 4.8 公众号管线（`wechat_publish.py`）

`build → queue → plan → publish → fix-cover → refresh → inspect → discover`。

- **通道**：Chrome CDP 9222 + 页面内 JS**（不是微信 API —— 未认证订阅号没有 `freepublish` 权限）**；手写 WebSocket，纯标准库；入口强制清掉 `http_proxy` 等环境变量，否则连本地调试端口会被代理掐。
- **登录态**：token 从**已登录页面动态抠**（URL / `wx.cgiData` / 整页 HTML），不能写死。
- **篇号（2026-09-25 修）**：篇号是**历史编号**不是排序位置，因为新案例是往 `cases.json` **头部插入**的，按位置编号会让老 30 篇全体后移并与新 9 篇撞号。现改为持久注册表 `data/issue_numbers.json`：空表时按 `wechat_published.json` 的**插入顺序**补种 1–30，再续 31–39；补种后必须先 `save` 再返回（曾因漏掉 save 导致每次重跑重种）。
- **封面**：HTML data URL → Chrome 截图 1080×460@2x（**必须带 doctype + charset**，否则 data URL 按 Latin-1 解码中文全乱）；「硬停」渐变（两个同位置色标）规避近黑色带；最多 10 轮 JS 实测收缩字号；先上传插入正文 → 再从「正文选择」设为封面（**顺序不能反**）；已有封面的草稿走另一套 Vue 组件，按类名找不到菜单（`NO_MENU`），故用 `_click_text_anywhere` 按精确文本点 + `_text_visible` 兜底。
- **稳定性**：逐篇 `save_published`（全量跑几小时，结尾才写会让已建好的草稿被重发）；`appmsgid` 是就地操作同一篇的唯一凭据；单篇异常 `continue` 并断 ws 重连；不关浏览器，保登录窗。
- **收尾判定**：封面与原创都到位才算 `ok`，否则 `partial`（曾无条件返回 ok，导致四篇一直躺在草稿箱没人管）；弹窗按钮挑选只搜 `.weui-desktop-dialog` 内且要求可见（曾因命中正文里的「确认」二字造成封面没设 + 原创失败两个故障）。

### 4.9 静态站（公开面）

`build_static`（5 步）→ `prerender` 内置；同源不同 flag：

| | `dist/`（默认） | `public/`（`--no-inbox`） |
|---|---|---|
| 定位 | 本地预览，**含 400 条未核实素材** | **对外发布版**，上传 OSS 的那一份 |
| `data.json` | 605 KB | 190 KB |

`data.json`/`data.js` 双写（后者是为了 file:// 双击能看，fetch 会被 CORS 拦）；每案例一个预渲染静态页 `case/<id>.html`（内联样式、**不引 app.js**、正文 ≥600 字、CI 独立复检）；`sitemap.xml` 只在配了域名时生成；自检包含「无绝对路径引用」（子目录部署会 404）。

### 4.10 部署

`release.py` 六步流水线：**contentpack → publish → fit → score → build → deploy**，`after` 字段是**有约束力的依赖声明**（忘改会直接红）。

- **零依赖自签名 OSS 上传**（hmac-sha1，不用 oss2/ossutil，「克隆下来就能跑」）；Region **必填且刻意不兜底**（曾兜底到杭州，报错里看不出是地域问题）。
- **inbox 硬闸门三态**：0 放行 / 正数拒绝 / **None（读不到）也拒绝** —— 第一版把「读不到」吞成 0，真实事故是 2026-09-20 把 441 条未核实素材推上公网。
- 每个 PUT 都带 `x-oss-acl: public-read`（Bucket 的 public-read **不被对象继承**）；Bucket Policy 允许匿名 GetObject；上线后**不带签名 GET 一次首页**自检（Bucket 为 private 时上传照样成功、访问才 403）。
- 缓存：`data.json/index/case/*` → `no-cache`；`app.js/style.css` → `max-age=300`。
- **全量覆盖、无 `?v=` 版本化、远端孤儿文件需手动删**（「下一次成功部署可纠正上一次」）。
- **失败即停，返回步骤序号作为退出码**；无回滚实现，靠幂等 + 全量覆盖补偿。
- `auto_deploy.py`：内容差异驱动的幂等兜底（SHA256 对比线上），`generated_at`/`stats.inbox` 归一化后再比，否则每天必然「发现差异」；**它不是发布主路径**。

### 4.11 CI 与分支拓扑

- `ci.yml`（`contents: read`）：PR + push main 触发，多版本 Python/Node 矩阵，`selftest` → 单测 → `release --selfcheck` → 构建静态站 → `prerender --check` → **断言 data/ 与仓库全程干净**。不部署。
- `harvest.yml`（`contents: write`）：每天 UTC 01:00（北京 09:00）+ 手动，**「采集 → 内联校验 → 推 `refs/heads/data` → `git reset origin/main`」**。不部署。
- **为什么独立的 data 分支**：此前机器提交直接进 main，导致本地每次 push 都要 rebase。机器数据与人工产物分分支后，main 只承载人工改动，本地 push 永远快进。`.gitignore` + `git reset origin/main` 双保险。
- main 分支的服务端保护规则**需人工确认**（仓库内无配置文件；实际生效的是权限最小化约定）。

### 4.12 公开站前台（7 视图）

精写案例（三档分块：精品 / 实核 / 备选，各自一句话明示「能不能直接引用」）/ 综合排行（四象限 + 木桶榜 + 双轴条 + 五维）/ 移植排行（六维小条 + 硬伤）/ 候选池 / 采集队列 / 信息源 / 方法论。

- 卡片：核实等级 badge + 主金额 + 右上角三件套 `个/国/综` + ⚠ 硬伤 + `修正 N`。
- 详情抽屉：14 个信息块，含**口径说明警戒条**（`metric_note`）、**「核实修正 · 别抄错」**（`corrections`）、可复刻度四维条、来源可点回原文。
- 排序 8 项（含木桶优先、修正最多、最好复刻、最近核实）；筛选＝搜索 + 核实等级 + 模式芯片 + 按分筛选（维度/分数/象限可叠加，只选维度自动补 ≥70）。
- **数据健康度横幅**：公开交代三档条数与「待核 N 条」，并劝读者引用前自己再搜一遍。这是本库招牌。
- 引流只出现在**顶部条 / 抽屉底 / 页脚**三处，**不插进卡片流**（引流位是正经组件不是装饰）；社区链接为空时渲染成灰标签「即将开通」而不是点不动的按钮。

---

## 5. 非功能需求

| 项 | 约束 |
|---|---|
| 依赖 | **零第三方库**。Python 标准库 + 原生 JS；CDP 客户端手写 WebSocket；OSS 签名手写 hmac-sha1 |
| 可达性 | 静态站可用 file:// 双击打开（故需 `data.js` 兜底） |
| 端口 | 公开站 127.0.0.1:5052；后台 127.0.0.1:5053（**只绑回环**）；CDP 9222 |
| 安全 | 后台口令 PBKDF2 + HttpOnly cookie + HMAC token + 连错 5 次锁 5 分钟；公开站没有登录接口，且 API 用 **404 而非 401**（不暴露后台存在）；两站共用同一个 Handler，`/api/data` 只在 admin 站带出草稿；`human_read` 时间戳由服务端铸，前端不能给自己盖背书戳；**没有 do_DELETE**（HTTP 删不掉任何数据） |
| 幂等 | harvest / build / deploy / publish 皆可重复跑；Promote 是唯一非幂等写入，但失败在写盘前返 409 |
| 性能 | 公众号全量发布要跑数小时（每篇封面开销）；39 个案例 → 40 页预渲染，`public/` 对外产物约 190 KB |

---

## 6. 本期更新（v1 → v2，2026-09-24/25）

| # | 变更 | 动机 |
|---|---|---|
| 1 | 采集队列移出 main，改由 **data 分支** 承载 | 根治本地 push 必须 rebase |
| 2 | 新增 TrustMRR **discovery 通道** + HN Algolia 搜索层 + **官网回填** | 素材面变宽，抽样更深 |
| 3 | 新增 **`resolve_slug.py`** 反查（sitemap 10,691 + API 90，六层证据 + **身份确认**） | 缺 slug 也能复核；带身份确认防同名误配 |
| 4 | **`source_kinds` 为空不得入库**（提升为 block 条件） | 无来源不许称已核实 |
| 5 | **篇号改持久注册表**；补种/续号后**必须先 `save_issue_numbers` 再返回** | 头部插入导致 30 篇重编号 + 撞号 |
| 6 | **占位文案不进正文/标题/封面**（第二族「无公开信息」），且放逐 strip 句号判空 | 占位文案泄漏到读者视野 |
| 7 | `refresh` 同步标题/摘要/作者；`--cover` 强制重画+换封面；`_click_text_anywhere` 兜底 | 已有封面草稿换不了封面 (`NO_MENU`) |
| 8 | 9 篇新草稿（31–39）入草稿箱 | cases 30→39 |

---

## 7. 已知问题与技术债

| 优先级 | 问题 | 影响 |
|---|---|---|
| **P0** | **9 条新案例（31–39）缺 `china_fit`/`solo_fit`/`why_it_works`/`playbook`**，导致 `test_make_article` 5 项失败 | 数据缺口非回归；需人工补齐 `score_china_fit.py` / `score_solo_fit.py` 依赖的手写评分表后重跑 `fit_ready` + `score_*` + `refresh` |
| P1 | `verification_downgraded` **已进 data.json（11 条）但前端不渲染** | 「这条曾被机器降级」对读者不可见，弱化了诚实度卖点 |
| P1 | `magicslides-app`（`appmsgid=100000125`）台账写 draft，草稿箱找不到 | 超出草稿列表窗口（只能拉最近 50/60）导致静默跳过，代码无告警 |
| P1 | `cmd_refresh` 只能拉最近 50/60 篇草稿；按标题反查脆弱（`insect-bite-id` 无 appmsgid） | 老草稿既不能补封面也不能 refresh |
| P2 | `source_kind` 徽章 README 说有、前端未渲染 | 语义漂移 |
| P2 | `tier_reason` / `quality_score` / `caliber` 等字段前端未用 | 透明度未打满 |
| P2 | 编辑器 DOM 选择器是硬编码实测值（`cover_input`/`save_draft` 至今为 `None`） | 微信改版会全线失效 |
| P2 | `deploy_oss` 全量覆盖、无版本化、孤儿文件不清理 | 删文件需手动 |
| P3 | `公众号-发布计划.md` 已过期（模板与现有九节骨架不一致） | 文档误导 |
| P3 | `README` 多处数字过期（写 24 条案例，实际 39） | 需随 PRD 一并刷新 |

---

## 8. 下一步 Roadmap

1. **补 9 条缺口**：写完 31–39 的评分表与 `why_it_works`/`playbook` → `fit_ready` → `score_*` → `refresh`，让 `test_make_article` 全绿。
2. **把降级记录放到前台**：详情页展示「本条等级曾由 X 降为 Y，原因…」，把「不许虚标」从后台规则变成读者可见的信任凭证。
3. **修草稿列表窗口**：`appmsgid` 全量回填 + 列表分页拉取，消除 refresh/fix-cover 的静默跳过。
4. **README 与 PRD 数据同步**：以本文数据为准刷新 README 的条形数字。
5. **发布链路观测**：群发后回写 `status=published` + `group_sent`（当前靠人工改台账）。

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
| partial | 公众号草稿已建但收尾（封面/原创）没完成 |

---

*本文描述的规则以代码为准：`scripts/verify_rules.py`（判定）、`server.py`（闸门）、`scripts/harvest.py`（采集）、`scripts/wechat_publish.py`（发布）。*
