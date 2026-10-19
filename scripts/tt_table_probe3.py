#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条表格方案收尾验证：两件事。

  F. 块间**换行** vs **无换行** —— 会不会多出空段落？
     （B 站那边已有结论`>[ \t\r\n]+<` → `><`，头条这边没处理过）
  G. **存草稿后重开，表格是否还在** —— 前端存活 ≠ 落库存活。
     这是铁律：不回读就不算成。

⚠ G 会真存一条草稿（头条没有「存草稿」按钮，草稿是自动存的）。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402

SEL = json.dumps(tp.SEL["body"])
TITLE = "zz表格验证-可删"

READ = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'NOT FOUND';
  return JSON.stringify({
    text: (e.innerText || '').length,
    html: e.innerHTML || '',
    tables: e.querySelectorAll('table').length,
    cells: e.querySelectorAll('td, th').length,
    empties: [].slice.call(e.querySelectorAll('p')).filter(
        function(p){ return !(p.innerText||'').trim(); }).length
  });
})(%s)"""

_CLEAR = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'no editor';
  e.focus();
  var r = document.createRange(); r.selectNodeContents(e);
  var s = getSelection(); s.removeAllRanges(); s.addRange(r);
  return 'selected';
})(%s)"""

TABLE = ('<div class="tableWrapper"><table><tbody>'
         '<tr><td><strong>付费意愿</strong></td><td><strong>3/5</strong></td></tr>'
         '<tr><td>支付可达</td><td>4/5</td></tr>'
         '<tr><td>合规空间</td><td>3/5</td></tr>'
         '<tr><td>获客迁移</td><td>1/5</td></tr>'
         '<tr><td>改造成本</td><td>1/5</td></tr>'
         '<tr><td>竞争空位</td><td>1/5</td></tr>'
         '</tbody></table></div>')

PARTS = [
    "<h2>能不能搬回国内</h2>",
    "<p><strong>中国移植分：46.1 / 100</strong></p>",
    TABLE,
    "<p>需求真实：三个障碍，一是合规，二是打包卖，三是免费替代够用。</p>",
    "<ul><li>能用现有渠道</li><li>能白嫖开源</li></ul>",
]


def clear_body(cdp):
    if cdp.eval(_CLEAR % (SEL, SEL)) != "selected":
        return False
    time.sleep(0.3)
    for ty in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": ty, "key": "a", "code": "KeyA",
            "modifiers": 2, "windowsVirtualKeyCode": 65})
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
    time.sleep(1.8)


def read(cdp):
    raw = cdp.eval(READ % (SEL, SEL))
    try:
        return json.loads(raw) if raw and raw.startswith("{") else {}
    except ValueError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-persist", action="store_true",
                    help="只做 F（换行对比），不做 G（不存草稿）")
    args = ap.parse_args()

    cdp, t = tp._open(tp.TT_EDITOR, wait=12)
    try:
        print("[login] %s" % tp._check_login(cdp))

        # ---- F：换行 vs 不换行 -------------------------------------------
        variants = [
            ("F1_换行join", "\n".join(PARTS)),
            ("F2_无换行join", "".join(PARTS)),
            ("F3_去标签间空白", re.sub(r">[ \t\r\n]+<", "><",
                                     "\n".join(PARTS))),
        ]
        for name, html in variants:
            clear_body(cdp)
            insert(cdp, html)
            d = read(cdp)
            print("\n=========== %s ===========" % name)
            print("  字数=%s <table>=%s 单元格=%s 空<p>=%s"
                  % (d.get("text"), d.get("tables"), d.get("cells"),
                     d.get("empties")))
            print("  落地：%s" % (d.get("html") or "")[:900])

        if args.skip_persist:
            clear_body(cdp)
            return

        # ---- G：存草稿 → 重开 → 回读 -------------------------------------
        print("\n\n########## G 落库回读 ##########")
        clear_body(cdp)
        insert(cdp, "".join(PARTS))
        ok_t = tp._focus_and_type(cdp, tp.SEL["title"], TITLE)
        print("标题框：%s（%r）" % (ok_t, TITLE))
        d = read(cdp)
        print("存前：<table>=%s 单元格=%s 字数=%s"
              % (d.get("tables"), d.get("cells"), d.get("text")))
        ok = tp._wait_autosave(cdp)
        print("自动保存：%s" % ok)
        time.sleep(3)
        cdp.close_target(t["id"])

        # 重开草稿箱里的这条。open_draft_editor 返回 (cdp, target, err)
        res = tp.open_draft_editor(TITLE, wait=30)
        c2, t2b, err = (list(res) + ["", ""])[:3]
        try:
            if not c2:
                print("[G] 找不到 %r —— %s" % (TITLE, err or "自动保存没成功"))
                return
            raw = c2.eval(READ % (SEL, SEL))
            try:
                d2 = json.loads(raw) if raw and raw.startswith("{") else {}
            except ValueError:
                d2 = {}
            print("\n[判据 G] 重开后：<table>=%s 单元格=%s 字数=%s"
                  % (d2.get("tables"), d2.get("cells"), d2.get("text")))
            print("  落地 HTML：%s" % (d2.get("html") or "")[:1200])
            ok_tab = (d2.get("tables") or 0) >= 1 and (d2.get("cells") or 0) >= 12
            print("\n  ==> %s" % ("✔ 表格落库存活（%d 单元格）"
                                  % d2.get("cells") if ok_tab
                                  else "✘ 表格没活下来"))
            c2.close_target(t2b["id"])
        finally:
            pass
    finally:
        pass


if __name__ == "__main__":
    main()