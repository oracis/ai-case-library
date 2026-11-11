#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓头条创作后台「作品管理」列表页真实调用的 API。

页面是 SPA，`page.evaluate` 读 innerText 只能拿到导航条（217 字），
所以改成监听 Network 请求：列表一定是某个 XHR 拉回来的，拿到 URL 和
Cookie 后就能直接问接口，不用再跟 DOM 较劲。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from juejin_publish import _CDP, _WS  # noqa: E402

URL = "https://mp.toutiao.com/profile_v4/manage/content?type=article"


def main():
    cdp = _CDP(9223)
    try:
        cdp.open(URL)
        ws = cdp.ws
        cdp.send("Network.enable")
        cdp.send("Page.enable")
        cdp.send("Page.reload", {"ignoreCache": False})
        hits = []
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                frame = ws.recv_text()
            except Exception:
                break
            m = json.loads(frame)
            if m.get("method") != "Network.requestWillBeSent":
                continue
            req = m["params"]["request"]
            u = req.get("url", "")
            if req.get("method") == "POST" and ("list" in u or "content" in u or "article" in u):
                hits.append(u)
            elif req.get("method") == "GET" and "mp.toutiao.com" in u and ("list" in u or "content" in u):
                hits.append(u)
        seen = []
        for u in hits:
            base = u.split("?")[0]
            if base not in seen:
                seen.append(base)
        print("候选列表 API（%d 个）：" % len(seen))
        for u in seen[:20]:
            print("  %s" % u)
        if not seen:
            print("✗ 没抓到；把 30 秒放宽或先手动开一次作品管理页")
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
