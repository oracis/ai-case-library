# -*- coding: utf-8 -*-
"""一次性探针：编辑器里到底有没有 `button.ce-btn.bg-red`「发布」按钮。

必须在**同一个进程**里跑完（开抽屉 → 开编辑器 → 看按钮）—— 进程一退出，
页面就回草稿箱了，回头再连看到的是另一张页面。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    print("草稿 %d 条" % len(drafts))
    d, why = x.pick_draft(drafts)
    print("最老：%s | %s" % (d.get("saved"), d.get("title")))

    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("编辑器：%s（标题=%r）" % (ok, got))
    if not ok:
        return
    time.sleep(3)
    print("URL:", sub.eval("location.href"))

    print("红按钮（滚动前）：%s" % json.dumps(x.publish_btn_state(sub),
                                              ensure_ascii=False))

    # 发布按钮可能在页面最底部、滚到位才渲染
    sub.eval("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2.5)
    print("红按钮（滚到底后）：%s" % json.dumps(x.publish_btn_state(sub),
                                                ensure_ascii=False))
    print("含 bg-red / ce-btn 的元素：")
    print(sub.eval("""
(function(){
  var o=[];
  [].slice.call(document.querySelectorAll('[class*=bg-red],[class*=ce-btn]'))
    .forEach(function(e){
      var rc=e.getBoundingClientRect();
      o.push({tag:e.tagName, c:String(e.className).slice(0,40),
              t:(e.innerText||e.textContent||'').trim().slice(0,10),
              x:Math.round(rc.x), y:Math.round(rc.y),
              w:Math.round(rc.width), h:Math.round(rc.height)});
    });
  return JSON.stringify(o);})()
""", refresh_context=True))
    print("文本为「发布」的元素：")
    print(sub.eval("""
(function(){
  var o=[];
  [].slice.call(document.querySelectorAll('*')).forEach(function(e){
    if((e.innerText||'').trim()!=='发布') return;
    var rc=e.getBoundingClientRect();
    o.push({tag:e.tagName, c:String(e.className).slice(0,40),
            x:Math.round(rc.x), y:Math.round(rc.y),
            w:Math.round(rc.width), h:Math.round(rc.height)});
  });
  return JSON.stringify(o);})()
""", refresh_context=True))
    print("全部可见 button：")
    print(sub.eval("""
(function(){
  var o=[];
  [].slice.call(document.querySelectorAll('button')).forEach(function(b){
    var rc=b.getBoundingClientRect();
    if(rc.width<=0) return;
    o.push({t:(b.innerText||'').trim().slice(0,10),
            c:String(b.className).slice(0,36),
            x:Math.round(rc.x), y:Math.round(rc.y),
            w:Math.round(rc.width), h:Math.round(rc.height),
            da:b.getAttribute('aria-disabled'),
            bu:b.getAttribute('aria-busy')});
  });
  return JSON.stringify(o);})()
""", refresh_context=True))
    total, broken, _bad = x._img_health(sub)
    print("图片：%d 张，裂 %d 张" % (total, broken))


if __name__ == "__main__":
    main()
