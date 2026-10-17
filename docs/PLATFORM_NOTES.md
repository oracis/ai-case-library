# 四平台接口能力与踩坑档案

> 本文是**详细档案**。MEMORY.md 只留铁律与索引。
> 全部为实测结论（头条/公众号 2026-10-16~17 更新），**别再靠猜**。

## 一、接口能力矩阵

| 平台 | 读 | 写 | 删 | 说明 |
|---|---|---|---|---|
| **B站** | ✅ 4 个接口 | ✅ `draft/add` 带 id 即更新 | ✅ `draft/delete` | 秒级完成，不开编辑器；已投稿列表 `x/space/article?mid=` |
| **头条** | ✅ `draft_list` + `mp_provider`（已发表只能导航式读） | ✅ `article/publish`（`--api`，需新鲜 `tt-anti-token`）+ 封面上传两级 `/spice/image` | ✅ `delete_draft` | 2026-10-17 更新，**「只能 UI」是旧结论**；群发与「给已有草稿补封面」仍 UI |
| **公众号** | ✅ `app_msg_list` + `appmsgpublish?sub=list` | ✅ `mp_editor_change_cover` / `mp_editor_set_content` | ✅ `operate_appmsg?sub=del` | 2026-10-16 更新，`action=del` 恒 200009 是猜错接口身 |
| **小红书** | ⚠ 无服务端 API | ⚠ 无 | ⚠ 无 | **草稿在浏览器本地 IndexedDB** |

### 封面白边体检（`scripts/verify_cover_edge.py`，2026-10-17 新增）

背景：headless 视口 ~780px < 封面画布 1080px 时，`Page.captureScreenshot` 的 clip
被裁到视口内，右侧 ~300px 变白边（含滚动条滑轨），**产物尺寸仍正常，肉眼发现不了**。
已对全部 47 张线上封面实测校准判据：**右缘 4.5% 区域平均亮度 > 200 ⇒ 白边脏封面**
（脏=255 白，净=47~75 设计底色；PROSP 修复前后 255→66）。

```bash
python scripts/verify_cover_edge.py --dir tmp/covers/audit --port 9222
# 退出码：0=全净；1=有脏；2=用法/连接错误
```

批量 `refresh --cover --cover-api` 跑完后**必须**重抓线上封面再体检一遍
（`tmp/wx_draft_audit.py` 抓图 + 本脚本判脏），别只看命令返回成功。

---

## 三、头条（2026-10-16~17 全部实测；此前「只能 UI」的记录已作废）

**全部接口都在 `/mp/agw/` 下，浏览器同源 fetch 即可（9223 端口 + PLAT_PROFILE）。**

### 已 API 化（可直接用）

| 用途 | 接口 | 关键点 |
|---|---|---|
| 读草稿箱 | `GET /mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231` | `{"code":0,"draft_list":[…]}` 全量；翻页用 `&end_cursor=<ts>`；`type=0/2` 同结果 |
| 删草稿 | `POST /mp/agw/creator_center/delete_draft?app_id=1231` | body **必须** `{"drafts":[{"draft_type":2,"gid":"…"}]}` |
| 读已发布 | `GET /api/feed/mp_provider/v1/?provider_type=mp_provider&aid=13&app_name=news_article&category=mp_article&stream_api_version=88&offset=N&count=10&client_extra_params={…}&app_id=1231` | 标题在 `data[i].assembleCell.itemCell.articleBase.title`；翻页 offset 0/10/20 |

代码：`fetch_draft_api` / `fetch_draft_api_raw` / `delete_draft_api` /
`fetch_published_articles`（都在 `scripts/toutiao_publish.py`）。

### 新增/保存（**纯 API 可用**，2026-10-17 定案）

```
POST /mp/agw/article/publish?source=mp&type=article&aid=1231&mp_publish_ab_val=0
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
tt-anti-token: HOikR1wqgY-6a2d77a42…            ← 🚨 必需，缺了返 code:7050
body: 20 个 form 字段（source=29 / title / content / save=0 /
      extra={…} / search_creation_info={…} / title_id=<ms>_<rand> /
      mp_editor_stat={} / pgc_feed_covers=[] / draft_form_data={"coverType":2} …）
resp: {"code":0,"data":{"pgc_id":"…"},"message":"保存成功"}
```

