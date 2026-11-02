# -*- coding: utf-8 -*-
"""小红书存量笔记批量改标题（走 /publish/update 直链）。

用法：
  python xhs_retitle.py --map data/xhs_title_rewrite_2026-11-02b.json --only 6ad9843b --dry
  python xhs_retitle.py --map ... --limit 5
  python xhs_retitle.py --list           # 回读列表标题

要点（都踩过）：
- 编辑页必须用 Target.createTarget 开新标签，旧标签的 React 状态可能是脏的；
- 标题 input[placeholder="填写标题会有更多赞哦"]，native setter + input 事件；
- 提交按钮是 <xhs-publish-btn>，shadowRoot closed，只能按 host 几何中心点：
  (x + w/2, y + h*0.46)；
- 点完自动跳 /new/note-manager，列表标题即刻更新。
"""
import argparse
import json
import sys
import time

sys.path.insert(0, r"D:\ai-case-library\scripts")

from wechat_publish import CDP  # noqa: E402

PORT = 9223
EDIT_TPL = "https://creator.xiaohongshu.com/publish/update?id=%s&noteType=normal"
TITLE_SEL = 'input[placeholder="填写标题会有更多赞哦"]'


def half_len(s):
    """小红书计数器口径近似：CJK 记 1，ASCII 记 0.5。"""
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in s)


def set_title(c, text):
    js = """(function(){var el=document.querySelector('%s');
      if(!el) return 'NO_INPUT';
      el.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,%s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return el.value;})()""" % (TITLE_SEL, json.dumps(text, ensure_ascii=False))
    return c.eval(js)


def read_title(c):
    return c.eval("""(function(){var e=document.querySelector('%s');return e?e.value:'NONE';})()""" % TITLE_SEL)


def click_publish(c):
    pos = c.eval("""(function(){var h=document.querySelector('xhs-publish-btn');
      if(!h) return 'NO_HOST';
      var r=h.getBoundingClientRect();
      return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height*0.46),
                             w:Math.round(r.width),h:Math.round(r.height)});})()""")
    if not pos or pos == "NO_HOST":
        return "NO_HOST"
    p = json.loads(pos)
    c.send("Page.bringToFront")
    time.sleep(0.4)
    c.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": p["x"], "y": p["y"]})
    time.sleep(0.4)
    c.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"], "y": p["y"],
                                        "button": "left", "clickCount": 1})
    c.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"], "y": p["y"],
                                        "button": "left", "clickCount": 1})
    return "CLICKED(%d,%d)" % (p["x"], p["y"])


def do_one(item, dry=False):
    nid = item.get("nid_full") or item["nid"]
    new = item["new"]
    c = CDP(PORT)
    t = c.new_target(EDIT_TPL % nid)
    tid = t["id"]
    out = {"nid": nid, "case": item.get("case"), "new": new, "ok": False, "msg": ""}
    try:
        if not c.connect_target(tid):
            out["msg"] = "connect failed"
            return out
        # 编辑页首屏要 10~15s 才挂出标题框。
        # 注意：页面跳转期间 eval 会因为执行上下文销毁而**永久阻塞**，
        # 所以不能「每 2s 轮询」，必须等跳转结束再读，并给 WS 设超时兜底。
        try:
            c.ws.sock.settimeout(60)
        except Exception:
            pass
        time.sleep(12)
        old = read_title(c)
        for _ in range(3):
            if old != "NONE":
                break
            time.sleep(8)
            try:
                old = read_title(c)
            except Exception:                                    # noqa: BLE001
                old = "NONE"
        c.send("Emulation.setDeviceMetricsOverride",
               {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(2)
        out["old"] = old
        if old == "NONE":
            out["msg"] = "title input not found (page not ready?)"
            return out
        if old.strip() == new.strip():
            out["ok"] = True
            out["msg"] = "already target"
            return out
        set_title(c, new)
        time.sleep(1.5)
        back = read_title(c)
        out["readback"] = back
        if back.strip() != new.strip():
            out["msg"] = "write failed: %r" % back
            return out
        if dry:
            out["ok"] = True
            out["msg"] = "dry: written, not submitted"
            return out
        out["click"] = click_publish(c)
        time.sleep(8)
        out["url"] = c.eval("location.href")
        if "note-manager" in (out["url"] or ""):
            out["ok"] = True
            out["msg"] = "submitted"
        else:
            txt = c.eval("(document.body.innerText||'').replace(/\\n+/g,' | ').slice(0,160)")
            out["msg"] = "no redirect: %s" % txt
        return out
    finally:
        try:
            c.close_target(tid)
        except Exception:
            pass


def list_titles():
    ts = [x for x in CDP(PORT).list_targets() if "note-manager" in (x.get("url") or "")]
    if not ts:
        return "NO_MANAGER_TAB"
    c = CDP(PORT)
    c.connect_target(ts[0]["id"])
    time.sleep(3)
    for _ in range(4):
        c.eval("""(function(){var d=document.querySelector('.content');if(d){d.scrollTop=d.scrollHeight-10;}})()""")
        time.sleep(3)
    return c.eval("""(function(){var out=[];var cards=document.querySelectorAll('.note-card');
      for(var i=0;i<cards.length;i++){var t=cards[i].querySelector('[class*=title]');
        out.push((t?t.innerText.replace(/\\n/g,'/'):''));}
      return JSON.stringify(out);})()""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=r"D:\ai-case-library\data\xhs_title_rewrite_2026-11-02b.json")
    ap.add_argument("--only", help="逗号分隔的 note_id 前缀")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        print(list_titles())
        return

    items = json.load(open(a.map, encoding="utf-8"))["items"]
    if a.only:
        keys = [k.strip() for k in a.only.split(",") if k.strip()]
        items = [it for it in items if any((it.get("nid_full") or it["nid"]).startswith(k) for k in keys)]
    if a.limit:
        items = items[: a.limit]

    print("待处理 %d 条" % len(items))
    over = [it for it in items if half_len(it["new"]) > 20]
    for it in over:
        print("  !! 超 20 字: %s %.1f %s" % (it["nid"][:8], half_len(it["new"]), it["new"]))
    if over:
        print("有超长标题，先修再跑")
        return

    results = []
    for i, it in enumerate(items, 1):
        print("[%d/%d] %s %s" % (i, len(items), it["nid"][:8], it["new"]), flush=True)
        try:
            r = do_one(it, dry=a.dry)
        except Exception as e:  # noqa: BLE001
            r = {"nid": it["nid"], "ok": False, "msg": "EXC %s" % e}
        print("      ->", r.get("ok"), r.get("msg"), "| old:", r.get("old"), flush=True)
        results.append(r)
        time.sleep(3)

    ok = sum(1 for r in results if r.get("ok"))
    print("\n成功 %d / %d" % (ok, len(results)))
    json.dump(results, open(r"D:\ai-case-library\tmp\xhs_retitle_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for r in results:
        if not r.get("ok"):
            print("  FAIL", r["nid"][:8], r.get("msg"))


if __name__ == "__main__":
    main()
