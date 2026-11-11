#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次浏览器会话读齐 CSDN 四个 tab（草稿箱/全部/已发布/回收站），只读不改。"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import chrome_cdp_launch as L          # noqa: E402
import csdn_publish as C               # noqa: E402
import csdn_list_drafts as LD          # noqa: E402
import wechat_publish as wp            # noqa: E402

PORT = 9224
STATUSES = ["draft", "all_v3", "publish", "deleted"]


def main():
    L.clear_proxy()
    L.remove_lock(C.LOGIN_PROFILE)
    chrome, _ = L.pick_chrome()
    args = L.build_args(chrome, C.LOGIN_PROFILE, port=PORT, headless=False)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    all_rows = {}
    try:
        if not L.wait_cdp(proc, timeout=45, port=PORT):
            raise SystemExit("CDP not up")
        time.sleep(2)
        cdp = wp.CDP(PORT)
        tid = cdp.new_target(LD.URL)["id"]
        cdp.connect_target(tid)
        cdp.send("Page.enable", {})
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": LD.HOOK})
        cdp.eval("location.reload()")
        print("等首屏 22s", flush=True)
        time.sleep(22)
        for st in STATUSES:
            tab = LD.TAB_NAMES[st]
            r = str(cdp.eval("(%s)(%s)" % (LD.RECT_JS, json.dumps(tab))))
            if r == "NOT_FOUND":
                all_rows[st] = []
                print("=== %-9s TAB NOT FOUND" % st, flush=True)
                continue
            p = json.loads(r)
            for t in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent",
                         {"type": t, "x": p["x"], "y": p["y"],
                          "button": "left", "clickCount": 1})
            time.sleep(10)
            rows = json.loads(str(cdp.eval("(%s)(%s)" % (LD.PICK_JS, json.dumps(st)))))
            all_rows[st] = rows
            print("=== %-9s %d 条" % (st, len(rows)), flush=True)
            for it in rows:
                print("   %-10s st=%-3s views=%-6s cover=%-5s %s"
                      % (it["id"], it["status"], it.get("views") or "-",
                         "有" if it["cover"] else "无", (it["title"] or "")[:44]),
                      flush=True)
    finally:
        proc.terminate()
    with open(os.path.join(ROOT, "tmp", "csdn_audit.json"), "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print("\n已写 tmp/csdn_audit.json")


if __name__ == "__main__":
    main()
