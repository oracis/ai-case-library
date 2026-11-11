"""以 DETACHED 方式起 9222 公众号 Chrome（ChromeCDP profile），跨脚本常驻。

背景（2026-10-16）：公众号 refresh 走 `_autostart_chrome_if_needed`，起的是
**当前 python 的子进程** —— 脚本被回收时 Chrome 一起死（ConnectionReset /
10061），所以每批跑不到 1~2 篇就被掐断（有头/无头都一样）。

而小红书/头条早就用 `_launch_9223.py`（DETACHED_PROCESS）把 9223 Chrome
**独立常驻**，publish 脚本只"连"不"起"，故任务被回收也不影响 Chrome，
能连着跑很久。公众号缺的就是这一步。

本脚本同法起 9222：Chrome 独立存活，refresh 内部 `_autostart_chrome_if_needed`
检测到 9222 已起会直接 `return True` 复用；即使 refresh 任务被杀，Chrome 仍在，
重连即可续跑。

⚠ 有头（headed）启动：公众号封面替换要弹"裁剪确认框"，无头下该按钮点不着
（CONFIRM_ERR，封面设不上），故这里强制 headless=False。

⚠ 沙箱/受管任务里 Chrome 会随「发起任务退出」被整组回收（2026-10-18 实测：
脚本打印 [ok] 后 30 秒内 9222 就 DOWN）。自动化场景请加 `--hold 1800`：
本进程保持存活，Chrome 就不会被回收；手动双击 bat 时不需要。
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chrome_cdp_launch as L

PORT = 9222
PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP")
DETACHED = 0x00000008  # DETACHED_PROCESS


def _probe(port):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % port, timeout=3) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=0,
                    help="启动后保持本进程存活 N 秒（受管任务里防 Chrome 被回收）")
    args = ap.parse_args()

    L.clear_proxy()
    if _probe(PORT):
        print("[ok] 9222 已在运行，复用（不重启）", flush=True)
        return 0
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    if chrome is None:
        print("✗ 找不到 Chrome", flush=True)
        return 1
    args_list = L.build_args(chrome, PROFILE, port=PORT, headless=False)  # 有头
    print("launch: %s" % " ".join(args_list[:4]), flush=True)
    proc = subprocess.Popen(
        args_list, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, close_fds=True, creationflags=DETACHED)
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        print("✗ 9222 启动失败（Chrome exit=%s）" % proc.poll(), flush=True)
        return 1
    print("[ok] 9222 已常驻启动（%s, detached, headed）profile=%s"
          % (label, PROFILE), flush=True)
    if args.hold > 0:
        print("[hold] 本进程保持 %d 秒（防止 Chrome 随任务退出被回收）"
              % args.hold, flush=True)
        try:
            time.sleep(args.hold)
        except KeyboardInterrupt:
            pass
        print("[hold] 到期退出（Chrome 仍在运行，可被后续任务复用）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
