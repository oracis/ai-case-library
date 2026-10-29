# howtomakemi.com 采集调研（2026-10-26，只读扫描，未写任何数据文件）

## 总览

- 站点：「开赛 CaseSite · 验证过的出海赚钱案例库」，howtomakemi.com（nginx/Ubuntu，非本库 OSS 部署域）。
- sitemap（sitemap-index.xml → sitemap-0.xml）共 1448 URL：案例页 **1446** + 首页 + /graveyard/。
- 与本地 seen_keys（ids/names/trustmrr_slugs 三口径）对账：slug 口径真新增 **915**，名称+slug 双口径后 **893**。
- 全量并发分类（逐页抓取，只读 GET）：

| 类别 | 条数 | 说明 |
|---|---|---|
| TrustMRR 收入案例（Stripe/RevenueCat/Creem/LemonSqueezy 验证） | **88** | 与库主线完全对口，含逐条「产品是什么/怎么赚的钱/学到什么/来源与数据」结构化正文 |
| 官方披露（融资/新闻口径） | 344 | 多为融资事件或未披露收入的公司（如 paper：月收入字段放的是 ¥2.28亿 A 轮，**口径混用红线**，入库必须单独标 source_type，不得进 MRR 漏斗） |
| AppStore 平台数据（iTunes API） | 264 | 以中国区 App 为主（轻颜相机/剪映/网易云等），收入未披露，只有评分/价格/开发者，**对收入口径库无采集价值** |
| 失败复盘（Loot Drop 翻译） | 180 | 墓碑区，含「怎么死的/烧钱/死因拆解/如果重做」，资料注明非原创翻译；可作独立内容线，不进收入案例库 |
| 扩展平台数据（Mozilla API） | 17 | Firefox 扩展日活/评分，收入多未披露（如 AdGuard、SponsorBlock） |

## TrustMRR 88 条的分档

- 估算月收入 ≥$300（晋升门槛 MIN_MRR=300，¥÷7.2 粗估）：**18 条**；<$300：67 条；零/未解析：3 条。

| slug | 页面显示月收入 | 验证 | 地区 |
|---|---|---|---|
| bustem-inc | ¥2,887,853 | Stripe 验证 | 🇺🇸 美国 |
| brand-on-demand-inc | ¥1,218,027 | Stripe 验证 | 🇺🇸 美国 |
| aeo-engine | ¥541,639 | Stripe 验证 | 🇺🇸 美国 |
| nutria-9e3eaa | ¥308,300 | RevenueCat 验证 | 🌐 地区未披露 |
| buildmyagent-io | ¥107,997 | Stripe 验证 | 🇺🇸 美国 |
| bookedin | ¥86,896 | Stripe 验证 | 🇺🇸 美国 |
| comp-ai | ¥84,282 | Stripe 验证 | 🇺🇸 美国 |
| 3ak-track-field | ¥79,323 | RevenueCat 验证 | 🇺🇸 美国 |
| coucou | ¥36,429 | Stripe 验证 | 🇫🇷 法国 |
| digital-natives-services-fz-llc | ¥25,388 | Stripe 验证 | 🇦🇪 阿联酋 |
| autoreels-ai | ¥8,864 | Lemon Squeezy 验证 | 🌐 摩洛哥 |
| firstrevenue | ¥8,561 | RevenueCat 验证 | 🌐 未披露 |
| airix-aeo-ai-visibility-platform | ¥7,231 | Stripe 验证 | 🇬🇧 英国 |
| authex | ¥6,424 | Stripe 验证 | 🇬🇧 英国 |
| 9x12tools | ¥6,337 | Stripe 验证 | 🇺🇸 美国 |
| bankconv | ¥5,880 | Stripe 验证 | 🇺🇸 美国 |
| agent-mma-llc | ¥4,946 | Stripe 验证 | 🇺🇸 美国 |
| 1milliontaps-lol | ¥3,582 | Stripe 验证 | 🇨🇿 捷克 |

⚠ 抽查提示：bustem-inc（¥288.8万/月）与 nutria-9e3eaa（¥30.8万/月）量级异常大，采前须先回 TrustMRR 原页核验口径（本机出口对 google 系全断，TrustMRR 直连可达）。另外 ae-intelligence 类两条 aeo-*（aeo-engine / airix）与库内已核过的 AEO 主题相邻但 slug 不同，是不同产品。

## 建议的采集方案（待确认后执行，本轮未执行）

1. **主采**：18 条 ≥$300 的 TrustMRR 案例 → 走既有 collect→triage 漏斗（url 指向 trustmrr.com/startup/<原slug> 前缀需先从页面「来源与数据」段拿真实 TrustMRR 链接，站内 slug ≠ TrustMRR slug）。
2. **不采**：AppStore 平台数据 264 条 + Mozilla 17 条（无收入口径）；官方披露 344 条默认不进 MRR 漏斗（口径混用红线），若要保留须单独 bucket + source_type=press。
3. **可选独立线**：失败复盘 180 条（Loot Drop 翻译稿），与收入库分开存放，适合做「墓碑」栏目而非收入案例。
4. 去重用既有 `harvest.seen_keys()`（slug 通道），页面里 trustmrr.com 原始链接做第二道校验。

中间数据（本次扫描产物，可复用）：`tmp_htm_new_norm.json`（893 真新增 slug）、`tmp_htm_classified.json`（全量分类）、`tmp_htm_trustmrr_revenue.json`（88 条 TrustMRR 收入明细）。