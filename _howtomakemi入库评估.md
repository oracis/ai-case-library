# howtomakemi.com 入库评估与执行结果（2026-10-26）

> 本文替代 2026-10-26 上午的 `_howtomakemi采集调研.md`（那份只有分档建议，没有实测）。
> 本轮把「TrustMRR 88 条」和「官方披露 344 条」两条线都跑到了结论，并按结论真的入库了 8 条。

## TL;DR

| 线 | 条数 | 结论 |
|---|---|---|
| TrustMRR 收入案例 | 88 | **入库 0 条**。16 条已从 TrustMRR 下架（HTTP 404，本地 `gone` 名单坐实）；其余 64 条现 MRR **全部 < $300**，零条过晋升门槛。且 howtomakemi 的「月收入」是快照，与 TrustMRR 现值不可互换（11 条已归零） |
| 官方披露（融资/新闻） | 344 | 344 条全抓、逐页解析。**收入口径 73 / 仅融资 68 / 无金额 183 / 待判 9**。其中「收入口径 + AI 相关 + 库内未见」**55 条**，本轮入库 **8 条**，剩 **47 条**进 backlog |
| AppStore 平台数据 | 264 | 不采。只有评分/价格/开发者，无收入口径 |
| 失败复盘（LootDrop 翻译） | 180 | 不采进收入库。可另开「墓碑」栏目 |
| 扩展平台数据（Mozilla） | 17 | 不采。日活/评分，收入多未披露 |

**一句话**：这个站对收入库的价值不在它的 TrustMRR 板块（已经过期到不能用），而在它的**官方披露板块里那些把 ARR 讲清楚的产品**。

---

## 一、TrustMRR 88 条：为什么一条都进不来

### 1.1 16 条已从 TrustMRR 下架

本地 `data/trustmrr_profiles.json` 的 `gone` 名单（404/410 确认不存在）里有它们的名字，逐个直连也确认 404：

`bustem-inc`（页面显示 ¥288.8万/月）、`buildmyagent-io`、`bookedin`、`coucou`、`firstrevenue`、
`airix-aeo-ai-visibility-platform`、`authex`、`9x12tools`、`bankconv`、`agent-mma-llc`、
`cancelpause`、`clickgram`、`blazehive`、`aelayerstool`、`affiliatespy`、`3ak-track-field`

⚠ 注意 `bustem-inc` 正是调研阶段被标成「量级异常，需回源核验」的那条——回源的结果是**档案已经没了**。
这类「聚合站还在、原始档案已下架」的条目，任何数字都无法核验，只能整条不入库。

### 1.2 剩下 64 条：现值全部低于晋升门槛

对 64 条（LIVE 且库内未见）做 `howtomakemi 快照 vs TrustMRR 现值` 对照：

- 11 条现值 **MRR = $0**（如 `1milliontaps-lol` 快照 $497 → 现 $0；`angular-material-blocks` $221 → $0）
- 10 条现值不到快照的一半
- 40 条现值高于快照（有增长，但基数仍小）
- **中位数：快照 $46 / 现值 $24.9**
- **快照 ≥ $300 的只有 1 条，现值 ≥ $300 的 0 条**

`harvest --source trustmrr_sitemap --tm-seeds` 的档案池选拔给出的也是同一结论：`低于 $100` 64 条，`→ 拉到 0 条`。

### 1.3 教训（已可复用于以后任何聚合站）

1. **聚合站的收入字段是快照，不是现值**。它没有义务跟着源站更新，入库前必须回源。
2. 「高收入」条目反而最容易失真：收入掉下来的产品，源站会下架，聚合站不会。
3. 现成工具已经能判这件事：`--tm-seeds` 走官方 `.md` 通道，404 记 `gone`，抓不到就说明源已不在。

---

## 二、官方披露 344 条：能用的只有五分之一

### 2.1 口径分档（逐页解析 344 页，成功 344）

| 档 | 条数 | 判据 | 处置 |
|---|---|---|---|
| 收入口径 | 73 | 页面把金额明确标成 ARR / 年营收 / 月收入（且金额附近没有出现在融资语境里） | 可用；其中 AI 相关 55 条 |
| 仅融资 | 68 | 金额只出现在「种子轮 / A 轮 / 领投 / Series」语境 | 不入库（融资额 ≠ 收入，库内无 funding 口径） |
| 无金额 | 183 | 月收入栏是「—」 | 不入库 |
| 待判 | 9 | 有金额但上下文无法判定口径 | 待人工 |

