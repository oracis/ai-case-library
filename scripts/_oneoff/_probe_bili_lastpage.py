# -*- coding: utf-8 -*-
"""一次性探针：验证「草稿箱倒序 ⇒ 末页最后一条 = 存入最久的那条」。

用户提的办法就是这么找的（末页最后一条）。`send_draft` 走的是 API
`draft/list` 按 mtime 升序取第一条。两者必须指向同一条草稿，否则脚本挑的和
人眼看到的就不是一回事 —— 这里用浏览器把末页最后一条捞出来对一下。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import bilibili_publish as bp          # noqa: E402
import bili_draft_api as bapi          # noqa: E402
import wechat_publish as wp            # noqa: E402

DRAFT_PAGE = "https://member.bilibili.com/platform/upload-manager/opus"

JS_STATE = """
(function(){
  var out={iframes:[].slice.call(document.querySelectorAll('iframe'))
             .map(function(f){return (f.src||'').slice(0,80);})};
  return JSON.stringify(out);})()
"""

JS_CARDS = """
(function(kw){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(x){return (x.src||'').indexOf(kw)>=0;});
  var d=f&&f.contentDocument?f.contentDocument:null;
  if(!d) return JSON.stringify({err:'no-iframe'});
  var cards=[].slice.call(d.querySelectorAll('*')).filter(function(e){
    var t=(e.innerText||'').trim();
    return t.indexOf('保存于')>=0 && t.length<400 &&
           e.getBoundingClientRect().width>0 &&
           e.querySelectorAll('*').length<40;});
  var pager=[].slice.call(d.querySelectorAll(
      '.vui_pagination li, .pagination li, [class*=pager] li'))
    .map(function(x){return (x.innerText||'').trim();}).filter(Boolean);
  return JSON.stringify({
    cards: cards.map(function(c){return (c.innerText||'').split('\\n')[0];}),
    pager: pager});})(arguments[0] || 'opus/management')
"""


def main():
    cdp = wp.CDP(bp.PUB_PORT)
    tid = cdp.new_target(DRAFT_PAGE)["id"]
    cdp.connect_target(tid)
    try:
        time.sleep(8)
        print("iframes:", (cdp.eval(JS_STATE, refresh_context=True) or "")[:300])
        # 找到装列表的 iframe，翻到最后一页
        for kw in ("opus/management", "management"):
            st = json.loads(cdp.eval(
                "(%s)(%s)" % (JS_CARDS, json.dumps(kw)),
                refresh_context=True) or "{}")
            if st.get("cards"):
                break
        print("分页条:", st.get("pager"))
        print("当前页首条:", (st.get("cards") or ["无"])[0])
        print("当前页末条:", (st.get("cards") or ["无"])[-1])
        print("卡片数:", len(st.get("cards") or []))
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass

    # API 侧挑的那条
    cdp2 = wp.CDP(bp.PUB_PORT)
    bp._api_tab(cdp2)
    drafts = bapi.list_drafts(cdp2) or []
    oldest = sorted(drafts, key=lambda d: d.get("mtime") or 0)[0]
    newest = sorted(drafts, key=lambda d: d.get("mtime") or 0)[-1]
    print("API 最久:", oldest.get("article_id"), (oldest.get("title") or "")[:40])
    print("API 最新:", newest.get("article_id"), (newest.get("title") or "")[:40])


if __name__ == "__main__":
    main()
