#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条「作品管理」到底发了几篇同一标题？——数列表接口响应里的命中次数。

背景：`mcd_toutiao_draft.py publish` 被我误跑了两次（第一次因 stdout 缓冲被
误判成卡死而 timeout 杀掉），需要确认有没有发重。页面是 SPA，读 DOM 拿不到，
所以直接蹭页面自己发的那个列表请求（`mcs.zijieapi.com/list`），
把响应体捞出来数标题出现次数。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from juejin_publish import _CDP  # noqa: E402

KEY = "我的积分正在悄悄过期"
URL = "https://mp.toutiao.com/profile_v4/manage/content?type=article"


def main():
    cdp = _CDP(9223)
    try:
        cdp.open(URL)
        ws = cdp.ws
        cdp.send("Network.enable")
        cdp.send("Page.reload", {"ignoreCache": True})
        meta, done = {}, []
        t0 = time.time()
        while time.time() - t0 < 35:
            try:
                frame = ws.recv_text()
            except Exception:
                break
            try:
                m = json.loads(frame)
            except ValueError:
                continue
            meth = m.get("method")
            if meth == "Network.requestWillBeSent":
                rid = m["params"]["requestId"]
                meta[rid] = m["params"]["request"]
            elif meth == "Network.loadingFinished":
                rid = m["params"]["requestId"]
                done.append(rid)
                r = meta.get(rid) or {}
                u = r.get("url", "")
                if "zijieapi.com/list" not in u and "agw" not in u:
                    continue
                try:
                    resp = cdp.send("Network.getResponseBody", {"requestId": rid})
                    body = resp.get("result", {}).get("body", "")
                except Exception as e:
                    print("  (取体失败 %s)" % e)
                    continue
                n = body.count(KEY)
                print("命中 %d 次 <- %s" % (n, u.split("?")[0]))
                if n:
                    import re
                    for m2 in re.finditer(KEY, body):
                        s = body[max(0, m2.start() - 300): m2.start() + 60]
                        print("  片段：%s" % s.replace("\\n", " ")[-320:])
                    break
        else:
            print("✗ 35 秒内没等含标题的列表响应，手动去后台看一眼")
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
