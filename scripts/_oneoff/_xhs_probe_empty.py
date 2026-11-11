"""探查小红书草稿箱里「暂无笔记标题」的空标题稿是什么内容（只读）。

步骤：开草稿箱抽屉 → 找标题为「暂无笔记标题」的卡片 → 坐标真实点击它的
「编辑」按钮 → 等编辑页加载 → 读标题输入框 value + 正文前 300 字 + 图片数
→ 导航回草稿箱页。不保存、不修改任何东西。

用法：python3 -u scripts/_xhs_probe_empty.py
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _js, _real_click, DRAFT_URL

JS_FIND_EDIT = """(function(){
  var items=[].slice.call(document.querySelectorAll('.draft-item'));
  for(var i=0;i<items.length;i++){
    var tt=items[i].querySelector('.draft-title-text');
    var t=(tt&&tt.innerText||'').trim();
    if(t.indexOf('暂无笔记标题')<0) continue;
    var bs=[].slice.call(items[i].querySelectorAll('.draft-actions .btn'))
      .filter(function(b){
        if((b.innerText||'').trim().indexOf('编辑')<0) return false;
        var r=b.getBoundingClientRect();
        return r.width>0 && r.height>0 && r.left>=0 &&
               r.left<window.innerWidth && r.top>=0 && r.top<window.innerHeight;
      });
    if(bs.length){
      items[i].scrollIntoView({block:'center'});
      bs[0].scrollIntoView({block:'center'});
      var r=bs[0].getBoundingClientRect();
      return JSON.stringify({title:t,
        x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});
    }
  }
  return null;
})()"""

JS_READ_PAGE = """(function(){
  var ti=document.querySelector("input.d-text[placeholder*='标题']");
  var bd=document.querySelector(".tiptap.ProseMirror[contenteditable='true']");
  var imgs=[].slice.call(document.querySelectorAll('img'))
    .filter(function(x){var r=x.getBoundingClientRect();return r.width>60&&r.height>60;});
  return JSON.stringify({
    url: location.href,
    title: ti ? (ti.value||'') : null,
    body: bd ? (bd.innerText||'').slice(0,300) : null,
    imgs: imgs.length
  });
})()"""


def main():
    print("打开草稿箱（9223）...", flush=True)
    sub = _draft_cdp()
    cards = fetch_drafts(sub)
    empt = [c for c in cards if "暂无笔记标题" in (c.get("title") or "")]
    print("草稿箱 %d 张，其中「暂无笔记标题」%d 张" % (len(cards), len(empt)),
          flush=True)
    if not empt:
        try:
            sub.close_target(sub.tid)
        except Exception:
            pass
        return
    for k in range(len(empt)):
        print("\n--- 探查第 %d 张空标题稿 ---" % (k + 1), flush=True)
        found = _js(sub, JS_FIND_EDIT)
        if not isinstance(found, dict):
            print("  找不到编辑按钮（可能已探查完）", flush=True)
            break
        print("  命中卡片标题：%r" % found.get("title"), flush=True)
        _real_click(sub, found)
        time.sleep(9)
        page = _js(sub, JS_READ_PAGE)
        print("  编辑页：%s" % json.dumps(page, ensure_ascii=False)[:600],
              flush=True)
        # 回草稿箱页继续找下一张
        sub.send("Page.navigate", {"url": DRAFT_URL})
        time.sleep(7)
        try:
            fetch_drafts(sub)
        except SystemExit:
            print("  [warn] 回箱后打不开抽屉", flush=True)
            break
    try:
        sub.close_target(sub.tid)
    except Exception:
        pass
    print("\n探查结束（未做任何修改）", flush=True)


if __name__ == "__main__":
    main()