- `save=0` **就是存草稿**（不是发布）；首次不带 `pgc_id`＝创建，带了＝更新。
- 🚨 **body 必须 form-urlencoded**：用 JSON 发 HTTP200 但不生效。
- ✅ **`tt-anti-token` 只需新鲜，不必逆向**（见下方「四段对照」）。
- 用法：`toutiao_publish.py publish --api`（token 取一次用一批）。

### 封面（`upload_cover_api`，两级 fetch，纯 API，2026-10-17 实证）

```
① POST /spice/image?upload_source=20020003&aid=1231&device_platform=web
   multipart/form-data，字段名 = image（<blob>），头只要 Accept
   → {"code":0,"data":{"image_uri":"tos-cn-i-6w9my0ksvp/<id>",
                        "image_url":"https://image-tt-private.toutiao.com/…~tplv-obj.image?…",
                        "image_width":3840,"image_height":2160,…}}

② POST /spice/image?…&device_platform=web&need_cover_url=1
   body: imageUrl=<①的 image_url>；🚨 必须带 tt-anti-token
   → data.cover_url = …~tplv-tt-cover-v2.image  ← 这才是封面直链

③ publish body 里封面字段是 pgc_feed_covers（不是 cover_tos_url！！）
   [{"id":"","url":"<②的 cover_url>","uri":"<①的 image_uri>","ic_uri":"",
     "thumb_width":3840,"thumb_height":2160,
     "extra":{"from_content_uri":"","from_content":"0"}}]
```

🚨 **踩坑（三个都实测撞过）**：
1. **字段真名是 `image_uri` / `image_url`**（不是 `uri` / `url`）——抽错
   会得到空 `uri`，然后 `publish` 返 `code:4009 第1个封面数据异常，请重新上传`。
   第②步的封面在 `data.cover_url`，`data.image_url` 是 obj 原图，别拿错。
2. `pgc_feed_covers` 的 `thumb_width/height` 取第①步返回的 `image_width/height`
   （我们本地产物正好 3840×2160），别硬编码 —— 服务端会拿它算裁剪。
3. 图片以 **base64 注入页面再组 FormData**（页面是最干净的签名环境），
   所有字符串必须 `json.dumps` 转义 —— 裸中文进 `Runtime.evaluate` 会坏
   （症状像 argus，其实是编码坏了）。

校验：`verify_draft_cover()` 回读 `draft_list` 的 `cover_image.image_uri`，
与第①步 uri 一致才算真落库（`code:0` 不算数 —— 头条的老毛病）。
端到端实测 `tmp/tt_cover_e2e.py` RC=0：上传 → 存稿 → 编辑器封面区出图。

### 还没 API 化的三处（2026-10-17 现状，别对外说「全 API」）

| 操作 | 现状 | 说明 |
|---|---|---|
| **群发（真正发布）** | UI（`publish_one` 点发布按钮） | 只抓过 `save=0` 存草稿接口，没抓过发布接口；头条发布可能还有额外验证 |
| **给已有草稿补/换封面** | UI（`cover` 子命令：草稿箱→编辑→图库上传） | 底层 `save_draft_api` 带 `pgc_id` 就是更新，理论上可 API 化，但没封装成命令 |
| **探针稿清理覆盖面** | ✅ 已补 `cleanup_titles()`（精确标题删） | 2026-10-17 实测：`cleanup_probe_draft` 只删 `__probe*` 前缀，`tt_title.py` 的中文测试标题**漏删** 7 条；探针脚本收尾应改用 `cleanup_titles(cdp, [测试标题…])`。⚠ 同标题下可能躺着正稿（3 条无封面测试稿与有封面正稿同标题）——那种情况**按 gid 删**，别用标题匹配；也别用 `dedup`（保留最新一条，会删掉正稿） |

