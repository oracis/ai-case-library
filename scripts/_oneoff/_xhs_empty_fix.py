"""探查小红书草稿箱里「暂无笔记标题」空稿的内容，并按内容自动修补。

背景：适配 10-14 深夜 ~10-15 凌晨的删发批次。publbee/podawaa/vid-ai 等
清理完后，箱内仍有 2 条标题为「暂无笔记标题」的草稿：
  10-14 22:59:57 —— 疑似 pet-plus-us（批量发布时标题没填上，正文/图存住了）
  10-13 23:19:45 —— 前次遗留，来源未知
这类稿没有标题，发布时会判无效，属于破碎稿。

流程（只读探测 → 配对 → 修）：
1) 开草稿箱，找「暂无笔记标题」卡片。
2) 两阶段定位「编辑」按钮（先 scrollIntoView，再按视口过滤量坐标）——
   单阶段会因为在视口外而被 filter 掉，这是上一版失败的原因。
3) 点编辑 → 读编辑页（标题 input + 正文 + 图片数）。点完可能开新标签，
   本 tab 读不到时自动切到最新的 xhs 目标再读。
4) 用正文里的产品名反查出 case id；命中且本地有干净 note.json ⇒
   删掉这条破碎稿 → 重新 publish 该 case，补一张完整草稿。
5) 最终输出全量清单。

用法：python3 -u scripts/_xhs_empty_fix.py
"""
import sys, os, time, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp
import chrome_cdp_launch as L
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _js, _real_click, DRAFT_URL

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XHS = os.path.join(ROOT, "scripts", "xhs_publish.py")
PY = sys.executable
ENV = dict(os.environ)
ENV.update({"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"})

# 阶段一：找到卡片并滚动进视口（只滤可见，不要求已在视口）
JS_SCROLL_EMPTY = """(function(){
  var items=[].slice.call(document.querySelectorAll('.draft-item'));
  for(var i=0;i<items.length;i++){
    var tt=items[i].querySelector('.draft-title-text');
    var t=(tt&&tt.innerText||'').trim();
    if(t.indexOf('暂无笔记标题')<0) continue;
    items[i].scrollIntoView({block:'center'});
    return JSON.stringify({n:1});
  }
  return null;
})()"""

