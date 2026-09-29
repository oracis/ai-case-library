# 生图支路（Agnes AI）

给「万物解释者」出配图，**不消耗 WorkBuddy 积分**。

```
python scripts/agnes_cover.py check                    # 先探连通性
python scripts/agnes_cover.py gen --case bustem         # 先跑 1 条看效果
python scripts/agnes_cover.py gen --limit 5             # 试 5 条
python scripts/agnes_cover.py status --verbose          # 对账
python scripts/agnes_cover.py all --target square --yes # 全库方图
```

---

## 为什么是「支路」

主链路是 `collect → review → store → make_article → publish`，**一行都没改**。
本脚本读同一份 `data/cases.json`，图像单独落到 `out/agnes/`，**不接管**任何现有产物。

和三套已有产物并存：

| 产物 | 尺寸 | 怎么来的 | 有字吗 |
|---|---|---|---|
| `out/toutiao/<id>/cover.png` | 3840×2160 | HTML 渲染 + CDP 截图 | ✅ 带大字标题 |
| `out/xhs/<id>/card-1.png` | 2160×2880 | 同上 | ✅ |
| `out/agnes/<id>.<target>.png` | 见下表 | **Agnes API** | ❌ 无字 |

**别用 AI 图替换 `cover.png`。** 头条/B站封面位要 3840×2160 且叠了标题字，
AI 图直接换上会丢掉标题。两套并存才互补：截图封面管封面位，AI 图管正文插图。

---

## 三个目标

| target | 请求 size | 用途 | 平台要求 |
|---|---|---|---|
| `square` | `1024x1024` | 正文插图 | — |
| `wide` | `3840x2160` | 横版 | 头条/B站封面 ≥600×336 |
| `tall` | `2160x2880` | 竖版 | 小红书卡片 |

```bash
python scripts/agnes_cover.py gen --case voklit --target tall
```

---

## 踩过的坑（服务端行为，别按常理猜）

### `size` 不严格遵守，**必须像素回读**

2026-09-30 实测：

| 请求 | 实得 | 结论 |
|---|---|---|
| `1024x1024` | 1024×1024 | 精确 |
| `2160x2880` | 2160×2880 | 精确 |
| `3840x2160` | 3845×2157 | 可用 |
| `1920x1080` | 1312×736 | **偏差大，不可用** |
| `4K` | 3845×2157 / **4096×4096** | **随机，不可控** |

→ ① 横版**必须显式写 `WIDTHxHEIGHT`，别用 `4K`**；
② 落盘后**必须回读像素**（脚本里的 `png_size()`），不能信请求值。
平台报「图片太小」时先查这里，**别去调 prompt**。

`size` 合法值只有 `1K` / `2K` / `3K` / `4K` / `WIDTHxHEIGHT`。
写 `16:9` 会 **400**：`size has invalid value "16:9"`。

### 限流比官方标称更严

- 图像 1K **20 RPM**、**4K 只有 1 RPM**（实测连发两次必 429）、视频 1 RPM
- 冷却窗口 **>2 分钟**
- 免费层**按 key 类型共享**，多建 key 不叠加额度

→ 脚本自带串行 + 最小间隔（`spec_gap()`）。**别并发** ——
CDP 那边也是刻意串行的（见 `MULTIPLATFORM.md`「为什么串行不并发」）。

### prompt 里不能有中文

`cases.json` 的 `one_liner` / `verdict` 全是中文。直接进 prompt，
图像模型会把它理解成"要把这些字画进画面"，出图带乱码文字。

→ 只有 `category`（→ 视觉风格映射）和英文产品名进 prompt。
中文句子里的 ASCII 碎片也要挡掉（`SaaS 买卖 + 数据服务` → `+`，
会变成 `subject: a + software product` 这种无意义提示）。

三条都被 `scripts/test_agnes_cover.py` 钉成断言，CI 每次跑。

### 图像 url 必须用完整路径

```
https://platform-outputs.agnes-ai.space/images/t2i/<task>/<name>.png
```

截断 `.png` 后缀会拿到 S3 风格的 404 XML。

---

## prompt 从哪来

只用 case 已有字段，**不额外采集、不写 `cases.json`**：

| 字段 | 用途 |
|---|---|
| `category` | → 视觉风格 + 画面主题（11 种，全覆盖） |
| `name` | 主体（34/37 是英文，直接可用） |
| `one_liner` | 仅取其 ASCII 词，中文不进图 |
| `metrics` | **不入画** — AI 画数字必糊，数字交给 HTML 排版 |
| `verdict` | 只用于选情绪基调，不直接进 prompt |

风格表写死不自由发挥，**这是整套图看起来像一套系列的前提**。
新增 category 时记得同时补 `CATEGORY_STYLE` 和 `CATEGORY_THEME`，
单测 `test_all_real_categories_covered` 会挡住遗漏。

调完 prompt 可以看落盘记录：

- `out/agnes/_prompt/<case>.<target>.txt` —— 纯 prompt
- `out/agnes/_prompts.jsonl` —— 带时间戳、task_id、revised_prompt

---

## key 从哪读

按顺序：

1. 环境变量 `AGNES_API_KEY`
2. `data/secrets.json` 的 `agnes_key`（**已在 `.gitignore` 里**）
3. `~/.workbuddy/keys/agnes-api-key.txt`

`check` 子命令会打印实际用了哪个来源，顺带验证两个端点的 `usage` 里
**有没有 `credit` 字段** —— 没有就说明不扣 WorkBuddy 积分。

---

## 已验证

| 项 | 结果 |
|---|---|
| 端到端 | `gen --case bustem` 16s 出图，506.8 KB，像素回读通过 |
| 鉴权 | 文本 + 图像端点均 200 |
| 不扣积分 | 图像响应 `usage=null`，**无 `credit` 字段** |
| 单测 | `python -m unittest scripts.test_agnes_cover` 15 tests OK |
| CI | 已挂进 `.github/workflows/ci.yml`（纯函数，不连网） |
| 回归 | `test_toutiao_publish` / `test_multiplatform` / `test_wechat_publish` 无影响 |

全库 37 条 × 3 目标还没批量跑过 —— `wide` 大约要 40 分钟（受 1 RPM 限制），
`square` 约 2 分钟。想批量前先跑 `gen --limit 5` 看风格是否满意。

---

## 待办

- [ ] 批量生成前先确认 `CATEGORY_STYLE` 的风格方向符合「万物解释者」的调性
- [ ] `wide` 目标与 `toutiao_publish.py` 的 HTML 封面做一次人工对比，选哪套上头条
- [ ] 图生图（`image` 参数传参考图）**目前服务端 400**，
      `image must be a public http(s) URL or valid image base64`，
      等官方支持了再考虑「让 AI 在既有截图上改风格」
