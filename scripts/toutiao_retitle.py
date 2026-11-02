#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条号：改**已发布**文章的标题（2026-11-02 打通）。

为什么不用删稿重发
------------------
头条号作品管理页每篇文章卡片上都有「修改」入口，点开是
    /profile_v4/graphic/publish?from=edit&pgc_id=<gid>
带原始正文与标题的编辑器，改完点「发布」即提交修改并重新进审核。
删稿重发会丢掉已积累的展现/阅读与评论，能用修改就别删。

两个实测坑
----------
1. 卡片上的「修改」是 <span>，**JS click() 不生效**（不跳转也不开新窗），
   必须 Page.bringToFront + Input.dispatchMouseEvent 真鼠标点。
2. 点击后**跳转到新标签页**，不是原地路由；要按 pgc_id 去 /json 里认领。
3. 标题框 placeholder 写明「2～30个字」，超 30 字服务端会截断（本脚本
   先本地判长，超了直接报错不动手）。

用法
----
  python toutiao_retitle.py --gid 7703128531567477282 --title "…" --dry
  python toutiao_retitle.py --gid 7703128531567477282 --title "…"
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as T          # noqa: E402
import wechat_publish as wp          # noqa: E402

TITLE_MAX = 30
TITLE_SEL = "textarea[placeholder*='文章标题'], input[placeholder*='文章标题']"


def js_rect(cdp, finder):
    """finder 必须返回**元素**，这里取它的视口坐标（已滚到中间）。"""
    js = """(()=>{var el=(%s);if(!el) return 'NONE';
      el.scrollIntoView({block:'center'});
      var r=el.getBoundingClientRect();
      if(!r.width||!r.height) return 'HIDDEN';
      return JSON.stringify({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});})()""" % finder
    raw = cdp.eval(js)
    if not raw or not str(raw).startswith("{"):
        return None
    return json.loads(raw)


def real_click(cdp, finder):
    p = js_rect(cdp, finder)
    if not p:
        return False
    try:
        cdp.send("Page.bringToFront")
    except Exception:                                    # noqa: BLE001
        pass
    time.sleep(0.3)
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": p["x"], "y": p["y"]})
    time.sleep(0.2)
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": p["x"],
                                          "y": p["y"], "button": "left", "clickCount": 1})
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": p["x"],
                                          "y": p["y"], "button": "left", "clickCount": 1})
    return True


def find_edit_tab(cdp, gid):
    for x in cdp.list_targets():
        u = x.get("url") or ""
        if x.get("type") == "page" and ("pgc_id=%s" % gid) in u:
            return x["id"], u
    return None, None


def open_edit_tab(cdp, gid):
    """在作品管理页真鼠标点该卡片的「修改」，返回新标签页 id。"""
    tid_list = [x for x in cdp.list_targets() if x.get("type") == "page"
                and "graphic/articles" in (x.get("url") or "")]
    if tid_list:
        list_tab = tid_list[0]["id"]
        cdp.connect_target(list_tab)
        time.sleep(3)
    else:
        t = cdp.new_target(T.TT_ARTICLES_PAGE)
        list_tab = t["id"]
        cdp.connect_target(list_tab)
        time.sleep(8)

    finder = """(()=>{var cards=[...document.querySelectorAll('.article-card-wrap')];
      var c=cards.find(function(e){
        var a=e.querySelector('a[href*="%s"]'); return !!a;});
      if(!c) return null;
      var sp=[...c.querySelectorAll('span')].find(function(e){return (e.textContent||'').trim()==='修改';});
      return sp||null;})()""" % gid
    if not real_click(cdp, finder):
        return None

    for _ in range(10):
        time.sleep(2)
        tid, url = find_edit_tab(cdp, gid)
        if tid:
            return tid
    return None


def set_title(cdp, title):
    js = """(()=>{var el=document.querySelector(%s);
      if(!el) return 'NO_INPUT';
      el.focus();
      var proto = el.tagName==='TEXTAREA'?HTMLTextAreaElement:HTMLInputElement;
      Object.getOwnPropertyDescriptor(proto.prototype,'value').set.call(el, %s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return el.value;})()""" % (json.dumps(TITLE_SEL), json.dumps(title, ensure_ascii=False))
    return cdp.eval(js)


def read_title(cdp):
    return cdp.eval("(()=>{var el=document.querySelector(%s);return el?el.value:'NONE';})()"
                    % json.dumps(TITLE_SEL))