⇒ 准确说法：**头条的「读草稿 / 建稿（含封面）/ 删稿」已纯 API 化；
「群发」和「给已有草稿换封面」还走 UI；所有写操作都需要一个活着的
编辑器页面供 `tt-anti-token`**（不点按钮，但浏览器要开着）。

### 🚨 标题截断残渣：「——一」是怎么存进草稿箱的（2026-10-17）

`make_toutiao_title` 旧逻辑 `cand[:TITLE_MAX]` 硬切 30 字符，实测把
「把 AI 建站工具生成的网站，转成 WordPress——一键迁移指南」切成
「…转成 WordPress——一」（切在破折号后一个字），服务端原样存下。
同 case 后来补了短标题 override（「WPConvert：AI 建站转 WP」）重建一稿，
旧残渣稿没删 ⇒ 草稿箱同 case 两条，用户截图才发现。

已修 `_truncate_title()`（`scripts/toutiao_publish.py`）：截断面落在
孤立标点 ⇒ 清掉；落在连接号后一两个字 ⇒ 连接号带残字一起清（干净收在
前半句）。仅当原标题真超 limit 才触发，不误伤完整的「A——B」短标题。
回归锁在 `scripts/test_toutiao_publish.py::TestTruncateTitle`。

⇒ **建稿路径上任何 `[:N]` 硬切都要当疑点查**：截断面落在破折号/英文
单词中间都会留残渣，服务端不会帮你收拾。

### 存量草稿「按最新逻辑刷新」：`refresh` 子命令（2026-10-17 实测 17 篇）

标题逻辑/override 变过之后，草稿箱里的存量稿不会自动跟进。刷新流程
（已固化成 `toutiao_publish.py refresh --all [--dry]`）：

```
扫描 draft_list → find_case_for_title（**只按冒号切、不按空格切**，
  「Pet Plus Us：…」按空格切会碎成 "pet" 匹配不上）→ 本地
  make_toutiao_title vs 线上标题 → 不一致的进清单
  （线上与某篇已发表「互相包含」= 已发表残留，不刷新，交给 scan_pub_dupes 删）
→ 删旧稿（delete_draft_api）→ 建新稿（publish_api_one rebuild=True）→ 统一回读
```

🚨 **三个踩过的坑**：
1. **`publish_api_one` 默认「meta.json 不在才 build」**——存量刷新的 case
   meta 都在，不传 `rebuild=True` 建出来的还是 meta 里的**旧标题**
   （API 照样返「保存成功」，极具误导性；nutria 第一篇就这么白刷了一次）。
2. **必须有头 Chrome**（`CHROME_HEADED=1`）：`grab_anti_token` 靠编辑器
   自己的自动保存触发，无头抓不到 token。且全程同一个**编辑器页**
   （`open_api_session`，首页 tab 被 argus 挡）。
3. 批量时 `verify=False` + 最后统一核对一次——逐条 draft_list 回读会把
   后续 publish 顶进风控。删旧失败就跳过不建新（避免双稿）。

### 🚨 四段对照实验：推翻「需逆向 token」的旧结论

`tmp/tt_fresh_replay.py`。上午一度判定「token 带时效 ⇒ 纯 API 化不可行」，
**错在把「token 过期」和「token 缺失」的 7050 当成一回事** —— 两者报文完全
一样，光看状态码根本分不开。同会话对照：

| 段 | 做法 | 结果 |
|---|---|---|
| A | hook 抓真实请求头 | 只有 3 个：`Accept` / `Content-Type` / **`tt-anti-token`** |
| B | 同页即时重放「原 body + 抓到的头」 | `code:0` ✅ |
| C | 同一 token **换 body（换标题）** | `code:0` ✅ |
| D | 我方 `_publish_draft_body()` + 抓到的头 | `code:0` ✅（3 个 pgc_id 各不相同） |

⇒ 同时证伪三件事：① 无需逆向生成算法；② token **不绑定 body**；
③ 我方 20 字段 body 形态本来就对。**唯一约束是 token 要新鲜。**

