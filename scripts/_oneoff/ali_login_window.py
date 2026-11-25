"""只读诊断/登录辅助：起有头 Chrome(9228) 打开阿里云写页，轮询报告登录态，不写任何台账。

用法（必须 CHROME_HEADED=1，且在一条命令内完成）：
  CHROME_HEADED=1 python -u scripts/_oneoff/ali_login_window.py --hold 600
"""
import argparse
import subprocess
import sys
import time

sys.path.insert(0, "scripts")
import chrome_cdp_launch as L  # noqa: E402
import yun_draft_cdp as Y  # noqa: E402

EDITOR_URL = "https://developer.aliyun.com/article/new"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=600, help="保持窗口秒数")
    ap.add_argument("--port", type=int, default=9228)
    a = ap.parse_args()

    L.clear_proxy()
    L.remove_lock(Y.PROFILE)
    chrome, _ = L.pick_chrome()
    proc = subprocess.Popen(
        L.build_args(chrome, Y.PROFILE, port=a.port, headless=False),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    if not L.wait_cdp(proc, timeout=60, port=a.port):
        print("CDP_FAIL 起不来端口", a.port, flush=True)
        return 2

    cdp = Y._CDP(a.port)
    cdp.open(EDITOR_URL)
    time.sleep(6)
    print("已打开:", cdp.eval("location.href"), flush=True)
    print(">>> 请在有头 Chrome 窗口扫码登录阿里云，登录后会自动回到编辑器页 <<<", flush=True)

    deadline = time.time() + a.hold
    last = ""
    while time.time() < deadline:
        try:
            url = cdp.eval("location.href") or ""
            has_title = cdp.eval("!!document.querySelector('#title')")
        except Exception as e:  # 页面跳转期间 context 可能失效
            url, has_title = f"<eval_err {type(e).__name__}>", False
        state = "EDITOR_OK" if ("article/new" in str(url) and has_title) else "LOGIN_OR_LOADING"
        line = f"{time.strftime('%H:%M:%S')} {state} title={has_title} url={str(url)[:90]}"
        if line[9:] != last:
            print(line, flush=True)
            last = line[9:]
        if state == "EDITOR_OK":
            print("LOGIN_READY", flush=True)
            break
        time.sleep(5)

    print("WINDOW_HOLD_END（窗口即将关闭，若已登录请尽快告知继续）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
