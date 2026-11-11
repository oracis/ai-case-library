# -*- coding: utf-8 -*-
"""抽样诊断：B站草稿封面是否被改标题操作抹掉。"""
import sys, os
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, "scripts")
import bili_draft_api as api, wechat_publish as wp, bilibili_publish as bp

cdp = wp.CDP(bp.PUB_PORT)
api._bili_tab(cdp)
ds = api.list_drafts(cdp)
by_aid = {str(d.get("article_id")): (d.get("title") or "") for d in ds}

RETITLED = {"343837", "343836", "343835", "343834", "343833", "343832",
            "343831", "343830", "343829", "343828", "343827", "343826",
            "343502", "343501", "343500", "343499", "343498", "343497"}

ret = sorted(RETITLED & set(by_aid))
untouched = sorted(set(by_aid) - RETITLED)
print("改过 %d 条 / 没改 %d 条\n" % (len(ret), len(untouched)))


def probe(aids, tag):
    print("=== %s ===" % tag)
    for aid in aids:
        try:
            v = api.view_draft(cdp, aid)
        except Exception as e:
            print("  aid=%s view 失败 %s" % (aid, e)); continue
        if not isinstance(v, dict):
            print("  aid=%s 非 dict" % aid); continue
        iu = v.get("origin_image_urls") or v.get("image_urls")
        print("  aid=%-7s image_urls=%-34s banner_url=%s" % (
            aid, str(iu)[:32] if iu else "<None>",
            (v.get("banner_url") or "<None>")[:44]))
    print()


probe(ret[-3:], "改过的（最后改的几条）")
probe(untouched[:3], "没改过的（对照）")