### `tt-anti-token` 是什么 / 从哪来

页面运行时生成的**写操作反滥用凭证**，75~78 字符，形如 `HOikR1wqgY-6a2d77a42…`
（前缀每次不同）。不在 HTML、不在 `window`、**不在 cookie**（用户提供的完整
cookie 清单里只有 `sessionid` / `passport_csrf_token` / `csrf_session_id` /
`odin_tt` / `ttwid` / `ttcid` —— 全是**会话身份**，不含反滥用凭证）。

**取法（不逆向）**：开编辑器页挂 hook → 触发一次页面自己的自动保存（填标题）
→ token 被记下。代价是留一条探针草稿，`cleanup_probe_draft()` 删掉。
⚠ 无头 Chrome 不触发自动保存（有头才行）。

**🚨 别和 msToken 混**：那是两套东西 ——
`mssdk.bytedance.com/web/r/token` 签发的是 **msToken**（挂 `_msToken` cookie，
服务内容**读取**接口的 argus/a_bogus 风控链）；`tt-anti-token` 服务的是
mp 站长后台的**写操作**。cookie 里两者都没有。
这层 argus/msToken 校验「环境像不像真浏览器」，**无法去浏览器化**
⇒ 头条的「已发表列表」只能 `Page.navigate` 导航式读（同源 fetch 被挡成 HTML）。

### 踩坑（都实测过）

- **hook 必记 send 的 `arguments[0]`**：只记 URL 会导致 body 猜三次全错。
- **`Page.navigate` 会重建 document**：先挂的 hook 被冲掉（抓到 0 请求）。
- **草稿箱 UI 懒加载**：URL 分页参数无效，得滚到「没有更多数据了」。
- **服务端宽容 ⇒ 200/code:0 不等于事办成**：delete 假 gid 也 success；
  publish 缺 token 返 7050；JSON body 也是 200。
- **编辑器 chunk**：`mp/graphic/index.*.js`（189KB，接口清单都在里面）、
  `mp/graphic/vmok-remote-entry.*.js`。

---

## 二、B站（全链路 API，`scripts/bili_draft_api.py`）

`bilibili_publish.py` **默认就是 API 路线**：`publish <id> --replace` 秒级完成。

接口：`draft/view` / `draft/list` / **`draft/add`（带 `article_id` 即更新）** /
`draft/delete`。**没有单独的 update 接口。**

### ⚠⚠ 请求形状只能靠 hook 抓，读压缩代码猜不出来

五个必错项，全部错才不 -400：

| 项 | 错 | 对 |
|---|---|---|
| Content-Type | form-urlencoded | **`application/json`** |
| csrf | 放 body | **放 URL query** |
| body | `csrf=..&arg=..` | **`{"arg":{...}}`** |
| 段落 node | `{type,text}` | **`{node_type:1,word:{words,font_size:17,color,dark_color,style,font_level}}`** |
| 签名 | 无 | URL 带 **WBI `w_rid`(md5) + `wts`** |

- 重抓用 `scripts/probe_bili_save_capture.py`。
- **⚠ axios 走 XHR 不走 fetch**，只hook `window.fetch` 抓不到。

### WBI 签名

- mixin_key 来自**免签**的 `/x/web-interface/nav`。
- `!'()*` **只在算签名时剔除**，发出去仍是原值（抓包确认）。
- `private_pub` 默认 **2**（不是 0）。

### HTML拆段落

**用整块匹配**（开标签+内容+同名列标签），**别用「交替分支+兜底分支」一个大正则**
—— 最左匹配会让兜底分支跨过 `<p>` 抢先命中，把真 `<p>`/`<blockquote>` 整段吞掉。

### 其他

- **API 路线不保留行内样式**（粗体/颜色/居中），要样式用 `--ui`。
- 封面从旧草稿 `origin_image_urls` 原样带回；传新封面仍需 UI。
- **判有没有封面看 `image_urls`，绝不看 `banner_url`**（草稿接口恒为 `""`）。
- ⚠ **`clean_body` 必须压掉标签间换行**：`re.sub(r">[ \t\r\n]+<", "><", body)`。
  标签间的 `\n` 被 ProseMirror 当成**额外空段落**（58px vs 正常 29px），
  全文凭空多出 20+ 个空段。

