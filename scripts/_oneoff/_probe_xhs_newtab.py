# -*- coding: utf-8 -*-
"""一次性探针：点草稿卡片「编辑」是不是**开了新标签页**？

用户给的 DOM 关键在容器名：`.publish-page-publish-btn` —— **publish page**。
而抽屉里点「编辑」打开的 overlay，URL 还停在 `?target=draft`，那边一个
`ce-btn` 都没有。所以真正的发布页很可能开在**另一个 target** 里，我们
从头到尾只在旧 tab 里找按钮，当然找不到。

验证：点编辑前后各列一次 targets，看有没有新 page。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import wechat_publish as wp
import xhs_publish as x

PORT = x.PUB_PORT
FIND = """
(function(){
  var w=document.querySelector('.publish-page-publish-btn');
  var btns=[].slice.call(document.querySelectorAll('button.ce-btn.bg-red'));
  return JSON.stringify({
    here: location.href,
    wrapper: !!w,
    red: btns.map(function(b){
      var rc=b.getBoundingClientRect();
      return {t:(b.innerText||'').trim(), x:Math.round(rc.x), y:Math.round(rc.y),
              w:Math.round(rc.width), h:Math.round(rc.height),
              da:b.getAttribute('aria-disabled'),
              bu:b.getAttribute('aria-busy')};
    }),
    comp: (function(){
      var c=document.querySelector('xhs-publish-btn');
      return c ? {submit_disabled:c.getAttribute('submit-disabled'),
                  submit_loading:c.getAttribute('submit-loading'),
                  submit_text:c.getAttribute('submit-text')} : null;
    })()
  });})()
"""


def pages():
    cdp = wp.CDP(PORT)
    return [t for t in cdp.list_targets() if t.get("type") == "page"]


def main():
    before = {t["id"]: t.get("url", "") for t in pages()}
    print("点之前 %d 个 page" % len(before))

    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    d, _ = x.pick_draft(drafts)
    print("目标：%s | %s" % (d.get("saved"), d.get("title")))
    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("编辑器判定：%s %r" % (ok, got))
    print("当前 tab：%s" % (sub.eval("location.href") or ""))
    print("当前 tab 找按钮：%s" % (sub.eval(FIND, refresh_context=True) or ""))

    time.sleep(5)
    after = pages()
    new = [t for t in after if t["id"] not in before]
    print("点之后 %d 个 page，新增 %d 个" % (len(after), len(new)))
    for t in new:
        print("  新 tab：%s" % (t.get("url") or "")[:110])
        s2 = wp.CDP(PORT)
        try:
            s2.connect_target(t["id"])
            print("     →", s2.eval(FIND, refresh_context=True))
        except Exception as e:
            print("     ERR", e)

    # 也把现有的 page 全扫一遍，看发布按钮是不是一直在某个别的 tab 里
    print("--- 扫所有 page")
    for t in after:
        s2 = wp.CDP(PORT)
        try:
            s2.connect_target(t["id"])
            r = s2.eval(FIND, refresh_context=True)
        except Exception:
            continue
        if r and '"red":[]' not in r:
            print("  命中：%s" % r)
        elif r and '"wrapper":true' in r:
            print("  有容器无按钮：%s" % r)


if __name__ == "__main__":
    main()
