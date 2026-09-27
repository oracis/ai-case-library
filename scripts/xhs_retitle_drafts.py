# -*- coding: utf-8 -*-
"""把草稿箱里已存草稿的标题，换成 xhs_publish.make_xhs_title 的新版。

背景：早期标题规则会退化到「海外小生意」这类通用词，36 条草稿里全是同一个
句式。现在规则+AI 覆盖表已经给出差异化标题，但草稿箱里的旧标题得就地改掉。

为什么必须真实键盘：小红书标题框是 Vue 受控 input，用 value setter + input
事件改 DOM 值，框架收不到（抓包验证：改完除了埋点请求什么都不发），草稿
不会真更新。必须 creating mouse click 聚焦 + Ctrl+A + Backspace + Input.insertText。

验收注意：草稿**列表有服务端缓存**，改完当场拉列表还是旧标题。必须
Page.reload(ignoreCache=True) 之后再看才准（2026-09-27 实测）。

用法：
  python scripts/xhs_retitle_drafts.py            # 干干看（只打印计划）
  python scripts/xhs_retitle_drafts.py --go       # 真改
  python scripts/xhs_retitle_drafts.py --verify   # 硬刷新后验收
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp      # noqa: E402
import xhs_publish as x          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFT_URL = "https://creator.xiaohongshu.com/publish/publish?target=draft"
DONE_FILE = os.path.join(ROOT, "out", "xhs", "_retitled.json")
_CLEANUP = None


def _done_ids():
    try:
        return set(json.load(open(DONE_FILE, encoding="utf-8")))
    except Exception:
        return set()


def _save_done(ids):
    os.makedirs(os.path.dirname(DONE_FILE), exist_ok=True)
    with open(DONE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def title_map():
    """认亲表：草稿标题（新旧都认）→ case id。"""
    m = {}
    for c in x.load_cases():
        cid = c["id"]
        nj = os.path.join(ROOT, "out", "xhs", cid, "note.json")
        old = None
        if os.path.isfile(nj):
            try:
                old = json.load(open(nj, encoding="utf-8")).get("title")
            except Exception:
                old = None
        for t in (old, x.make_xhs_title(c)):
            if t:
                m[x.norm_title(t)] = cid
    return m


JS_OPEN_EDIT = "(function(){%s\n" % x.JS_CLICK_CHAIN + """
  var sv=%s;
  var cards=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    var t=(x.innerText||'');
    return t.indexOf('保存于')>=0 && t.indexOf('编辑')>=0 && t.indexOf('删除')>=0; });
  cards = cards.filter(function(c){
    return !cards.some(function(o){ return o!==c && c.contains(o); }); });
  for(var i=0;i<cards.length;i++){
    if((cards[i].innerText||'').indexOf(sv)>=0){
      var e=[].slice.call(cards[i].querySelectorAll('*')).filter(function(x){
        return (x.innerText||'').trim()==='编辑' && x.children.length===0; })[0];
      if(!e) return 'noedit';
      jclick(e);
      return 'clicked';
    }
  }
  return 'notfound';
})()"""


def key(sub, ttype, key_, code, vk, mod=None):
    p = {"type": ttype, "key": key_, "code": code, "windowsVirtualKeyCode": vk}
    if mod:
        p["modifiers"] = mod
    sub.send("Input.dispatchKeyEvent", p)


def _title_value(js):
    return js("(function(){var e=document.querySelector(%s);return e?e.value:null;})()"
              % json.dumps(x.SEL_TITLE))


def click_focus(sub, js):
    """真实鼠标点击标题框聚焦。

    只用 JS focus() 不行：那样 Ctrl+A 选不中、Backspace 也删不掉，
    Input.insertText 会变成**追加**，把标题拼成一长串（2026-09-27 实测，
    startclaw/stan 两条草稿标题当场被拼成 70 多字）。必须真实鼠标点击。
    """
    box = js("(function(){var e=document.querySelector(%s);if(!e)return null;"
             "e.scrollIntoView({block:'center'});"
             "var r=e.getBoundingClientRect();"
             "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()"
             % json.dumps(x.SEL_TITLE))
    if not box:
        return False
    px, py = box["x"], box["y"]
    sub.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
    time.sleep(0.2)
    sub.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": px, "y": py,
                                          "button": "left", "clickCount": 1})
    time.sleep(0.15)
    sub.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": px, "y": py,
                                          "button": "left", "clickCount": 1})
    time.sleep(0.5)
    for _i in range(2):                       # 全选不总是生效，多试一次
        key(sub, "keyDown", "a", "KeyA", 65, 2)
        key(sub, "keyUp", "a", "KeyA", 65, 2)
        time.sleep(0.3)
    return True


def wipe_title(sub, js, max_press=400):
    """删到全空，且 Vue state 也真的空了。

    连发 Backspace 不 sleep 会被 Vue 的批处理吃掉几个：DOM 看着空了，
    Vue state 还是旧值，接着 insertText 就成了「旧值 + 新文本」的拼接串
    （2026-09-27 把 stan 的标题拼成 96 字）。所以每个字符之间留 40ms，
    删完还要 blur→focus 回读一次确认 state 同步。
    """
    for i in range(max_press):
        if not _title_value(js):
            break
        key(sub, "keyDown", "Backspace", "Backspace", 8)
        key(sub, "keyUp", "Backspace", "Backspace", 8)
        time.sleep(0.04)
    if _title_value(js):
        return False
    # blur 让 Vue 重渲染一次：state 还存着旧值的话这里会被打回原形
    js("(function(){var e=document.querySelector(%s);if(e){e.blur();}})()"
       % json.dumps(x.SEL_TITLE))
    time.sleep(0.8)
    if _title_value(js):                      # 被还原了 → state 没同步
        click_focus(sub, js)
        for i in range(100):
            if not _title_value(js):
                break
            key(sub, "keyDown", "Backspace", "Backspace", 8)
            key(sub, "keyUp", "Backspace", "Backspace", 8)
            time.sleep(0.04)
        js("(function(){var e=document.querySelector(%s);if(e){e.blur();}})()"
           % json.dumps(x.SEL_TITLE))
        time.sleep(0.8)
        if _title_value(js):
            return False
    click_focus(sub, js)
    return True


def set_title_robust(sub, js, new_title):
    """点击聚焦 → 删空 → insertText → 校验 → 失焦等 autosave。"""
    click_focus(sub, js)
    if not wipe_title(sub, js):
        return False, "删不空"
    sub.send("Input.insertText", {"text": new_title})
    time.sleep(1.2)
    got = _title_value(js)
    if got != new_title:                      # 再来一轮，别直接追加
        click_focus(sub, js)
        wipe_title(sub, js)
        sub.send("Input.insertText", {"text": new_title})
        time.sleep(1.2)
        got = _title_value(js)
    if got != new_title:
        return False, "写回不符：%r" % (got,)
    js("(function(){var e=document.querySelector(%s);if(e){e.blur();}})"
       % json.dumps(x.SEL_TITLE))
    time.sleep(6)                             # 等 Vue autosave 落地
    return True, "ok"


def clear_and_type(sub, js, new_title, attempts=2):
    """真实键盘换标题。title 框是 Vue 受控 input，value setter 那条路走不通。"""
    why = "unknown"
    for _k in range(attempts):
        ok, why = set_title_robust(sub, js, new_title)
        if ok:
            return True, "ok"
    return False, why


def retitle_one(sub, js, draft, new_title):
    """打开一条草稿 → 真实键盘换标题 → 回列表。返回 (ok, 说明)。"""
    if js(JS_OPEN_EDIT % json.dumps(draft["saved"])) != "clicked":
        return False, "点不到编辑"
    time.sleep(6)
    sub.eval("1", refresh_context=True)

    ok, why = clear_and_type(sub, js, new_title)
    if ok:
        time.sleep(2)                     # autosave 收尾
    sub.send("Page.navigate", {"url": DRAFT_URL})
    time.sleep(8)
    try:
        sub.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        sub.send("Page.bringToFront", {})
    except Exception:
        pass
    sub.eval("1", refresh_context=True)
    return ok, why


def repair_orphans(sub, js, mapping, cases, rounds=6):
    """救那些标题被拼坏的草稿（认亲表里认不出来）。

    拼接串里通常含着正确的新标题子串，靠它反查是哪条 case，然后整条重写。
    每处理完一条都要重新拉列表：改过的会排到最前，旧的 saved 快照会失效，
    抽屉也得重开（2026-09-27 实测）。
    """
    for _r in range(rounds):
        cards = x.fetch_drafts(sub=sub) or []
        orphans = [d for d in cards
                   if not mapping.get(x.norm_title(d.get("title", "")))]
        if not orphans:
            print("没有标题被拼坏的草稿了。")
            return
        done_one = False
        for d in orphans:
            hit = None
            for cid, c in cases.items():
                nt = x.make_xhs_title(c)
                if nt and nt in (d.get("title") or ""):
                    hit = (cid, nt)
                    break
            if not hit:
                print("  [跳过] 认不出是哪条：%s" % d.get("title", "")[:60])
                continue
            cid, nt = hit
            print("[修复] %-18s %s… → %s"
                  % (cid, d.get("title", "")[:30], nt), flush=True)
            ok, why = retitle_one(sub, js, d, nt)
            print("    %s %s" % ("✅" if ok else "❌", why), flush=True)
            done_one = True
            break                             # 改完列表会重排，重新拉
        if not done_one:
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="真改（默认只列计划）")
    ap.add_argument("--verify", action="store_true", help="硬刷新后验收")
    ap.add_argument("--keep-tab", action="store_true",
                    help="跑完不关标签页（默认关掉，避免攒一堆 publish 页）")
    ap.add_argument("--fix-orphans", action="store_true",
                    help="修复标题被拼坏的草稿（认亲表认不出来的那些）")
    ap.add_argument("--unmark-bad", action="store_true",
                    help="配合 --verify：把验收不符的从完成名单摘掉，便于重跑")
    ap.add_argument("--limit", type=int, default=0, help="最多改几条，0=不限")
    args = ap.parse_args()

    cdp = wp.CDP(x.PUB_PORT)
    t = cdp.new_target(DRAFT_URL)
    time.sleep(7)
    sub = wp.CDP(x.PUB_PORT)
    sub.connect_target(t["id"])
    tid = t["id"]
    # 后台标签页会被 Chrome 节流（JS/渲染变慢，实测能把单条拖到 10 分钟卡住）。
    # 打开焦点模拟 + 提到前台，让页面认为自己一直可见。
    try:
        sub.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except Exception:
        pass
    try:
        sub.send("Page.bringToFront", {})
    except Exception:
        pass

    def _cleanup():
        if not args.keep_tab:
            try:
                cdp.close_target(tid)
            except Exception:
                pass
    global _CLEANUP
    _CLEANUP = _cleanup

    def js(e):
        r = sub.eval(e, refresh_context=True)
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except Exception:
                pass
        return r

    if args.verify:
        sub.send("Page.reload", {"ignoreCache": True})
        time.sleep(10)
        sub.eval("1", refresh_context=True)

    cards = x.fetch_drafts(sub=sub) or []
    mapping = title_map()
    cases = {c["id"]: c for c in x.load_cases()}
    print("草稿 %d 条，认亲表 %d 个 key" % (len(cards), len(mapping)))

    if getattr(args, "fix_orphans", False):
        repair_orphans(sub, js, mapping, cases)
        return

    # —— 只列计划/验收分支 ——
    if args.verify or not args.go:
        plan = []
        for d in cards:
            cid = mapping.get(x.norm_title(d.get("title", "")))
            if not cid:
                print("  [孤儿] %s | %s" % (d.get("saved"), d.get("title")))
                continue
            plan.append((d, cid, x.make_xhs_title(cases[cid])))
        done0 = _done_ids()
        todo0 = [(d, cid, nt) for d, cid, nt in plan if cid not in done0]
        print("待改 %d 条，已标记完成 %d 条" % (len(todo0), len(plan) - len(todo0)))
        for d, cid, nt in todo0[:60]:
            flag = "  " if d.get("title") != nt else "✓ "
            print("  %s%-18s %-26s → %s" % (flag, cid, d.get("title"), nt))
        if args.verify:
            bad = [(cid, d.get("title"))
                   for d, cid, nt in plan if cid in done0 and d.get("title") != nt]
            print("\n验收：标题还没换过来的 %d 条 %s" % (len(bad), bad or ""))
            if bad and args.unmark_bad:
                keep = set(done0) - {cid for cid, _ in bad}
                _save_done(keep)
                print("已把这几条从完成名单里摘掉（%d → %d），下次 --go 会重跑它们"
                      % (len(done0), len(keep)))
        else:
            print("（dry-run，加 --go 真改）")
        return

    # —— 真改 ——
    # 每条之前都要**重新拉列表**：navigate 回草稿箱后抽屉是关着的，
    # 不重开就找不到「编辑」按钮；而且改过的那条会排到最前，旧的 saved
    # 快照会失效。所以不能一次性排计划（2026-09-27 实测踩过）。
    done = _done_ids()
    cases_left = {cid for cid in mapping.values() if cid not in done}
    n_max = len(cases_left) * 3 + 5
    if args.limit:
        n_max = min(n_max, args.limit)
    ok_n = fail_n = 0
    for i in range(n_max):
        if not cases_left:
            break
        try:
            drafts = x.fetch_drafts(sub=sub) or []
        except SystemExit as e:
            drafts = []
            print("  [warn] 抽屉没开：%s，等 8 秒再来" % e, flush=True)
            time.sleep(8)
            try:
                drafts = x.fetch_drafts(sub=sub) or []
            except SystemExit as e2:
                print("抽屉还是打不开，停：%s" % e2)
                break
        except Exception as e:
            print("  [warn] 拉列表异常 %s: %s" % (type(e).__name__, e), flush=True)
            time.sleep(8)
            continue
        target = None
        for d in drafts:
            cid = mapping.get(x.norm_title(d.get("title", "")))
            if cid and cid in cases_left:
                target = (d, cid)
                break
        if not target:
            print("列表里找不到还没改的草稿了，停。")
            break
        d, cid = target
        nt = x.make_xhs_title(cases[cid])
        print("[%d] %-18s %-26s → %s" % (i + 1, cid, d.get("title"), nt), flush=True)
        ok, why = retitle_one(sub, js, d, nt)
        print("    %s %s" % ("✅" if ok else "❌", why), flush=True)
        if ok:
            done.add(cid)
            cases_left.discard(cid)
            _save_done(done)
            ok_n += 1
        else:
            fail_n += 1
            if fail_n >= 3:
                print("连续失败 3 次，停下来让人看看。")
                break

    print("\n本轮改完 %d 条，失败 %d 条。硬刷新验收……" % (ok_n, fail_n), flush=True)
    sub.send("Page.reload", {"ignoreCache": True})
    time.sleep(10)
    sub.eval("1", refresh_context=True)
    cards2 = x.fetch_drafts(sub=sub) or []
    bad = [(cid, d.get("title")) for d in cards2
           for cid in [mapping.get(x.norm_title(d.get("title", "")))]
           if cid and cid in done and d.get("title") != x.make_xhs_title(cases[cid])]
    print("验收：草稿 %d 条，标题不符 %d 条 %s" % (len(cards2), len(bad), bad or ""))


if __name__ == "__main__":
    try:
        main()
    finally:
        _CLEANUP and _CLEANUP()
