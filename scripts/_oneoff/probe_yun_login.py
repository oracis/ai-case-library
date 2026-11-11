#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：重开 9228 / chrome-yun-profile 后，阿里云到底还是不是登录态。

不写任何平台数据、不发布、不改 cookie。只回答：
  1. 磁盘 profile 重开后访问 developer.aliyun.com 是否仍登录；
  2. 访问 /article/new 是否被踢到 passport 登录页；
  3. 当前浏览器里阿里云登录票据是 session cookie 还是持久 cookie。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y  # noqa: E402


def read_page(cdp, url, wait=7):
    cdp.open(url)
    time.sleep(wait)
    href = cdp.eval("location.href")
    title = cdp.eval("document.title")
    body = cdp.eval("(document.body ? (document.body.innerText||'').slice(0,600) : '')")
    return href, title, body or ""


def main():
    proc, cdp = Y._connect()
    print("9228 =", "本次新起" if proc is not None else "复用已有实例")
    try:
        for url in ("https://developer.aliyun.com/",
                    "https://developer.aliyun.com/article/new"):
            href, title, body = read_page(cdp, url)
            print("\n=== 打开", url, "===")
            print("最终 URL =", href)
            print("title    =", title)
            marks = [w for w in ("登录", "请登录", "立即登录", "扫码登录",
                                 "免费注册", "退出", "个人中心", "内容审核")
                     if w in body]
            print("关键文案 =", marks)
            print("正文片段 =", repr(body[:160].replace("\n", " ")))

        r = cdp.send("Network.enable")
        r = cdp.send("Network.getAllCookies")
        cookies = (r.get("result") or {}).get("cookies") or []
        ali = [c for c in cookies if "aliyun" in (c.get("domain") or "")]
        print("\n=== cookie（浏览器内存视角，共 %d 条 aliyun） ===" % len(ali))
        keys = ("login_aliyunid_ticket", "login_aliyunid", "hssid",
                "cr_token", "isg", "tfstk", "cna", "aliyun_site",
                "login_current_pk")
        for k in keys:
            hit = [c for c in ali if c.get("name") == k]
            if not hit:
                print("  %-24s MISSING" % k)
                continue
            c = hit[0]
            print("  %-24s domain=%-28s expires=%s session=%s len=%d"
                  % (k, c.get("domain"), c.get("expires"),
                     c.get("session"), len(c.get("value") or "")))
    finally:
        try:
            cdp.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
