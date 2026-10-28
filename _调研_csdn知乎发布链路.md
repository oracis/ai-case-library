# CSDN 与知乎发布链路调研（2026-10-28）

> 状态：**桌面调研完成，未本机实测**。结论来自 2026 年公开实测文章/开源项目交叉验证，
> 每条判断都标注了来源可信度。动手前按 B 站先例「请求形状只能靠 hook 抓」做实机验证。

---

## 一、CSDN（优先接，成本最低）

### 1.1 结论速览

| 项 | 结论 |
|---|---|
| 官方公开 API | ❌ 无（「CSDN没开放发文API」社区共识 + 文库问答确认） |
| 私有接口 | ✅ `POST https://bizapi.csdn.net/blog-console-api/v3/mdeditor/saveArticle` |
| 鉴权 | Cookie（登录态）+ 阿里云 API 网关签名（**静态 secret，可离线计算**） |
| 依赖浏览器 | ❌ 纯 requests 即可 —— 与 B 站纯 API 同级 |
| 正文格式 | `markdowncontent`（原始 MD）与 `content`（渲染后 HTML）**分开传** |
| 回读 | 响应 `data.url`；编辑器 URL `editor.csdn.net/md/?articleId=xxx` |
| 风控 | 有频率限制（社区口径：日 ≤2-3 篇、间隔 ≥30min），滑块验证码自动化过不了 |

### 1.2 签名细节（阿里云 API 网关那套）

请求头四件套：

```
x-ca-key: 203803574          ← 固定值（app 标识）
x-ca-nonce: <uuid4 每次新>    ← 重复会被判重放
x-ca-signature-headers: x-ca-key,x-ca-nonce
x-ca-signature: <HMAC-SHA256 后 Base64>
```

签名字符串（分隔符必须是 `\n`，空行不是装饰、一字不能差）：

```
POST\n*\n*\napplication/json\n\nx-ca-key:203803574\nx-ca-nonce:{nonce}\n/blog-console-api/v3/mdeditor/saveArticle
```

- secret（社区流传固定值）：`9znpamsyl2c7cdrr9sas0le9vbc3r6ba`
- ⚠ 参与签名的 Accept / Content-Type 必须与实际请求头**完全一致**，否则恒报「签名错误」且不告诉你错哪
- ⚠ 上面签名串里的 Accept 行值有 `*/*` 与 `application/json` 两个版本的说法（不同文章不一致）——
  **实机第一件事：hook 编辑器真实 saveArticle 请求抓签名字符串形状**，别信任何一篇的拼写

### 1.3 payload 形状（多来源交叉一致）

```json
{
  "title": "...",
  "markdowncontent": "原始 Markdown",
  "content": "渲染后的 HTML",
  "readType": "public",
  "type": "original",
  "pubStatus": "publish",        // 或 "draft" 存草稿
  "tags": "人工智能",
  "status": 0,
  "source": "pc_mdeditor"
}
```

⚠ 两个经典坑：
1. **只有 Cookie 会 401**（bizapi 域必须带签名头）；
2. **把 Markdown 同时塞进 markdowncontent 和 content** → 页面上 `#`、`**`、代码块全乱。
   正确：`markdowncontent` 放原始 MD，`content` 放渲染 HTML。

探活（类似 verify_cookie.py 的免密核验）：空 body POST saveArticle，
`code==200` 即 Cookie 有效，不会真的发文。

### 1.4 风控与账号安全（多来源交叉）

| 项 | 口径 |
|---|---|
| 发布频率 | 单账号日 ≤2-3 篇、间隔 ≥30min、请求间隔随机 2-5s（有实测：间隔 2s 跑 200 篇没出事，但那是读接口） |
| 验证码 | 登录/发布可能弹滑块，**自动化过不了，需人工介入** |
| Cookie 有效期 | 约 30 天，与登录设备绑定；过期信号：302→/account/login、401、403 |
| UA/Referer | 必须稳定（Chrome UA 起手、后续换 curl 默认 UA 会触发设备指纹风控 403） |
| ToS | 协议禁止未授权自动化发布 —— 最稳路径是**只存草稿，发布那一下人工点**（首期可采用） |

