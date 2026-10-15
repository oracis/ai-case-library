# 案例库小程序（虚拟支付 · 个人主体）

海外软件生意拆解库的微信小程序端。**卖功能，不卖内容**：

| | 免费 | 付费 |
|---|---|---|
| 内容 | 全部案例浏览、双轴评分速查、案例摘要 | 深度报告（单篇 ¥6.60 / 会员 ¥12 月） |
| 功能 | — | 全站 CSV 导出（¥19.90）、会员期内报告全解锁 |

> 为什么内容免费：① 个人主体「工具」类目下，付费墙锁**知识内容**是驳回高发区，锁**功能**不是；
> ② Web 站（case.ydtgo.top）本来就全文公开，小程序提供的是结构化报告的便捷形态。
> 底部页脚与报告页都向读者写明了这一点，不做误导。

## 架构

```
miniprogram/                     小程序端（WXML，不进云）
  app.js / app.json / app.wxss
  pages/                        5 个页面（3 tab + 详情 + 报告）
  utils/
    config.js                   ENV_ID / 云函数名 / 道具展示信息 ← 部署要改
    fmt.js                      收入口径格式化（红线 1 的执行点）
    api.js                      云函数调用 + storage 缓存
    pay.js                     wx.requestVirtualPayment 流程封装
  cloudfunctions/
    get-cases/                  案例出口（字段白名单 + 登录态权益）
    pay-order/                  下单：outTradeNo + signData + 两套签名
    pay-notify/                 发货推送（HTTP 触发，幂等发货）
    query-order/                query_order 查单兜底 + 权益刷新
    export-cases/               CSV 导出（服务端校验权益）
    sync-data/                  HTTP 触发，接本地同步脚本的 upsert
scripts/
  sync_cases_cloudbase.js       data/cases.json → CloudBase（契约唯一定义处）
  verify_miniprogram.js         专项验证（签名跨语言对照 / 契约 / WXML / 路由）
```

数据流：`data/cases.json` →（sync 脚本，扁平化）→ CloudBase `cases` 集合
→（get-cases 云函数，白名单）→ 小程序前端。

---

## 部署（五步）

### Step 1 · MP 后台：开通虚拟支付 + 建道具

前置：小程序主体为**个人**、服务类目含「**工具**」、已完成**认证**（30 元）与**备案**。

1. 【支付与交易 → 虚拟支付】→ 开通 → 填个人资料（身份证、提现银行卡、支付管理员）→ 审核约 5 分钟 → 扫码签约
2. 记下三个关键信息（后面云函数要用）：

   | 信息 | 位置 |
   |---|---|
   | AppID | MP 后台 → 设置 |
   | OfferID | 虚拟支付 → 基本配置 |
   | 现网 AppKey | 虚拟支付 → 基本配置 |

3. 【道具管理】创建三个道具（**ID 与价格必须与云函数 config 一致**）：

   | 道具 key | 后台道具 ID（你创建时填） | 价格 | 用途 |
   |---|---|---|---|
   | `report_single` | 如 `report_single` | ¥6.60 | 解锁单篇深度报告 |
   | `export_all` | 如 `export_all` | ¥19.90 | 全站 CSV 导出 |
   | `member_month` | 如 `member_month` | ¥12.00 | 30 天会员 |

4. 如需 iOS 收款：【账号设置 → 基本信息】配小程序简称 → 虚拟支付 → 基本配置 → 开通苹果 IAP
   （iOS 走 Apple 支付，费率 12%，约 45-60 天结算；Android 等 1%，T+3）

### Step 2 · 云开发：建环境与集合

CloudBase 控制台新建环境，记下**环境 ID**（形如 `case-lib-1a2b3c`）。建三个集合：

| 集合 | 内容 | 建议权限 |
|---|---|---|
| `cases` | 案例（sync 脚本写入） | 仅管理端读写（前端全走云函数，不直连） |
| `orders` | 订单（pay-order 创建，notify/query 推进状态） | 仅管理端读写 |
| `users` | 权益（openid → reports/export_all/member_until） | 仅管理端读写 |

`orders` 建议给 `out_trade_no`、`openid` 建索引；`users` 给 `_openid` 建索引。

### Step 3 · 填配置

