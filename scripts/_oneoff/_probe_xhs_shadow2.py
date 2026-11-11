# -*- coding: utf-8 -*-
"""一次性探针：`<xhs-publish-btn>` 的内部按钮是不是在 **closed shadow DOM** 里。

线索链：
· 抽屉编辑器里有 `xhs-publish-btn` 组件（`submit-text="发布"`），
  但 `document.querySelectorAll('button.ce-btn.bg-red')` 是空的 ——
  ⇒ 按钮是组件的**内部实现**，不在 light DOM 里。
· 之前的 shadow 探针统计 `shadowRoot` 数为 0 —— 那是 closed 模式，
  `element.shadowRoot` 返回 null，JS 侧根本看不见。
⇒ 走 CDP `DOM.getDocument({pierce:true})`，协议层能穿透 closed shadow。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import xhs_publish as x


def walk(node, out, depth=0, path=""):
    name = node.get("nodeName") or ""
    attrs = {}
    for a in node.get("attributes") or []:
        attrs[a] = None
    if name.lower() in ("xhs-publish-btn", "button") or "ce-btn" in str(
            node.get("attributes")):
        out.append({"depth": depth, "name": name,
                    "attrs": (node.get("attributes") or [])[:24],
                    "path": path})
    kids = node.get("children") or []
    for i, k in enumerate(kids):
        walk(k, out, depth + 1, path + "/%s[%d]" % (name, i))
    # shadow roots 也在 pierce 结果里
    for sr in node.get("shadowRoots") or []:
        out.append({"depth": depth, "name": "#shadow-root(" + name + ")",
                    "attrs": [], "path": path})
        for i, k in enumerate(sr.get("children") or []):
            walk(k, out, depth + 1, path + "/#shadow[%d]" % i)


def main():
    sub = x._draft_cdp()
    drafts = x.fetch_drafts(sub)
    d, _ = x.pick_draft(drafts)
    print("目标：%s | %s" % (d.get("saved"), d.get("title")))
    ok, got = x._open_draft_editor(sub, d.get("title"))
    print("编辑器：%s %r" % (ok, got))
    if not ok:
        return
    time.sleep(4)

    print("--- 组件属性（light DOM 看得见的部分）")
    print(sub.eval("""
(function(){
  var c=document.querySelector('xhs-publish-btn');
  if(!c) return 'no component';
  var o={attrs:{}, shadow: c.shadowRoot ? 'open' : 'null(closed or none)',
         rect:(function(){var r=c.getBoundingClientRect();
               return [Math.round(r.x),Math.round(r.y),
                       Math.round(r.width),Math.round(r.height)];})(),
         html_len: c.outerHTML.length};
  [].slice.call(c.attributes).forEach(function(a){ o.attrs[a.name]=a.value; });
  return JSON.stringify(o);})()
""", refresh_context=True))

    print("--- CDP DOM.getDocument(pierce=true) 找按钮")
    doc = sub.send("DOM.getDocument", {"depth": -1, "pierce": True})
    root = (doc.get("result") or {}).get("root") or {}
    out = []
    walk(root, out)
    for o in out[:40]:
        print("  %s%s attrs=%s" % ("  " * min(o["depth"], 6), o["name"],
                                   o["attrs"]))


if __name__ == "__main__":
    main()