`has_ai` 的判定用页面「tag + name + 一句话 + 产品是什么」做关键词匹配（AI/Agent/大模型/LLM/智能/生成式…）。
`tag` 字段本身只有三值：官方披露 186 / 平台数据可查 140 / 第三方估算 12 —— 后两者不提供收入，第一条才是主战场。

### 2.2 名称去重很重要

只按 slug 去重会漏：库里有 `gojiberryai`（MRR $424,368），站点上是 `gojiberry`。
用 `harvest.seen_keys()` 的 ids + slugs + names（`text_clean.norm_name`）三口径后，344 → **333 条真新增**。

---

## 三、本次入库 8 条（`data/cases.json` 255 → 263）

全部 `verification=partial`、`needs_review=true`。理由：howtomakemi 是聚合站，页面给了具名来源
（访谈、评测、官网）但没有可点的一手链接，够不到 `official` —— 与 `audit_evidence.py` 把 sierra 从
official 降级的判据一致。

¥ → $ 只做「除 6.72」这一个动作（该常数由站点自身换算反推、且与 cursor ¥67.2亿 = $1B、
lovable ¥13.44亿 = $200M 对得上）；**原始 ¥ 文案逐字写进每组 `metric_note`**，任何时候可回页核对。

| id | 产品 | 口径 | 数字（页面原文） | 折算 | 看点 |
|---|---|---|---|---|---|
| `arcads` | AI 演员生成视频广告 | annual | 2026 年营收约 ¥1.01亿 | ≈$15.0M | 10 人、6000 付费客户、人均营收 ¥672万 |
| `instantly` | 冷邮件可交付性平台 | arr | ARR 约 ¥2.57亿 / MRR ¥2,150.4万 | ≈$38.2M / $3.2M | 零融资、24,500 付费、ARPU ¥874 |
| `ahron` | Agentic HR（流程挖掘 + agent） | arr | ARR 突破 ¥753万 | ≈$1.12M | 4 人、零销售人员 |
| `fyxer` | Gmail/Outlook 里的 AI 执行助理 | arr | ARR 超 ¥1.14亿（2025-08） | ≈$17.0M | 8 个月涨十几倍、3 个月留存 90% |
| `heyreach` | LinkedIn 多账号自动化 | arr | ARR 约 ¥1.21亿（2026） | ≈$18.0M | 零融资、6500 客户、被 YC 拒两次 |
| `campfire` | AI 原生 ERP | mrr | 月收入 ¥336万 | ≈$500K | 月结 15 天→3 天、十二周融一亿美金 |
| `wispr-flow` | 语音输入 | arr | ARR ¥6,720万（2025-10） | ≈$10.0M | 付费转化 19%、270 家 F500 |
| `build-concierge` | 工地服务 AI 运营层 | arr | ARR 接近 ¥1,344万 | ≈$2.0M | 12 家客户、单客户 4 周 3863 工单 |

评分（`score_china_fit` + `score_solo_fit` 已写回）：

| id | china_fit | solo_fit | composite | 硬伤 |
|---|---|---|---|---|
| arcads | 62.0 | 68.6 | 63.3 | — |
| build-concierge | 62.6 | 50.7 | 53.1 | — |
| fyxer | 61.2 | 60.0 | 60.2 | — |
| ahron | 59.1 | 51.0 | 52.6 | — |
| wispr-flow | 59.1 | 63.4 | 60.0 | 免费输入法占位 |
| campfire | 50.4 | 31.4 | 35.2 | 会计准则 + 存量厂商 |
| instantly | 49.3 | 65.2 | 52.5 | 邮件群发合规 |
| heyreach | 35.7 | 63.8 | 41.3 | 平台缺失 + 自动外发合规 |

全部落在「别碰」象限（阈值 70）——这是实情：这些公司的护城河是创始人履历、平台生态或存量客户，
不是产品形态。它们的价值在**方法可搬**（定价结构、信任设计、ROI 叙事），这也是每篇 `verdict` 的落点。

---

## 四、待入库 backlog：47 条

筛选条件：官方披露 + 收入口径 + AI 相关 + 三口径去重后未见 + 未在本轮入库。