### ⚠⚠ 任何「点按钮」型操作都必须回读校验

旧版点完「保存为草稿」就 `return True`，实测远端正文一字未变而脚本报成功
—— 整轮 37 条全「成功」但用户看到的还是旧排版。
B站已有 `_verify_saved()`（回读 mtime + summary 前缀）。

### `cover --force` 曾经是假的

force 只影响 `cover_all` 的跳过判断，`cover_one` 内部永远「已存在→跳过」。
现已把 force 透传进去，已有封面先 `_drop_existing_cover()` 再上传（删不掉不阻断）。

### UI 路线的其余坑

见 `.workbuddy/memory/2026-10-01.md`：tab 累积 20 个必超时；编辑器 iframe 里
`_iframe_js` 的形状坑（别套两层 IIFE、匿名函数要写成表达式、调用点写
`return <fn>(args)`）；封面上传用 `DataTransfer` 页面内塞 file input，且
`change` 是同步的所以 `files[0].size` 必须派发前取；路由是 `/opus/management/drafts`。

---

## 三、小红书（草稿在本地 IndexedDB，无服务端 API）

`scripts/xhs_draft_idb.py` 是封装层：`list_drafts` / `get_draft` / `put_draft` /
`delete_draft`（全部实测通过）。

DB `draft-database-v1`，stores `article-draft` / `image-draft` / `video-draft` /
`audio-draft`。图文笔记在 `image-draft`，主要字段 `content / draftId / uid / timeStamp`。

- ⚠ **这是「编辑器本地自动保存」，不是云端同步**：换浏览器/清缓存/换机器草稿
  就没了。**只能当临时草稿**，别当远端事实来源。
- `put_draft` 只写本地存储**不上传图片** —— 图片要先走
  `/api/media/v1/upload/creator/permit` + COS拿 `fileid` 再写进 `content.imgList`，
  否则草稿里是坏图。写完 UI 未必立刻刷新，重新导航一次即可。
- `xhs_publish.fetch_drafts()` 仍是 DOM 抓取（几十秒），可换 IDB 读（秒级），
  但**别改签名**（`publish_multi` 在用）。
- ⚠ **存草稿只能「离开编辑页」**（`_save_draft`）—— 图文发布页**没有「存草稿」按钮**，
  导航回首页即自动暂存，靠轮询草稿信号确认。
  流程：`publish --dry` 填满 → `_save_draft` → `mark_drafted`。
- ⚠ **`--dry` 不是「什么都不做」**：照常连浏览器、切 tab、传 5 张图、填正文、
  勾原创声明，只是不点「发布」。
- ⚠ **补发前必须先 `build`**：各平台文案独立生成，别假设另一平台已同步
  （quran-unlock 小红书标题还写着已被推翻的「$676」）。
- **`mark_drafted` 只增不减**，对账要**反向清理台账**：purge 删掉的记录不会被自动
  移除，重跑就重复标记。正解 = 台账 ∩ 远端。⚠ 换封面只能删了重发。

### 已发布列表抓取（2026-10-17 实证，补上「无服务端 API」的读一侧）

创作平台**有**笔记管理页（已发布/审核中/未通过），只是没开放接口，走 DOM：

- 入口 `https://creator.xiaohongshu.com/creator/notemanage?roleType=creator`
  （会 302 到 `/new/note-manager?roleType=creator`；`/publish/manage`、
  `/new/note-manage` 直接导航都会 404「页面不见了」，别用）
- 标题选择器 `span.note-card__title`，卡片 `div.note-card`
- ⚠ **列表在自己的滚动容器里**（`div.content`，scrollHeight>clientHeight），
  `window.scrollTo` 无效——必须滚容器；连滚两轮无新增才算加载完
  （首屏常不足 10 条，别用固定阈值判断结束）
