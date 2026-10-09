#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条表格方案实测：对比几种 HTML 写法在编辑器里的落地结果。

要回答：
  A. `<table>` 前后带换行（`\n`）会不会多出空段落？
  B. `<td>` 里放裸文本 vs 放 `<p>`，哪种被规范化成什么？
  C. 带表头行（两行：第一行是标题列）能不能活？
  D. 连续两张表（中间隔一个段落）能不能活？
  E. 灌进去后**存草稿再重开**是否还在？（前端存活 ≠ 落库存活）

⚠ 每轮都会在草稿箱留记录 —— 跑完用 --cleanup 清空，并用 toutiao_publish
   dedup 或人工删掉重复草稿。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402

SEL = json.dumps(tp.SEL["body"])

# 清空正文：走真实按键管线（execCommand 清不干净，ProseMirror state 不同步）
_CLEAR = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'no editor';
  e.focus();
  var r = document.createRange();
  r.selectNodeContents(e);
  var s = getSelection();
  s.removeAllRanges();
  s.addRange(r);
  return 'selected';
})(%s)"""

_READ = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'NOT FOUND';
  return JSON.stringify({
    text: (e.innerText || '').length,
    html: e.innerHTML || '',
    tables: e.querySelectorAll('table').length,
    empties: [].slice.call(e.querySelectorAll('p')).filter(
        function(p){ return !(p.innerText||'').trim(); }).length
  });
})(%s)"""


def clear_body(cdp):
    got = cdp.eval(_CLEAR % (SEL, SEL))
    if got != "selected":
        return False
    time.sleep(0.3)
    for m in (2,):                             # Ctrl+A
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "code": "KeyA",
            "modifiers": m, "windowsVirtualKeyCode": 65})
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA",
            "modifiers": m, "windowsVirtualKeyCode": 65})
    time.sleep(0.3)
    for ty in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": ty, "key": "Delete", "code": "Delete",
            "windowsVirtualKeyCode": 46})
    time.sleep(0.8)
    return True


def insert(cdp, html):
    cdp.eval("(function(){var e=document.querySelector(%s);"
             "if(e){e.focus();} return 'ok';})()" % SEL)
    cdp.send("Runtime.evaluate", {
        "expression": ("document.execCommand('insertHTML', false, %s)"
                       % json.dumps(html, ensure_ascii=False)),
        "returnByValue": True})
    time.sleep(1.6)


def read(cdp):
    raw = cdp.eval(_READ % (SEL, SEL))
    try:
        return json.loads(raw) if raw and raw.startswith("{") else {}
    except ValueError:
        return {}


ROW = ("<tr><td>%s</td><td>%s</td></tr>")

CASES = [
    ("A_带换行", "<h2>能不能搬回国内</h2>\n"
     "<p><strong>中国移植分：46.1 / 100</strong></p>\n"
     '<div class="tableWrapper"><table><tbody>'
     + "".join(ROW % (a, b) for a, b in [("付费意愿", "3/5"),
                                         ("支付可达", "4/5"),
                                         ("合规空间", "3/5")])
     + "</tbody></table></div>\n"
     "<p>正文继续。</p>\n"),
    ("B_td裸文本", "<div class=\"tableWrapper\"><table><tbody>"
     + "".join(ROW % (a, b) for a, b in [("付费意愿", "3/5"),
                                         ("支付可达", "4/5")])
     + "</tbody></table></div>"),
    ("C_带表头", "<div class=\"tableWrapper\"><table><tbody>"
     "<tr><th>维度</th><th>内容</th></tr>"
     + "".join(ROW % (a, b) for a, b in [("付费意愿", "3/5"),
                                         ("支付可达", "4/5")])
     + "</tbody></table></div>"),
    ("D_连续两表", "<div class=\"tableWrapper\"><table><tbody>"
     + "".join(ROW % (a, b) for a, b in [("付费意愿", "3/5"),
                                         ("支付可达", "4/5")])
     + "</tbody></table></div>\n"
     "<p>中间段落</p>\n"
     "<div class=\"tableWrapper\"><table><tbody>"
     + "".join(ROW % (a, b) for a, b in [("获客迁移", "1/5"),
                                         ("改造成本", "1/5")])
     + "</tbody></table></div>"),
    ("E_td里加粗", "<div class=\"tableWrapper\"><table><tbody>"
     "<tr><td><strong>付费意愿</strong></td><td><strong>3/5</strong></td></tr>"
     "<tr><td><p>支付可达</p></td><td><p>4/5</p></td></tr>"
     "</tbody></table></div>"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    cdp, t = tp._open(tp.TT_EDITOR, wait=12)
    try:
        print("[login] %s" % tp._check_login(cdp))
        for name, html in CASES:
            clear_body(cdp)
            insert(cdp, html)
            d = read(cdp)
            print("\n=========== %s ===========" % name)
            print("  字数=%s  <table>数=%s  空<p>数=%s"
                  % (d.get("text"), d.get("tables"), d.get("empties")))
            print("  落地 HTML：")
            print("  " + (d.get("html") or "")[:1200].replace("\n", "\n  "))
        if args.cleanup:
            clear_body(cdp)
            print("\n[cleanup] 末尾正文字数 = %s" % read(cdp).get("text"))
    finally:
        cdp.close_target(t["id"])


if __name__ == "__main__":
    main()