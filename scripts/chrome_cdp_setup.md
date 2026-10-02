# Chrome CDP 登录态不稳 —— 根因与修复

> 2026-10-02 定位并**三次修正**。用户症状：登录后 Chrome 一会就退出，需反复重登；
> 以前登录能用很久。
>
> ⚠ **本文档第二节的「Cookies 大小判据」已被推翻**，见下面「零、重大修正」。
> ⚠ **启动命令缺 `--no-sandbox` 是「启动即退」的真正根因**，见「一.5」。
> ⚠ **登录不需要扫码，点一下「登录」按钮即可**，见「一.6」。
> 三条判定规则请直接看**零**节。

## 零、重大修正：Cookies 文件大小**不能**当登录态判据

这是本轮实测挖出的最大认知错误，必须记住：

| profile | Cookies 文件大小 | cookie 总行数 | 微信相关行数 | 实际登录态|
|---|---|---|---|---|
| CDP（`ChromeCDP`） | **32 KB** | 38 | **12** | **完好，能正常登录** |
| 日常 Chrome | **1.4 MB** | 900 | **8** | — |

**日常 Chrome 的 cookie 库大43 倍，微信 cookie 反而比 CDP profile 少 4 条。**

原因：SQLite 文件大小由**历史记录、已删除页、WAL 残留**决定，
跟「当前有多少条 cookie」没有正相关。日常 Chrome 浏览了几个月，
文件里全是历史膨胀；CDP profile 只装了自动化真正需要的少数几个站。

⇒ **正确判据只有一个：能不能实拉拿到 token。**
用 `scripts/chrome_login_state.py`，它最后一步会真连CDP 拉一次后台。

⚠ 凡是写着「Cookies 应 >100KB，32KB 就是丢了」的地方，
都是基于错误判据写出来的，**别再照用**。

## 一、根因（实测证据）

### 1. `ChromeCDP` **不是 junction**，是个独立的空 profile

之前 MEMORY.md 记的「junction 指向原User Data」**根本没生效**
（大概率是系统重装/重启后 junction 丢了），实测：

```python
# 检查reparse point（junction 的标记）
os.lstat(path).st_file_attributes & 0x400   # 0x400 = reparse point
```

结果 `ChromeCDP` 及其下**所有子目录** `islink=False reparse=False` ——
它是个**普通目录**，跟原 profile 没有任何关系。

### 2. 为什么「以前能用很久」

Chrome **136 起**（官方公告）改了行为：

> `--remote-debugging-port` / `--remote-debugging-pipe`
> **不再对默认数据目录生效**，必须搭配 `--user-data-dir` 指向非标准目录。
> 非标准目录**使用不同的加密密钥**（App-Bound Encryption）。

所以从136 开始，**只要用了非默认 profile，就等于换了一个独立浏览器**：
登录态、历史、扩展全都是新的，跟你日常 Chrome 不互通。

**这不是故障，是官方设计** —— CDP 必须用独立 profile，
所以它的登录态**天然就是一份需要单独维护的资产**，
和日常 Chrome 那份不互通、也不该期待互通。

官方给浏览器自动化场景的推荐是 **Chrome for Testing**：
不受上述限制，且profile 完全由你控制。

### 3. 「Chrome 掉了」必须先分两种，**别混**

| 现象 | 根因 | 处理 |
|---|---|---|
| `10061` 连不上 / `10054` WS 被重置 / 启动即退（无报错无窗口） | **进程被环境回收**，或残留 `Default\LOCK` | 重跑一次即可，**与登录态无关** |
| 页面持续「请重新登录」+ 重导航 10 轮仍NO_TOKEN | **Cookie 真丢**（会话过期 / profile 未落盘） | 人工扫码一次 |

⚠ 实测反复验证：登录态**一直是好的** —— `ChromeCDP` 只有 38 条 cookie，
12 条微信 cookie，登录可用。真正让 refresh 失败的一直是
**Chrome 进程活不过一次 Bash 调用**，不是登录态。

⇒ 结论：把精力放在**怎么让 Chrome 活得久**（启动脚本 + LOCK 清理 +
避免并发任务抢端口），而不是反复重登。

### 4. 官方文档没有支持「新版 Chrome 天生会话更短」

查了官方 remote-debugging-port 公告，只讲 profile 隔离与 App-Bound，
**没有**任何关于 cookie 生命周期 / 会话时长变化的内容。
So你观察到的「一会就退出」= 进程死，**不是** cookie 自己过期。

### 5. ⚠⚠ 启动即退的真根因：本机沙箱拦子进程，**必须加 `--no-sandbox`**

这是「Chrome 一启动就退、9222 从不开」的**唯一真凶**，此前一直被误判成
「登录态掉了」/「版本问题」/「LOCK 残留」。2026-10-02 用**全新临时 profile**
跑了一个标志矩阵（每个组合用独立 profile，避免 LOCK 干扰）：

| 参数组合 | 结果 |
|---|---|
| baseline（只有 port + user-data-dir） | **FAIL exit=3** |
| `--disable-gpu` | **FAIL exit=3** |
| **`--no-sandbox`** | **CDP OK** ✅ |
| `--no-sandbox` + gpu off | CDP OK ✅ |
| `--no-sandbox --single-process` | CDP OK ✅ |

`exit=3` = Chromium `RESULT_CODE_KILLED_BAD_MESSAGE`
（"a bad message caused the process termination"）—— 本机**沙箱拦住
renderer/GPU 子进程的创建**，主进程收到「坏消息」后自杀。

