# Chrome CDP 登录态不稳 —— 根因与修复

> 2026-10-02 定位。用户症状：登录后 Chrome 一会就退出，需反复重登；
> 以前登录能用很久。

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

### 2. 决定性证据：Cookies 大小差43 倍

| 文件 | 大小 | 含义 |
|---|---|---|
| `ChromeCDP/Default/Network/Cookies` | **32 KB** | 空骨架，几乎没有 cookie |
| `Chrome/User Data/Default/Network/Cookies` | **1.4 MB** | 真实登录态 |

登录态根本没被共享过去。

### 3. 为什么「以前能用很久」

Chrome **136 起**（官方公告）改了行为：

> `--remote-debugging-port` / `--remote-debugging-pipe`
> **不再对默认数据目录生效**，必须搭配 `--user-data-dir` 指向非标准目录。
> 非标准目录**使用不同的加密密钥**（App-Bound Encryption）。

所以从136 开始，**只要用了非默认 profile，就等于换了一个独立浏览器**：
登录态、历史、扩展全都是新的，跟你日常 Chrome 不互通。

而 `ChromeCDP` 这个 profile 里Cookies 只有 32KB ——说明它的登录态
**也不稳定**：微信后台的会话 cookie 在这个 profile 里没被可靠保存。

官方给浏览器自动化场景的推荐是 **Chrome for Testing**：
不受上述限制，且profile 完全由你控制。

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

## 四、验证登录态是否真的持久

跑 `scripts\chrome_cdp_check.bat`，它会：
1. 启动 Chrome（先清 LOCK）
2. 检查 CDP端口
3. 拉一次 `mp.weixin.qq.com` 列表，看 `Network/Cookies` 的大小

**判据**：Cookies 文件应在 **100 KB 以上**（1.4 MB 是原 profile 的量级）。
如果只有 32 KB 左右 → 登录态又丢了，得重新扫码。

## 五、官方出处

- Changes to remote debugging switches to improve security
  https://developer.chrome.com/blog/remote-debugging-port
  （明确写"for browser automation scenarios, we recommend using Chrome for Testing"）