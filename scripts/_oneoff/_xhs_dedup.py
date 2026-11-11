"""小红书：同名草稿去重，只保留最新的一张。

用法：python3 -u scripts/_xhs_dedup.py <标题片段> [--keep newest|oldest]

场景：批量发布/重发后同一案例在草稿箱里留下 2 张同名稿（DOM 顺序最新在前）。
按 DOM 顺序定位第 N 张同名片，坐标点击删除并确认，保留剩下的最新那张。
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp
import chrome_cdp_launch as L
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _js, _real_click

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
    want = sys.argv[1] if len(sys.argv) > 1 else None
    keep = "newest"
    if "--keep" in sys.argv:
        i = sys.argv.index("--keep")
        if i + 1 < len(sys.argv):
            keep = sys.argv[i + 1]
    if not want:
        print("需要标题片段，例如：python3 scripts/_xhs_dedup.py 'Pet Plus Us'")
        return
    print("去重：标题含「%s」，保留 %s" % (want, keep), flush=True)

    ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=L.PLAT_PROFILE)
    print("autostart ok=%s" % ok, flush=True)
    if not ok:
        print("✗ 9223 起不来")
        return

    sub, cards = reopen()
    hits = [c for c in cards if want in (c.get("title") or "")]
    print("命中 %d 张：%s" % (len(hits), [(c.get("title"), c.get("saved"))
                                          for c in hits]), flush=True)
    if len(hits) <= 1:
        print("无需去重", flush=True)
        try:
            sub.close_target(sub.tid)
        except Exception:
            pass
        return

    # DOM 顺序最新在前；保留 newest ⇒ 删掉除第 0 张以外的（从最旧开始删）
    targets = list(range(len(hits) - 1, 0, -1)) if keep == "newest" \
        else list(range(1, len(hits)))
    for n in targets:
        print("删第 %d 张（DOM 序，0=最新）" % n, flush=True)
        coord = _js(sub, JS_DEL_NTH % (json.dumps(want, ensure_ascii=False), n))
        if not isinstance(coord, dict) or not coord.get("x"):
            print("  拿不到删除按钮坐标，跳过", flush=True)
            continue
        _real_click(sub, coord)
        time.sleep(1.4)
        cc = _js(sub, xp._JS_PURGE_CONFIRM_COORD)
        if isinstance(cc, dict) and cc.get("x"):
            _real_click(sub, cc)
        time.sleep(2.5)

    sub, cards = reopen(sub)
    hits = [c for c in cards if want in (c.get("title") or "")]
    print("去重后 %d 张：%s" % (len(hits), [(c.get("title"), c.get("saved"))
                                           for c in hits]), flush=True)
    print("草稿箱共 %d 张" % len(cards), flush=True)
    try:
        sub.close_target(sub.tid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