# 阶段二：滚动之后再量坐标
JS_COORD_EMPTY = """(function(){
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
    URL: location.href.slice(0,90),
    title: ti ? (ti.value||'') : null,
    body: bd ? (bd.innerText||'').slice(0,240) : null,
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


def read_editor(sub):
    """读编辑页内容；若编辑页开在新标签里，自动切过去读。"""
    time.sleep(9)
    r = _js(sub, JS_READ_PAGE)
    if isinstance(r, dict) and (r.get("body") or r.get("title") is not None):
        return r, sub
    # 点编辑可能开了新标签 → 找最新的 xhs page 目标
    print("  [info] 本 tab 读不到编辑器，尝试找新开的标签", flush=True)
    try:
        root = wp.CDP(xp.PUB_PORT)
        tids = [t for t in root.list_targets()
                if t.get("type") == "page" and "xiaohongshu.com" in (t.get("url") or "")]
    except Exception as e:
        print("  [warn] list_targets 失败：%s" % e, flush=True)
        return r, sub
    if not tids:
        return r, sub
    t = tids[-1]
    print("  [info] 切到新标签 %s" % (t.get("url") or "")[:70], flush=True)
    try:
        alt = wp.CDP(xp.PUB_PORT)
        alt.connect_target(t["id"])
        time.sleep(3)
        r2 = _js(alt, JS_READ_PAGE)
        return (r2, alt) if isinstance(r2, dict) else (r, sub)
    except Exception as e:
        print("  [warn] 切标签失败：%s" % e, flush=True)
        return r, sub


def guess_cid(body, title, name_map):
    """用正文/标题里的产品名反查 case id。"""
    hay = ((body or "") + " " + (title or "")).upper()
    best, bestlen = None, 0
    for cid, names in name_map.items():
        for n in names:
            if n and n.upper() in hay and len(n) > bestlen:
                best, bestlen = cid, len(n)
    return best


def load_name_map():
    """case id -> 可能出现在正文里的名（产品名 / note 标题）。"""
    m = {}
    cases = json.load(open(os.path.join(ROOT, "data", "cases.json"),
                           encoding="utf-8"))
    items = cases if isinstance(cases, list) else cases.get("cases", cases)
    for c in items:
        cid = c.get("id")
        if not cid:
            continue
        names = []
        nm = (c.get("name") or "").strip()
        if nm:
            names.append(nm)
            names.append(nm.split(".")[0].split(" ")[0])
        nj = os.path.join(ROOT, "out", "xhs", cid, "note.json")
        if os.path.isfile(nj):
            try:
                t = json.load(open(nj, encoding="utf-8")).get("title", "")
                if t:
                    names.append(t.split("：")[0])
            except Exception:
                pass
        m[cid] = [n for n in names if len(n) >= 3]
    return m


def run_pub(cid, timeout=540):
    t0 = time.time()
    try:
        p = subprocess.run([PY, XHS, "publish", "--case", cid], cwd=ROOT,
                           timeout=timeout, capture_output=True, text=True,
                           env=ENV)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, "TIMEOUT %ds" % timeout, timeout


def main():
    print("==== 0) 启动 9223 ====", flush=True)
    ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=L.PLAT_PROFILE)
    print("autostart ok=%s" % ok, flush=True)
    if not ok:
        print("✗ 9223 起不来，停下")
        return

    print("\n==== 1) 开箱 + 定位空标题稿 ====", flush=True)
    sub, cards = reopen()
    empt = [c for c in cards if "暂无笔记标题" in (c.get("title") or "")]
    print("共 %d 张，空标题稿 %d 张：%s"
          % (len(cards), len(empt), [c.get("saved") for c in empt]), flush=True)

    name_map = load_name_map()
    findings = []
    for k in range(len(empt)):
        print("\n--- 探查第 %d 条空稿 ---" % (k + 1), flush=True)
        sub, cards = reopen(sub)
        if not _js(sub, JS_SCROLL_EMPTY):
            print("  找不到空标题卡片（可能已探完）", flush=True)
            break
        time.sleep(1.2)
        coord = _js(sub, JS_COORD_EMPTY)
        if not isinstance(coord, dict) or not coord.get("x"):
            print("  [warn] 拿不到编辑按钮坐标", flush=True)
            break
        print("  编辑按钮坐标 %s,%s" % (coord["x"], coord["y"]), flush=True)
        _real_click(sub, coord)
        page, reader = read_editor(sub)
        print("  编辑页：%s" % json.dumps(page, ensure_ascii=False)[:520],
              flush=True)
        cid = guess_cid(page.get("body") if isinstance(page, dict) else "",
                        page.get("title") if isinstance(page, dict) else "",
                        name_map)
        print("  → 反查 case：%s" % cid, flush=True)
        findings.append({"saved": empt[k].get("saved"), "page": page,
                         "cid": cid})
        # 回到草稿箱（一个新 target，避开可能的离页确认框）
        sub = _draft_cdp()
        try:
            fetch_drafts(sub)
        except SystemExit:
            print("  [warn] 回箱后打不开抽屉", flush=True)
            break

    print("\n==== 2) 探查结论 ====", flush=True)
    for f in findings:
        print("  %s → case=%s | 图片 %s | 正文 %s"
              % (f["saved"], f["cid"],
                 (f["page"] or {}).get("imgs"),
                 ((f["page"] or {}).get("body") or "")[:40].replace("\n", " ")),
              flush=True)

    # ---- 3) 修补：删破碎稿 + 重发整稿（只处理反查到 case 的）----
    print("\n==== 3) 修补 ====", flush=True)
    fix_list = [f for f in findings
                if f["cid"] and os.path.isfile(
                    os.path.join(ROOT, "out", "xhs", f["cid"], "note.json"))]
    for f in fix_list:
        cid = f["cid"]
        print("--- 修 %s（原稿 %s）---" % (cid, f["saved"]), flush=True)
        try:
            sub, cards = reopen(sub)
        except (Exception, SystemExit) as e:
            print("  [err] 重开抽屉失败 %s" % e, flush=True)
            continue
        before = len(cards)
        r = xp._purge_one(sub, "暂无笔记标题")   # 删最新一条空标题稿
        print("  删破碎稿：%s" % r, flush=True)
        try:
            xp._unmark(xp._draft_file(), cid)
        except Exception:
            pass
        rc, out, dt = run_pub(cid)
        last = out.strip().splitlines()[-3:] if out.strip() else []
        print("  重发 %s：rc=%d (%.0fs) %s" % (cid, rc, dt, "\n".join(last)),
              flush=True)

    print("\n==== 4) 最终清单 ====", flush=True)
    try:
        sub, cards = reopen(sub)
        print("共 %d 张" % len(cards), flush=True)
        for i, c in enumerate(cards, 1):
            print("  [%2d] %-30s %s" % (i, c.get("title"), c.get("saved")),
                  flush=True)
    except (Exception, SystemExit) as e:
        print("最终清单失败：%s" % e, flush=True)


if __name__ == "__main__":
    main()
