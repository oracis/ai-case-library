"""收尾：publbee 去重（删最旧的英文封面稿）+ 探查「暂无笔记标题」空稿 + 最终全量清单。

背景：_xhs_cover_resave.py 发布完 6 个中文封面干净稿后复核发现 publbee
箱内有 2 张同名稿《Publbee：海外小生意》——旧稿（英文封面，10-14 00:56
批次）在删除阶段因 fetch_drafts 抓取glitch 漏删，新稿（中文封面，刚发）
又发了一张。DOM 顺序最新在前 ⇒ 同名卡片里取最后一张（最旧）删。

另探查箱内 2 条「暂无笔记标题」空标题稿是什么内容（只读，不修改）。
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp
import chrome_cdp_launch as L
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _js, _real_click, DRAFT_URL

ENV_OK = True  # no_proxy 由外层 bash 设置

JS_LIST_MATCH = """(function(WANT){
  var items=[].slice.call(document.querySelectorAll('.draft-item'));
  var out=[];
  for(var i=0;i<items.length;i++){
    var tt=items[i].querySelector('.draft-title-text');
    var t=(tt&&tt.innerText||'').trim();
    if(WANT && t.indexOf(WANT)<0) continue;
    var tm=items[i].querySelector('.draft-time');
    out.push({title:t, time:(tm&&tm.innerText||'').trim()});
  }
  return JSON.stringify(out);
})(%s)"""

JS_DEL_NTH = """(function(WANT, N){
  var items=[].slice.call(document.querySelectorAll('.draft-item'));
  var idx=-1;
  for(var i=0;i<items.length;i++){
    var tt=items[i].querySelector('.draft-title-text');
    var t=(tt&&tt.innerText||'').trim();
    if(t.indexOf(WANT)<0) continue;
    idx++;
    if(idx!==N) continue;
    var bs=[].slice.call(items[i].querySelectorAll('.draft-actions .btn'))
      .filter(function(b){
        if((b.innerText||'').trim().indexOf('删除')<0) return false;
        var r=b.getBoundingClientRect();
        return r.width>0 && r.height>0;
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
})(%s, %d)"""

JS_FIND_EDIT_EMPTY = """(function(){
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
    title: ti ? (ti.value||'') : null,
    body: bd ? (bd.innerText||'').slice(0,300) : null,
    imgs: imgs.length
  });
})()"""


def reopen(old=None):
    if old is not None:
        try:
            old.close_target(old.tid)
        except Exception:
            pass
        time.sleep(2)
    sub = _draft_cdp()
    cards = fetch_drafts(sub)
    return sub, cards


def main():
    print("==== 0) 启动 9223 ====", flush=True)
    ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=L.PLAT_PROFILE)
    print("autostart ok=%s" % ok, flush=True)
    if not ok:
        print("✗ 9223 起不来，停下")
        return

    print("\n==== 1) 全量清单（权威快照）====", flush=True)
    sub, cards = reopen()
    for i, c in enumerate(cards, 1):
        print("  [%2d] %-30s %s" % (i, c.get("title"), c.get("saved")),
              flush=True)
    print("共 %d 张" % len(cards), flush=True)

    # ---- 2) publbee 去重 ----
    print("\n==== 2) publbee 去重 ====", flush=True)
    pb = [c for c in cards if "Publbee" in (c.get("title") or "")]
    print("publbee 稿 %d 张：%s" % (len(pb), [(c.get("title"), c.get("saved"))
                                             for c in pb]), flush=True)
    if len(pb) >= 2:
        # DOM 顺序最新在前 ⇒ 删第 len-1 张（最旧=英文封面稿）
        n_target = len(pb) - 1
        coord = _js(sub, JS_DEL_NTH % (json.dumps("Publbee", ensure_ascii=False),
                                       n_target))
        print("目标（最旧第 %d 张）删除按钮坐标：%s" % (n_target, coord),
              flush=True)
        if isinstance(coord, dict) and coord.get("x"):
            _real_click(sub, coord)
            time.sleep(1.4)
            cc = _js(sub, xp._JS_PURGE_CONFIRM_COORD)
            if isinstance(cc, dict) and cc.get("x"):
                _real_click(sub, cc)
            time.sleep(2.5)
        sub, cards = reopen(sub)
        pb2 = [c for c in cards if "Publbee" in (c.get("title") or "")]
        print("删后 publbee 稿 %d 张：%s" % (len(pb2),
              [(c.get("title"), c.get("saved")) for c in pb2]), flush=True)
        if len(pb2) != 1:
            print("[warn] publbee 仍不止 1 张，需人工看一眼", flush=True)
    else:
        print("无需去重", flush=True)

    # ---- 3) 探查空标题稿 ----
    print("\n==== 3) 探查「暂无笔记标题」空稿 ====", flush=True)
    empt = [c for c in cards if "暂无笔记标题" in (c.get("title") or "")]
    print("空标题稿 %d 张：%s" % (len(empt), [(c.get("saved")) for c in empt]),
          flush=True)
    for k in range(len(empt)):
        print("--- 探查第 %d 张 ---" % (k + 1), flush=True)
        found = _js(sub, JS_FIND_EDIT_EMPTY)
        if not isinstance(found, dict):
            print("  找不到编辑按钮（可能已看完）", flush=True)
            break
        _real_click(sub, found)
        time.sleep(9)
        page = _js(sub, JS_READ_PAGE)
        print("  内容：%s" % json.dumps(page, ensure_ascii=False)[:500],
              flush=True)
        sub.send("Page.navigate", {"url": DRAFT_URL})
        time.sleep(7)
        try:
            fetch_drafts(sub)
        except SystemExit:
            print("  [warn] 回箱后打不开抽屉", flush=True)
            break

    # ---- 4) 最终清单 ----
    print("\n==== 4) 最终全量清单 ====", flush=True)
    try:
        sub, cards = reopen(sub)
        for i, c in enumerate(cards, 1):
            print("  [%2d] %-30s %s" % (i, c.get("title"), c.get("saved")),
                  flush=True)
        print("共 %d 张" % len(cards), flush=True)
    except (Exception, SystemExit) as e:
        print("最终清单失败：%s" % e, flush=True)


if __name__ == "__main__":
    main()
