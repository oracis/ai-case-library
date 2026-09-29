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
| 公众号 | `data/wechat_published.json` | ❌ 只能存草稿 | 个人订阅号无群发 API；build 是 `make_article` + `wechat build` 两段 |
| 小红书 | `data/xhs_drafts.json` | ✅ `--yes` | **绝不能透传 `--dry`**：它的 `--dry` 会照常填表，只是不点发布（污染过 bustem 草稿） |
| 今日头条 | `data/toutiao_drafts.json` + `_published.json` | ✅ `--yes` | 封面是硬要求，`cover.png` 缺了就当产物不完整 |
| B站专栏 | **只有清单** | ❌ 只存草稿 | 草稿只存在远端，本地无从判断；LV1–3 每天 1 篇，LV4–6 5 篇 |

## 为什么串行不并发

批量发版抢同一个 Chrome/CDP 会互相踩。实测并发时子进程批量
`ConnectionAbortedError: [WinError 10053]`、`TimeoutError`，甚至把头条
草稿箱 tab 挤掉。串行 + 单条重试已经够快（约 70 秒/条）。

并发的正确解法是「一个平台一个 Chrome 实例」，那是以后的事。

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

## B站封面（2026-09-29 补）

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

## B站正文排版（2026-09-30 补）

用户反馈的「样式丢失、每段分得太开、小标题没有样式且和下面内容隔了太多
行」，实际是**四个独立问题**，前两个才是主因：

| 症状 | 真因 | 修法 |
|---|---|---|
| 整篇被引用块包住、样式全丢 | 清空正文用了 `execCommand`，ProseMirror 内部 state 不同步，旧内容自己回来，`insertHTML` 静默无效，正文全灌进遗留的 `<blockquote class="eva3-blockquote">` | 清空改走 CDP `Input.dispatchKeyEvent` 的 Ctrl+A → Delete |
| 段落之间多出空行 | **HTML 标签之间的换行符**被 Tiptap 当成额外空段落 | `clean_body` 末尾 `re.sub(r">[ \t\r\n]+<", "><", body)` |
| 小标题不像标题 | B站编辑器**没有小标题功能**（见下） | h2/h3 → `<p><strong>▶标题</strong></p>` |
| 标题与正文间距过大 | B站给 h2 的规则是 `margin-top:36px; margin-bottom:0` | 同上，改成普通段落后统一 24px |

### 四个坑的实测细节

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

### 修复前后对照（coral 实测）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 顶层 blockquote 高 | 2492px（吞掉整篇） | 58px（只包导语） |
| 顶层节点数 | 82 | 41 |
| 空段落 | 20+ 个 58px | 0 |
| 残留 h2/h3 | 8 个 | 0 |
| 块间距 | 36px(h2) / 24px 混杂 | 统一 24px |
| 全文高 | 5785px | 2514px |

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

## 待办

- 公众号与小红书的草稿箱读取（`verify` 目前只支持头条与 B站）。
- 图片上传：微信正文里的图在小红书/B站侧没处理（`article.py` 只给 HTML，
  平台适配器自己剥图）。B站**封面**已自动（见上），正文内嵌图仍未处理
  → AI 配图已能出（`agnes_cover.py`），但**接进正文 HTML 这步还没做**。
- B站草稿的**分区/话题/文集**没填（`category` 停在默认「生活」），人工终审。
- 头图 `kibu` / `pieter-levels` 不在草稿箱（已发布），改封面要走
  作品管理 → 修改，人工。
- 「一个平台一个 Chrome 实例」的并发方案。
