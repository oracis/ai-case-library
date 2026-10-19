#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条编辑器「表格能力」探针（一次性诊断脚本，可反复跑）。

要回答三个问题：
  Q1 工具栏里有没有「插入表格」入口？（扫 title/aria-label/innerText/class）
  Q2 `insertHTML` 灌真`<table>` 会被保留，还是被降级成一个 `<p>`？
  Q3 降级成什么样？（把编辑器 innerHTML 原样吐出来）

用法：
    python -X utf8 scripts/tt_table_probe.py            # 只扫工具栏 + schema
    python -X utf8 scripts/tt_table_probe.py --insert   # 额外真灌一次 table
    python -X utf8 scripts/tt_table_probe.py --cleanup   # 灌完清空正文（避免留草稿）

⚠ 头条发文页**边填边自动存草稿**（toutiao_publish.publish_one 的注释），
   所以任何真填内容的动作都会在草稿箱留一条记录 —— 探针默认只读。
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_publish as wp                                    # noqa: E402
import toutiao_publish as tp                                   # noqa: E402


# ---- Q1：扫工具栏 -----------------------------------------------------------
_SCAN_TOOLBAR = r"""(function(){
  var out = {matches: [], icons: [], editable: []};
  var KEY = /表格|table|网格|grid|单元格|cell/i;
  // 1) 所有可点元素里找关键字
  var all = [].slice.call(document.querySelectorAll(
      'button, a, [role=button], [class*=icon], [class*=toolbar] *'));
  var seen = {};
  all.forEach(function(e){
    var txt = ((e.innerText||'') + ' ' + (e.getAttribute('title')||'') + ' ' +
               (e.getAttribute('aria-label')||'') + ' ' + (e.getAttribute('data-tooltip')||'')).trim();
    var cls = (e.className||'').toString();
    if (KEY.test(txt) || KEY.test(cls)){
      var k = txt + '|' + cls;
      if (seen[k]) return;
      seen[k] = 1;
      out.matches.push({tag: e.tagName, text: txt.slice(0,60), cls: cls.slice(0,90)});
    }
  });
  // 2) 工具栏按钮图标（多半只有 svg，用 class 认）
  var icons = [].slice.call(document.querySelectorAll(
      'svg, [class*=svg], i[class*=icon], [class*=toolbar] > *'));
  icons.slice(0, 80).forEach(function(e){
    var cls = (e.className||'').toString() || (e.getAttribute('class')||'');
    if (!cls) cls = e.tagName;
    out.icons.push(cls.slice(0,70));
  });
  // 3) 正文区
  var ed = document.querySelector(%s);
  out.editable.push(ed ? ((ed.className||'').toString().slice(0,60) +
            ' | ce=' + ed.getAttribute('contenteditable')) : 'NOT FOUND');
  return JSON.stringify(out);
})(%s)"""