⚠ 下表仍有 4 条混了融资语境（`tern-group` / `rox` / `develop-health` / `primeintellect`），
入库前必须回页确认金额到底是 ARR 还是轮次。另 `research-match` 与 `gistly` 页面注明是
**TrustMRR Stripe 授权验证**，这两条可以直接走 TrustMRR 通道拿到支付级证据，优先级最高。

| slug | 产品 | 页面月收入 | 地区 | 页面口径 |
|---|---|---|---|---|
| higgsfield | Higgsfield | ¥47.04亿 | 🇺🇸 美国 | 2026 年 8 月，较 1 月 ¥13.44亿增长 2.5 倍 |
| medvi | MEDVi | ¥26.95亿 | 🇺🇸 美国 | 净利率 16.2% |
| huntress | Huntress | ¥16.8亿 | 🌐 哥伦比亚 | 月收入 |
| lovable | Lovable | ¥13.44亿 | 🇸🇪 瑞典 | 2025 年 12 月披露，Bloomberg/Reuters 核实 |
| bland-ai | Bland AI | ¥6.72亿 | 🇺🇸 美国 | YC，2023-06 |
| glean | Glean | ¥6.72亿 | 🇺🇸 美国 | 截至 2025 年 1 月 31 日 |
| hightouch | Hightouch | ¥6.72亿 | 🇺🇸 美国 | 月收入 |
| micro1 | micro1 | ¥6.72亿 | 🇺🇸 美国 | 2025 年底 |
| primeintellect | Prime Intellect | ¥6.72亿 | 🌐 未披露 | 估值 ¥67.2亿，Radical Ventures 领投 ⚠ |
| retell | Retell AI | ¥5.38亿 | 🇺🇸 美国 | 2026 年 8 月披露 |
| cognition | Cognition | ¥4.91亿 | 🇺🇸 美国 | 2025-06，2024-09 为 ¥672万，九个月约 70 倍 |
| skello | Skello | ¥3.77亿 | 🇫🇷 法国 | 月收入 |
| appliedcompute | Applied Compute | ¥3.36亿 | 🇺🇸 美国 | 2026-Q3 |
| hello-clever | Hello Clever | ¥3.36亿 | 🇦🇺 澳大利亚 | 2026 年 8 月 |
| openrouter | OpenRouter | ¥3.36亿 | 🇺🇸 美国 | 较 2025 年 10 月的 ¥6,720万涨 5 倍 |
| paraform | Paraform | ¥3.36亿 | 🇺🇸 美国 | 未披露绝对值 |
| cruva | Cruva | ¥3.16亿 | 🇺🇸 美国 | 月收入 |
| decagon | Decagon | ¥2.96亿 | 🇺🇸 美国 | 2025 年底 |
| n8n | n8n | ¥2.69亿 | 🇩🇪 德国 | 截至 2025 年 3 月的公司口径 |
| fable-security | Fable Security | ¥2.08亿 | 🇺🇸 美国 | 月收入 |
| omnidocs | Omnidocs | ¥2.03亿 | 🇩🇰 丹麦 | 含 Xink |
| tern-group | TERN Group | ¥2.02亿 | 🇦🇪 阿联酋 | 2025 年 9 月 ¥1.61亿 A 轮 ⚠ |
| opencode | OpenCode | ¥1.68亿 | 🇨🇦 加拿大 | 月收入 |
| lexroom | Lexroom | ¥1.57亿 | 🇮🇹 意大利 | 2025 年底 ¥5,376万、2024 年底 ¥672,000 |
| sanas | Sanas | ¥1.41亿 | 🇺🇸 美国 | Inc 记 2 年做到 ¥4.17亿、2026 冲 ¥8.74亿 |
| viverdeia | Viver de IA | ¥1.34亿 | 🇧🇷 巴西 | 2026 |
| develop-health | Develop Health | ¥1.3亿 | 🇺🇸 美国 | 2023-05，South Park Commons 领投 ⚠ |
| morph | Morph | ¥1.28亿 | 🇺🇸 美国 | 见数据口径 |
| picturethis | PictureThis（形色） | ¥1.01亿 | 🇨🇳 中国 | 2026-07 FluxBuilder 报道 |
| serviceform | Serviceform | ¥1.01亿 | 🇫🇮 芬兰 | Seedtable 2026-07 估测 |
| doomers | Doomers | ¥6,720万 | 🇺🇸 美国 | 2026 年 9 月更新口径 |
| rox | Rox | ¥5,376万 | 🇺🇸 美国 | General Catalyst 领投 ⚠ |
| stimuler | Stimuler | ¥3,696万 | 🇮🇳 印度 | 约 ¥307.8万/月 run rate |
| jobright | Jobright | ¥3,360万 | 🇺🇸 美国 | 月收入 |
| geviti | Geviti | ¥2,016万 | 🇺🇸 美国 | 公开口径约 ¥2,016万 |
| outward | Outward Intelligence | ¥672万 | 🇺🇸 美国 | ARR 超 ¥6,720万，约 ¥557.8万/月 |
| rosie | Rosie | ¥672万 | 🇺🇸 美国 | 月收入 |
| tjm-labs | TJM Labs | ¥672万 | 🇺🇸 美国 | 第三方估算，公司未审计披露 |
| boredhumans | BoredHumans | ¥492.6万 | 🇺🇸 美国 | 公开报道 ARR 约 ¥5,913.6万 |
| crustdata | Crustdata | ¥336万 | 🇺🇸 美国 | 公司官网「We hit ¥6,720万 ARR!」 |
| workhero | WorkHero | ¥336万 | 🇺🇸 美国 | 2025 年秋，Built In 职位页 |
| sandra-ai | Sandra AI | ¥295.7万 | 🇫🇷 法国 | GetLatka 估算，约 ¥24.9万/月 run rate |
| autoshorts | AutoShorts.ai | ¥69.2万 | 🇺🇸 美国 | 2026 年 8 月公开数据（与正文 ¥67.2万 存在 3% 出入） |
| research-match | Research Match | ¥36.4万 | 🇺🇸 美国 | **TrustMRR Stripe 授权验证**，2026-07-15 快照 |
| salesrobot | SalesRobot | ¥33.6万 | 🇮🇳 印度 | 月收入 |
| gistly | Gistly | ¥32万 | 🇯🇵 日本 | **TrustMRR Stripe 授权验证**，2026-07-15 快照 |
| crowdreply | CrowdReply | ¥665 | 🌐 未披露 | 早期阶段，无可比收入 |

