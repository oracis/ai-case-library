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

## 与旧编排的关系

`publish_both.py` / `publish_all.py` 仍在用（三平台 + 老参数），**新活一律
走 `publish_multi.py`**：多了 B站适配器、清单状态机、单条重试、断点续传。

## 待办

- 公众号与小红书的草稿箱读取（`verify` 目前只支持头条与 B站）。
- 图片上传：微信正文里的图在小红书/B站侧没处理（`article.py` 只给 HTML，
  平台适配器自己剥图）。B站草稿目前不设封面与分区，人工终审时补。
- 「一个平台一个 Chrome 实例」的并发方案。