### 1.5 UI 路线（兜底，已知坑）

- 编辑器是 CKEditor5：文件输入框上传一次后即被 Vue 销毁 → 二次上传超时；
- 图片裁剪组件**强裁 16:9**，纵向图大部分内容丢失（对小红书 3:4 封面复用不利）；
- 逐字符 `keyboard.type` 会触发自动缩进/自动列表，把 markdown 弄乱（`-` 变 `- -`）→ 剪贴板 Ctrl+V 一次粘贴；
- 选择器平均每季度改版 → 需多组回退选择器 + 失败截图。

### 1.6 对本项目的接入判断

- 我们的草稿源就是 markdown，`markdowncontent` 直传 **改动最小**；
- 公众号金额清洗器会剥标题数字，「标题禁金额」铁律同样适用；
- 标签需映射到 CSDN 数字/名称标签（「人工智能」在 CSDN 是文字标签，比掘金简单）；
- 预计工作量：签名 + 探活 + saveArticle ≈ 半天（风险点仅在签名串形状，hook 一次就能钉死）。

---

## 二、知乎（价值最高，但成本和风险都高一档）

### 2.1 结论速览

| 项 | 结论 |
|---|---|
| 官方公开 API | ❌ 个人无（api.zhihu.com 那套是开放平台 OAuth 体系，发文权限不对个人开发者开放） |
| Web 私有接口 | ✅ `POST https://www.zhihu.com/api/v4/articles`（建草稿）→ `POST /api/v4/articles/{id}/publish`（发布） |
| 鉴权 | Cookie（`z_c0` 登录态 + `d_c0` 设备标识）+ **x-zse-96 动态签名** |
| 依赖浏览器 | API 路线不需要，但签名算法需移植（有现成开源实现） |
| 正文格式 | HTML（Draft.js 富文本） |
| 风控 | 比 CSDN 严一档；签名版本号随前端改版漂移 |
| ⚠ 关键空白 | **写接口 2026-05-30 后无公开实测成功案例**（zhihu-mcp-server 自报「publish 未测试」） |

### 2.2 x-zse-96 签名（zse96 v2）

请求头：

```
x-zse-93: 101_3_3.0            ← 签名算法版本号（会漂移，不是永恒常量）
x-zse-96: 2.0_<签名>
x-requested-with: fetch
x-xsrftoken: <来自 cookie 或页面 meta>
```

签名生成（来自 zhihu-plus-plus 逆向，多个 2026 年项目移植一致）：

```python
source = f"101_3_3.0+{path}+{d_c0}"     # d_c0 是 cookie 里的设备标识
md5_hash = hashlib.md5(source.encode()).hexdigest()
# 自定义块加密（ZK/ZB 常量表）或 AES-CBC（key/iv 固定，从前端 JS 逆向）
signature = encrypt(md5_hash)
x_zse_96 = "2.0_" + signature.hex()
```

⚠ 维护负担评估（与 B 站 WBI 对比）：WBI 的 mixin_key 来自**免签 nav 接口**、可运行时自取；
x-zse-96 的版本号和加密表**硬编码在前端混淆 JS 里**，知乎改版就要重新逆向——
这是知乎路线长期维护成本高于 B 站/CSDN 的主因。

### 2.3 UI 路线（zhihu-author / zhihu_web.mjs 先例，2025-2026 实测过）

- 编辑器是 **Draft.js** 富文本 → 与公众号 ProseMirror 同类坑：合成 paste 只改 DOM 表面不更新内部 state，
  必须走**富文本剪贴板**（marked + sanitize-html allowlist → clipboard insert），且「缺依赖硬报错、不许 execCommand 兜底」；
