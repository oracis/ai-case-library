# TrustMRR 抓取能力诊断：到底漏了什么

调查时间：2026-09-16 · 对象：`scripts/harvest.py` 的 `harvest_trustmrr()`

---

## 一句话结论

**远没抓全。** 现抓取只拿到「名字 + 简介 + 一个 URL」，而 TrustMRR 实际提供 **三条更好的数据通道**
（表格直解 / 详情页 JSON-LD / 公开 JSON API），每条都带**官网链接**和 **20~35 个字段**。
你说的「每一项都有链接、Visit 指向官网、里面有很多数据」——**全部成立，而且官方还专门为 AI 准备了
纯文本与 JSON 接口，我们一个都没用。**

---

## 一、你截图里的三件事，逐条核实

### ① 榜单每一项都有链接 —— 是真的

首页 Leaderboard 是**标准 HTML 表格**，每行一个 `<a href="/startup/xxx">`：

```html
<tr data-slot="table-row" ...>
  <a class="contents" href="/startup/chatbase">
    <td>🥈</td>
    <td>Chatbase / Create an AI chatbot for your business...</td>
    <td><a href="/founder/yasser_elsaid_">@yasser_elsaid_</a></td>
    <td class="text-right font-mono">$863,632</td>
    <td>MoM Growth</td>
  </a>
</tr>
```

实测首页有 **72 个 `/startup/` 详情页链接**。而我们的抓取器**完全没碰表格** ——
它走的是 JSON-LD（那是降级数据，只有 name/description/url）。

### ② 详情页有 Visit 按钮指向官网 —— 是真的

`https://trustmrr.com/startup/stan` 里：

```html
<a ... class="... bg-primary ...">Visit<svg .../></a>
<!-- href = https://stan.store?ref=trustmrr -->
```

更关键的是**官网地址明写在 JSON-LD 里**：

```json
{"@type":"Organization","name":"Stan",
 "url":"https://stan.store/",          ← 官网字段，明文
 "category":"Content Creation",
 "foundingDate":"2023-04-19",
 "keywords":["Content Creation","Software","B2C","stripe"]}
```

**这就是你问的「为什么没有官网」的答案 —— 官网一直在页面上，是我们没读。**

### ③ 里面有很多数据 —— 是真的，而且超乎预期

| 通道 | 位置 | 字段量 |
|---|---|---|
| 表格直解 | 首页 `<tr>` | 排名 / MRR / MoM 增速 / 创始人 |
| 详情页 JSON-LD | `/startup/<slug>` | 官网、分类、成立日期、国家、创始人 + X 账号、revenue 三项、订阅数 |
| **官方 .md** | `/startup/<slug>.md` | 3.5KB 纯文本，收入时序（日/月/全期） |
| **公开 JSON API** | **`/api/ai`** | **35+ 字段**（见下） |

---

## 二、重大发现：TrustMRR 官方为 AI 准备了专门接口

### `https://trustmrr.com/api/ai` —— 公开 JSON，无需鉴权

实测 HTTP 200，`application/json`，15.5KB。顶层结构：

```json
{
  "metricAccess": {...},
  "recentlyListedStartups": [ 25 条 ],
  "bestDeals":              [ 25 条 ]
}
```

单条字段（实测 Vid.AI）：

```json
{
  "name": "Vid.AI",                    "slug": "vidai-llc",
  "url": "https://trustmrr.com/startup/vidai-llc",
  "markdownUrl": ".../vidai-llc.md",
  "icon": "https://files.stripe.com/...",
  "description": "Turn any idea or script into...",
  "website": "https://vid.ai",         ← ★ 官网，直接给
  "isMobileApp": false,
  "country": "US",
  "foundedDate": "2024-09-24T00:00:00.000Z",
  "category": "Artificial Intelligence",
  "paymentProvider": "stripe",
  "targetAudience": null,
  "revenue": { "last30Days": 45951.7, "mrr": 63750.5, "total": 1598657.34 },
  "customers": 6626,
  "activeSubscriptions": 847,
  "askingPrice": 1500000,
  "profitMarginLast30Days": 69,
  "growth30d": -13.44,  "growthMRR30d": -15.96,
  "multiple": 2.72,     "rank": 63,
  "visitorsLast30Days": 128576,
  "googleSearchImpressionsLast30Days": null,
  "revenuePerVisitor": 0.353,
  "onSale": true,       "firstListedForSaleAt": "...",
  "listingTier": "premium",
  "pageviewCount": 6465, "offerCount": 10,
  "stealthMode": false,
  "xHandle": "priymrj", "xFounderName": "Priyam Raj",
  "xProfilePicture": "https://pbs.twimg.com/..."
}
```

