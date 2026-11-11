#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""试验：把日常 profile 的 Cookies + Local State 复制成种子 profile，
看 Chrome 能否解开 v20 cookie（能的话签到就永远不用人工登录）。

只读源 profile，只写目标 seed 目录。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

LOCAL = os.environ.get("LOCALAPPDATA") or r"C:\Users\DELL\AppData\Local"
SRC = os.path.join(LOCAL, "Google", "Chrome", "User Data")
DST = r"C:\Users\DELL\chrome-ms-seed"
CHROME = os.path.join(LOCAL, r"Google\ChromeForTesting\chrome-win64\chrome.exe")
PORT = 9335


def copy_file(rel_dst, rel_src):
    s = os.path.join(SRC, rel_src)
    d = os.path.join(DST, rel_dst)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    try:
        shutil.copy2(s, d)
        return "copy2 ok"
    except OSError as e:
        # Chrome 占用中：改走字节读 / sqlite backup
        try:
            with open(s, "rb") as f:
                data = f.read()
            with open(d, "wb") as f:
                f.write(data)
            return "raw read ok (%d B)" % len(data)
        except OSError as e2:
            if s.endswith("Cookies"):
                src = sqlite3.connect("file:%s?mode=ro"
                                      % s.replace("\\", "/"), uri=True)
                try:
                    dst = sqlite3.connect(d)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                return "sqlite backup ok"
            return "FAIL %s / %s" % (e, e2)


def main():
    print("[seed] 复制 Cookies / Local State")
    print("  Local State  ->", copy_file("Local State", "Local State"))
    print("  Cookies      ->", copy_file(
        os.path.join("Default", "Network", "Cookies"),
        os.path.join("Default", "Network", "Cookies")))
    for extra in ("Preferences",):
        p = os.path.join("Default", extra)
        if os.path.exists(os.path.join(SRC, p)):
            print("  %-12s -> %s" % (extra, copy_file(p, p)))

    lock = os.path.join(DST, "Default", "LOCK")
    if os.path.exists(lock):
        try:
            os.remove(lock)
        except OSError:
            pass

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        env.pop(k, None)

    args = [CHROME, "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % DST, "--no-first-run",
            "--no-default-browser-check", "--no-sandbox",
            "--headless=new", "--disable-gpu", "about:blank"]
    proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    ok = False
    end = time.time() + 40
    while time.time() < end:
        if proc.poll() is not None:
            print("[seed] Chrome 早退 code=%s" % proc.poll())
            break
        try:
            op.open("http://127.0.0.1:%d/json/version" % PORT, timeout=2).read()
            ok = True
            break
        except Exception:
            time.sleep(1)
    print("[seed] CDP ready =", ok)
    if not ok:
        sys.exit(1)

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import wechat_publish as wp   # 本地导入：复用其 CDP 封装连 seed profile

    cdp = wp.CDP(port=PORT)
    t = cdp.new_target("https://www.modelscope.cn/magicube/usage")
    tid = t.get("id") or t.get("targetId")
    cdp.connect_target(tid)
    time.sleep(7)
    js_probe = """
    (function () {
      var t = document.body ? document.body.innerText : '';
      var loginBtn = /登录\\s*\\/\\s*注册|登录注册/.test(t);
      var m = t.match(/我的魔粒\\s*\\n?\\s*([0-9][0-9,]*)/);
      return JSON.stringify({loginBtn: loginBtn, magicube: m ? m[1] : null,
                             head: t.slice(0, 120)});
    })()
    """
    print("[seed] 页面判据:", cdp.eval(js_probe))
    cdp.close_browser()
    time.sleep(1)
    if proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/PID", str(proc.pid), "/T"],
                       check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    print("[seed] done")


if __name__ == "__main__":
    main()
