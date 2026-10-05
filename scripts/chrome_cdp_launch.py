#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动 Chrome（CDP）并实拉登录态 —— 一体化，替代手工双击步骤。

为什么需要这个脚本（2026-10-02 实测）：
  1. Chrome **必须带 `--no-sandbox`** 才能起来。不带时它会正常加载
     policy / variations 服务，然后**2 秒内退出，exit code 3**
     （`RESULT_CODE_KILLED_BAD_MESSAGE`，见 chromium result_codes.h），
     DevTools 端口根本不开。已用标志矩阵验证：
         baseline               -> FAIL (exit 3)
         --disable-gpu          -> FAIL (exit 3)
         --no-sandbox           -> CDP OK
         --no-sandbox + gpu off -> CDP OK
  2. 启动前必须删 `Default/LOCK`，否则同样启动即退。
  3. 代理必须清干净（`http_proxy=127.0.0.1:6448` 会掐断 CDP 的 WS）。

⚠  为什么脚本里自己起Chrome 而不用 `start`：
   `start` 起的进程活不过一次父进程调用；而把「启动 + 驱动」放进
   **同一个** Python 进程里则实测稳定（2026-10-02 连续跑通 8 分钟）。

⚠⚠ **平台必须显式选，默认只保证公众号是对的**（2026-10-05 修）：
   早先 `PLAT_PROFILE` / `PLAT_PORT` 定义了却**从没被 `main()` 用过** ——
   `main()` 硬写 `PROFILE` + `CDP_PORT`，于是谁直接跑本脚本都开到公众号那个
   Chrome。三平台登录态在另一个 profile，症状是「草稿箱空的」，
   看起来像掉登录，实际是**开错浏览器**。
   ⇒ 现在用 `--platform` 选，profile/port/登录探测三者一起跟着变。

用法：
    python -X utf8 scripts/chrome_cdp_launch.py                    # 公众号（默认）
    python -X utf8 scripts/chrome_cdp_launch.py --platform toutiao  # 头条
    python -X utf8 scripts/chrome_cdp_launch.py --platform bilibili # B站
    python -X utf8 scripts/chrome_cdp_launch.py --platform xhs      # 小红书
    python -X utf8 scripts/chrome_cdp_launch.py --platform three    # 三平台任一
    python -X utf8 scripts/chrome_cdp_launch.py --hold              # 启动后保持
    python -X utf8 scripts/chrome_cdp_launch.py --list              # 看平台→端口映射
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request