- 「全部 N」= 图文+视频+长文+播客；N 与 `图文笔记(48)` 是两个数

### 草稿箱删除（purge）

`python scripts/xhs_publish.py purge --match "标题" --all`。
⚠ `_purge_one` **前提是抽屉已打开**（`.draft-item` 有渲染）——生产路径
`cmd_purge` 会先 `fetch_drafts()` 点开抽屉+图文 tab，**自建脚本直接调
`_purge_one` 会 `nobtn`**（2026-10-17 踩过：先 `fetch_drafts(sub)` 再删）。

### 覆盖层（方案 A：只救小红书）

`data/xhs_note_overrides.json` 只覆盖小红书，不污染四平台共用源。覆盖：
`title` / `body` / `tags` / `cards.{headline,what,money,why,playbook}`。

⚠ **卡片必须一起覆盖**，否则等于把违规文案重新印到图片上。
合规检查（`xhs_compliance`）必须覆盖标题、正文、卡片文字三处。

---

## 四、公众号（个人订阅号，只能 CDP）

官方 `draft/switch` **已废弃**，官方文档「适用范围」只列服务号，
个人订阅号通常返回 `48001 api unauthorized`。
⇒ **个人订阅号仍只能 CDP 操作后台**，别再找服务端 API。

### 读

- 草稿箱只读回读：`_draft_list(cdp, token)` → `app_msg_list`（**不是** `app_msg_info`），
  `type=77` 才是图文草稿；`update_time` 是 **Unix 秒**。
- ⚠ **草稿存的是保存那一刻的快照**，不会跟着本地 HTML 变。改了正文要
  `refresh --case <id> --cover` 灌回**已存在**的草稿（`publish` 是新建会多一条）。
  用户按新标题找不到旧草稿时先回读 —— 多半是「旧标题的旧草稿还在」。

### ⚠ `系统错误(320003)` 三步定位法

`320003` 是站内错误码，官方和社区都没有明确解释。**错误页上没有 `#title`**，
所以脚本后续的 `NO_EL` / `NO_API` / `NO_FILE_INPUT` / `NO_COVER_BTN`
**全是错误页造成的后果，不是选择器本身失效**。

定位三步：
1. 独立 tab 打开列表页（确认不是整个会话挂了）。
2. 打开另一条编辑器（对照：这条能开就是单条问题）。
3. 等 30 秒重试问题条目。

实测存在**两个叠加原因**：
1. 短时间内高频打开编辑器触发服务端限流（暂时性，等一会就好）。
2. **草稿记录级损坏**（永久性）—— 2026-10-01 实测共**6 条**：
   | case | appmsgid |
   |---|---|
   | `magicslides-app` | `100000125` |
   | `stan` | `100000130` |
   | `1lookup` | `100000134` |
   | `gojiberryai` | `100000138` |
   | `sierra` | `100000142` |
   | `genius-ai` | `100000146` |

   特征：**编辑页稳定返回 320003**（不是偶发），且**草稿列表接口里也没有它们**
   （`count` 拉到 100 仍只有 31 条，少的就是这6 个篇号）。
   ⇒ 只能删除重建，refresh 刷不动。

#### ⚠ 怎么区分「限流」和「记录损坏」——交替测试

**错误页文案一模一样，无法从页面本身判断**。唯一可靠办法是**交替试开**：

```python
for cid, aid in [('MISSING', 100000146), ('OK', 100000150),
                 ('MISSING', 100000134), ('OK', 100000154)]:
    open_editor(aid); check_ready()
```

- MISSING 的**稳定失败** + 正常条目**稳定成功** ⇒ 记录损坏（等多久都没用）。
- 两者**都失败** ⇒ 限流（等几分钟，整轮重跑）。

⚠ **别用「等 30 秒重试」当唯一判据** —— 实测那 6 条等 30 秒、5 分钟、
第二天回来都是同一个错误，而同期正常条目一直能开。

`scripts/wechat_verify_refresh.py --probe-missing` 已封装这个交替测试。

#### 损坏草稿黑名单（`data/wechat_broken_drafts.json`）