# ProseMirror / Tiptap schema 探测：看编辑器认哪些节点
_PROBE_SCHEMA = r"""(function(){
  var ed = document.querySelector(%s);
  if (!ed) return 'no editor';
  var out = {pmView: null, keys: [], tableNodes: 0, existingTables: 0};
  // ProseMirror 实例挂在编辑器 DOM 上的概率低，改为统计既有节点类型
  var seen = {};
  [].slice.call(ed.querySelectorAll('*')).forEach(function(e){
    var n = e.tagName.toLowerCase();
    seen[n] = (seen[n]||0) + 1;
  });
  out.pmView = seen;
  out.tableNodes = seen['table'] || 0;
  return JSON.stringify(out);
})(%s)"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--insert", action="store_true",
                    help="真灌一次<table>看是否被降级（会在草稿箱留记录）")
    ap.add_argument("--cleanup", action="store_true",
                    help="结束前清空正文（配合 --insert 用）")
    ap.add_argument("--keep", action="store_true",
                    help="不关标签页（自己接着看）")
    args = ap.parse_args()

    sel = json.dumps(tp.SEL["body"])
    cdp, t = tp._open(tp.TT_EDITOR, wait=12)
    try:
        href = tp._check_login(cdp)
        print("[login] %s" % href)

        raw = cdp.eval(_SCAN_TOOLBAR % (sel, sel))
        data = json.loads(raw) if raw and raw.startswith("{") else {}
        print("\n=== Q1 工具栏找「表格」 ===")
        if data.get("matches"):
            for m in data["matches"]:
                print("  [命中] <%s> text=%r cls=%r"
                      % (m["tag"], m["text"], m["cls"]))
        else:
            print("  [零命中] 工具栏里没有 title/aria-label/class 含"
                  "表格/table/网格/grid/单元格/cell 的元素")
        print("\n--- 正文区 ---")
        for e in data.get("editable", []):
            print("  %s" % e)
        print("\n--- 工具栏图标 class（前 60） ---")
        for c in (data.get("icons") or [])[:60]:
            print("  %s" % c)

        raw2 = cdp.eval(_PROBE_SCHEMA % (sel, sel))
        if raw2 and raw2.startswith("{"):
            d2 = json.loads(raw2)
            print("\n=== Q2 编辑器内节点统计 ===")
            for k, v in sorted((d2.get("pmView") or {}).items(),
                               key=lambda kv: -kv[1])[:25]:
                print("  %-14s %d" % (k, v))

        if args.insert:
            print("\n=== Q3 真灌 <table> ===")
            html = ("<table><tbody>"
                    "<tr><td>付费意愿</td><td>3/5</td></tr>"
                    "<tr><td>支付可达</td><td>4/5</td></tr>"
                    "<tr><td>合规空间</td><td>3/5</td></tr>"
                    "</tbody></table>")
            cdp.eval("(function(){var e=document.querySelector(%s);"
                     "if(e){e.focus();} return 'ok';})()" % sel)
            cdp.send("Runtime.evaluate", {
                "expression": ("document.execCommand('insertHTML', false, %s)"
                               % json.dumps(html, ensure_ascii=False)),
                "returnByValue": True})
            time.sleep(2)
            got = cdp.eval(
                "(function(){var e=document.querySelector(%s);"
                "return e? (e.innerHTML||'') : 'NOT FOUND';})()" % sel)
            print("  灌入后编辑器 innerHTML（截 1500 字）：")
            print("  " + (got or "")[:1500].replace("\n", "\n  "))
            ntab = cdp.eval(
                "(function(){var e=document.querySelector(%s);"
                "return e? e.querySelectorAll('table').length : -1;})()" % sel)
            print("\n  [判据] <table> 节点数 = %s（>0 才叫真表格）" % ntab)
            if args.cleanup:
                time.sleep(0.5)
                cdp.eval("(function(){var e=document.querySelector(%s);"
                         "if(!e) return 'x';"
                         "var r=document.createRange(); r.selectNodeContents(e);"
                         "var s=getSelection(); s.removeAllRanges(); s.addRange(r);"
                         "return 'sel';})()" % sel)
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "a", "code": "KeyA",
                    "modifiers": 2, "windowsVirtualKeyCode": 65})
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "a", "code": "KeyA",
                    "modifiers": 2, "windowsVirtualKeyCode": 65})
                time.sleep(0.4)
                for ty in ("keyDown", "keyUp"):
                    cdp.send("Input.dispatchKeyEvent", {
                        "type": ty, "key": "Delete", "code": "Delete",
                        "windowsVirtualKeyCode": 46})
                time.sleep(1)
                left = cdp.eval(
                    "(function(){var e=document.querySelector(%s);"
                    "return e? (e.innerText||'').trim().length : -1;})()" % sel)
                print("  [cleanup] 清空后正文字数 = %s" % left)
    finally:
        if args.keep:
            print("\n[keep] 标签页留着：%s" % t["id"])
        else:
            cdp.close_target(t["id"])


if __name__ == "__main__":
    main()