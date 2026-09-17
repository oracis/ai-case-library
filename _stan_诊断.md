# Stan 这条候选的溯源诊断

调查时间：2026-09-13 · 对象：`data/candidates.json` 中的 `stan`

---

## 一句话结论

**Stan 确实存在，AI 把它判成「同名撞车」是错的。**
它来自 **TrustMRR 榜单**（不是官网）；「没有官网」是因为**整个候选池的 36 条全部丢了 `source_url` 字段**，不是 stan 单独的毛病；你给的 `https://www.stan.store/` **就是它**。

---

## 一、材料是从哪里来的？

### 来源：TrustMRR 首页的 JSON-LD 榜单，不是官网

```
来源站点  https://trustmrr.com/          （TrustMRR：SaaS 收购市场 + 验证收入数据库）
抓取位置  首页 <script type="application/ld+json"> 里的
          "@graph" → ListItem 列表（name = "Top startups by verified revenue"）
抓取代码  scripts/harvest.py → harvest_trustmrr(limit)
采集日期  2026-09-11（候选记录的 added_at）
```

我在榜单 JSON-LD 里确实抓到了这一条：

```json
{"@type":"ListItem","position":2,
 "url":"https://trustmrr.com/startup/stan",
 "item":{"@type":"Organization","name":"Stan",
         "url":"https://trustmrr.com/startup/stan"}}
```

**注意 `url` 字段指向的是 `trustmrr.com/startup/stan`，不是公司官网** —— 这正是 `harvest.py:333` 那行
`url = it.get("url") or (it.get("item") or {}).get("url") or "https://trustmrr.com/"` 取到的值。

### 我实地打开 `https://trustmrr.com/startup/stan` 看到了什么

```
标题      Stan - $2,844,652 last 30 days | TrustMRR
简介      Stan enables people to make living and work for themselves
           （＝候选里那句「帮创作者靠自己活着和工作」的原文）
All-time revenue   $76,627,685
Ranked             #3 on TrustMRR
MRR                $3,569,654
Active subscriptions  101,590
Founder            Vitalii Dodonov
Followers          15.6k on 𝕏
Founded            April 2023 · United States
Domain Rating      89 / 100 → stan.store
Tech stack         Stripe
```

**结论：候选记录里的每一项（数字、创始人、简介、品类）都能在这页对上。来源就是这条 TrustMRR 详情页。**

---

## 二、为什么「没有官网」？

**不是 stan 的问题 —— 候选池 36 条，0 条带 `source_url`。**

实测统计：

| 数据文件 | 条数 | 带 `source_url` |
|---|---|---|
| `data/inbox.json`（采集队列） | 344 | **344 条全有** ✅ |
| `data/candidates.json`（候选池） | 36 | **0 条有** ❌ |

而 `source_url` 正是前台候选卡片用来渲染那行来源链接的字段：

```js
// static/app.js:531
${c.source_url ? `<div ...><a href="${esc(c.source_url)}" ...>${esc(c.source_url).slice(0,88)} ↗</a></div>` : ''}
```

`source_url` 为空 → 整段判定为假 → **那行链接直接不渲染**，所以页面上看着像「找不到官网」。

### 根因：这批候选是在「字段生成之前」转进去的

时间线比对：

- `harvest.py:579` 现在会给每条写 `"source_url": it.get("url")`；
- 但这批候选的 `added_at` **全部是 2026-09-11**，且**没有一条带 `promoted_from_inbox` 标记**；
- 而 `server.py:844` 的 `to-candidates` 会写入 `promoted_from_inbox`。

两者一起说明：**这批候选不是走 `to-candidates` 接口转的，而是 09-11 那次直接用脚本灌进 `candidates.json` 的**
（当时 `harvest.py` 写候选时还没带 `source_url`，后来才补上 —— 队列里的 344 条都有，就是补过之后的产物）。

所以这不是「stan 缺官网」，是**这批历史候选整体缺来源字段**。

---

## 三、`stan.store` 是你给的那个网址吗？

**是同一个主体，可以确认。**

