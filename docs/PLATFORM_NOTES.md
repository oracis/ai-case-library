# 四平台接口能力与踩坑档案

> 本文是**详细档案**。MEMORY.md 只留铁律与索引。
> 全部为 2026-09-30~ 10-01 实测结论，**别再靠猜**。

## 一、接口能力矩阵

| 平台 | 读 | 写 | 删 | 说明 |
|---|---|---|---|---|
| **B站** | ✅ 4 个接口 | ✅ `draft/add` 带 id 即更新 | ✅ `draft/delete` | 秒级完成，不开编辑器 |
| **头条** | ❌ | ❌ | ❌ | **只能 UI**，串行 ~68s/条 |
| **公众号** | ✅ `app_msg_list` | ⚠ 只有 `refresh` 灌已有草稿 | ⚠ 只有 UI | `action=del` 恒 `200009` |
| **小红书** | ⚠ 无服务端 API | ⚠ 无 | ⚠ 无 | **草稿在浏览器本地 IndexedDB** |

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
2. `genius-ai`（`appmsgid=100000146`）**记录级损坏**，等 30 秒后仍返回 320003
   → 只能删除重建，refresh 刷不动。

### ⚠ `refresh` 的假成功与加固

- **`保存: OK (NO_EL)` 是假成功**，按钮根本没点到。只有
  **`保存: OK (CLICKED:保存为草稿)`** 才表示点击命中 —— 即使命中，仍需远端回读。
- `_open_draft_editor(strict=True)`：编辑器没就绪就抛 `DraftEditorError`。
  不 strict 时错误页会被当成正常编辑器继续走，每条都假成功又白等 timeout。
- 编辑器打不开的条目**单列到 `editor_errors`**，不混进 `fail_n`
  —— 这是「服务端问题」，混进去会让人一直查选择器。
- 连续失败退避（20~90s）+ 单条重试一次：限流是暂时的、记录损坏是永久的，
  **重试一次就能把这两类分开**。条与条之间留 6s。

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
