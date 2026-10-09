#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真表格验收：把重建后的稿子灌进头条编辑器，回读表格是否存活。

⚠ 这就是铁律「本地 HTML 对不算数，必须回读远端」的落地 —— 本地
`out/toutiao/<id>/article.html` 里有 `<table>` 不代表头条编辑器认。

判据（三条都满足才算过）：
  1. `<table>` 节点数 == 本地稿里的表格数
  2. `<td>` 单元格数 == 本地稿里的单元格数
  3. 正文里**没有**降级残留（`标签：值` 的列表形态）

⚠ 头条发文页边填边自动存草稿 ⇒ 每次验收都会在草稿箱留一条记录。
   标题统一用 `zz表格验收-可删`，跑完用 `toutiao_publish.py dedup` 收拾。

用法：
    python -X utf8 scripts/tt_table_verify.py --case uplinked-b-v
    python -X utf8 scripts/tt_table_verify.py --all      # 全库41 条（慢，约 25 分钟）
    python -X utf8 scripts/tt_table_verify.py --case quran-unlock --keep-title
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402

SEL = json.dumps(tp.SEL["body"])

READ = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'NOT FOUND';
  return JSON.stringify({
    text: (e.innerText || '').length,
    tables: e.querySelectorAll('table').length,
    cells: e.querySelectorAll('td, th').length,
    //降级残留判据：「付费意愿：3/5」这种被拍平成列表的形态
    degraded: [].slice.call(e.querySelectorAll('li')).filter(
        function(li){ return /：\\s*\\d+\\/5$/.test(li.innerText || ''); }).length,
    sample: (e.innerHTML || '').slice(0, 400)
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
    time.sleep(0.9)
    return True


def read(cdp):
    raw = cdp.eval(READ % (SEL, SEL))
    try:
        return json.loads(raw) if raw and raw.startswith("{") else {}
    except ValueError:
        return {}


def verify_one(cdp, cid, keep_title=False):
    path = os.path.join(tp.OUT, cid, "article.html")
    with open(path, encoding="utf-8") as f:
        full = f.read()
    body = full.split("\n", 1)[1] if full.startswith("<h1>") else full
    want_tables = body.count("<table")
    want_cells = body.count("<td>")

    clear_body(cdp)
    title = ("zz表格验收-可删" if not keep_title else None)
    if title:
        tp._focus_and_type(cdp, tp.SEL["title"], title)
    cdp.eval("(function(){var e=document.querySelector(%s);"
             "if(e){e.focus();} return 'ok';})()" % SEL)
    cdp.send("Runtime.evaluate", {
        "expression": ("document.execCommand('insertHTML', false, %s)"
                       % json.dumps(body, ensure_ascii=False)),
        "returnByValue": True})
    time.sleep(2.0)
    d = read(cdp)
    got_t, got_c = d.get("tables") or 0, d.get("cells") or 0
    ok = (got_t == want_tables and got_c == want_cells
          and not d.get("degraded"))
    return ok, want_tables, want_cells, got_t, got_c, d.get("degraded") or 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="uplinked-b-v")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep-title", action="store_true",
                    help="用真标题（验收完草稿箱里就是正式稿，可直接发）")
    ap.add_argument("--cleanup", action="store_true", default=True)
    args = ap.parse_args()

    cases = ([x for x in tp.load_cases() if x["id"] == args.case]
             if not args.all else list(tp.load_cases()))
    if not cases:
        print("找不到案例 %s" % args.case)
        return 1

    cdp, t = tp._open(tp.TT_EDITOR, wait=12)
    passed = failed = 0
    try:
        print("[login] %s" % tp._check_login(cdp))
        for c in cases:
            try:
                ok, wt, wc, gt, gc, deg = verify_one(
                    cdp, c["id"], args.keep_title)
            except Exception as e:                # noqa: BLE001
                print("  %-20s [ERR] %s" % (c["id"], e))
                failed += 1
                continue
            flag = "✔" if ok else "✘"
            print("  %s %-20s 本地 %d表/%d格 → 远端 %d表/%d格%s"
                  % (flag, c["id"], wt, wc, gt, gc,
                     "（降级残留 %d）" % deg if deg else ""))
            passed += ok
            failed += (not ok)
        clear_body(cdp)
    finally:
        cdp.close_target(t["id"])
    print("\n验收：%d/%d 通过" % (passed, passed + failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())