交替测试确认是记录级损坏后，把case id 写进这个文件。之后：

- `refresh` **自动跳过**黑名单里的条目，不再白等两轮打开 + 退避
  （实测单条能吃掉 3-4 分钟，6 条就是 20 分钟，批量看起来就是「卡住不动」）。
- `--include-broken` 可强制重试（确认服务端已恢复时用）。
- `--exclude-case id1,id2` 临时排除本次不想碰的（不改文件）。

```bash
python -X utf8 scripts/wechat_publish.py refresh --dry   # 看黑名单跳过效果
```

⚠ **判断「批量卡住了」之前先看黑名单输出** —— 跳过行会明确列出跳了哪几条，
比盯进度条可靠。

### ⚠ 中断后从断点续跑：`--skip-refreshed-after`

批量被中断（手动停、Chrome 被关、限流）后重跑会**从头再刷一遍**，
每条 1-2 分钟，白等几十分钟。用记录里的 `refreshed_at` 跳过已刷的：

```bash
python -X utf8 scripts/wechat_publish.py refresh \
    --skip-refreshed-after "2026-10-01 20:04"
```

⚠ `refreshed_at` 只在**保存成功**时写，所以它天然只标记成功条目——
中断点之前的不会漏掉。

### ⚠ `refresh` 的假成功与加固

- **`保存: OK (NO_EL)` 是假成功**，按钮根本没点到。只有
  **`保存: OK (CLICKED:保存为草稿)`** 才表示点击命中 —— 即使命中，仍需远端回读。
- `_open_draft_editor(strict=True)`：编辑器没就绪就抛 `DraftEditorError`。
  不 strict 时错误页会被当成正常编辑器继续走，每条都假成功又白等 timeout。
- 编辑器打不开的条目**单列到 `editor_errors`**，不混进 `fail_n`
  —— 这是「服务端问题」，混进去会让人一直查选择器。
- 连续失败退避（20~90s）+ 单条重试一次：限流是暂时的、记录损坏是永久的，
  **重试一次就能把这两类分开**。条与条之间留 6s。

### ⚠ `保存: OK` 不等于正文落库 —— 必须另起进程回读

`保存: OK` 的判据只是「保存后 URL 出现了 `appmsgid=`」，
那是**编辑器自己跳转**的信号。历史教训：整轮 37 条全报「成功」，
用户看到的还是旧排版。

⇒ 刷完必须**另起一个进程**按篇号回读（`scripts/wechat_verify_refresh.py`）。
放同一个进程里不行 —— 那个进程的 CDP 连接与页面状态都是热的，
无法代表「服务端现在到底是什么」。

⚠⚠ **`--fresh-seconds` 别设太紧**（2026-10-01 踩过）。批量跑 40 分钟刷 37 条时，
最后一条距「批次开始」已 40 分钟，用「距今 1800 秒」会把**刚刷好的**判成 STALE，
白折腾一轮。正确口径是 `--since <批次开始时间戳>`：

```bash
python -X utf8 scripts/wechat_verify_refresh.py --since 1790856240
```

判据优先级：`update_time >= 批次起点` > 标题一致 > 编辑器可打开。

### ⚠ 别拿 warn 当失败结论

`refresh --cover` 常报「封面处理异常: timed out」+「CDP 页面连接断开，已重连」，
但最终 `OK (CLICKED:保存为草稿)` 且封面确实换了 —— 要回读 img src 核实。

---

## 五、对账（`scripts/wechat_reconcile.py`）

**对账先做，别先动手。**

- ⚠⚠ **它按标题反查，标题变了就全盘失配**（2026-10-01 实测踩坑）：
  本地 rebuild 后 h1 变成正式长标题、远端还是旧版短标题 → 匹配不上 →
  `remote_len=0` → 状态 `?` → 被当成「不需要改」。
  **`0/1350` 恰恰是「没匹配上远端」的信号，不是「远端是空的」。**
  当时据此报了「32 条全部不需要改」，事后证明 **32/32 条正文全是旧版**。
  ⇒ **对账的键必须是「篇号」**（`拆解海外 · 第 N 篇`，唯一稳定标识），不要用标题。