- ⚠ **编辑器主发布按钮点了可能没有二次确认框，直接发布** → UI 探查/截图脚本绝不能误点发布按钮
  （对应我们「探查脚本单 tab 复用」纪律）；
- ⚠ 发布成功的**唯一判据**（先例项目的验证契约，与我们「已发表列表按标题命中」完全同构）：
  ① 浏览器离开 `/p/xxx/edit`；② 最终 URL 精确等于 `https://zhuanlan.zhihu.com/p/xxx`；③ 已发表标题 == 草稿标题。
  toast / URL 不变 / 退出码 0 **都不算成功**；
- 远程图片必须 http(s) URL，本地路径 / data URL 会被拒；
- 图片上传/专栏/回答各有独立端点。

### 2.4 需要警惕的幻觉资料

百度智能云一篇「知乎 API v4 权威指南」给出 `api.zhihu.com/v4/articles` + access_token + image_ids 的
「发布代码」——**与个人号实际体系不符，疑似 AI 幻觉**（知乎个人发文从未开放 access_token 体系）。
判据沿用 skill 铁律：认证体系对不上个人号的「教程」一律不采信。

---

## 三、对比结论与接入建议

| 维度 | CSDN | 知乎 |
|---|---|---|
| 长尾搜索价值 | ★★★（百度收录） | ★★★（知乎站内+搜索引擎双收录） |
| 接入难度 | 低（静态签名，纯 requests） | 中-高（动态签名需移植，写接口未验证） |
| 长期维护成本 | 低（secret 11 个月未变） | 中（签名版本随改版漂移） |
| 与现有管线复用 | markdown 直传，改动最小 | HTML 转换 + 标签/话题映射 |
| 批量风险 | 日 2-3 篇上限，滑块需人工 | 未验证，风控口径不明 |
| 现成代码参考 | 多个 2026 MCP/脚本 | zhihu-plus-plus（**AGPL-3.0 传染**，移植需重写不直接引用） |

**建议路线**：

1. **先接 CSDN**（预计半天）：hook 编辑器抓一次真实 saveArticle 签名形状 → 纯 requests 实现
   「存草稿 + 探活」→ 日限 2-3 篇灰度。首期甚至可以只存草稿、人工点发布（零封号风险）。
2. **知乎分两步**：先照 juejin/worldprogramming 两篇的 python 实现移植 x-zse-96，**只调建草稿接口**
   验证签名有效性（草稿无风险）；签名通了再试 publish；全链路不通则退 CDP UI 路线
   （Draft.js 剪贴板注入 + zhihu_web.mjs 的验证契约）。
3. **两条线共同的铁律**（沿用现有约定）：
   - 发布成功唯一判据 = 平台已发表列表回读按标题命中；
   - 标题禁金额；批量限频 + 随机延迟；断点续跑；台账独立（`data/csdn_drafts.json` / `zhihu`），
     `drafts_pending` 不得视为已推送。

## 四、本机实测结果（2026-10-28，补）

> 桌面调研的可疑点全部实机钉死，探针 `scripts/_probe_csdn.py` 可复现。

