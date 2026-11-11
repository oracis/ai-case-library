#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立只读回读：公众号已发表列表按标题命中《StartClaw…》。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import wechat_publish as wp  # noqa: E402
import wechat_sync as ws  # noqa: E402

wp._autostart_chrome_if_needed(port=9222)
cdp = wp.CDP(9222)
_tid, tok = wp._connect_mp(cdp)
if not tok:
    raise SystemExit("✗ 拿不到 token，无法回读")
pubs, ok = ws.fetch_published_remote(cdp, tok)
print("fetch_ok=%s total=%d token=%s" % (ok, len(pubs), tok))
hits = [p for p in pubs if "StartClaw" in (p.get("title") or "")]
for p in hits:
    print("HIT", {k: p.get(k) for k in
                  ("title", "appmsg_id", "appmsgid", "update_time", "create_time")})
print("TITLE_EXACT_MATCH", bool(hits))
print("REMOTE_ID_2247485035", any(
    "2247485035" in str(p.get("appmsg_id") or p.get("appmsgid") or "")
    for p in pubs))
