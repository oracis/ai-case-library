# -*- coding: utf-8 -*-
"""一次性探针 2：挖「发布」按钮为什么 disabled。

探针 1 已经推翻了旧结论 —— **封面设好了按钮照样是灰的**
（`.selected-cover` 在、`.vui_button--disabled` 也在）。所以「封面校验」
这个说法是错的，得换个角度找真正的原因。

思路：Vue 2 会往每个渲染出来的 DOM 上挂 `__vue__`，用它读组件自身的
$data/$props/$computed 名字和取值，找形如 `canPublish / publishDisabled`
的开关，比看文本靠谱得多。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import bilibili_publish as bp          # noqa: E402
import wechat_publish as wp            # noqa: E402

AID = int(sys.argv[1]) if len(sys.argv) > 1 else 342400

JS_VUE = """
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(f){return (f.src||'').indexOf('read-editor')>=0;});
  var d=f&&f.contentDocument?f.contentDocument:null;
  if(!d) return JSON.stringify({err:'no-iframe'});
  var sk=function(o){ try{ return JSON.stringify(o); }catch(e){ return 'ERR:'+e.message; } };
  var btn=[].slice.call(d.querySelectorAll('button,[role=button]'))
           .find(function(b){return (b.innerText||'').trim()==='发布';});
  if(!btn) return JSON.stringify({err:'no-btn'});
  var out={cls:btn.className, disabled:btn.disabled};
  var v=btn.__vue__;
  out.has_vue = !!v;
  if(!v) return JSON.stringify(out);
  // 沿 $parent 往上找带可疑字段的组件
  var chain=[], p=v, guard=0;
  while(p && guard++ < 12){
    var keys=Object.keys(p.$data||{}).concat(Object.keys(p._computedWatchers||{}));
    var hit=keys.filter(function(k){
      return /publish|submit|valid|can|disabled|complete|required/i.test(k);});
    var vals={};
    hit.forEach(function(k){ try{ vals[k]=sk(p[k]); }catch(e){ vals[k]='ERR'; } });
    chain.push({name:(p.$options && p.$options.name) || 'anon', keys:keys.slice(0,400), hit:vals});
    p=p.$parent;
  }
  out.chain=chain;
  return JSON.stringify(out);})()
"""

JS_TEXT = """
(function(){
  var f=[].slice.call(document.querySelectorAll('iframe'))
        .find(function(f){return (f.src||'').indexOf('read-editor')>=0;});
  var d=f&&f.contentDocument?f.contentDocument:null;
  if(!d) return 'no-iframe';
  return d.body.innerText.slice(0, 3000);})()
"""


def main():
    cdp = wp.CDP(bp.PUB_PORT)
    bp._close_stale_bili_tabs(cdp)
    tid = cdp.new_target(bp.BILI_EDIT_URL % AID)["id"]
    cdp.connect_target(tid)
    try:
        ok = False
        for attempt in (1, 2):
            time.sleep(7 if attempt == 1 else 4)
            if bp._wait_iframe_ready(cdp, "read-editor", bp.SEL["body"], 30):
                ok = True
                break
        if not ok:
            print("iframe 没起来")
            return
        time.sleep(3)
        v = cdp.eval(JS_VUE)
        try:
            o = json.loads(v)
        except Exception:
            print("VUE RAW:", str(v)[:3000])
            return
        print("发布按钮 cls=%s disabled=%s vue=%s"
              % (o.get("cls"), o.get("disabled"), o.get("has_vue")))
        for c in o.get("chain") or []:
            print("\n-- 组件 %s" % c.get("name"))
            print("   hit:", json.dumps(c.get("hit"), ensure_ascii=False)[:600])
            print("   keys:", ",".join(c.get("keys") or [])[:900])
        print("\n==== iframe 文本 ====")
        print((cdp.eval(JS_TEXT) or "")[:2500])
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


if __name__ == "__main__":
    main()
