"""Launch Chrome with CDP, run a child script against it, then clean up.

The sandbox reaps the whole process tree when the calling shell exits, so a
detached Chrome cannot outlive one Bash call. This wrapper keeps launch,
work and teardown inside a single invocation.

Usage:
    python scripts/cdp_session.py [--keep] <script.py> [args...]
"""
import os
import subprocess
import sys
import time
import urllib.request

PY = sys.executable
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\DELL\chrome-debug-profile"
DETACHED = 0x00000008 | 0x00000200
ARGS = [
    CHROME,
    "--remote-debugging-port=9222",
    "--user-data-dir=" + PROFILE,
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-allow-origins=*",
    "about:blank",
]

ENV = dict(os.environ)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    ENV.pop(k, None)
ENV["NO_PROXY"] = "127.0.0.1,localhost"
ENV["no_proxy"] = "127.0.0.1,localhost"


def probe():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:9222/json/version", timeout=4) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def main():
    keep = "--keep" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--keep"]
    if not argv:
        print("usage: cdp_session.py [--keep] <script.py> [args...]")
        return 2

    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    subprocess.Popen(ARGS, creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

    for _ in range(25):
        time.sleep(1)
        if probe():
            print("[cdp_session] CDP ready on 9222", flush=True)
            break
    else:
        print("[cdp_session] CDP failed to come up")
        return 1

    try:
        rc = subprocess.call([PY, "-X", "utf8"] + argv, env=ENV)
    finally:
        if not keep:
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[cdp_session] chrome stopped")
    return rc


if __name__ == "__main__":
    sys.exit(main())