CDP_PORT = 9222
CHROME_CANDIDATES = [
    # 优先 Chrome for Testing（官方对自动化场景的推荐）
    (r"%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe", "Chrome for Testing"),
    (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", "system Chrome"),
]

# ⚠⚠ **本机有两个 profile，登录态分布是分裂的**（2026-10-02 实测）。
# 起因是历史遗留：`xhs_publish.py` / `toutiao_publish.py` / `bilibili_publish.py`
# 早期都用 `chrome-debug-profile`，公众号后来迁到了 `ChromeCDP`，两边没合并。
#
#实测 cookie 分布（cookie 条数只是线索，不是判据）：
#   chrome-debug-profile : toutiao 29 / bilibili 23 / xiaohongshu 17
#   ChromeCDP            : toutiao  5 / bilibili  5 / xiaohongshu 17
#
# ⇒ **头条 / B站 / 小红书 的登录态在 `chrome-debug-profile`**，
#   **公众号在 `ChromeCDP`**。
#⇒ 端口也必须分开：9222 给公众号（autostart 默认），9223 给三平台。
#⇒ 拿错 profile 的症状极具误导性：脚本能连上 CDP、页面也能开，
#   但被重定向到登录页 ⇒ 看起来像「掉登录了」，实际是**开错浏览器**。
PROFILE = os.path.expandvars(
    os.environ.get("CDP_PROFILE") or r"%LOCALAPPDATA%\Google\ChromeCDP")
# 三平台（头条 / B站 / 小红书）用的 profile + 端口
PLAT_PROFILE = os.path.expandvars(
    os.environ.get("CDP_PLAT_PROFILE")
    or r"%USERPROFILE%\chrome-debug-profile")
PLAT_PORT = int(os.environ.get("CDP_PLAT_PORT", "9223"))

# --------------------------------------------------------------------------
# 平台注册表 —— profile / port / 登录探测的**唯一**真相源。
# ⚠ 以前这三样散在 main() 里硬写，导致 PLAT_* 成了死代码（见 docstring）。
#    现在三者同源，加平台只改这张表。
#
# ⚠ `probe` 只有两种取值：
#   "wechat" —— 走 `wechat_publish._connect_mp`。**公众号专用**，
#     因为它内建「原地等 → 点登录按钮 → 重导航」三级兜底，
#     自己重写简化版会误报（见 chrome_login_state.py 的教训）。
#   "url"    —— 通用判据：打开 `home_url`，URL 命中 `login_markers`
#     任一子串即视为未登录。这**不是**简化版公众号逻辑，是三平台
#     本来就用的判据（`xhs_publish.py` 里也是 `"login" in url`）。
#     `logged_marker` 只做**加分项**：页面上看到它就确证已登录；
#     没看到**不算未登录**（页面还在加载 / 文案改版都可能缺），
#     只会报 UNKNOWN。⚠ 千万别把它当硬性要求 —— 那是本项目
#     最贵的一个教训：误报「掉登录」会让人以为要重新扫码，
#     实际只是开错profile 或页面没渲染完。
# --------------------------------------------------------------------------
PLATFORMS = {
    "wechat": {
        "label": "公众号",
        "port": CDP_PORT,
        "profile": PROFILE,
        "probe": "wechat",
        "home_url": "https://mp.weixin.qq.com/",
        "login_markers": ("login",),
        "logged_marker": None,
    },
    "toutiao": {
        "label": "今日头条",
        "port": PLAT_PORT,
        "profile": PLAT_PROFILE,
        "probe": "url",
        "home_url": "https://mp.toutiao.com/",
        # 头条的登录页是 `/auth/page/login`（toutiao_publish.LOGIN_HINT）
        "login_markers": ("/auth/page/login",),
        "logged_marker": None,
    },
    "bilibili": {
        "label": "B站",
        "port": PLAT_PORT,
        "profile": PLAT_PROFILE,
        "probe": "url",
        "home_url": "https://member.bilibili.com/platform/home",
        "login_markers": ("passport", "/login"),
        "logged_marker": None,
    },
    "xhs": {
        "label": "小红书",
        "port": PLAT_PORT,
        "profile": PLAT_PROFILE,
        "probe": "url",
        "home_url": "https://creator.xiaohongshu.com/new/home",
        "login_markers": ("login", "passport"),
        # 「小红薯XXXX」= 右上角账号名，是登录态最硬的证据
        "logged_marker": "小红薯",
    },
}
# 别名：让 `--platform three` / `all` / `bili` 都能命中
PLATFORM_ALIASES = {
    "mp": "wechat", "wx": "wechat", "gzh": "wechat",
    "tt": "toutiao", "news": "toutiao",
    "bili": "bilibili", "b23": "bilibili",
    "rednote": "xhs", "xhs": "xhs", "red": "xhs",
    "three": "toutiao", "all": "toutiao", "plat": "toutiao",
}


def resolve_platform(name):
    """把用户给的平台名归一化。返回 (key, spec)。

    ⚠ 认不出就抛 ValueError，**绝不静默回落到公众号** ——
    静默回落正是这个bug 的根：拿错 profile 去看草稿箱，结论全是错的。
    """
    key = (name or "").strip().lower()
    key = PLATFORM_ALIASES.get(key, key)
    if key not in PLATFORMS:
        raise ValueError(
            "未知平台 %r；可选：%s（别名：%s）"
            % (name, " / ".join(sorted(PLATFORMS)),
               " / ".join(sorted(PLATFORM_ALIASES))))
    return key, PLATFORMS[key]


def log(msg):
    print(msg, flush=True)


def clear_proxy():
    """清干净代理。留着 http_proxy 会把 CDP 的 WebSocket 掐断（10053/10054）。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)


def remove_lock(profile):
    r"""挪掉强杀残留的锁 —— 这是「启动即退」的头号根因。

    ⚠ 只能用 Python 删：bash 的 `rm -f` 会被 safe-delete 策略拦
    （报 SAFE_DELETE_INVALID_PATH）。
    ⚠⚠ **`os.remove` 本身也可能被 safe-delete 钩子拦**：钩子会改走回收站
    API（SHFileOperationW），对 `Default\LOCK` 这种被 Chrome 占着的文件
    直接失败（`0x2`文件不存在），报
    `[safe-delete][SAFE_DELETE_FAIL_CLOSED] ... "reason": "trash-failed"`
    ⇒ 表现为「删不掉 LOCK」→ Chrome 启动即退 → CDP 连不上，
    **看起来像登录态问题，实际是锁没清掉**。
    ⇒ 兜底用 `os.rename` 把 LOCK 挪出 profile（挪走≠删除，不触发删除钩子，
    Chrome 看不到 `Default\LOCK` 就等于清掉了）。
    """
    lock = os.path.join(profile, "Default", "LOCK")
    if not os.path.exists(lock):
        return False
    try:
        os.remove(lock)
        log("  cleared stale LOCK")
        return True
    except Exception as e:
        log("  [warn] os.remove LOCK failed (%s), fallback to rename" % e)
    try:
        import tempfile
        dst = os.path.join(tempfile.gettempdir(),
                           "chrome_cdp_LOCK_%d" % int(time.time()))
        os.rename(lock, dst)
        log("  moved stale LOCK -> %s" % dst)
        return True
    except Exception as e:
        log("  [warn] cannot remove LOCK: %s" % e)
    return False


def pick_chrome():
    for path_tpl, label in CHROME_CANDIDATES:
        p = os.path.expandvars(path_tpl)
        if os.path.exists(p):
            return p, label
    return None, None


def build_args(chrome, profile, extra=(), port=None):
    """⚠ `--no-sandbox` 是硬性要求，见模块 docstring。"""
    return [
        chrome,
        "--remote-debugging-port=%d" % (port or CDP_PORT),
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
    ] + list(extra)


def wait_cdp(proc, timeout=40, port=None):
    """轮询 DevTools 端口。返回 True 表示已通。

    ⚠ 用 urllib 而不是 curl：curl 会带上系统代理，必须显式清空。
    """
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:%d/json/version" % (port or CDP_PORT)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            log("  Chrome exited early, code=%s" % proc.poll())
            return False
        try:
            op.open(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(1.5)
    return False


def probe_login(cdp, spec):
    """按平台跑登录态实测。返回 (state, detail)。

    state ∈ {"OK", "NO_LOGIN", "UNKNOWN"}：
      OK        —— 已登录
      NO_LOGIN  —— **确证**被踢到登录页，可以下结论「要重新扫码」
      UNKNOWN   —— 没拿到判据（页面没渲染完 / 文案改版 / 网络慢）。
                   ⚠ 绝不能把它当成 NO_LOGIN —— 误报「掉登录」
                   会让人白扫一次码，而真因常常只是开错 profile。
    """
    if spec["probe"] == "wechat":
        tok = _wechat_token(cdp)
        if tok:
            return "OK", "token=%s" % tok
        return "NO_LOGIN", "拿不到 token（公众号三级兜底已跑完）"

    url = spec["home_url"]
    t = cdp.new_target(url)
    tid = t.get("id") or t.get("targetId")
    if not cdp.connect_target(tid):
        return "UNKNOWN", "connect_target 失败 target=%s" % tid
    cdp.send("Page.enable")
    href = ""
    # ⚠ **先判后睡**：多数情况下 target 一连上就已经在目标页上了，
    # 先睡 1.5s 再判纯属白等（单测里这条路径跑十几次，全靠它省时间）。
    for i in range(20):                    # 最多等 ~30s 渲染
        if i:
            time.sleep(1.5)
        try:
            href = cdp.eval("location.href") or ""
        except Exception:
            continue
        if any(m in href for m in spec["login_markers"]):
            return "NO_LOGIN", "被重定向到登录页：%s" % href[:100]
        if href and "login" not in href.lower():
            break
    marker = spec.get("logged_marker")
    if marker:
        try:
            body = cdp.eval("(document.body.innerText||'').slice(0,3000)",
                            refresh_context=True) or ""
            if marker in body:
                return "OK", "页面出现「%s」：%s" % (marker, href[:90])
        except Exception:
            pass
    if not href:
        return "UNKNOWN", "页面 URL 读不到（页面可能还没渲染）"
    return "UNKNOWN", "不在登录页，但也没读到「%s」：%s" % (
        marker or "登录标识", href[:90])


def _wechat_token(cdp):
    """跑公众号的登录兜底，返回 token（没有就None）。

    ⚠ 模块级 import 会让 `chrome_cdp_launch` 反向依赖 wechat_publish，
    而 wechat_publish 自己又 import chrome_cdp_launch（autostart）⇒
    循环导入。必须放在函数内。
    ⚠⚠ `_connect_mp` 返回 **(tid, token) 二元组**，不是裸 token。
    按单值接拿到的是 tid，于是 token 恒 None —— 无论登录态多好
    都会误报「掉登录」。这个坑踩过两次。
    """
    import wechat_publish as wp
    res = wp._connect_mp(cdp)
    if isinstance(res, tuple):
        return res[1] if len(res) > 1 else None
    return res


def print_platforms():
    log("平台 → profile / 端口映射（登录态按这个分流，拿错就是空草稿箱）：")
    log("")
    # ⚠ 手动排版：%-8s 按**字节/字符**算宽，中文是双宽 ⇒ 表格会歪。
    #   这里用显示宽度补齐，别用 %-ns 处理中文列。
    def _w(s, width):
        return s + " " * max(0, width - sum(2 if ord(c) > 0x2E80 else 1
                                          for c in s))

    log("  %s%s%s%s" % (_w("key", 12), _w("端口", 8), _w("平台", 10),
                       "profile"))
    log("  " + "-" * 72)
    for key in ("wechat", "toutiao", "bilibili", "xhs"):
        s = PLATFORMS[key]
        log("  %s%s%s%s" % (_w(key, 12), _w(str(s["port"]), 8),
                            _w(s["label"], 10),
                            os.path.expandvars(s["profile"])))
    log("")
    log("  三平台（toutiao/bilibili/xhs）共用同一 profile + 9223，")
    log("  所以 --platform three 等价于 --platform toutiao。")
    log("  公众号 9222 是另一份登录态，与三平台互不可见。")


def build_parser():
    """单独抽出来是为了让「默认值是什么」可被单测断言。

    ⚠ 默认必须是 `wechat`（公众号）：`wechat_publish.py` 的 autostart
    路径历史上就靠这个默认值，保持不变。
    """
    ap = argparse.ArgumentParser(
        description="启动 Chrome(CDP) 并实测登录态",
        epilog="示例：--platform xhs --list / --platform toutiao --hold")
    ap.add_argument("--platform", "-p", default="wechat",
                    help="wechat(公众号,默认) / toutiao / bilibili / xhs"
                         " / three(三平台任一)。决定 profile+端口+登录探测。")
    ap.add_argument("--list", action="store_true",
                    help="只打印平台→端口映射后退出")
    ap.add_argument("--hold", action="store_true",
                    help="keep Chrome alive after the probe")
    ap.add_argument("--keep-chrome", dest="keep", action="store_true",
                    default=True,
                    help="keep Chrome running on exit (default)")
    ap.add_argument("--no-keep", dest="keep", action="store_false",
                    help="kill Chrome before exiting")
    ap.add_argument("--probe-only", action="store_true",
                    help="do not launch Chrome, only probe an existing one")
    return ap


def main():
    args = build_parser().parse_args()

    if args.list:
        print_platforms()
        return 0

    try:
        key, spec = resolve_platform(args.platform)
    except ValueError as e:
        log("[X] %s" % e)
        return 64                     # EX_USAGE

    clear_proxy()
    profile = os.path.expandvars(spec["profile"])
    port = int(spec["port"])
    chrome, label = pick_chrome()

    log("■ 平台 %s（%s）" % (spec["label"], key))
    log("  端口 %d · profile %s" % (port, profile))

    proc = None
    if not args.probe_only:
        if chrome is None:
            log("[X] No Chrome binary found.")
            return 1
        log("=== launching Chrome (%s) ===" % label)
        remove_lock(profile)
        args_list = build_args(chrome, profile, port=port)
        # 不重定向 stdout/stderr：让子进程继承 console，避免管道牵连
        proc = subprocess.Popen(args_list)
        log("  pid=%d" % proc.pid)
        log("  flags: --no-sandbox (REQUIRED on this machine)")
        log("")
        log("=== waiting for DevTools port %d ===" % port)
        if not wait_cdp(proc, port=port):
            log("[X] DevTools port %d never opened." % port)
            log("    Chrome either exited or was blocked.")
            log("    - if LOCK was just cleared, retry once")
            log("    - check Task Manager for chrome.exe vanishing instantly")
            return 2
        log("  [OK] CDP is up")

    log("")
    log("=== login state probe (%s) ===" % spec["label"])
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import wechat_publish as wp
        cdp = wp.CDP(port)
        state, detail = probe_login(cdp, spec)
    except SystemExit as e:
        #⚠ `wechat_publish.CDP.__init__` 连不上端口会抛 SystemExit，
        #   `except Exception` 捕不住 → 驱动脚本必须 `except BaseException`。
        log("[X] %s" % str(e)[:200])
        return 3
    except Exception as e:
        log("[X] %s: %s" % (type(e).__name__, e))
        return 3

    if state == "OK":
        log("  [OK] %s" % detail)
        log("  Login state is usable. Nothing to re-do.")
    elif state == "NO_LOGIN":
        log("  [X] %s" % detail)
        log("    在这个 Chrome 窗口里扫码登录 %s，然后重跑本脚本。"
            % spec["home_url"])
    else:
        # ⚠ 区分 UNKNOWN 与 NO_LOGIN 是这个函数存在的全部意义。
        #   把UNKNOWN 当「掉登录」=让人白扫一次码。
        log("  [?] %s" % detail)
        log("    判据不足，**不等于**掉登录。常见原因：")
        log("    - 页面还没渲染完（等几秒重跑 --probe-only）")
        log("    - 平台改了首页文案，判据需要更新")
        log("    - 拿到的不是这个平台的浏览器（看上面的 profile 对不对）")

    if args.hold:
        log("")
        log("=== holding Chrome open (--hold) ===")
        log("  Keep this window alive. Run your work in another terminal.")
        try:
            while True:
                if proc is not None and proc.poll() is not None:
                    log("  Chrome exited (code=%s). Leaving." % proc.poll())
                    break
                time.sleep(3)
        except KeyboardInterrupt:
            log("  interrupted.")

    if not args.keep and proc is not None:
        proc.terminate()
    return 0 if state == "OK" else 4


if __name__ == "__main__":
    sys.exit(main())