def live_title(gid):
    """从已发布列表 API 回读标题（唯一的真相来源）。"""
    cdp, t = T._open(T.TT_ARTICLES_PAGE, wait=8)
    try:
        for pg in range(1, 4):
            cdp.send("Page.navigate", {"url": T._article_api_url((pg - 1) * 10, pg)})
            time.sleep(4)
            txt = cdp.eval("document.body.innerText") or ""
            if not txt.startswith("{"):
                break
            d = json.loads(txt)
            for it in (d.get("data") or []):
                cell = (it.get("assembleCell") or {}).get("itemCell") or {}
                base = cell.get("articleBase") or {}
                if str(base.get("gidStr") or "") == str(gid):
                    return base.get("title") or ""
            if not d.get("has_more"):
                break
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:                                # noqa: BLE001
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gid", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--dry", action="store_true", help="只填标题并回读，不点发布")
    ap.add_argument("--keep", action="store_true", help="跑完不关编辑页（留给人工看）")
    a = ap.parse_args()

    title = re.sub(r"\s+", " ", a.title).strip()
    n = len(title)
    print("目标标题（%d 字）：%s" % (n, title))
    if n > TITLE_MAX:
        print("✗ 超过头条 30 字上限，会被服务端截断 —— 先改短")
        return 2
    if n < 2:
        print("✗ 标题太短")
        return 2

    cdp = wp.CDP(T.CDP_PORT)
    tid, url = find_edit_tab(cdp, a.gid)
    if tid:
        print("复用已开的编辑页：%s" % url)
    else:
        print("在作品管理页点「修改」…")
        tid = open_edit_tab(cdp, a.gid)
        if not tid:
            print("✗ 打不开编辑页（卡片里没找到该 gid 的「修改」）")
            return 3
        print("✓ 编辑页已开")

    cdp.connect_target(tid)
    time.sleep(6)
    # 9223 的窗口有时只有 780x440，编辑器右下角的「发布」会被右侧「头条创作助手」
    # 侧栏压住，鼠标点下去落在侧栏上、什么也不发生。放大到 1440x900 才露出按钮。
    try:
        cdp.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    except Exception:                                    # noqa: BLE001
        pass
    time.sleep(2)
    # 刷新一次拿服务端真实标题：预演（--dry）会把 DOM 里的值改掉但没提交，
    # 不刷新会把「本地写过」误当成「线上已是新标题」而跳过发布。
    try:
        cdp.send("Page.reload", {"ignoreCache": True})
    except Exception:                                    # noqa: BLE001
        pass
    time.sleep(10)
    old = read_title(cdp)
    print("原标题（%d 字）：%s" % (len(old or ""), old))
    if old == title:
        print("标题已是目标值，无需改动")
        return 0

    got = set_title(cdp, title)
    time.sleep(2)
    now = read_title(cdp)
    print("写入回读：%s" % now)
    if now != title:
        print("✗ 标题没写进去（React 状态未同步）")
        return 4

    if a.dry:
        print("（预演完成，未点发布；编辑页保留）")
        return 0

    print("点「发布」…")
    ok = real_click(cdp, """(()=>{var b=[...document.querySelectorAll('button')]
      .find(function(x){return (x.textContent||'').trim().replace(/\\s+/g,'')==='发布';});
      return b||null;})()""")
    if not ok:
        print("✗ 没找到「发布」按钮")
        return 5

    # 可能弹二次确认
    for i in range(8):
        time.sleep(3)
        u = cdp.eval("location.href") or ""
        if "publish?from=edit" not in u:
            print("✓ 已离开编辑页：%s" % u)
            break
        dlg = cdp.eval("""(()=>{var d=[...document.querySelectorAll('[class*=modal],[class*=dialog]')]
          .filter(function(e){return e.offsetWidth;});
          return d.length?d[0].innerText.replace(/\\s+/g,' ').slice(0,80):'';})()""")
        if dlg:
            print("  弹窗：%s" % dlg)
            for w in ("确认发布", "确定", "确认", "继续发布"):
                if real_click(cdp, """(()=>{var b=[...document.querySelectorAll('button')]
                  .find(function(x){return (x.textContent||'').trim().replace(/\\s+/g,'')==='%s';});
                  return b||null;})()""" % w):
                    print("  已点：%s" % w)
                    break
    else:
        print("⚠ 弹窗没消，停在：%s" % cdp.eval("location.href"))

    print("等服务端回读…")
    for i in range(5):
        time.sleep(9)
        lt = live_title(a.gid)
        print("  第 %d 次回读：%s" % (i + 1, lt))
        if lt == title:
            print("✓ 线上标题已更新")
            return 0
    print("⚠ 列表接口仍是旧标题（修改后需重新审核，可能有缓存延迟）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
