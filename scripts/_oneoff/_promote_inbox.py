#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 inbox 深核条目晋升进候选池（自签 cookie，免明文密码）。一次性工具。"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth  # noqa: E402
import ai_verify as AV  # noqa: E402

ADMIN_BASE = os.environ.get("CASE_LIB_ADMIN_BASE", "http://127.0.0.1:5053")

os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"

# 深核队列头部：今天新货 7 + 存量头部 5
IDS = [
    "nutria-9e3eaa",
    "fastwrite-io",
    "downconvert-sketchup-files",
    "macwall",
    "growingupvideo",
    "homy",
    "stealth-rdp",
    "white-glove-content",
    "boothie-ai-photo-editor",
    "pet-plus-us",
    "wagiff-app",
    "quotedeck",
]


def main():
    cfg = auth.load_admin()
    token = auth.make_token(cfg["secret"])
    cookie = auth.cookie_header(token)
    for cid in IDS:
        req = urllib.request.Request(
            ADMIN_BASE + "/api/inbox/%s/to-candidates" % cid,
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            new_id = (d.get("candidate") or {}).get("id")
            print("OK  %-28s -> candidate id: %s" % (cid, new_id))
        except urllib.error.HTTPError as e:
            print("FAIL %-28s HTTP %s %s" % (cid, e.code,
                  e.read().decode("utf-8", "replace")[:80]))
        except Exception as e:  # noqa: BLE001
            print("FAIL %-28s %s" % (cid, e))


if __name__ == "__main__":
    main()
