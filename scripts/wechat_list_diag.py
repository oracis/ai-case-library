#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：草稿箱列表页 DOM 到底渲染了哪些卡片、每页几条、翻页参数是什么。

为什么需要这个（2026-10-02）：
    `delete --appmsgid 100000188` 返回 `NO_CARD` —— 但同一个 appmsgid 在
    **列表接口**里明明能查到（`草稿列表共 38 篇`）。接口有、DOM 没有 ⇒
    只有一种解释：**DOM 只渲染了当前页**（草稿箱默认每页 10 条），
    旧草稿排在第 2 页以后，`document.querySelector('[data-appid="..."]')`
    必然落空。
    ⇒ `_delete_draft_ui()` 在翻到目标所在页之前，永远只能拿到 NO_CARD。

本脚本只读，打印：
  1. 当前页卡片的 data-appid 全集；
  2. 列表接口拿到的 appmsgid 全集；
  3. 两者的差（= 落在别的页上的）；
  4. 翻页控件的形态（分页按钮/下拉/URL 参数），供后续实现翻页用。

用法：
    python -X utf8 scripts/wechat_list_diag.py            # 探当前页
    python -X utf8 scripts/wechat_list_diag.py --page 2   # 试翻到第 2 页
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as W          # noqa: E402


LIST_TPL = ("https://mp.weixin.qq.com/cgi-bin/appmsg?begin=%d&count=%d"
            "&type=77&action=list&lang=zh_CN&token=%s")


def nav_and_count(cdp, tok, begin, count, action=None):
    """导航到指定 begin/count，返回 (渲染卡片数, 卡片 id 列表)。"""
    url = LIST_TPL % (begin, count, tok)
    if action:
        url += "&action=" + action
    cdp.send("Page.enable")
    cdp.send("Page.navigate", {"url": url})
    last = 0
    for _ in range(25):
        time.sleep(1)
        try:
            n = cdp.eval("document.querySelectorAll('[data-appid]').length",
                         refresh_context=True)
            last = int(n or 0)
            # 连续两次数量不变视为渲染稳定
            if last >= count:
                break
        except Exception:
            pass
    import json
    try:
        ids = json.loads(dom_cards(cdp))
    except Exception:
        ids = []
    return last, ids


def dom_cards(cdp):
    """当前页 DOM 里所有卡片的 data-appid。"""
    return cdp.eval("""(function(){
      var out=[];
      document.querySelectorAll('[data-appid]').forEach(function(el){
        out.push(String(el.getAttribute('data-appid')));
      });
      return JSON.stringify(out);
    })()""", refresh_context=True) or "[]"


def pager_info(cdp):
    """翻页控件长什么样 —— 决定怎么翻。"""
    return cdp.eval("""(function(){
      var info={};
      // 分页按钮：weui 分页
      var pager=document.querySelector('.weui-desktop-pagination,.weui-pagination');
      info.pager_text = pager ? (pager.innerText||'').replace(/\\s+/g,' ').trim().slice(0,300) : null;
      // 每页条数下拉
      var sel=document.querySelector('select');
      info.selects=[];
      document.querySelectorAll('select').forEach(function(s){
        info.selects.push({name:s.name||'', options: s.options.length,
          value: s.value, text:(s.options[s.selectedIndex]||{}).text});
      });
      // 页码链接
      var links=[];
      document.querySelectorAll('a').forEach(function(a){
        var h=a.getAttribute('href')||'';
        if(h.indexOf('page=')>-1||h.indexOf('begin=')>-1){
          links.push((a.innerText||'').trim()+' -> '+h.slice(0,120));
        }
      });
      info.page_links=links.slice(0,12);
      // 当前页码高亮
      var cur=document.querySelector('.weui-desktop-pagination__item_is_active, .active');
      info.current = cur ? (cur.innerText||'').trim() : null;
      return JSON.stringify(info);
    })()""", refresh_context=True) or "{}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=W.CDP_PORT)
    ap.add_argument("--page", type=int, default=0,
                    help=">1 时尝试用 URL 参数直接跳到该页（探参数是否生效）")
    args = ap.parse_args()

    W._autostart_chrome_if_needed(args.port)
    cdp = W.CDP(args.port)
    _tid, tok = W._connect_mp(cdp)
    if not tok:
        raise SystemExit("NO_TOKEN")

    api = W._draft_list(cdp, tok, count=200)
    api_ids = [str(d.get("appmsgid")) for d in api]
    print("接口草稿数=%d" % len(api_ids))
    target = "100000188"

    # ---- 探1：begin/count 能不能直接翻页 ----
    for begin in (0, 10, 20, 30):
        n, ids = nav_and_count(cdp, tok, begin, 10)
        hit = target in ids
        print("[begin=%-3d count=10] 渲染=%-3d %s"
              % (begin, n, ">>> 含目标" if hit else ""))
        if hit:
            print("\n[结论] 翻页参数生效：begin=%d count=10 能拿到目标" % begin)
            break
    else:
        print("\n[结论] begin 翻页无效，试试 count 拉大")
        n, ids = nav_and_count(cdp, tok, 0, 100)
        print("[begin=0 count=100] 渲染=%d 含目标=%s" % (n, target in ids))

    import json
    try:
        pager = json.loads(pager_info(cdp))
    except Exception:
        pager = {"raw": pager_info(cdp)}
    print("\n[翻页控件]")
    print(json.dumps(pager, ensure_ascii=False, indent=2)[:1200])


if __name__ == "__main__":
    main()