# -*- coding: utf-8 -*-
"""核实 B站 aid=342403《Insect Bite ID》是否真的已发表（空间公开列表）。"""
from __future__ import annotations

import os
import sys

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chrome_cdp_launch as cl  # noqa: E402
import wechat_publish as wp  # noqa: E402
import bili_draft_api as api  # noqa: E402

PORT = 9223


def main():
    wp._autostart_chrome_if_needed(PORT, profile=cl.PLAT_PROFILE)
    cdp = wp.CDP(PORT)
    # 随便连一个能 eval 的 target（空间接口不需要登录态，但要走同源 fetch）
    tids = [t for t in cdp.list_targets()
            if t.get("type") == "page" and "bilibili" in (t.get("url") or "")]
    if not tids:
        t = cdp.new_target("https://www.bilibili.com/")["id"]
        cdp.connect_target(t)
    else:
        cdp.connect_target(tids[0]["id"])
    mid = api.my_mid(cdp)
    print("mid =", mid)
    arts = api.list_published_articles(cdp, mid=mid, max_page=5)
    print("已发表专栏总数(前5页):", len(arts))
    hit = [a for a in arts if "Kibu" in (a.get("title") or "")
           or "智障" in (a.get("title") or "") or "发育障碍" in (a.get("title") or "")]
    print("命中 Kibu:", hit if hit else "未找到")
    print("== 最新 8 篇 ==")
    for a in arts[:8]:
        print("  id=%s %s" % (a.get("id"), (a.get("title") or "")[:50]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