- ⚠ 状态 `?` / `remote_len=0` **一律当「未知」处理**，不能进「无需改」结论。
- ⚠ **本地 `out/` 不被 git 追踪**，且 `make_article --all` 会一次性重生成全部 html
  → **文件 mtime 永远相同，不能用来判断内容变没变**，只能逐条比纯文本。
- 比对要复用 `plain(html, drop_title=_h1(html))` 剥掉独立 `<h1>` 与题头行，
  否则每条都报「不一致」假阳性。
- `--fetch` 逐条打开编辑器抓远端正文（32 条≈11 分钟，**别接 `| tail`**）。
- 用 `out/xhs/*/note.json` 标题反查（`_card_to_cid`）分 5 类：远端有/台账无、
  台账有/远端无、两边都有、远端重复、孤儿草稿。
  2026-10-01 靠这个发现「台账多 2 条」其实是 purge 假记录，避免了重复发布。

### 「已发布还躺在草稿箱」扫描（`scripts/scan_pub_dupes.py`）

四平台都是「从草稿箱发布后草稿不消失」⇒ 群发后草稿箱里留下一批已发表文章。
本脚本只读扫四平台，按**标题去空白精确相等**算交集，输出待删清单。
2026-10-17 首跑：6 条命中（头条 Coral/UpLinked、B站 Viktor/UpLinked B.V.、
小红书 Bustem/StartClaw、公众号 Visualizee.ai），全部删除+核对消失。

- ⚠ **公众号精确匹配会漏**：已发布是长口径标题、草稿是短口径（同一篇文章）。
  脚本对公众号附「产品名级」（首个冒号前）提示，只列不删，人工定。
- ⚠ **B站已发布列表用 `x/space/article`（空间公开数据、免签）**，
  比创作中心 upload-manage 稳（后者已改版重定向到首页抓不到）。
- 删用各平台既有命令：头条 `delete_draft_api` / B站 `delete_draft` /
  公众号 `delete --appmsgid` / 小红书 `purge --match "标题" --all`。
- 🚨 **删后必须回读核对**（头条对假 gid 也返 code:0；B站 delete 响应会超时
  但实际已删——2026-10-17 实测 344019 超时、核对已消失）。

---

## 六、多平台编排（`scripts/publish_multi.py`）

别用 `publish_both.py`（设计取舍见 `docs/MULTIPLATFORM.md`）。

- 状态机 `pending/building/draft_saved/published/failed`，
  **`building` 不算完成**（崩在这要能续跑）；写盘 tmp + `os.replace` 原子替换。
- 幂等判据 = 清单OR 平台状态文件，**首次必须先 `sync`**（否则 37 条被当全新重发）。
- `data/publish_manifest.json` **要入库**（B站草稿只存在远端，这是唯一事实来源）。
- 批量**必须串行**（并发抢同一个 CDP → 批量 10053/Timeout，单条约 70s）。
- `publish_both.py --dry` **不能透传**给小红书/头条（实测污染过bustem 草稿）。
- `case` 是**位置参数**：`run <id> --replace`，没有 `--case`。
- Windows 子进程要设 `PYTHONIOENCODING=utf-8`，否则乱码破坏 `parse_batch`。
- `verify` 只支持头条与 B站草稿箱回读；差异分 `missing`/`extra`/`unmatched_titles`。
- 状态分层：manifest 是本地状态，平台草稿箱才是远端事实。
  单平台脚本不自动回写manifest，要显式 `mark`/`sync`。

---

## 七、批处理通用守则

- 必须 `flush=True` 逐条打印 + 打印单条/累计耗时 + **一律不要接 `| tail -N`**
  （块缓冲，全程零输出，看着像卡死）。踩了 3 次。
- 长任务用受管后台任务，**不能靠 `nohup ... &`**（进程会被回收，实测 2 次）。
  `covers --all` 要 ~10 分钟，必然撞 590s 超时且掐在中途。
