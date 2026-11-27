#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：小红书笔记管理页按标题回读刚发的稿（独立双回读）。

2026-11-26 续发：xhs send-draft 自报 published 但「没对上案例 id 未记账」，
用现成 xhs_publish._readback_published 去笔记管理页按标题命中才算证据。

用法：
    python scripts/_oneoff/xhs_verify_publish.py --title 上传房间照片换墙换地板
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PORT = 9223
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-debug-profile")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    args = ap.parse_args()

    import chrome_cdp_launch as cl
    import wechat_publish as wp
    import xhs_publish as x
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    wp._autostart_chrome_if_needed(PORT, profile=PROFILE)
    cdp = wp.CDP(PORT)
    # 笔记管理页用独立 tab 做回读，避免复用发布 tab 的陈旧 DOM
    tid = cdp.new_target(x.NOTE_MANAGER_URL)["id"]
    sub = wp.CDP(PORT)
    sub.connect_target(tid)
    ok = x._readback_published(sub, args.title, timeout=40)
    print("已发布列表按标题 %r 命中: %s" % (args.title, "PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
