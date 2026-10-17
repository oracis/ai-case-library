# 小红书草稿在哪个 profile —— 结论与用法

> 2026-10-02 定位。用户现象：打开 creator.xiaohongshu.com，右上角  
> 「小红薯6474A1BD」，侧边栏**草稿箱(0)**，但 `data/xhs_drafts.json`  
> 记着 37 条草稿。  
> **2026-10-17 更新**：`chrome-debug-profile` 里实测 **48 条**（持续增长），
> 并修复了 bat 的「端口已起」分支把页面丢给默认浏览器的 bug（见第五节）。

## 一、根因：不是账号问题，是 **profile** 问题

**两个 profile，同一个账号，草稿箱差 48 条**：

| profile                              | 端口   | 账号          | 草稿箱           |
| ------------------------------------ | ---- | ----------- | ------------- |
| `C:\Users\DELL\chrome-debug-profile` | 9223 | 小红薯6474A1BD | **草稿箱(48)** ✅ |
| `%LOCALAPPDATA%\Google\ChromeCDP`    | 9222 | 小红薯6474A1BD | **草稿箱(0)** ❌  |
| 日常 Chrome（默认 User Data）            | 无    | 小红薯6474A1BD | **草稿箱(0)** ❌  |

草稿是**跟着 profile 走的本地数据**，不是跟着账号走的服务器数据。  
同一账号在两个 profile 里，就是两份互不可见的本地草稿箱。

为什么会出现两个 profile：

- `scripts/xhs_publish.py` 里`PUB_PORT = 9222` 配的profile 是  
  `C:\Users\DELL\chrome-debug-profile`（历史遗留）。
- 公众号 autostart（`chrome_cdp_launch.py`）用的是  
  `%LOCALAPPDATA%\Google\ChromeCDP`。

你截图里那个「草稿箱(0)」=ChromeCDP —— 也就是**公众号那一份**。  
顺手用同一个 Chrome 打开小红书，就打开了没有草稿的那份。

## 二、怎么保证「无论怎么打开都打开对的那份」

### 方案 A（推荐）：双击 `scripts\xhs_open_right.bat`

它会：

1. 用**独立端口 9223** 启动 Chrome for Testing；
2. **`--user-data-dir=C:\Users\DELL\chrome-debug-profile`**（有草稿那份）；
3. **直接打开草稿箱页**（不是发布页）；
4. 端口已起时**用同一个 `--user-data-dir` 再唤起一次 CfT**
   （Chrome 按 profile 单例，已在跑的实例会收下这个 URL，
   控制台显示「正在现有的浏览器会话中打开」），标签页落进**那个**窗口；
5. 跑 `xhs_draftbox_verify.py` 把侧边栏 `草稿箱(N)` 回读到控制台，
   **当场证明打开对了**。

⚠ **不要复用 9222** —— 那是公众号的 ChromeCDP，正是没有草稿的那份。

### 方案 B：把 ChromeCDP 的小红书草稿「搬」到 ChromeCDP

如果你想让所有平台共用一个 profile（长期更省心），  
需要在小红书后台把 36 条草稿重新发布一遍到 ChromeCDP 那个登录态上。  
**注意这不会「搬」，是「重建」** —— 小红书没有草稿迁移接口，  
草稿绑本地 cookie/session，换 profile 后必须重发。

### 方案 C：什么都不做

只要记住：**看草稿一律用 `xhs_open_right.bat`**，  
公众号走9222，小红书走 9223，各用各的 profile，互不干扰。

## 三、2026-10-17：「bat 以前能用，现在草稿箱 0」的根因

用户原话：*「我用你以前的 xhs_open_right.bat 打开，是可以看见草稿箱
里面的文章的，但现在没了」*。

**草稿一条没丢**（IndexedDB 里 48 条，侧边栏也写着 48），
丢的是**打开方式**。旧 bat 的「端口已起」分支：

```bat
if "%ST%"=="UP" (
  start "" "%DRAFT%"        ← 把 URL 交给默认浏览器
)
```

`start "" <url>` 走 Windows shell 关联 ⇒ **默认浏览器 = 用户日常 Chrome**，
而日常 Chrome 那份 profile 里草稿箱恒为 0。控制台却打印
"opening draft box in that window" —— **提示与事实相反**。

时序自证：重启后**第一次**双击 bat（9223 没起）→ 真启动 CfT → 看到 48；
CfT 窗口不关，**之后再双击**（9223 已起）→ 走进空 profile → 0。
所以体感就是「以前能看见，现在没了」。

### 修法（已落地，`scripts/test_xhs_draftbox.py` 锁回归）

端口已起时**用同一个 `--user-data-dir` 再唤起一次 CfT**：

```bat
start "" "%CFT%" --user-data-dir="%XHS_PROFILE%" ... "%DRAFT%"
```

Chrome 按 user-data-dir **单例**：已在跑的实例收下 URL
（弹「正在现有的浏览器会话中打开」），标签页落进有草稿的窗口。
再跑 `xhs_draftbox_verify.py` 把侧边栏 `草稿箱(N)` 回读到控制台。

顺带修了 bat 里 `REMTwo profiles...`（`REM` 后缺空格）——
cmd 按 GBK 读批处理，这会直接报一句 `'REMTwo' is not recognized`。

## 四、排查方法（可复用）

```bash
# 只看磁盘事实（cookie 条数/大小，仅供参考，不是判据）
python -X utf8 scripts/xhs_profile_who.py --live

# 真打开两个 profile 各一次，报告账号名 + 侧边栏草稿数
python -X utf8 scripts/xhs_which_drafts.py --all
```

⚠ **判据必须是侧边栏那个 `草稿箱(N)` 的数字**，不要用这两个：

- ❌ **DOM 卡片计数**：草稿列表懒加载，`document.querySelectorAll(...)`  
  实测返回 **0**，而侧边栏明明写着 36。**两个判据指向相反结论**，  
  信 DOM 会误判成「草稿不存在」。
- ❌ **Cookies 文件大小 / cookie 条数**：本项目记忆里的血泪教训 ——  
  日常 Chrome 1.4MB/900 条但微信 cookie 只有 8 条，  
  CDP profile 36KB 却登录完好。**两个 profile 的小红书 cookie  
  分别是 15 条和 18 条，都"正常"，完全区分不出草稿在哪。**

⚠ cookie 的 `value` 列是 App-Bound 加密的，**查不出账号 ID**，  
Local Storage 里也只有 `_renderInfo` 这类无关键  
⇒ **账号和草稿数只能靠真打开一次看**。

## 五、其他踩坑

- `Page.enable` 必须在 `new_target()` + `connect_target()` **之后**调，  
  否则报「未连接页面 target」。
- 新版 Chrome 已禁 HTTP `/json/new`（GET/POST 都 405），  
  必须用浏览器级 WS 发 `Target.createTarget`。
- 本机 Chrome **必须带 `--no-sandbox`**，否则 2 秒内自杀（exit code 3）。
- 启动前删 `%USERPROFILE%\chrome-debug-profile\Default\LOCK`，  
  残留会让 Chrome 启动即退。