**云函数侧**（6 个云函数各自的 `lib/config.js`，或 CloudBase 控制台配环境变量更稳）：
`APPID` / `OFFER_ID` / `APP_KEY` / `APP_SECRET` / 三个 `PID_*`（后台道具 ID）/ `SYNC_SECRET`（随机长字符串）。

> 🔴 AppKey / AppSecret **只能**放在云函数。写进小程序端代码 = 公开（包体可反编译）。

**小程序端**：`miniprogram/utils/config.js` 里 `ENV_ID` 改成你的环境 ID。

### Step 4 · 部署云函数

微信开发者工具导入 `miniprogram/`（appid 会自动替换为真实 AppID）。对 6 个云函数目录逐个
右键 →「上传并部署：云端安装依赖」（依赖 wx-server-sdk ~4.0.2）。

其中两个需要 **HTTP 触发**：

| 云函数 | 触发方式 | 触发地址用途 |
|---|---|---|
| `pay-notify` | HTTP 触发 | 地址填到 MP 后台【开发与服务 → 开发管理 → 消息推送】的 URL |
| `sync-data` | HTTP 触发 | 地址给本地同步脚本用（`--url` 参数） |

> pay-notify 的验签由微信云开发网关完成，业务代码不要再验签。
> 返回体固定 `<xml><ErrCode>0</ErrCode>…</xml>`，非 0 平台会重试（最多 15 次）。

### Step 5 · 同步数据 + 测试支付

```bash
# 先 dry 验证映射（不发起请求）
node scripts/sync_cases_cloudbase.js --dry

# 正式同步（url 是 sync-data 的 HTTP 触发地址）
node scripts/sync_cases_cloudbase.js --url "https://..." --secret "你的SYNC_SECRET"
```

然后在开发者工具里跑通：购买 → 支付（先用**沙箱**：`lib/config.js` 的 `ENV` 改 `1`，
MP 后台虚拟支付开沙箱；上线前改回 `0`）→ 收到发货推送 → 权益到账。
**上线前用一笔小额真单走完整闭环**，并核对后台账单金额。

---

## 验证

```bash
node scripts/verify_miniprogram.js
```

37 项断言，包括：签名的 **Node 实现与官方 Python 算法逐字节对照**（跨语言，防抄错）、
`mapCase` 输出键必须全部落在 `get-cases` 白名单内（防字段静默漏传）、WXML 标签配对、
页面路由与云函数目录完整性。

---

## 红线（违反了会出事）

1. **月支付限额 10 万元**（全终端合计）。超限额的后果官方文档未写明，按「不能超」规划。
2. **只做道具直购**（`mode=short_series_goods`），不引入代币/预付余额/充值模式。
3. **金额单位「分」，全程不换算**；价格以云函数 config 为准，前端只传「买哪个」。
4. **`post_body` 必须与实际请求体逐字节一致**（签名的就是发出去的字符串，不格式化、不改键序）。
5. **发货以推送为准**，`success` 回调可能丢失；幂等按 `wx_order_id`（库里按 `out_trade_no` 判重，同订单）。
6. **收入口径不许混用**（库规红线 1）：MRR 与近 30 天收入是独立字段，各自带口径标签，永不相加。
7. **个人主体工具类目**：付费课程/小说/社群/AI 生成内容付费会被驳回。只卖工具功能。
8. **禁止绕开支付**：不放个人收款码、不引导外部链接支付。

## 已知取舍

- **墓碑区（12 条倒闭公司）未进小程序**：一期聚焦验证付费闭环，二期可由 get-cases 顺带输出。
- **导出走剪贴板**：`wx.openDocument` 不支持 csv；云函数仍会上传一份到云存储（返回 fileID 备用）。
- **软墙 vs 硬墙**：深度报告内容在 Web 站公开，小程序端软墙（体验门）；导出是纯计算功能，
  云函数 `export-cases` 里硬校验权益（服务端门）。两处门的边界写在各自文件头注释里。
- **`_lib-template/`** 是公共库模板，各云函数的 `lib/` 是它的副本（云函数上传不包含上级目录）。
  改公共逻辑要同步到每个云函数，或用环境变量把配置挪到 CloudBase 控制台。