症状特征（很有辨识度）：
- 日志正常加载完 policy / variations，**2 秒内进程消失**；
- 无崩溃记录、无 JS 报错；
- 9222 从不开放；
- 与 LOCK、登录态**都无关**。

⇒ 所有 CDP 启动路径（`chrome_cdp_launch.py` / `wechat_publish.py` 的 autostart /
`.bat`）**一律带 `--no-sandbox`**。

### 6. 公众号登录**不需要扫码**：点一下「登录」按钮就行

页面显示「请重新登录」时，会话 cookie 其实**一直都在**，只是后台要你点一次
才签发新 session。实抓 DOM 找到的按钮是：

```html
<a id="jumpUrl" href="/cgi-bin/loginpage?url=%2Fcgi-bin%2Fhome">登录</a>
```

⚠ **它没有 `login` class**，所以 `.login_btn, [class*="login"]` 这类选择器
会完全落空（踩过这个坑）。正确做法是**优先 `#jumpUrl`**，文本「登录」兜底。

2026-10-02 零人工干预实测通过：

```
[login] 页面显示「登录」按钮（会话还在），已自动跳转到登录页
[login] 点「登录」按钮后直接拿到 token=754545568（无需扫码）
```

已内建在 `_connect_mp()` → `_try_click_login()`，用户无感。

## 二、修复方案

### 已下载就位

`%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe`
（Chrome for Testing 154.0.8037.92，205 MB）

### 关键选择：**用独立 profile，不要 junction**

❌ **不要**再做junction 指向原 `User Data`。
- App-Bound Encryption 绑定**原始路径**，junction 能骗过 Chrome 但
  **换来的加密密钥不同**，结果就是「登录态看着在、实际解不开」。
- 更重要：这么做**同时污染你的日常 profile**（App-Bound 会写数据到原路径）。

✅ **正确做法**：给 CDP 用**完全独立的 profile**，登录一次就用它。

推荐目录：`%LOCALAPPDATA%\Google\ChromeCDP`（就是现有的那个，
虽然之前是误建，但它现在是干净的空 profile，正好拿来当专用 profile）

### 启动命令

```bat
"%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP" ^
  --no-first-run --no-default-browser-check --no-sandbox
```

⚠ **`--no-sandbox` 是硬性必需**（见「一.5」），漏了必定启动即退。

```bat
"%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP"
```

### ⚠ 三条必须遵守的规则

1. **每次启动前删`Default/LOCK`**
   ```bat
   del /f /q "%LOCALAPPDATA%\Google\ChromeCDP\Default\LOCK" 2>nul
   ```
   强杀后残留会让 Chrome **启动即退**（无报错无窗口，进程闪一下就没）。
   判据：换临时 profile 能起、ChromeCDP 起不来 → 就是 LOCK。
   ⚠ 极易误判成「版本问题」或「profile 坏了」。

2. **不要用 `Incognito` / 无痕**

3. **不要在同一个 profile 里既日常浏览又跑 CDP** —— 会话互相踢。

## 三、为什么以前能用很久，现在不行

| | Chrome <136 | Chrome ≥136（本机154） |
|---|---|---|
| CDP 指向默认 profile | ✅ 允许，登录态共享 | ❌ **被禁** |
| 需要的额外配置 | 无| 必须 `--user-data-dir` |
| 登录态 | 跟日常 Chrome 同一份 | **独立一份**，易丢 |

⇒ 从 136 开始就必须接受「CDP 有自己的登录态」这个前提，
**并且让那个 profile 的 cookie 稳定保存**。之前的问题是没有专门维护它，
profile 被反复强杀、LOCK 残留、cookie 没落盘。

## 四、验证登录态是否真的可用

跑 `scripts\chrome_cdp_check.bat`，它会：
1. 清 `Default\LOCK`
2. 启动 Chrome for Testing
3. 检查 CDP 端口
4. 调 `scripts\chrome_login_state.py` 体检，**最后一步实拉 token**

**唯一判据**：最后那步能不能拿到 token。

拿到 → 登录态可用，直接跑 refresh，**不用再扫码**。
拿不到 → 真掉登录了，才需要人工扫码。

⚠ **现在通常连这个 .bat 都不用双击**：项目脚本已内置 autostart，
`wechat_publish.py refresh` / `wechat_verify_refresh.py` / `chrome_login_state.py`
都会自己清代理 → 删 LOCK → 带 `--no-sandbox` 起 Chrome → 轮询 9222 →
必要时自动点「登录」→ 实拉 token。`.bat` 只留作人工排查时的入口。

⚠ **不要看 Cookies 文件大小**，理由见零节。
`chrome_login_state.py` 会把文件大小也打印出来，但那行后面标着
「← 仅供参考，不是判据」，别被它误导。

⚠ Chrome 运行时 `Cookies` 文件可能被独占锁，`shutil.copy2` 会报
`PermissionError: [WinError 32]`。**这是文件锁，不是登录态问题**——
`chrome_login_state.py` 已改三级降级（copy2 → 直接读 → sqlite backup）。

也可以只查磁盘（Chrome 没起时）：

```bash
python -X utf8 scripts/chrome_login_state.py --skip-live
```

## 五、官方出处

- Changes to remote debugging switches to improve security
  https://developer.chrome.com/blog/remote-debugging-port
  （明确写"for browser automation scenarios, we recommend using Chrome for Testing"）