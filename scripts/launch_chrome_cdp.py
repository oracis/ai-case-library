"""Launch a detached Chrome with CDP on port 9222, detached from the caller's process tree."""
import subprocess
import sys
import time
import urllib.request

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\DELL\chrome-debug-profile"
ARGS = [
    CHROME,
    "--remote-debugging-port=9222",
    "--user-data-dir=" + PROFILE,
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-allow-origins=*",
    "about:blank",
]


def probe():
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open("http://127.0.0.1:9222/json/version", timeout=4) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        return None


if __name__ == "__main__":
    if "--kill" in sys.argv:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], check=False)
        time.sleep(2)
        print("killed")
        sys.exit(0)

    cur = probe()
    if cur:
        print("ALREADY_RUNNING")
        print(cur[:200])
        sys.exit(0)

    subprocess.Popen(ARGS, creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(1)
        cur = probe()
        if cur:
            print("CDP_OK")
            print(cur[:300])
            sys.exit(0)
    print("CDP_FAIL")
    sys.exit(1)