### `https://trustmrr.com/startup/<slug>.md` —— 官方 AI 读本

实测 3.5KB 纯文本。包含：Profile / **Verification Sources**（支付网关验证状态）/
**Revenue 全时段表** / **日活收入 30 天** / **月度收入时序** / Metric Snapshots /
Startup Insights / Tech Stack / **SEO Domain Data** / Screenshots / Acquisition，
末尾还附一段**可直接解析的推荐列表 JSON**。

还声明了：`https://trustmrr.com/llms.txt`（3KB，站点说明）与 `/api/ai`（公开 AI 端点）。

---

## 三、我们漏了什么：字段对照

| 字段 | 现在 | API 里 |
|---|---|---|
| 名称 / 简介 | ✅ | ✅ |
| **官网 website** | ❌ **完全没有** | ✅ |
| 排名 rank | ❌（只写进 headline 文字） | ✅ |
| **MRR / 月收入 / 累计收入** | ❌ 只写「具体数字待补」 | ✅ 三个数 |
| 增速 growth30d / MoM | ❌ | ✅ |
| 客户数 / 活跃订阅 | ❌ | ✅ |
| 利润 margins | ❌ | ✅ |
| 访客数 / 每次访问收入 | ❌ | ✅ |
| 交易倍数 multiple / 要价 | ❌ | ✅ |
| **分类 category** | ❌ 写「未分类」 | ✅ |
| 国家 / 成立日期 | ❌ origin 写「未披露」 | ✅ |
| **创始人 + X 账号** | ❌ note 里手动写文字 | ✅ 结构化 |
| Markdown 详情地址 | ❌ | ✅ |

**实测对比**：我们抓的 `clearwell` 条目的 headline 是
`"TrustMRR 验证收入榜条目（具体数字待补）"` —— 而 API 里 Clearwell 的 MRR 是现成的。
**等于把有数据的字段写成了「待补」。**

---

## 四、为什么会这样

`harvest_trustmrr()` 只读了首页 JSON-LD 的 `ListItem`，那里 `url` 恰好是**榜单自身页面**，
于是全落成 `https://trustmrr.com/` 或 `/startup/xxx`，「官网」概念根本没进来。
**表格、详情页、`.md`、`/api/ai` 四条路，一条都没走。**

这也解释了上一轮 stan「没有官网」——
不是采集时机问题，是**采集器压根不读官网字段**。

---

## 五、建议改法（未动代码）

| 优先级 | 改什么 | 怎么做 |
|---|---|---|
| **P0** | 主通道换成 `/api/ai` | 一次请求拿 50 条 × 35 字段，**官网/MRR/排名/分类/创始人全齐**，最省事 |
| **P0** | 补 `source_url` + 官网 | 候选记录补 `source_url`（详情页）与 `website`（官网）两个字段，前台才能显示 |
| **P0** | 修「未分类」「未披露」 | 分类、国家、成立日期直接用 API 值 |
| **P1** | 详情页走 `.md` | 需要收入时序/验证来源时抓 `.md`，比解析 HTML 稳 |
| **P1** | 榜单表格直解 | 补榜单排名与 MoM 增速（表格里有，JSON-LD 没有） |
| **P1** | 存量回填 | 用 slug 批量拉 `/api/ai` 回填已有 42 条 trustmrr 条目 + 36 条候选 |
| **P2** | 遵守 `llms.txt` | 官方已声明抓取规则，落地时读一下 |

---

## 附：本次实测的原始证据

- `_probe/home.html`（1.3MB 首页，含 Leaderboard 全部 42 行）
- `_probe/stan.html`（详情页，含 Visit→`stan.store?ref=trustmrr`）
- `_probe/stan.md`（官方 AI 读本，3.5KB）
- `_probe/api_ai.json`（公开 API，50 条 × 35 字段）
