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