| 项 | 桌面调研 | **实测** |
|---|---|---|
| 签名 secret | 社区流传值，未验证 | ✅ **有效**（头与签名一致 → 业务层响应；故意不一致 → `HMAC signature does not match`） |
| to_sign 形状 | 两个版本说法不一 | ✅ 网关只要求**与实际头一致**：accept `*/*` 或 `application/json` 都放行 |
| 端点 | bizapi v3 | ✅ 确认；无 Cookie POST → `401 请登录后操作！`（业务层） |
| 老接口 mp.csdn.net | 预期已死 | ✅ **405 openresty，确认死亡** |
| MetaWeblog | 预期关闭 | ✅ **HTTP 461 错误页，确认死亡** |
| 空 body 探活 | DAMO 称 code=200 | ✅ `{"code":200,"data":"成功"}`，**不建稿、无 articleId** → 就是现成的探活 |
| 空 body vs 全字段 | 未提 | ⚠ 缺字段（仅 title/md 等）也回 200「成功」但**不建稿**；字段齐（readType/type/pubStatus/tags/status/source）才回 `data.url`+`id` |
| 🚨 日配额 | 「日 ≤2-3 篇」 | ✅ **本账号（sinat_32396037）日限 2 篇，saveArticle 连存草稿都计入**；超限报 `400300011 今天发表文章数量已达到限制的2篇`（与发布等级挂钩，等级越高配额越多） |
| 401 迷团 | — | ⚠ 同一 payload 有时 401「请登录」有时 400300011「超限」，疑似不同后端节点行为不一致；**见到 401 先查配额，别急着重登** |
| whoami 陷阱 | — | 🚨 `/blog-console-api/v1/user/info` **对任何登录态都回 401**，不能当探活（login 循环曾因此空转 8 分钟）；探活只用空 body saveArticle |
| GET 签名 | — | ⚠ GET 不带 Content-Type 时签名串里不能有 ctype 行（页面内实测 HMAC 不匹配）；POST 路线不受影响 |
| 登录 cookie | 「约 30 天」 | ✅ 已落盘 `data/csdn_cookie.json`（38 个 cookie，票据 UserName/UserInfo/UserToken/p_uid/SESSION 齐全）；登录浏览器是独立 profile `chrome-csdn-profile`（9226），不动其他平台 |

**🚨 后续实测推翻两处关键假设（2026-10-28 晚）**：

1. **saveArticle 没有「存草稿」语义**：`pubStatus:"draft"` 被服务端无视，
   两篇「草稿」直接**发表到了公开博客**（get-business-list 可见、计入配额）。
   所谓「日配额 2 篇」就是**发表**配额——之前计入的是这两篇真发表，不是草稿。
   编辑器真草稿端点待 hook（「保存草稿」按钮合成 click 触发不了 Vue handler，
   与公众号 ProseMirror 合成事件不进内部状态同型）。
2. **回读与删除路由已实锤**：
   - 回读判据：`GET blog.csdn.net/community/home-api/v1/get-business-list?...&businessType=blog&username=<u>`（blog 域免签名、纯 Cookie；发布成功 = 此列表按标题命中）
   - 删除：`POST bizapi.csdn.net/blog/phoenix/console/v1/article/del` body `{"articleId":<int>}` —— 实测连删 3 篇（已发表 + 自动保存草稿）全 `data:true`，误发的 3 篇测试稿已全部清除并回读复核为空
   - ⚠ blog 域接口请求过密会被边缘 WAF 521（list 命令已带退避 + 翻页间隔）

脚本已改安全形态：`publish`（明确叫发表）、`draft` 故意报错不可用、
`list --query`（回读）、`delete --article-id`。

**管线含义**：CSDN = **每天最多 2 篇发表的慢渠道**（本账号等级），
50 条积压 ≈ 25 天；`publish_multi` 排期限速，且**没有草稿缓冲**——
发表前文案必须先过本地守卫，发错了只能删（有删除 API，可自动纠错）。

## 五、来源清单

- CSDN 签名与 payload：《MCP 实战：给 Cursor 接本地工具》(blog.csdn.net/boyzhaotian/162931729, 2026)、
  DAMO 开发者矩阵示例、公众号「蜜学编程」签名详解、devpress.csdn.net MCP 实战（2026）
- CSDN 风控/UI 坑：csdn-blog-mcp（LobeHub，日 2 篇/间隔 30min 口径）、CSDN Bot 实战（164672834）、
  Word→MD 自动发布工具（163949471，CKEditor5/16:9 裁剪坑）
- 知乎签名：《开源项目从 0 到 1》（worldprogramming.org + juejin 7672737696579747890）、
  zhihu-mcp-server（GitHub iteng007，2026-05-30 接口状态自报）、JMY2003/zhihu_web.mjs 验证契约
