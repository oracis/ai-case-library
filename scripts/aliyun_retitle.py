#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云**已发布文章**就地改标题。

背景
----
阿里云没有可用的 article/edit/<id> 入口（会被重定向到首页）。真实入口是
文章页「编辑」按钮指向的 `/article/new/cloudnative?publish=<aid>` —— 这个
地址会带出**已发布正文 + 当前标题**，改完点「发布文章」即原地更新（URL
/article/<aid> 不变）。

坑（2026-11-02 实测）
--------------------
1. `input[placeholder="请填写标题"]` 是受控输入，必须用 native setter +
   input/change 事件，直接赋 value 会被框架状态覆盖。
2. **不要调 `el.blur()`** —— 会触发校验/弹窗，导致 renderer 阻塞、
   Runtime.evaluate 30s 不返回（表现为 websocket TimeoutError）。
3. `_connect()` 返回的 cdp 在 `open()` 之前 `ws is None`，settimeout 要在
   open() 之后设。

用法
----
    python -X utf8 scripts/aliyun_retitle.py --aid 1777851 --title "新标题"
    python -X utf8 scripts/aliyun_retitle.py --aid 1777851 --title "..." --dry
"""
import argparse
import json
import sys
import time
import urllib.request

import yun_finalize as F


def set_title(cdp, title):
    js = """(()=>{const el=document.querySelector('input[placeholder="请填写标题"]');
      if(!el) return 'NO_INPUT';
      el.focus();
      const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      s.call(el, %s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return el.value;})()""" % json.dumps(title, ensure_ascii=False)
    return cdp.eval(js)


def find_buttons(cdp, words):
    js = """JSON.stringify([...document.querySelectorAll('button,a,[role=button]')]
      .map((b,i)=>({i:i,t:(b.textContent||'').trim().replace(/\\s+/g,''),
                    v:!!(b.offsetWidth||b.offsetHeight)}))
      .filter(x=>x.v&&x.t&&%s.includes(x.t)).slice(0,8))""" % json.dumps(words, ensure_ascii=False)
    raw = cdp.eval(js)
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def click_text(cdp, exact):
    js = """(()=>{const bs=[...document.querySelectorAll('button,a,[role=button]')]
      .filter(b=>(b.textContent||'').trim().replace(/\\s+/g,'')===%s&&(b.offsetWidth||b.offsetHeight));
      if(!bs.length) return 'NO_BTN';
      bs[0].click();return 'CLICKED';})()""" % json.dumps(exact, ensure_ascii=False)
    return cdp.eval(js)


CONFIRM_FINDER = """(function(){
  var cands=[...document.querySelectorAll('button,.next-btn,[role=button]')].filter(function(b){
    var t=(b.textContent||'').trim().replace(/\\s+/g,'');
    return t==='确认' && b.offsetParent!==null && (b.offsetWidth||b.offsetHeight);
  });
  return cands[0]||null;
})()"""


def real_click_confirm(cdp):
    """JS click() 对 Fusion 弹窗按钮无效（合成事件被丢），必须真实鼠标点击。

    2026-11-02 实测：光 dispatchMouseEvent 还不够 —— 窗口/标签页没有焦点时
    事件会被丢掉（弹窗纹丝不动）。必须先 `Page.bringToFront` + `Input` 先发
    一次 mouseMoved 再 press/release。
    """
    cdp.send("Page.bringToFront")
    time.sleep(0.2)
    pos = F._pos_of(cdp, CONFIRM_FINDER)
    if not isinstance(pos, dict):
        return False
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]})
    time.sleep(0.1)
    F._click_real(cdp, pos)
    return True


def dump_dialog_buttons(cdp):
    js = """JSON.stringify((()=>{const m=[...document.querySelectorAll('[class*=modal],[class*=dialog],[class*=confirm]')]
      .filter(e=>e.offsetWidth||e.offsetHeight).pop();
      if(!m) return [];
      return [...m.querySelectorAll('button,.next-btn,[role=button]')].map(b=>({
        tag:b.tagName, cls:(b.className||'').toString().slice(0,40),
        t:(b.textContent||'').trim().replace(/\\s+/g,'')}));})())"""
    raw = cdp.eval(js)
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def dialog_text(cdp):
    js = """(()=>{const d=[...document.querySelectorAll('[class*=modal],[class*=dialog],[class*=confirm]')]
      .filter(e=>(e.offsetWidth||e.offsetHeight));
      return d.map(e=>(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,120)).join(' || ');})()"""
    return cdp.eval(js) or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    proc, cdp = F._connect()
    url = "https://developer.aliyun.com/article/new/cloudnative?publish=%s" % a.aid
    try:
        cdp.open(url)
        cdp.ws.sock.settimeout(90)
        cdp.bws.sock.settimeout(90)
        time.sleep(12)
        old = cdp.eval("""(()=>{const e=document.querySelector('input[placeholder="请填写标题"]');
          return e?e.value:'NONE';})()""")
        print("旧标题:", old)
        got = set_title(cdp, a.title)
        print("写入后:", got)
        if got != a.title:
            raise SystemExit("✗ 标题未写入，停手")
        time.sleep(2)
        if a.dry:
            print("（--dry，未点发布）")
            return
        print("按钮:", [b["t"] for b in find_buttons(cdp, ["发布文章", "存为草稿", "继续发布", "确定", "确认"])])
        print("点发布:", click_text(cdp, "发布文章"))
        for i in range(6):
            time.sleep(3)
            d = dialog_text(cdp)
            if d:
                if i == 0:
                    print("  弹窗内按钮:", dump_dialog_buttons(cdp))
                if real_click_confirm(cdp):
                    print("  真实鼠标点「确认」")
            cur = cdp.eval("location.href")
            if cur and "/article/new" not in cur:
                print("✓ 跳转:", cur)
                break
        else:
            print("最终 URL:", cdp.eval("location.href"))
    finally:
        try:
            cdp.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.path.insert(0, r"D:\ai-case-library\scripts")
    main()
