# TrustMRR 抓取通道（2026-09-24 定稿，2026-09-30 迁出记忆文件）

原 `MEMORY.md` 第 7 / 7b 条的完整内容，保留作查证细节。

7. **深核的抓取通道有三层，按序退让**（2026-09-24 定稿，勿再用 cn.bing 打头）：
   `known_urls()` → 常规抓取 → **不成功才用无头 Chrome**。关键是 `known_urls`：
   ① TrustMRR 挂牌页一律换官方 `/startup/<slug>.md`（llms.txt 定为 AI 入口，
   13KB 干净 Markdown，含挂牌价/倍数/逐日收入；**别对 TrustMRR 用无头浏览器**，
   llms.txt 明确要求别爬渲染后的 HTML）；② 候选没 URL 时查免 key 的
   `/api/ai/discovery`（50 条带 website）+ id 直拼（slug 常与 id 不同，如
   `livecrew-ai`→`livecrew`、`localkdock`→`localdock`）；③ 仍未果才搜索（cn.bing 兜底）。
   单条详情 `get_startup(slug)` 要 OAuth，免不了。
   **给候选补 `website` 提升最大**（le19emetrou：5%→73% 置信度、0→80 分）。
   候选 id ≠ 真 slug，探测时要试变体，别一次 404 就判「已下架」。
7b. **缺 `trustmrr_slug` 不等于不能复核**（2026-09-24 定稿）。闸门要的是
   `source_kinds` 非空，官网（official）/ 创始人（founder）/ 评测（review）都算；
   voklit 实测挂牌页已撤、只靠官网照样入库。要找回 TrustMRR 证据就用两张官方索引：
   - `https://trustmrr.com/startup-sitemap.xml`（**robots.txt 公布**，全站
     **10,691 条 slug**）—— 存在性检查。必须显式要 **gzip**：`http_get` 发的是
     `identity`，1.7MB 那种大小 120 秒都拉不完，gzip 后 160KB / 6 秒。
   - `https://trustmrr.com/api/ai`（llms.txt 公布，89 条）—— 名称/官网→slug
     匹配表，含 askingPrice/multiple，比 `/api/ai/discovery` 多一批。
   实现：`ai_verify.resolve_trustmrr_slug(confirm=)` + `scripts/resolve_slug.py`
   （dry-run 默认，`--write` 才落盘；默认只处理 cases/candidates）。
   `known_urls()` 兜底顺序 = 反查（confirm=True）→ id 直拼。
   **坑：sitemap 只证明 slug 存在，不证明同一个产品。** `private-venture-1`
   按 id 命中同名 slug，抓回却是另一个隐身挂牌（创始人/收入全对不上）——
   所以站点索引层必须抓 `.md` 核对 `# 标题`，不符或抓不到一律退回无果。
   Wayback 捞已撤页在本机不通（archive.org 502/timeout，需代理）。

7c. ⚠ **`.md` 后缀只对 `/startup/` 有效，`/founder/` 必须去后缀**（2026-10-05 实测）：
   `https://trustmrr.com/founder/hypitai.md` 返回 **HTTP 200，但内容是 404 页**
   （62KB 全是 Next.js 骨架，含 `NEXT_HTTP_ERROR_FALLBACK;404` 与 "Founder not found"）。
   **HTTP 200 完全掩盖 404** —— 只看状态码会把它当有效证据写进来源。
   正确端点：`https://trustmrr.com/founder/<handle>`（126KB，`<title>` 形如
   "Hypit AI (@hypitai) - 1 startup · $31,309 revenue"，带Total/MRR/粉丝/子项目列表）。
   与 startup 路径**正好相反**（`/startup/<slug>.md` 才是 llms.txt 指定的 AI 入口）。
   ⚠ 判据：不能只看 HTTP 码，必须查`<title>` 或搜 `NEXT_HTTP_ERROR_FALLBACK`；
   两条路径交叉印证（founder 页给「共几个 startup / 合计收入 / MRR / 粉丝」，
   startup `.md` 给「逐日收入 / 订阅数 / 口径 / 挂牌状态」—— 互补而非重复），
   拼接时两边数字必须对得上（Hypit 案例两边 MRR 同为 $14,124，交叉通过）。

7d. 🚨 **`.md` 返回 5xx 时的 HTML/JSON-LD 回落（2026-11-12 加，PRD #142）**
   —— 这是对第 7 条「别爬渲染后的 HTML」的**严格受限例外**，不是改口径：
   - **触发条件极窄**：`audit_delisted.probe()` 只在 `.md` 重试 3 次仍
     **HTTP 5xx** 时回落一次 `GET /startup/<slug>`。429 是限流（不许借更大的
     HTML 页绕过）、4xx 是拒绝、404 是下架、本机网络抖动都不回落。
   - **不爬渲染文本、不起无头浏览器**：只读 HTML 里内嵌的 schema.org
     `application/ld+json` → `Organization.additionalProperty`（官方结构化
     字段，按 `name` 取值，不按位置）。入口 `AD.parse_html_fallback()`。
   - **三重校验**：含 `NEXT_HTTP_ERROR_FALLBACK` 的 200 伪 404 页不认；
     页面 `@id`/`url` 必须带请求的 slug（防 CDN 串页，同 7b 的教训）；
     至少要有 `Verified revenue, last 30 days` 或 `Current MRR`。
   - 🚨 **它不能替 `.md` 作判定**：HTML 页**没有**
     `### Monthly revenue timeline`，而 `check_one()` 的完整月环比（COLLAPSE）
     与峰值留存（PEAK_RETENTION）只吃那条时间线。所以回落结果带
     `channel="html_fallback"` / `monthly_available=False`，由
     `promote_to_candidates.check_one()` **挂起**（`ok=None`）并打印实测数字，
     绝不自动通过。收入低于 `MIN_MRR` 的仍可直接判拒绝（那道闸不需要时间线）。
   - ⛔ **不许冒充 provenance**：JSON-LD 只有 `Payment provider = Stripe`，
     没有 `.md` 的 `Verified payment provider API source: ... (API key)`；
     `verified_via` 留 `None`，支付商另存 `payment_provider`。
   - ✅ **交叉对账（同刻、同 slug、`.md` 200 时）**：`meltkit` / `cliputils`
     两条的 mrr / 近 30 天 / 累计 / 订阅 / 两个 growth / 利润率逐项一致
     （仅取整差，如 $497.00 vs $497.39），`revenueLastSyncedAt` 与 `.md` 的
     `Revenue last synced` **逐字相同** ⇒ 数字同源，不是另编一套。
   - ✅ **反证「为什么必须挂起」**：11-12 13:4x 平台 `.md` 全面恢复后重跑
     110 条干跑，原先 27 条 500 挂起里 **8 条被完整月环比 / 峰值留存判拒**
     （`framenet` 完整月环比 −50.5%、`elyx-ai` 留存 35%、`failment` 4%…）。
     若当初拿 HTML 数字直接放行，这 8 条腰斩/萎缩生意会被写成案例。
