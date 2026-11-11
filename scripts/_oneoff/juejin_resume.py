#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回电脑后一条命令收尾掘金：等登录 → 验身份 → 存草稿 → 列出。

为什么单独做一个：人在外面只有手机时，掘金登录走不通（需要本机 Chrome
抓 HttpOnly cookie）。回到电脑后，本来要敲四条命令，这里合成一条。

**不自动发布** —— 发布是整条链路里唯一不可逆的动作，留给人确认。

用法：
    python scripts/_oneoff/juejin_resume.py                 # 默认等你 900 秒登录
    python scripts/_oneoff/juejin_resume.py --wait 300
    python scripts/_oneoff/juejin_resume.py --now           # 已登录，跳过等待
    python scripts/_oneoff/juejin_resume.py --publish       # 存完草稿直接发布（慎用）
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JJ = os.path.join(ROOT, "scripts", "juejin_publish.py")
PORT = 9223
ARTICLE = os.path.join("out", "articles", "mcdpoints.md")


def run(*args, **kw):
    cmd = [sys.executable, os.path.relpath(JJ, ROOT)] + list(args)
    print("\n$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT, **kw)


def chrome_alive(port=PORT):
    try:
        r = urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=5)
        return json.loads(r.read().decode("utf-8")).get("Browser", "?")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="回电脑后收尾掘金：等登录→存草稿")
    ap.add_argument("--wait", type=int, default=900, help="等你登录的最长秒数")
    ap.add_argument("--now", action="store_true", help="已登录，跳过等待")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--tags", nargs="*", default=["GitHub"])
    ap.add_argument("--publish", action="store_true", help="存完草稿直接真发布")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(ROOT, ARTICLE)):
        raise SystemExit("✗ 找不到 %s，先跑 make_article" % ARTICLE)

    if args.now:
        rc = run("whoami")
        if rc != 0:
            print("\n→ cookie 无效，改走登录流程")
            args.now = False

    if not args.now:
        b = chrome_alive(args.port)
        if not b:
            print("✗ %d 端口那个调试 Chrome 没跑起来。先另开一个终端："
                  % args.port)
            print("    python scripts/_launch_9223.py")
            print("  然后在这个浏览器里登录 https://juejin.cn/ ，再重跑本脚本。")
            return 1
        print("✓ 调试 Chrome（%s）在 %d 端口" % (b, args.port))
        print("\n>>> 请在那个浏览器里登录掘金 <<<")
        print("    登录后脚本会在 5 秒内自动检测到，不用回来敲回车。\n")
        rc = run("login", "--port", str(args.port), "--wait", str(args.wait))
        if rc != 0:
            return rc

    rc = run("draft", "--file", ARTICLE, "--tags", *args.tags, "--replace")
    if rc != 0:
        return rc
    run("list")

    print("\n" + "=" * 60)
    if args.publish:
        print("下一步（你已要求直接发布）：")
        print("  python scripts/juejin_publish.py publish --case mcdpoints")
    else:
        print("草稿已就位，**还没有公开发布**。确认后执行：")
        print("  python scripts/juejin_publish.py publish --case mcdpoints")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
