# -*- coding: utf-8 -*-
"""只读：回读某条 B站草稿的封面 URL（用于换封面前后对比）。

用法：
  python out/bili/cover_url_probe.py uplinked-b-v

⚠ 判据只有一条：**远端 image_urls[0] 的完整 URL 字符串**。
  cover-status 只给「有/无」布尔，看不出「换没换」——
  同一个草稿重新上传，aid 不变、有封面仍是有封面，
  但必须比对 URL 本身才能证明图真的换了。
"""
import json
import os
import sys
import time

sys.path.insert(0, "scripts")
import chrome_cdp_launch as _L  # noqa: E402
import wechat_publish as wp  # noqa: E402
import bilibili_publish as bp  # noqa: E402

cid = sys.argv[1] if len(sys.argv) > 1 else "uplinked-b-v"

html = bp.load_article_html(cid)
if not html:
    print("✗ 找不到 out/articles/%s.html" % cid)
    sys.exit(1)
title = bp.make_title(cid, html)

# Chrome 会被父进程回收，起Chrome + 驱动必须同进程。
wp._autostart_chrome_if_needed(bp.PUB_PORT, profile=_L.PLAT_PROFILE)

cdp = wp.CDP(bp.PUB_PORT)
tid = None
try:
    tid = cdp.new_target(bp.BILI_DRAFT_LIST_PAGE)["id"]
    if not cdp.connect_target(tid):
        print("✗ connect_target 失败 tid=%r" % (tid,))
        sys.exit(1)
    time.sleep(6)
    aid, drafts = bp._pick_draft(cdp, tid, title)
    if not aid:
        print("✗ 草稿箱里没找到《%s》（现有 %d 条）" % (title, len(drafts)))
        sys.exit(1)
    url = bp._banner_of(cdp, tid, aid) or ""
    print(json.dumps({
        "cid": cid,
        "title": title,
        "aid": aid,
        "cover_url": url,
        "has_cover": bool(url),
    }, ensure_ascii=False, indent=2))
finally:
    if tid:
        try:
            cdp.close_target(tid)
        except Exception:  # noqa: BLE001
            pass