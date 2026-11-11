#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令内完成：起有头 9222 → 等人工扫码登录 → 跑 wechat_fifo --go。

为什么需要包装（2026-11-08）：本机 Chrome 随命令结束被整组回收，
`wechat_fifo --go` 的 read_remote() 拿不到 token 会直接 SystemExit，
不会等人扫码。所以登录与发表必须在同一条前台命令的进程树内完成。
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
PY = sys.executable

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ["CHROME_HEADED"] = "1"

import chrome_cdp_launch as L  # noqa: E402
import wechat_publish as wp  # noqa: E402

PORT = 9222
profile = os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP")
chrome, label = L.pick_chrome()
print("Chrome:", label, flush=True)
L.remove_lock(profile)
args = L.build_args(chrome, profile, port=PORT, headless=False)
proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
if not L.wait_cdp(proc, timeout=45, port=PORT):
    print("✗ 9222 没起来，停止（不发布）")
    sys.exit(2)
print("9222 已在线（有头窗口）", flush=True)

cdp = wp.CDP(PORT)
t = cdp.new_target("https://mp.weixin.qq.com/")
tid = t.get("id") or t.get("targetId")
cdp.connect_target(tid)
cdp.send("Page.enable")


def token_now():
    try:
        href = cdp.eval("location.href", refresh_context=True) or ""
    except Exception:
        return None, ""
    m = re.search(r"[?&]token=(\d+)", href)
    return (m.group(1) if m else None), href


tok, href = token_now()
print("初始 href:", href[:140], "| token:", tok, flush=True)
if not tok:
    print("⚠ 请在弹出的 Chrome 窗口里【用微信扫码登录公众号后台】；"
          "登录后自动继续（最多等 180 秒）", flush=True)
    end = time.time() + 180
    while time.time() < end:
        time.sleep(3)
        tok, href = token_now()
        if tok:
            break
    if not tok:
        print("✗ 等待超时，未登录 ⇒ 不发布（href=%s）" % href[:140])
        sys.exit(3)
print("✅ 已登录 token=%s" % tok, flush=True)

print("→ 开始 FIFO 对账 + 发表；终点会再弹一次【管理员微信扫码验证】，"
      "请再扫一次", flush=True)
rc = subprocess.call(
    [PY, "-X", "utf8", os.path.join(ROOT, "scripts", "wechat_fifo.py"),
     "--go", "--timeout", "300"], cwd=ROOT)
print("FIFO_EXIT=%s" % rc, flush=True)
sys.exit(rc)
