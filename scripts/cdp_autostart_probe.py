"""诊断：为什么 `_autostart_chrome_if_needed()` 没把 Chrome 拉起来。

用法：python -u -X utf8 scripts/cdp_autostart_probe.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 9223


def probe(tag):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/json/version" % PORT, timeout=4) as r:
            j = r.read().decode("utf-8", "replace")
        print("[%s] CDP 通：%s" % (tag, j[:90]))
        return True
    except Exception as exc:                # noqa: BLE001
        print("[%s] CDP 不通：%s" % (tag, exc))
        return False


def chrome_count():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                             capture_output=True, text=True, timeout=20).stdout
        return sum(1 for ln in out.splitlines()
                   if ln.lower().startswith("chrome.exe"))
    except Exception:                       # noqa: BLE001
        return -1


def main():
    import chrome_cdp_launch as cl
    import wechat_publish as wp
    import bilibili_publish as bp

    print("PLAT_PROFILE =", getattr(cl, "PLAT_PROFILE", "?"))
    print("PUB_PORT =", bp.PUB_PORT)
    print("初始 chrome 进程数 =", chrome_count())
    probe("before")

    print("\n--- 调 _autostart_chrome_if_needed ---")
    try:
        r = wp._autostart_chrome_if_needed(bp.PUB_PORT,
                                           profile=cl.PLAT_PROFILE)
        print("返回:", r)
    except Exception as exc:                # noqa: BLE001
        import traceback
        traceback.print_exc()
        r = None
    time.sleep(3)
    print("起后 chrome 进程数 =", chrome_count())
    probe("after")

    print("\n--- 再等 8 秒 ---")
    time.sleep(8)
    print("chrome 进程数 =", chrome_count())
    if not probe("after-wait"):
        print("\n--- LOCK 情况 ---")
        prof = getattr(cl, "PLAT_PROFILE", "")
        lock = os.path.join(prof, "Default", "LOCK")
        print("profile:", prof)
        print("LOCK 存在:", os.path.exists(lock))
        if os.path.exists(lock):
            try:
                os.rename(lock, lock + ".stale-%d" % int(time.time()))
                print("已用 os.rename 挪走残留 LOCK")
            except Exception as exc:        # noqa: BLE001
                print("挪走失败:", exc)
        print("\n--- 再试 autostart ---")
        try:
            print("返回:", wp._autostart_chrome_if_needed(
                bp.PUB_PORT, profile=cl.PLAT_PROFILE))
        except Exception as exc:            # noqa: BLE001
            import traceback
            traceback.print_exc()
        time.sleep(5)
        print("chrome 进程数 =", chrome_count())
        probe("final")
    return 0


if __name__ == "__main__":
    sys.exit(main())
