"""通用 CDP 启动器：任意端口 + 任意 profile（第二个/第三个账号用）。

为什么不用 `_launch_9222.py` / `_launch_9223.py`
------------------------------------------------
那两个是**写死端口 + 写死 profile** 的历史脚本。多账号场景下每加一个号
就要复制一份脚本、还容易把 profile 和端口配错（症状：CDP 连得上、页面
能开，但被重定向到登录页 —— 其实是开成了另一个号的浏览器）。

⚠ 一个账号一个 profile、一个 profile 一个端口，**绝不共用**：
Chrome 的 profile 目录同时承载 cookie 与 localStorage，两个号共用一个
profile 必然互相顶掉登录态。

⚠ 受管后台任务里的 `--hold` 同样是硬要求：不带它，任务一结束整个进程组
被回收，30 秒内端口就 refused。

用法
----
    # 起第二个 B 站号的浏览器（profile 缺省 = %USERPROFILE%\\chrome-bili2-profile）
    python scripts/_launch_cdp.py --port 9224 --name bili2 --hold 10800

    # 起完顺手打开某页面（方便直接扫码）
    python scripts/_launch_cdp.py --port 9224 --name bili2 --open https://passport.bilibili.com/login

    # 只看现在有哪些端口活着
    python scripts/_launch_cdp.py --list
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import chrome_cdp_launch as L  # noqa: E402

DETACHED = 0x00000008  # DETACHED_PROCESS
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def port_alive(port):
    """CDP 端口是否在监听（2s 硬超时，绕开系统代理）。"""
    try:
        OPENER.open("http://127.0.0.1:%d/json/version" % port, timeout=2).read(64)
        return True
    except Exception:
        return False


def profile_of(name):
    """把 `--name` 映射成 profile 目录。目录名带项目前缀，便于一眼认出用途。"""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "chrome-%s-profile" % name)


def open_url(port, url):
    """在已有 CDP 上开新标签页（/json/new 需要 PUT）。"""
    req = urllib.request.Request(
        "http://127.0.0.1:%d/json/new?%s" % (port, url), method="PUT")
    try:
        r = OPENER.open(req, timeout=8)
        d = json.loads(r.read().decode("utf-8", "replace"))
        return d.get("id")
    except Exception as e:
        print("[warn] 开标签页失败：%s" % e, flush=True)
        return None


def main():
    ap = argparse.ArgumentParser(description="通用 CDP 启动器（多账号）")
    ap.add_argument("--port", type=int, help="CDP 端口，如 9224")
    ap.add_argument("--name", default="", help="账号代号（决定 profile 目录名）")
    ap.add_argument("--profile", default="", help="直接给 profile 绝对路径")
    ap.add_argument("--open", default="", help="起完后打开的 URL")
    ap.add_argument("--hold", type=int, default=0,
                    help="保持任务存活 N 秒（受管后台任务必填）")
    ap.add_argument("--list", action="store_true", help="列出 9222–9230 的存活情况")
    a = ap.parse_args()

    if a.list:
        for p in range(9222, 9231):
            print("  %d %s" % (p, "✓ 在跑" if port_alive(p) else "· 未启动"))
        return 0
    if not a.port:
        raise SystemExit("缺 --port（或用 --list 看现状）")
    profile = a.profile or profile_of(a.name or ("cdp%d" % a.port))
    # ⚠ 必须展开 %USERPROFILE% / %LOCALAPPDATA%（2026-10-31 实测踩到）：
    #   在 Git Bash 里 `--profile "%USERPROFILE%\x"` 传给 Python 的是**字面串**
    #   （bash 不认 Windows 的 %VAR% 语法），不展开就会在 cwd 下建一个名叫
    #   `%USERPROFILE%` 的目录 —— Chrome 用空 profile 启动，看着「起成功了」，
    #   实际没有任何登录态，症状是打开后台全是未登录页面。
    profile = os.path.expandvars(os.path.expanduser(profile))

    L.clear_proxy()
    if port_alive(a.port):
        print("[ok] %d 已在运行，复用（不重启）" % a.port, flush=True)
    else:
        L.remove_lock(profile)
        chrome, label = L.pick_chrome()
        if chrome is None:
            raise SystemExit("✗ 找不到 Chrome")
        argv = L.build_args(chrome, profile, port=a.port)
        print("launch: %s" % " ".join(argv[:5]), flush=True)
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=DETACHED)
        if not L.wait_cdp(proc, timeout=45, port=a.port):
            raise SystemExit("✗ %d 启动失败（Chrome exit=%s）"
                             % (a.port, proc.poll()))
        print("[ok] %d 已常驻（%s, detached）\n     profile=%s"
              % (a.port, label, profile), flush=True)

    if a.open:
        tid = open_url(a.port, a.open)
        print("[open] %s → target=%s" % (a.open, tid), flush=True)

    if a.hold > 0:
        print("[hold] 保持 %ds（Ctrl+C 即回收）" % a.hold, flush=True)
        try:
            time.sleep(a.hold)
        except KeyboardInterrupt:
            pass
        print("[hold] 到时退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
