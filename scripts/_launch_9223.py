"""以 DETACHED 方式起 9223 Chrome（chrome-debug-profile），供头条/视频号等复用。

⚠ 为什么还要 --hold
------------------
Chrome 虽然是 DETACHED 起的，但在「受管后台任务」里跑时，任务一结束整个进程组
会被回收（实测 30 秒内 9223 即 10061/refused）。所以把启动器放在受管任务里跑时
必须带 `--hold <秒>`，用一段 sleep 把任务本身撑住：

    python scripts/_launch_9223.py --hold 3600     # 后台任务，保活 1 小时

不带 --hold 就是老行为（起完立即返回），只适合在真实终端里手动执行。

为什么不用脚本内起 Chrome
------------------------
_autostart_chrome_if_needed 起的是当前 python 子进程，脚本一退出 Chrome 就被
回收（10061）。DETACHED_PROCESS 让 Chrome 独立存活，后续多次 publish 子进程
复用（它们内部 _autostart 检测到 9223 已起会直接复用）。
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import chrome_cdp_launch as L

PROFILE = L.PLAT_PROFILE
PORT = L.PLAT_PORT
DETACHED = 0x00000008  # DETACHED_PROCESS


def port_alive(port):
    """CDP 端口是否已监听（带 2s 硬超时，不吃系统代理）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % port, timeout=2).read(64)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=0,
                    help="起完后保持任务存活 N 秒（受管后台任务必填，否则 Chrome 被回收）")
    args = ap.parse_args()

    L.clear_proxy()
    if port_alive(PORT):
        print("[ok] %d 已在运行，复用（不重启）" % PORT, flush=True)
    else:
        L.remove_lock(PROFILE)
        chrome, label = L.pick_chrome()
        if chrome is None:
            print("✗ 找不到 Chrome", flush=True)
            return 1
        launch_args = L.build_args(chrome, PROFILE, port=PORT)
        print("launch: %s" % " ".join(launch_args[:4]), flush=True)
        proc = subprocess.Popen(
            launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=DETACHED)
        if not L.wait_cdp(proc, timeout=45, port=PORT):
            print("✗ %d 启动失败（Chrome exit=%s）" % (PORT, proc.poll()), flush=True)
            return 1
        print("[ok] %d 已常驻启动（%s, detached）profile=%s"
              % (PORT, label, PROFILE), flush=True)

    if args.hold > 0:
        print("[hold] 保持任务存活 %ds（Ctrl+C 即回收 Chrome）" % args.hold, flush=True)
        try:
            time.sleep(args.hold)
        except KeyboardInterrupt:
            pass
        print("[hold] 到时退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