| 证据 | 内容 |
|---|---|
| TrustMRR 详情页 | Domain Rating 一栏写明 **`stan.store`**（89/100） |
| 打开 `https://www.stan.store/` | 标题 **`Stan - Your Creator Store`**，HTTP 200 |
| 简介互证 | `helping creators make a living/work for themselves` ↔ 候选「帮创作者靠自己活着和工作」 |
| 商业模型 | 候选写「订阅制 + 交易市场」 ↔ stan.store 是创作者开店卖订阅/数字商品 |

补充两点：
- `trustmrr.com/startup/stan-store` 是 **404**，正确的详情页路径就是 `/startup/stan`；
- `stan.store` 是前端 SPA（禁用 JS 只显示一句提示），所以纯 curl 抓不到正文 —— 这不代表它不存在。

---

## 四、⚠️ 顺带纠正一个大问题：AI 判错了

`data/verifications.json` 里 stan 的最新草稿把它判成 **`disputed`（存疑）**，理由写的是：

> 「本次抓回的原文里，名为 Stan 的条目全部是 Eminem 与 Dido 的 2000 年歌曲……该候选疑为同名撞车。」

**这个判定是错的，是 AI 搜索被同名噪音带偏了。** 它的 5 条来源里：

| 来源 | 是否相关 |
|---|---|
| `mingnify.com` 讲 TrustMRR 的博客 | 勉强相关（但没提 Stan） |
| 百度百科「Stan」（Eminem 歌曲） | ❌ 无关 |
| `music.163.com` 网易云 Eminem《Stan》 | ❌ 无关 |
| `y.qq.com` QQ音乐 Eminem《Stan》MV | ❌ 无关 |
| `github.com/chinatian/trustmrr` | 勉强相关 |

**5 条来源里 3 条是流行歌曲**，AI 据此推不出平台，就下了「不存在」的结论。实际 TrustMRR 详情页上数据一应俱全。

另外草稿里那句「榜单前几名月收入为 $800k–$900k」也是**过时数据** —— 现在榜首是 $3,569,654，那条二手博客的数字早就不对了。**这正是「只信二手转述会出事」的活样本。**

### 这暴露了两个真问题

1. **无关来源没有被剔除**：`ai_verify.py` 会把搜索引擎返回的所有结果都当证据，`music.163.com` / `y.qq.com` 这种明显跑题的链接也进了来源表，直接污染结论。
2. **纯中文搜索 + 二手博客的检索策略不够**：对海外产品，应该优先抓 TrustMRR / 官网 / 创始人 𝕏，而不是中文聚合站。

---

## 五、建议的修法（未改动代码，等你点头）

| 优先级 | 修什么 | 怎么修 |
|---|---|---|
| **P0** | 候选池补回 `source_url` | 按 `harvest_source` 回查 `inbox.json`，把同 id / 同名的 `source_url` 回填；或直接在页面上改成从 `inbox` 侧读取 |
| **P0** | 前端「没有来源」要说清楚 | 现在字段空就整段不渲染 → 改成显示「来源未记录」灰字，避免误以为没有官网 |
| **P1** | `ai_verify` 剔除无关来源 | 加域名黑名单 + 标题/正文相关性打分，跑题的来源不进 `sources` 表 |
| **P1** | 海外产品检索策略 | 优先 TrustMRR / 官网 / 创始人账号，中文聚合站降权 |
| **P2** | stan 这条本身 | 用 `trustmrr.com/startup/stan` 作为一手来源重跑核实，口径是 **MRR $3,569,654**（不是 all-time $76.6M） |

### 关于口径的提醒

候选 note 里那句担心是对的，且现在有答案：

- **MRR $3,569,654/月** → 年化约 **$42.8M**
- **All-time revenue $76,627,685** → 累计

TrustMRR 页面上两个数都有。**注意页面标注了 `Stripe API key expired. Data last updated Apr 20, 2026`** —— 也就是说这个数字是 **2026-04-20 的快照**，已过期 5 个月，不能当成现值用。这一条比口径问题更关键。
