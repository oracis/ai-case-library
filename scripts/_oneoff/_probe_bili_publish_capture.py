# -*- coding: utf-8 -*-
"""一次性探针：抓 B站编辑器「发布」按钮发出的**真实请求**。

背景（2026-10-20 晚，用户提的思路）：
- 已知 `draft/add` 只存草稿（带 submit=1 也不发布，草稿不进已发表列表）；
- 用户指出「都是 add，但传的参数不一样，一个存为草稿，一个就正式发布了」
  ⇒ 发布按钮发的很可能是**另一个 add**（或同 add 不同参数）。
- 今天投稿额度已满（编辑器提示「已达到当日投稿上限」），发布按钮 disabled
  ⇒ **正好安全抓形状**：服务端会以额度错误拒掉，不会真发布。

做法（沿用 2026-10-01 定案的抓包法：hook XHR，别静态读 bundle）：
1. 新开编辑器页（BILI_EDIT_URL），等 read-editor iframe 就绪；
2. 在 **iframe 的 contentWindow** 里 hook XMLHttpRequest + fetch
   （axios 实例住在 iframe 里，hook 外层 page 什么也抓不到）；
3. 找「发布」按钮 → 打印 class/disabled → 去掉 disabled 再 click；
4. 收集请求 + 响应，落盘 .cache/bili_publish_capture.json。
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")
import bilibili_publish as bp          # noqa: E402
import bili_draft_api as bapi          # noqa: E402
import wechat_publish as wp            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, ".cache", "bili_publish_capture.json")

# 在顶层 page 上下文跑：同源 iframe 可以直接摸到 contentWindow。
JS_HOOK = r"""
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(x){return (x.src||'').indexOf('read-editor')>=0;});
  if(!f) return 'no-iframe';
  var w=f.contentWindow;
  window.__cap=[];
  var XO=w.XMLHttpRequest.prototype.open,
      XS=w.XMLHttpRequest.prototype.send;
  w.XMLHttpRequest.prototype.open=function(m,u){
    this.__m=m; this.__u=u; return XO.apply(this,arguments);
  };
  w.XMLHttpRequest.prototype.send=function(b){
    var self=this;
    var rec={kind:'xhr', url:String(self.__u), method:String(self.__m),
             body:(b==null?null:String(b)).slice(0,20000),
             ts:new Date().toISOString()};
    window.__cap.push(rec);
    this.addEventListener('load', function(){
      try{
        rec.status=self.status;
        rec.response=String(self.responseText||'').slice(0,4000);
      }catch(e){ rec.resp_err=String(e); }
    });
    return XS.apply(this,arguments);
  };
  var OF=w.fetch;
  w.fetch=function(u,o){
    var rec={kind:'fetch', url:String((u&&u.url)||u),
             method:(o&&o.method)||'GET',
             body:((o&&o.body)==null?null:String(o.body)).slice(0,20000),
             ts:new Date().toISOString()};
    window.__cap.push(rec);
    return OF.apply(this,arguments).then(function(r){
      rec.status=r.status;
      r.clone().text().then(function(t){ rec.response=t.slice(0,4000); },
                            function(){});
      return r;
    });
  };
  return 'hooked';
})()
"""

JS_BTN = r"""
(function(){
  var d=null;
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(x){return (x.src||'').indexOf('read-editor')>=0;});
  if(f&&f.contentDocument) d=f.contentDocument;
  if(!d) return JSON.stringify({err:'no-iframe'});
  var btns=[].slice.call(d.querySelectorAll('button,[role=button]'));
  var pub=btns.filter(function(b){
    return (b.innerText||'').trim()==='发布';});
  var info=pub.map(function(b){
    var rc=b.getBoundingClientRect();
    return {cls:String(b.className).slice(0,80),
            disabled:b.disabled,
            aria_disabled:b.getAttribute('aria-disabled'),
            x:Math.round(rc.x), y:Math.round(rc.y),
            w:Math.round(rc.width), h:Math.round(rc.height)};
  });
  return JSON.stringify({found:info.length, btns:info});
})()
"""

JS_CLICK = r"""
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(x){return (x.src||'').indexOf('read-editor')>=0;});
  var d=f&&f.contentDocument;
  if(!d) return 'no-iframe';
  var btn=[].slice.call(d.querySelectorAll('button,[role=button]'))
    .find(function(b){return (b.innerText||'').trim()==='发布';});
  if(!btn) return 'no-btn';
  var before={cls:btn.className, disabled:btn.disabled};
  btn.removeAttribute('disabled');
  if(btn.classList) btn.classList.remove('disabled','vui_button--disabled');
  btn.scrollIntoView({block:'center'});
  btn.click();
  return JSON.stringify(before);
})()
"""


def main():
    aid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cdp = wp.CDP(bp.PUB_PORT)

    if aid is None:
        # 自己新开草稿箱页拿列表 —— 不碰 Chrome 恢复出来的旧 tab
        # （_close_stale_bili_tabs 之后立刻连旧 tab 会撞上 Chrome 的异步清理，
        #   表现为 WS 被中止 WinError 10053）。
        tid_api = cdp.new_target(bapi.DRAFT_PAGE)["id"]
        cdp.connect_target(tid_api)
        time.sleep(8)
        drafts = bapi.list_drafts(cdp) or []
        if not drafts:
            print("草稿箱空的")
            return 1
        aid = sorted(drafts, key=lambda d: d.get("mtime") or 0)[0]["article_id"]
    print("目标草稿 aid=%s" % aid)

    tid = cdp.new_target(bp.BILI_EDIT_URL % aid)["id"]
    cdp.connect_target(tid)
    try:
        if not bp._wait_iframe_ready(cdp, "read-editor", bp.SEL["title"], 30):
            print("read-editor iframe 没就绪")
            return 1
        time.sleep(3)

        print("hook:", cdp.eval(JS_HOOK, refresh_context=True))
        print("按钮:", cdp.eval(JS_BTN, refresh_context=True))

        print("click:", cdp.eval(JS_CLICK, refresh_context=True))
        cap = []
        for _ in range(25):
            time.sleep(1.0)
            raw = cdp.eval("JSON.stringify(window.__cap || [])",
                           refresh_context=True)
            try:
                cap = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:                            # noqa: BLE001
                cap = []
            if cap and any(c.get("response") for c in cap):
                break                                   # 有响应了，不用再等
        print("\n抓到 %d 个请求" % len(cap))
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"aid": aid,
                       "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "requests": cap}, f, ensure_ascii=False, indent=2)
        print("已落盘:", OUT)
        for c in cap:
            print("\n--- [%s] %s %s" % (c.get("kind"), c.get("method"),
                                        str(c.get("url"))[:150]))
            print("    status:", c.get("status"))
            body = c.get("body") or ""
            print("    body 前 400:", body[:400])
            if c.get("response"):
                print("    resp 前 300:", str(c["response"])[:300])
    finally:
        try:
            cdp.close_target(tid)
        except Exception:                                # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
