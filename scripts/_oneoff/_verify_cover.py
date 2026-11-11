# -*- coding: utf-8 -*-
"""验收：B站草稿封面修复结果（远端回读）。"""
import sys, os
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, "scripts")
import bili_draft_api as api, wechat_publish as wp, bilibili_publish as bp

TARGET = ["343837", "343836", "343835", "343834", "343833", "343832", "343831",
          "343830", "343829", "343828", "343827", "343826",
          "343502", "343501", "343500", "343499", "343498", "343497"]

cdp = wp.CDP(bp.PUB_PORT)
api._bili_tab(cdp)
ds = api.list_drafts(cdp)
titles = {str(d.get("article_id")): (d.get("title") or "") for d in ds}

ok = miss = 0
for aid in TARGET:
    try:
        v = api.view_draft(cdp, aid)
    except Exception as e:
        print("  !! aid=%s view 失败 %s" % (aid, e)); miss += 1; continue
    iu = (v.get("origin_image_urls") or v.get("image_urls")) if isinstance(v, dict) else None
    if iu:
        ok += 1
        print("  OK  aid=%s 封面=%s" % (aid, str(iu[0])[:56]))
    else:
        miss += 1
        print("  !!  aid=%s 仍无封面 《%s》" % (aid, titles.get(aid, "")[:30]))

print()
print("目标 %d 条：有封面 %d / 仍缺 %d" % (len(TARGET), ok, miss))