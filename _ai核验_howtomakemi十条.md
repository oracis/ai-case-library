# AI 核验：howtomakemi 入库的 10 条

2026-10-26 21:13–21:26｜模型 `deepseek-ai/DeepSeek-V4.1-Flash-1017`（ModelScope 免费档，实测响应 2.8s）
｜跑法 `python scripts/verify_case.py <id>`（逐条，服务端规则引擎为准，`publish=False`）

覆盖对象：本轮从 howtomakemi 入库的全部 10 条 —— 官方披露线 8 条（arcads / instantly / ahron /
fyxer / heyreach / campfire / wispr-flow / build-concierge）+ TrustMRR 降级线 2 条（gistly / research-match）。

## 结论表

| 案例 | 库内口径 | AI 口径 | 一致 | AI 等级 | 质量分 | 抓页 | 判定 |
|---|---|---|---|---|---|---|---|
| heyreach | arr | arr | ✅ | official | **85** | 2 | 可发布·精品（含 1 条 AI 反证） |
| instantly | arr | arr | ✅ | official | 60 | 2 | 可发布·精品 |
| research-match | mrr | mrr | ✅ | official | 60 | 2 | 可发布·精品 |
| arcads | annual | annual | ✅ | official | 60 | 2 | **还差 1 项**：AI 否掉页面数字 |
| ahron | arr | arr | ✅ | official | 25 | 2 | **还差 1 项**：AI 否掉页面数字 |
| gistly | mrr | mrr | ✅ | official | 15 | 2 | 可发布·精品 |
| fyxer | arr | arr | ✅ | official | 15 | 2 | 可发布·精品（含 1 条 AI 反证） |
| campfire | mrr | mrr | ✅ | partial | 15 | 1 | **还差 1 项**：AI 否掉页面数字 |
| wispr-flow | arr | arr | ✅ | partial | 15 | 1 | 可发布·实核 |
| build-concierge | arr | arr | ✅ | founder | 15 | 1 | **还差 1 项**：AI 否掉页面数字 |

## 一、口径 10/10 一致，数字无错位

AI 独立判定的口径与入库时写的 `caliber` **十条全对**，`arr / mrr / annual` 也对得上原文
（instantly $38.2M ARR + $3.2M MRR、fyxer $17.0M、heyreach $18.0M、ahron $1.12M、
wispr $10.0M、campfire $500K、gistly $47.6K、research-match $54.2K）。
上一轮那张 `data/htm_backlog_caliber.json` 口径表**经得起这轮复核**。

## 二、4 条 `caliber_consistent_ai=False`，全栽在同一个地方

四条（arcads / ahron / campfire / build-concierge）的 AI 判定都是「口径与数字不一致」，
原因完全同构：**页面「💰 月收入」模板栏与同页正文自相矛盾**。这是站点的模板缺陷，不是我抄错：

- **arcads**：月收入栏 ¥1.01亿，正文是「2026 年**营收** ¥1.01亿」。若按月收入口径，年化约
  $180M，而同页 ARR 台阶是 2024-06 $1M → 2025-05 $6M → 2025-11 $10M → 2026 年 $15M，
  差一个数量级。→ 入库按 `annual` 正确。
- **ahron**：月收入栏 ¥753万，正文是「2026 年 8 月 **ARR** 突破 ¥753万」。差 **12 倍**。
  且页面自己写明「第三方 ARR 数据库估算，未经审计」。
- **campfire**：月收入 ¥336万，但未标时点、未定义经常性，且仅见聚合站转述（无一手链接）。
- **build-concierge**：月收入 ¥112.2万 是页面用 ¥1,344万 ÷ 12 **反推**的；同段还并存
  「年化营业额接近 ¥266.4万」，两个数互相矛盾。

AI 同时纠了站点级问题：**页脚「收入数据经 Stripe / Paddle 验证」是站点通用声明**，
逐条核对后，arcads / ahron / build-concierge 的原文里都没有任何支付侧数据 ——
所以这 4 条的「支付验证」徽标不能当本案例的验证方式读。

## 三、AI 的 `verification` 判定偏乐观，不能用来升等级

10 条里 AI 给了 **7 条 `official`**，但依据只是「抓到了官网首页」（`source_kinds` 含 `official`）。
实际上：gistly 官网连定价都不公开（唯一入口 Book a demo），arcads / ahron 官网没有收入数字。
**「抓到官网」≠「数字来自官方」**。库内这 10 条维持 `verification=partial`，不按 AI 结论上调。

## 四、这轮不是独立复核（必须记住的边界）

10 条**全部**只抓到 1–2 页，且抓的就是案例 `sources` 里登记的那两个地址：
`已知地址：抓到 2 页` / `检索：抓到 2 页原文` —— **独立检索 0 命中**。
与库里既有结论一致（cn.bing 假阳性、Google CSE 无凭据、DDG 出口封锁）。
所以本轮实质是「对着同一份聚合站页面 + 官网自证」核，能核出页面内部矛盾，
核不出「这个数字在别处是否成立」。要真正独立复核，得先解决检索出口。

## 五、门槛：5 条 `solo_possible` 被否

fyxer / heyreach / campfire / wispr-flow / build-concierge 被 AI 明确否掉「个人能做的路径」，
与上一轮两套评分把它们放进「别碰」象限的结论一致。arcads / instantly / ahron /
research-match 三态门槛全 yes，gistly 是 yes + unknown。

## 六、发现一个显示 bug（未修，待确认）

`scripts/verify_case.py` 结尾打印：

```
→ 够精品档（15 ≥ 60）      ← gistly / fyxer 都是这句
```

原因：档位由 `verify_rules.py:369` 决定 —— **只要有一手来源就进精品池，与分数无关**；
而这行文案写死成「分数 ≥ 阈值」，于是 15 分也印成「15 ≥ 60」。
纯显示问题（不影响写库、不影响 tier 计算），但会误导读日志的人。
建议改成打印 `tier_reason`（`有一手来源（official/secondary），按默认政策进精品池`）。

## 七、副作用与状态

- `data/verifications.json` 14 条（含本轮 10 条草稿，**gitignore，不入库**），每条都带完整
  `corrections` 与 `caliber_reason`，可直接回查 AI 的原始论据。
- `human_read_at` 全部为空 —— 人工核读标记没有被 AI 代勾（红线守住了）。
- `cases.json` 仍是 265 条，**本轮未 publish、未改动任何案例**。
- `_backfill_backup/` 被写了 30 个备份文件（10 次 × 3 份，15MB，gitignore）。
- 本地后台 5052/5053 由本轮启动，仍在跑；`http://127.0.0.1:5053` 可直接看这 10 条草稿。

## 复现

```bash
python server.py &                     # 5052/5053
python scripts/verify_case.py gistly   # 单条；逐条跑，别并发（每次都改 candidates.json）
```