---

## 五、明确不采的三类（附理由）

1. **AppStore 平台数据 264 条**：只有评分、价格、开发者，收入未披露。库是收入口径库，进不来。
2. **失败复盘 180 条（Loot Drop 翻译稿）**：本站自己注明是翻译非原创，且属于「怎么死的」，
   与收入案例的方法论混在一本库里会污染口径。建议另开独立栏目再做。
3. **扩展平台数据 17 条（Mozilla）**：Firefox 扩展的日活/评分，收入多未披露。

---

## 六、复现命令与产物

```bash
# 1) TrustMRR 线：种子清单 → 官方 .md 通道（404 记 gone，抓不到就说明源已下架）
python scripts/harvest.py --source trustmrr_sitemap \
       --tm-seeds data/tm_seeds_howtomakemi.txt --tm-sweep 64 --max-inbox 0

# 2) 官方披露线：344 页逐页抓 + 字段解析（只读，输出 tmp/htm_official_scan.json）
python scripts/_oneoff/scan_htm_official.py

# 3) 入库（8 条，幂等：已存在的 id 自动跳过）
python scripts/_oneoff/ingest_htm_official.py --dry   # 先看
python scripts/_oneoff/ingest_htm_official.py         # 再写

# 4) 补两套评分 + 重建静态站
python scripts/score_china_fit.py
python scripts/score_solo_fit.py
python scripts/build_static.py

# 5) backlog 表
python scripts/_oneoff/htm_backlog_table.py
```

产物：

| 文件 | 内容 |
|---|---|
| `tmp_htm_trustmrr_revenue.json` | 88 条 TrustMRR 明细（slug/名称/页面收入/验证方/地区） |
| `tmp_htm_classified.json` | 893 条新增的粗分类 |
| `tmp/htm_official_scan.json` | 344 条官方披露的结构化解析（产品是什么/怎么赚的/流量/创始人/来源） |
| `data/tm_seeds_howtomakemi.txt` | 64 个 LIVE 且库内未见的 TrustMRR slug（种子清单） |
| `tmp/backlog_table.md` | 47 条 backlog |
| `scripts/_oneoff/` | 本轮三个一次性脚本（扫描 / 入库 / backlog） |
