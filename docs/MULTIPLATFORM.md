# 一次发布多平台

2026-09-29 定稿。参考 [Wechatsync](https://github.com/wechatsync/Wechatsync)
与[这篇 CSDN 文章](https://blog.csdn.net/fzil001/article/details/160228873)，
但**只抄它们对的部分**。

## 参考了什么，拒绝了什么

| 来源 | 抄的 | 不抄的 | 原因 |
|---|---|---|---|
| Wechatsync | 用本机浏览器登录态、数据不过第三方服务器、统一核心 + 平台适配器、草稿优先、CLI 多入口 | Chrome MV3 扩展形态 | 本项目全是本地 Python 脚本，塞进扩展要多一层打包、权限申请与跨域审批，换不来任何东西 |
| CSDN 那篇 | 统一 `Article` 模型、`platforms/*` 适配器、配置决定启用平台、顺序调度记录结果 | 它的调度实现 | 结果只记在内存数组、`pending` 也算成功、无重试、无幂等、进程一挂全丢；平台失败会被部分成功掩盖 |

**最关键的一条差异**：CSDN 示例里「成功」= 没抛异常。本项目批量发 37 条时
真实踩到的是「本地记录 37 条、草稿箱只有 35 条，kibu / pieter-levels 去哪了
说不清」。所以本层的核心不是适配器，而是**落盘的状态机**。

## 结构

```
scripts/publish_multi.py            统一入口（CLI）
scripts/multiplatform/
  article.py                        统一 Article 模型 + 标题优先级
  state.py                          发布清单：状态机 / 幂等 / 断点续传
  runner.py                         顺序调度 + 单条重试 + 失败不中断
  adapters/
    base.py                         Adapter 基类 + 注册表
    wechat.py  xiaohongshu.py
    toutiao.py  bilibili.py         四个平台适配器
```

适配器是**薄壳**：所有 CDP 选择器、封面上传、草稿箱点击仍在既有的
`wechat_publish.py` / `xhs_publish.py` / `toutiao_publish.py` /
`bilibili_publish.py` 里。那是几个月踩坑的沉淀，不能为了架构好看重写。

## 调度模型：按平台聚合，不按 case

`Runner.run()` 先把「每个平台各自待办哪些 case」算出来，再**按平台**批量
提交，不是「一条 case 依次跑完四个平台」（2026-09-30 改）。

原因：B站这类平台的批量必须在一个进程内串行跑完。逐条 spawn 子进程，
每条都要重起 CDP 连接、重开草稿箱 tab —— 37 条下来既慢（70s/条）又会把
页面 WebSocket 拖超时（实测跑到第 2 条就 `TimeoutError: timed out`）。

```python
# 支持批量的平台：一次提交，脚本内串行
Adapter.batch(cids) -> Result        # 谁支持看 supports_only
Adapter.parse_batch(out) -> (done, failed)   # 逐条判定成败
# 不支持的：退回逐条 run_one()
```

⚠ **批量不能只看退出码定成败。**「15 条挂 1 条」整体 rc≠0，但那 14 条
已经存好了。按 rc 一刀切会把成功的误标 `failed`，下次重发一遍 —— 对
B站这种「一条草稿要 1 分钟」的平台代价很高。所以 `Adapter.run(full=True)`
拿完整 stdout，交给 `parse_batch()` 逐条回写状态。

## 状态机

清单落盘在 `data/publish_manifest.json`，结构
`{cid: {platform: {state, at, title, attempt, error?}}}`。

| 状态 | 含义 | 默认跳过 | 备注 |
|---|---|---|---|
| `pending` | 还没发 | 否 | |
| `building` | 正在生成平台稿 | **否** | 崩在这状态要能重跑捡起来 |
| `draft_saved` | 远端草稿箱已确认有它 | 是 | |
| `published` | 已公开发布 | 是 | |
| `failed` | 上次失败 | 是 | 需 `--retry` 才重跑 |

写盘用「临时文件 + `os.replace`」原子替换。批量跑到第 20 条被 Ctrl-C，
前 19 条的状态不会因为半个 JSON 全丢。

**幂等判据是「清单 OR 平台状态文件」**，两者任一为已完成即跳过。平台状态
文件（`data/toutiao_published.json` 等）里有的、清单里没有的，会被**回写进
清单**——所以 `publish_multi.py sync` 必须在第一次 `run` 之前跑一次，
否则 37 条会被当成全新重发一遍。

## 各平台差异（踩过的坑，写在这里免得重犯）

| 平台 | 幂等来源 | 真发布 | 特殊约束 |
|---|---|---|---|
| 公众号 | `data/wechat_published.json` | ⚠️ 扫码前全自动、扫码后自动校验，但两种模式都要管理员扫码 | 个人订阅号无群发 API；build 是 `make_article` + `wechat build` 两段；「发表」用 `wechat_publish.py send` |
| 小红书 | `data/xhs_drafts.json` | ✅ `--yes` | **绝不能透传 `--dry`**：它的 `--dry` 会照常填表，只是不点发布（污染过 bustem 草稿） |
| 今日头条 | `data/toutiao_drafts.json` + `_published.json` | ✅ `--yes` | 封面是硬要求，`cover.png` 缺了就当产物不完整 |
| B站专栏 | **只有清单** | ✅ `--yes`（`send-draft` 走编辑器 UI 点「发布」，2026-10-20） | 草稿只存在远端，本地无从判断；**每天限 1 篇**（本账号 lv0~lv3 档，lv4+ 才 5 篇），额度用完发布按钮变灰，脚本明确报失败 |
| 掘金 | `data/juejin_drafts.json` | ✅ `publish --draft-id/--case` | **唯一纯 REST 的平台**：不开浏览器、不点 DOM，cookie 鉴权；正文吃 **Markdown**（不是 HTML）；普通账号**只能带 1 个标签** |

## 为什么串行不并发

批量发版抢同一个 Chrome/CDP 会互相踩。实测并发时子进程批量
`ConnectionAbortedError: [WinError 10053]`、`TimeoutError`，甚至把头条
草稿箱 tab 挤掉。串行 + 单条重试已经够快（约 70 秒/条）。

并发的正确解法是「一个平台一个 Chrome 实例」，那是以后的事。

## 掘金专章（2026-10-25 新增）

掘金是五家里**唯一不需要浏览器**的：`juejin_publish.py` 全程 REST，
连 CDP 都不建立（只有首次 `login` 用一次 CDP 抓 cookie）。

### 接口（全部实测，非照抄网文）

| 用途 | 方法 | 路径 | 备注 |
|---|---|---|---|
| 验登录 | GET | `/user_api/v1/user/get` | 未登录时 `err_no:0` 但 `data=null` |
| 分类列表 | POST | `/tag_api/v1/query_category_list` | **免登录** |
| 标签列表 | POST | `/tag_api/v1/query_tag_list` | **免登录**，须带 `category_id` |
| 建草稿 | POST | `/content_api/v1/article_draft/create` | `edit_type:10` = Markdown |
| 更新草稿 | POST | `/content_api/v1/article_draft/update` | |
| 草稿列表 | POST | `/content_api/v1/article_draft/query_list` | |
| 删草稿 | POST | `/content_api/v1/article_draft/delete` | |
| **发布** | POST | **`/content_api/v1/article/publish`** | 带 `draft_id` |

⚠⚠ **网传的 `article_draft/publish` 不存在**（实测 `err_no:2 请求路由不存在`）。
多篇多平台教程都写的是这个路径，照抄会得到 `err_no:2` —— 而这个错误码
和「参数不对」长得一样，很容易误判成自己参数写错，一查就是半天。

判定方法：无登录态时打这些路由，返回 `err_no:2` = 路由不存在，
返回 `403 must login` = 路由真的存在。这样不用登录就能画出接口地图。

### 三个坑

1. **探测写接口别用空体。** 有篇实测用 `{}` 试探 `create`，结果 `err_no:0`
   —— 真的建出一份空草稿，还得靠 delete 擦屁股。**登录之后，探测只能用只读接口**
   （`whoami` / `list` / `categories` / `tags`）。
2. **cookie 是 HttpOnly，读 `document.cookie` 拿到的是空的。**
   必须用 CDP 的 `Network.getAllCookies`。`login` 里已经这么做了。
3. **普通账号只能带 1 个标签。** 多传会被截断。脚本检测到 >1 会取第一个并告警。

### 正文是 Markdown，不是 HTML

`mark_content` + `edit_type:10`。所以掘金的稿**不需要 HTML 母版** ——
`out/articles/<id>.md` 直接喂，独立稿给 `--file xxx.md` 即可。
这跟公众号/头条/B站三家都不一样（那三家都要 HTML），适配器的
`requires` 因此声明的是 `.md`。

⚠ 统一 `Article` 模型的 `ok` 只认 `.html`，一篇只有 .md 的 case 会被
runner 判「缺母版」直接 failed。掘金适配器的 `build()` 会现补 `.md`
（`make_article --format md`），并且**只补 md、不碰 html** 产物。

### 用法

```bash
python scripts/juejin_publish.py login                # 首次，轮询到登录成功为止
python scripts/juejin_publish.py whoami
python scripts/juejin_publish.py draft --file out/articles/<id>.md --tags GitHub
python scripts/juejin_publish.py draft --case <id>    # 案例稿
python scripts/juejin_publish.py list
python scripts/juejin_publish.py publish --case <id>  # 真发布
```

### 不在电脑前怎么登录（2026-10-25）

`login` 要开本机 Chrome 抓 cookie，人不在机器旁边就走不通。补一条不依赖
浏览器的通道：

```bash
python scripts/juejin_publish.py set-cookie "sessionid=xxx; sid_tt=yyy; ..."
```

接受三种输入：整条 `Cookie:` 头（可多行、可带前缀）、`a=b; c=d` 片段、
CDP `Network.getAllCookies` 导出的 JSON 数组。写盘后立刻 `whoami` 验真，
不是登录态会直接报错（避免拿着游客 cookie 往下走）。

⚠ 手机端**拿不到**这个 cookie：掘金登录票据是 HttpOnly，`document.cookie`
读不到，手机浏览器也没有 devtools。要拿到只能靠抓包 App（Android: HTTP
Canary，iOS: Stream）抓一条 `juejin.cn` 的请求，复制它的 `Cookie` 头。
⛔ 短信验证码登录接口（`passport/web/send_sms_code`）实测返回
`error_code:16 该应用无权限` —— 那是掘金内部 passport SDK 的口子，
第三方脚本调不通，别在这条路上耗时间。

cookie 三通道：环境变量 `JUEJIN_COOKIE` > `data/juejin_cookie.json`。
⚠ cookie 等同密码，`data/juejin_cookie.json` **已加进 .gitignore**
（不能靠 `data/` 通配——那里头 `cases.json`、`bili_drafts.json`、
`publish_manifest.json` 都是要跟踪的业务数据）。

## 用法

```bash
# 首次：把既有记录灌进清单（必须先于第一次 run）
python scripts/publish_multi.py sync

# 看清单（不连浏览器）
python scripts/publish_multi.py queue
python scripts/publish_multi.py queue --platforms bilibili

# 执行
python scripts/publish_multi.py run --case prosp
python scripts/publish_multi.py run --all
python scripts/publish_multi.py run --all --dry      # 只打印子命令
python scripts/publish_multi.py run --all --yes      # 真公开发布

# 状态 / 重试 / 重置
python scripts/publish_multi.py status
python scripts/publish_multi.py retry                # 重试全部 failed
python scripts/publish_multi.py retry --case kibu
python scripts/publish_multi.py reset --case kibu    # 强制重发

# 只跑某些平台（--platforms 每个子命令都能带）
python scripts/publish_multi.py run --all --platforms bilibili
python scripts/publish_multi.py status --platforms bilibili,toutiao
```

### 断点续传（批量中途断连后补跑）

B站批量跑到一半机器休眠 / CDP 断连是常态。脚本层支持只跑指定 id：

```bash
# 照着上轮日志 [23/37] 之后的编号补跑
python scripts/bilibili_publish.py publish --replace --retries 2 \
  --only promptmonitor-io,prosp,rezi,search1api,shipfast
```

`--retries N` 是**单条**重试（默认 1），单条异常不中断整轮，结束时打印
`失败 N 条：...`。`--keep-tabs` 保留浏览器 tab 供人工核对（默认每条跑完
清掉，否则废 tab 累积会拖垮 WebSocket）。

# 远端回读校验（只读，不点任何按钮）
python scripts/publish_multi.py verify --platform bilibili
python scripts/publish_multi.py verify --platform toutiao

# 人工核对远端后直接改状态（如确认已发布）
python scripts/publish_multi.py mark bilibili --case kibu --state published
```

## 远端校验（`verify`）

`state.py` 记的 `draft_saved` 只是「子进程退出码 0」，而这轮批量发版已出现过
两次假成功：头条封面上传返回 ok 但 `img.src` 没变；本地记录 37 条而草稿箱
只有 35 条。所以 `verify` 会**只读**地拉草稿箱标题列表跟清单对账，分三类报：

- `missing`：清单说已发、远端草稿箱找不到 → 多半是**已发布后从草稿箱消失**
  （实测 `kibu` / `pieter-levels` 就是这样），人工去作品管理核对后用
  `mark ... --state published` 改回来；
- `extra`：远端有、清单没记 → 上次崩在写盘前，跑 `sync` 补；
- `unmatched_titles`：远端标题匹配不上任何 case → 人工看。

匹配用多级候选键（id / 大写变体 / 连字符前缀 / 平台精修标题 / 案例名），
并对大小写与空白做归一 —— 不归一的话 `Coral AI` 匹配不上 `coral`，差异会被
误报成「远端少了这条」。

公众号与小红书的草稿箱读取还没做（`verify` 会明确说不支持），目前只有
头条与 B站。

## 封面文案口径（2026-10-18 重做）

四平台（头条 / 公众号 / B站 / 小红书）封面共用一套文案，唯一入口是
`wechat_publish.cover_desc(c)`，版式固定两行：**项目名 + 一句话介绍**。

取值优先级：`cover_kv.desc`（完全手控，不再二次加工）> `cover_desc`
（数据层手写）> `one_liner` > `what_it_does` 首句 > **空串（整行不渲染）**。
「没简要介绍就不显示这一行」是用户明确要求，所以宁可空着，也不印残句。

**规则一：封面不放会过期的数字。** 金额、MRR/ARR、订阅数、用户数、毛利、
留存、倍数都是抓取当天的快照，印上去就是印一个会变的数。

**规则二：指标按「子句」整句丢弃，不做词语级剔除。** 旧实现用
`_MONEY_TOKEN.sub()` 只剔金额本身，把单位和区间留在原地，实测撕出 19 条
废句（255 条里）：

```
…并定时发布：–199/月，万、2.7 万用户。     ← 金额剔了，区间和「万」留下
…开发者服务，万、145 个订阅。
```

现在是先按 `：，；。` 切子句，**整句只承载指标就整句不要**，剩下的自然句
拼回去；拼不出像样的一句话就继续往下试候选，全都不行就返回空串。
另外剥掉句首重复的项目名（封面第一行已是大字号名，实测 38 条在第二行
又念了一遍）。

**规则三：数据层不留裸 Markdown。** 渲染面全部是 `esc()` 直出，不解析
Markdown，`**加粗**` 只会显示成两个星号。强调改用「」。
`test_toutiao_publish.TestNoRawMarkdown` 把这条钉成机制（含
`china_fit.note` / `solo_fit.delivery_note` / metric_note）。

自查：

```bash
cd scripts && python -m unittest test_toutiao_publish   # 含全库 255 条封面金额/残渣判据
```

## B站专章

B站是四个平台里坑最多的（没有状态文件、批量要在一个进程内跑、正文排版
有自己的规则），单开一章。

### 封面（2026-09-29 跑通）

B站专栏草稿**可以带自定义封面**，草稿阶段就能设，不必等发布。上传链路是
手工点出来的，脚本照抄：

```
草稿接口拿 article_id
  → /platform/upload/text/new-edit?aid=<id>，等 /york/read-editor iframe
  → 「发布设置」区「自定义封面」是**开关**（input.vui_switch-input），默认关；
    打开后才渲染 .select-cover .upload-button「添加封面」
  → 点它动态创建 hidden input[type=file]（父 .select-method，accept .jpg/.png，
    要求 ≥600x336；本地头条封面 3840x2160 直接够用）
  → DataTransfer 页面内赋值 + dispatchEvent('change')
  → 裁剪弹窗「选择封面的截取位置」→ 确定（等 .selected-cover 落地才算成）
  → 「保存为草稿」
```

```bash
python scripts/bilibili_publish.py cover --case prosp   # 单条（幂等）
python scripts/bilibili_publish.py cover                # 批量，只补没封面的
python scripts/bilibili_publish.py cover --force        # 连已有封面的也重设
python scripts/bilibili_publish.py cover-status         # 回读远端对账
```

四个坑，都写进代码注释了：

1. **远端判据是 `image_urls` / `origin_image_urls`，不是 `banner_url`。**
   草稿接口的 `banner_url` **恒为 `""`**，哪怕封面已设好也一样。拿它判
   「有没有封面」会永远误判成没有（`cover-status` 一开始就报 0/37，
   改成读 `image_urls` 后正确识别出 2 条）。
2. **`Page.fileChooserOpened` 在这个同域 iframe 上收不到。** 点「添加封面」
   后 hidden input 确实出现了，但事件 drain 12 秒一个都没有。改用
   `DataTransfer` 页面内赋值，稳定。
3. **`change` 事件是同步的。** Vue 的处理函数在 `dispatchEvent` 返回前就把
   input 清空了，`files[0].size` 必须在派发**之前**取。
4. **已有封面时按钮变了。** 没封面是「添加封面」`.upload-button`，
   已有封面是「重新上传」`.selected-action` 里的第二个按钮 ——
   探测只认前者会误报「上传按钮点不到」。

跑批量前先 `_close_stale_bili_tabs()`：探查时堆积的 20 个 B站 tab 会把
页面 WebSocket 拖到 `TimeoutError`（实测 22 个 page 时必超时）。

### 正文排版（2026-09-30 定位）

用户反馈过两轮，实际是**五个独立问题**，前两个才是「样式丢失、段落分太开」
的主因：

| 症状 | 真因 | 修法 |
|---|---|---|
| 整篇被引用块包住、样式全丢 | 清空正文用了 `execCommand`，ProseMirror 内部 state 不同步，旧内容自己回来，`insertHTML` 静默无效，正文全灌进遗留的 `<blockquote class="eva3-blockquote">` | 清空改走 CDP `Input.dispatchKeyEvent` 的 Ctrl+A → Delete |
| 段落之间多出空行 | **HTML 标签之间的换行符**被 Tiptap 当成额外空段落 | `clean_body` 末尾 `re.sub(r">[ \t\r\n]+<", "><", body)` |
| 小标题不像标题 | B站编辑器**没有小标题功能**（见下） | h2/h3 → `<p><strong>▶标题</strong></p>` |
| 标题与正文间距过大 | B站给 h2 的规则是 `margin-top:36px; margin-bottom:0` | 同上，改成普通段落后统一 24px |
| **表格形式的数据样式全丢** | 公众号的「表格」是 flex 伪装的，不是 `<table>`；剥 style + 剥裸 span 把标签和值粘成一团 | 连续一组转「整组一个 blockquote + 每行 `标签　值`」，见下 |

#### 五个坑的实测细节

1. **`execCommand` 不能用来清空 ProseMirror。** DOM 上看着删干净了，
   但编辑器内部 state 没同步 —— 下一步读 `children` 时旧内容**自己回来了**，
   随后 `insertHTML` 直接被丢弃（无报错）。`Input.dispatchKeyEvent` 走真实
   输入管线，删完是干净的 `<p class="is-empty is-editor-empty">`。
2. **标签间换行 = 空段落。** `<p>A</p>\n<p>B</p>` 灌进去是 A、空段、B。
   coral 实测：灌入后全文高 **5785px**、夹 20+ 个 58px 空段（正常段 29px）；
   压掉换行后同样内容 **2514px**、间距回到 B站统一的 24px。
   ⚠ 只压标签**之间**的空白，段内文字之间的空格是正文内容（`付费意愿 3/5`）。
3. **B站没有小标题功能。** 工具栏全 DOM 扫描找「标题/heading」零命中；
   CSS 里 `.ProseMirror :where(p,h1..h6,blockquote…){margin:0;font-size:inherit}`
   把 h1-h6 默认样式全清掉，唯一给 h2 的规则是
   `font-weight:500; margin-top:36px; font-size:18px`（不加粗 + 上 36px
   + 下 0）。`insertHTML` 灌进去的 `style` 会被 Tiptap 剥掉，灌完用 JS 加
   style、**存草稿重开后也会被洗掉**。所以只能走 `<p><strong>`（`strong`
   能存活）。带序号的 `## 一、xxx` 保留序号不加 `▶`。
4. **「点了保存」不等于「存上了」。** 旧版 `publish_one` 点完按钮就
   `return True`，实测远端正文一字未变而脚本报成功。现在有
   `_verify_saved()`：保存后回读草稿接口，要求 **mtime 已更新** 且
   **summary 前缀匹配**（`summary` 是 B站自己截的前 250 字，够当正文开头
   指纹；⚠ 别指望它全文对账，coral 本地 807 字远端只有 250）。
5. **「表格」是 flex 伪装的，B站也不支持真表格。** 见下一节。

#### 伪表格 → 键值块（第 5 个坑展开）

公众号模板里那些灰底圆角的「数据表格」（付费意愿 3/5、官方口径 $3,150 MRR
等）**不是 `<table>`** —— 37 篇里一篇真表格都没有，全是：

```html
<p style="display:flex;justify-content:space-between">
  <span style="color:#57606a">官方口径</span>
  <span style="font-weight:bold">$3,150 MRR</span>
</p>
```

两个 `<span>` 两端对齐伪装成两列。而 `clean_body` 剥 style、又剥裸 span，
结果就是 `<p>官方口径$3,150 MRR</p>` —— 标签和值直接粘连，全角空格都没留。
影响面：**37/37，共 500 行**（每篇 11–16 行）。

**为什么不能转成真 `<table>`**（实测）：工具栏扫描 title/aria-label/innerText
找「表格/table」零命中；`insertHTML` 灌 `<table><tr><td>A</td><td>B</td></tr>…`
会被**整体降级成一个 `<p>`**，所有单元格粘成一串（"官方口径$3,150 MRR客户
44 个…"），连换行都没有 —— **比不处理还糟**。

**采用方案**：连续一组伪表格行 → **一个** `<blockquote>`，每行
`<p>标签　值</p>`，值加粗；标签列按组内最长标签补**全角空格**对齐
（标签全是中文短词，实测只有 17 种、2–5 字，中文等宽假设很稳）。

三种候选的实测渲染（3 行样本）：

| 方案 | 高度 | 视觉分组 |
|---|---|---|
| 纯段落 + 全角空格 | 135px | 无 |
| **整组一个 blockquote** | **135px** | **22px 左缩进，整组成块** ✓ |
| 每行单独 blockquote | 159px | 有但更碎更高 |

单个 blockquote 的 `margin-top` 就是 24px（和 `p` 一样），**不会额外撑高**，
还白送 22px 缩进把整组框成「卡片」，最接近原来的灰底表格。
⚠ 别用「每行一个 blockquote」——连续多个 blockquote 各自带 24px 边距，
正是坑 1 里「每段分得太开」的成因。

⚠ **顺序敏感**：这一步必须在「去 style」和「剥裸 span」**之前**，否则
识别特征（style 里的 flex、span 分列）已经被抹掉了。测试
`test_order_matters_flex_before_strip_span` 钉住了这个顺序。

#### 修复前后对照（coral 实测）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 顶层 blockquote 高 | 2492px（吞掉整篇） | 58px（只包导语） |
| 顶层节点数 | 82 | 41 |
| 空段落 | 20+ 个 58px | 0 |
| 残留 h2/h3 | 8 个 | 0 |
| 块间距 | 36px(h2) / 24px 混杂 | 统一 24px |
| 全文高 | 5785px | 2514px |
| 数据行 | 标签值粘连 `官方口径$3,150 MRR` | 3 组键值 blockquote，标签列对齐 |

## 与旧编排的关系

`publish_both.py` / `publish_all.py` 仍在用（三平台 + 老参数），**新活一律
走 `publish_multi.py`**：多了 B站适配器、清单状态机、单条重试、断点续传。

## 配图（Agnes 生图支路）

上面「待办」里那条"正文内嵌图仍未处理"有个现成落点：
`scripts/agnes_cover.py` 读同一份 `cases.json` 出 AI 配图，落到 `out/agnes/`。

**它是支路，不是主链路的一环** —— `collect → review → store → make_article →
publish` 一行没改，产物也**不覆盖** `out/toutiao/<id>/cover.png`（那个要带标题字，
AI 图替不了）。详见 **[AI_IMAGE_BRANCH.md](AI_IMAGE_BRANCH.md)**。

```bash
python scripts/agnes_cover.py check      # 探连通性 + 确认不扣积分
python scripts/agnes_cover.py gen --case bustem
```

## 四平台「真发布」能力（2026-10-19 实测）

一次命令能建四平台的稿，但**「发出去」这一步四家能力不齐**，差别如下
（`--yes` 时生效）：

| 平台 | 建稿 | 真发布 | 单条耗时 | 卡在哪 |
|---|---|---|---|---|
| 今日头条 | API（`draft/add`） | ✅ API `save=1`+`entrance=main` | 20–27s | 要有头 Chrome 供 `tt-anti-token` |
| 小红书 | UI | ⚠️ `send-draft` 已能定位发布按钮（2026-10-20：closed shadow DOM 里的 `ce-btn bg-red`）；**真点一次仍需人拍板** | 1–2min | 创建走 UI；API 创建被 461 风控。草稿箱**不分页**（一次读全 43 条）。真发的实际拦路虎是**草稿图 CDN 签名过期全裂** |
| 公众号 | UI 存草稿 | ✅ UI「发表」（2026-10-19） | ~30s/条 + 等扫码 | **两种模式都要管理员/运营者扫码**（物理动作，脚本检测到二维码就停下等）；API 无权限（个人订阅号 `freepublish` 48001） |
| B站专栏 | API（`draft/add`） | ✅ UI「发布」（2026-10-20，`send-draft`） | ~1min | 草稿 47 条随便发，但**每天最多 1 篇**（本账号 lv0~lv3 档，lv4+ 才 5 篇），额度用完发布按钮直接变灰 |
| 掘金 | API（`article_draft/create`） | ✅ API `article/publish` | 秒级 | 无频次限制；卡点只在**登录态**（cookie 过期后全部接口 403，脚本明确报「未登录」而不假成功） |

**公众号的「发表」已自动化到扫码前最后一步 + 扫码后自动校验**（`wechat_publish.py send`，默认发布模式）：
开编辑器 → 设「群发通知」开关 → 确认两层 → check 全过后弹出「微信验证」二维码 ⇒
脚本停下来等，管理员/运营者扫码后流程继续，脚本自动判定结果、回读校验、落账。
**发布和群发两种模式都要扫码**（2026-10-19 实测：模式区别只在推不推送粉丝、
占不占每日 1 次群发额度；曾据「探针里发布链路没弹二维码」误判发布不扫码 ——
那是探针拦掉了 check 请求，流程根本没走到扫码那步）。`--notify` 走群发模式，
扫码框 5 分钟内没扫就记 `timeout` 而不谎报成功。实测链路与选择器见
`scripts/wechat_publish.py`「发表自动化」一节与 `scripts/probe_wechat_send.py`。

**小红书从草稿箱发布**（`xhs_publish.py send-draft`，2026-10-20）：
开草稿箱 → 挑一条（默认**存入最久**的）→ 点「编辑」进编辑器 → 图片体检 →
点发布 → 回读笔记管理校验 → 记账。已实测跑通的部分：草稿箱**不是分页**
（一次读全 43 条，B站 opus 才是分页）、点「编辑」能稳定打开编辑器。

**发布按钮在 closed shadow DOM 里**（2026-10-20 定位到）：

```html
<xhs-publish-btn is-publish="true" is-save-draft="true" submit-text="发布"
  save-text="暂存离开" submit-disabled="false" submit-loading="false">
  #shadow-root (closed)
    <button type="button" class="ce-btn white">暂存离开</button>
    <button type="button" class="ce-btn bg-red" aria-busy="false"
            aria-disabled="false">发布</button>
</xhs-publish-btn>
```

`element.shadowRoot` 是 **null**，所以 `document.querySelectorAll('button.ce-btn.bg-red')`
**永远返回空** —— 这就是之前三套兜底全扑空的真正原因（整页 1.48MB 的
`outerHTML` 里 `ce-btn` 出现 0 次，不是按钮不存在、不是页面改版）。
只有 CDP 协议层能穿透：`DOM.getDocument({pierce:true})`。

因此 `_click_publish` 的主策略：
1. **读能不能点**看宿主属性（light DOM 就能读）—— `is-publish` /
   `submit-disabled` / `submit-loading`，两个开关缺一个就**不敢点**；
2. **点**走 CDP —— pierce 找 nodeId → `DOM.getContentQuads` 取坐标 →
   真实鼠标事件。禁用时直接返回未点到，**不许**退到坐标兜底（旁边就是
   「暂存离开」，戳错了草稿就白改了）。

`--dry` 会把组件属性 + 穿透拿到的按钮坐标打出来，确认对得上再真点。
`publish_component_state()` / `find_shadow_button()` / `quad_center()`
都是纯函数，用 node + fake DOM 单测，见 `test_xhs_send_draft.py`。

两个踩过的坑记在这里：
· 判定编辑器开了没有，**必须**看标题输入框有没有值 —— 左侧导航常驻
  「发布笔记」，用「页面含『发布』」判定会 100% 假阳性（第一次探针被骗过）
· 草稿图片会**裂**：CDN 签名过期（实测那条 URL 直接 403），今天多篇草稿
  10/15 张裂。所以发布前强制体检，有裂图默认拦下 —— 发裂图笔记是事故

```bash
python scripts/xhs_publish.py send-draft --dry              # 开到编辑器并体检，不点发布
python scripts/xhs_publish.py send-draft --case CheckVibe   # 按标题关键词挑一条发
```

**B站「发布」按钮：之前整整误诊了一轮**（2026-10-20 晚翻案）。

之前这里写的是「headless 下恒 disabled（封面校验过不了）」，于是得出结论
「B站不能自动发布，只能人工点」。实测拆开一看：草稿封面早就设好了
（`.selected-cover` 在 DOM 里、`origin_image_urls` 有值），按钮照样灰 ——
编辑器顶部那行橙字写的是：

> **已达到当日投稿上限，只能保存草稿哦~**

真正拦路的是**每日投稿额度**（本账号 lv0~lv3 档 = 1 篇/天；lv4+ 5 篇/天，官方公告只写面上的 5），跟封面、跟 headless 都没
关系。当时是在同一天连发数篇的语境下顺手归因，把「今天额度用完了」记成了
「B站这条路走不通」。

现在 `bilibili_publish.py send-draft` 走真发布（`ui_publish_draft`）：
补封面（没有才补）→ 开编辑器 → `publish_btn_state` 读按钮 → `publish_verdict`
判能不能点 → 点发布 → 等「提交成功」→ **回读已发表列表查 aid** → 记账。

⚠ **判据顺序是有讲究的**：先看「投稿上限」提示再看 `disabled`。反过来看就会
得出「封面/正文没过校验」的错误结论，重蹈上面那次误诊。`publish_verdict`
是纯函数，这条顺序 `test_bilibili_publish.py` 有用例盯着。

```bash
python scripts/bilibili_publish.py send-draft --dry     # 只问「今天还能发吗」，不点
python scripts/bilibili_publish.py send-draft           # 发最久那条
python scripts/bilibili_publish.py send-draft --case genius-ai
```

额度用完时它**明确报失败**（退出码 1），绝不写清单、绝不假装成功
—— `--dry` 例外，那是问询，退出 0。

```bash
python scripts/publish_multi.py run --case <id>          # 只存草稿（默认）
python scripts/publish_multi.py run --case <id> --yes    # 能发的真发
python scripts/publish_multi.py run --near 3 --yes       # 接下来 3 条
python scripts/publish_multi.py run --case <id> --dry    # 只看计划
```

**发车前会自动跑一次本地账本体检**（毫秒级、不开浏览器）：账本文件坏 ⇒ 拦车；
重复/缺远端 ID这类历史欠账 ⇒ 只提示不拦。明知有欠账还要发就加
`--no-preflight`。详见 `docs/LEDGER_CONSISTENCY.md` 第五节。

`--yes` 撞上只能存草稿的平台时，状态记 `draft_saved` 并在结尾**点名**
（`⚠ 公众号、B站专栏 只到草稿`），不会安静降级成「都成功了」。

### B站编辑器：能开，卡点是「发布」没实现（2026-10-19 更正）

⚠ 本节先前写成「B站前端在 Chrome 154 下挂不起来」，**那是误判**，已推翻。
用户在同一个 9223 浏览器里手动打开一切正常（账号「世界逻辑」）；复测也正常：

- `member.bilibili.com/platform/upload-manager/opus`（图文管理）、
  `.../upload/text/new-edit?aid=<aid>`（专栏编辑器）都正常渲染，
  `#app` 约 48KB；编辑器内 `read-editor` iframe 就位，标题/正文可读，
  按钮齐全：`删除 / 重新上传 / 添加话题 / 选择文集 / 发布设置 / 保存为草稿 / 发布`。
- 真·空白只出现在把 `/york/read-editor?aid=x` 当**顶层页**打开时 ——
  那本来就是 iframe 专用路由，不是故障。当时把这个当成了全站问题。
- 现状：列表在 iframe `member.bilibili.com/opus/management` 里（`/opus`、
  `/drafts` 两个子路由）；「编辑」按钮点开**新标签**到 `new-edit?aid=`。
  合成 `.click()` 不生效（无用户手势，弹窗被拦），要点必须走真实鼠标事件
  或直接 `new_target(new-edit)`。

真正的卡点：`bilibili_publish.publish_one()` 只点到 `保存为草稿`，
**从没实现点「发布」**，所以 B站永远停在草稿。

发布真身（编辑器 bundle 里 `publishArticle()`）：

```
POST /x/dynamic/feed/create/opus?csrf=<bili_jct>
{draft_id_str, opus_req:{upload_id, opus:{opus_source:2, title, content,
  article:{category_id:15, list_id, originality, reproduced},
  pub_info:{editor_version}}}, scene:12,
 meta:{app_meta:{from:"create.creative.h5", mobi_app:"web"}}, option:{…}}
```

请求还带 `HttpSvcRiskEnvToken` / `HttpSvcRiskUserLog` 风控头（页面 SDK 现算），
**直接 curl 打不通**，要走页面点击。另实测两点：点「发布」会先自动存一次草稿；
有节流，连点会弹「您操作的太快啦~」并伴随 `保存失败，请尝试手动保存`。

⚠ 2026-10-19 探测时的副作用已清理：两次实验标签已关；期间只在草稿 `342372`
（Bustem）上触发过失败的自动保存，远端内容未改动。

### 公众号「发表」：入口确认，尚未自动化

草稿箱卡片只有「编辑 / 删除」，**「发表」在编辑页顶栏**（与「预览」并列）。
appmsgid 可从 `/cgi-bin/appmsg?action=list_ex&type=77` 直接读，编辑页 URL 是
`/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid=<id>`。

还差两步实测定稿：① 点「发表」后的确认弹窗结构（订阅号的「群发通知」开关
与每天 1 次的限制都在这个弹窗里）；② 发表完成后回读「发表记录」。
**这一步会真发出去、不可撤回**，首次验证要用指定的一篇稿子做。

## 待办

- 公众号 `send`（编辑页点发表 + 回读发表记录）——见上，需一次真实发布验证。
- B站 `create/opus` 发布接口（`upload_id` + wbi 签名），或等 B站修编辑器。
- 公众号与小红书的草稿箱读取（`verify` 目前只支持头条与 B站）。
- 图片上传：微信正文里的图在小红书/B站侧没处理（`article.py` 只给 HTML，
  平台适配器自己剥图）。B站**封面**已自动（见上），正文内嵌图仍未处理
  → AI 配图已能出（`agnes_cover.py`），但**接进正文 HTML 这步还没做**。
- B站草稿的**分区/话题/文集**没填（`category` 停在默认「生活」），人工终审。
- 头图 `kibu` / `pieter-levels` 不在草稿箱（已发布），改封面要走
  作品管理 → 修改，人工。
- 「一个平台一个 Chrome 实例」的并发方案。
