# -*- coding: utf-8 -*-
"""一次性探针：发布按钮是不是「图片没加载完就不给点」。

抽屉编辑器里一条 ce-btn 都没有，而那条草稿 15 张图裂了 10 张 ——
所以假设：图片全部就位后，底部红色「发布」按钮才会渲染。
从最老的草稿往前试，找第一条**零裂图**的，看它有没有按钮。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x

TRY = 6


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    oldest = sorted(drafts, key=lambda d: d.get("saved") or "")[:TRY]
    print("待试 %d 条（最老优先）" % len(oldest))
    for i, d in enumerate(oldest, 1):
        sub.send("Page.navigate", {"url": x.DRAFT_URL})
        time.sleep(6)
        sub.eval("1", refresh_context=True)
        x._wait_draft_entry(sub)
        ds = x.fetch_drafts(sub)
        hit = [z for z in ds if z.get("title") == d.get("title")]
        target = hit[0] if hit else d
        ok, got = x._open_draft_editor(sub, target.get("title"))
        if not ok:
            print("[%d] %s -> 编辑器没打开" % (i, d.get("title")))
            continue
        time.sleep(3)
        total, broken, _bad = x._img_health(sub)
        btn = x.publish_btn_state(sub)
        print("[%d] %s | 图 %d 张裂 %d | 红按钮 %s" % (
            i, (d.get("title") or "")[:24], total, broken,
            json.dumps(btn, ensure_ascii=False)))
        if broken == 0:
            print("    ↑ 零裂图，重点看这条")
    print("--- 结束")


if __name__ == "__main__":
    main()
