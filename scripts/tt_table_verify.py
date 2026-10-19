#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真表格验收：把重建后的稿子灌进头条编辑器，回读表格是否存活。

⚠ 这就是铁律「本地 HTML 对不算数，必须回读远端」的落地 —— 本地
`out/toutiao/<id>/article.html` 里有 `<table>` 不代表头条编辑器认。

判据（三条都满足才算过）：
  1. `<table>` 节点数 == 本地稿里的表格数
  2. `<td>` 单元格数 == 本地稿里的单元格数
  3. 正文里**没有**降级残留（`标签：值` 的列表形态）

🚨🚨 2026-10-09 事故（本脚本即元凶，必须记住）：
   旧版 `clear_body()` **无条件 `return True`**，从不回读验证清空是否真成功；
   而清空失败后代码仍继续 `insertHTML` ⇒ 新正文被**追加**到旧正文后面
   ⇒ 远端表格数翻倍（`kibu` 本地 3 表/34 格 → 远端 6 表/66 格）。
   旧版还把标题写成 `zz表格验收-可删`，实测在草稿箱留下 2 条垃圾稿。
   ✅ 现在的硬约束：
     - `clear_body()` **必须回读**并用**占位符识别**判空（清空后 ProseMirror
       会显示 `请输入正文`，innerText 长度 7，**不是 0**）；
     - 清空结果非 True ⇒ **立即中止该案例，绝不 insertHTML**；
     - 默认**不再改标题** ⇒ 不再产生 `zz表格验收-可删` 测试稿。

用法：
    python -X utf8 scripts/tt_table_verify.py --case kibu
    python -X utf8 scripts/tt_table_verify.py --all      # 全库 41 条（慢）
    python -X utf8 scripts/tt_table_verify.py --case X --keep-title
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402
import tt_body_fill as bf                # 占位符判空 + 已就绪的正确范式  # noqa: E402

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

def clear_body(cdp):
    """清空正文并**回读验证**。返回 True/False/None(未知)。

    🚨 与旧版的区别：旧版无条件 `return True`，清空失败也报成功，调用方
    于是把新正文**追加**到旧正文后面（表格数翻倍事故的根因）。
    ✅ 现在委托给 `bf.clear_body()`，它走按键管线后用**占位符识别**回读判空
    （清空后 ProseMirror 显示 `请输入正文`，innerText 长度 7，不是 0）。
    """
    return bf.clear_body(cdp)


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

    # 🚨 先等编辑器就绪，否则会对着半空的编辑器动手
    ready = False
    for _ in range(10):
        time.sleep(2)
        if bf.is_empty(cdp) is not None:
            ready = True
            break
    if not ready:
        raise RuntimeError("编辑器一直没就绪，不敢动手")

    before = read(cdp)
    if (before.get("tables") or 0) == want_tables \
            and (before.get("cells") or 0) == want_cells:
        # 🚨 幂等短路：已是目标就别清空重灌，否则平白多一次写远端的风险
        return True, want_tables, want_cells, before["tables"], before["cells"], 0

    cleared = clear_body(cdp)
    # 🚨🚨 清空没成功就**绝不 insertHTML** —— 否则新正文追加到旧正文后面
    if cleared is not True:
        return False, want_tables, want_cells, \
            before.get("tables") or 0, before.get("cells") or 0, -1

    if keep_title:
        # 仅在显式 --keep-title 时才动标题；默认不改 ⇒ 不产生 zz 测试稿
        pass
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
                    help="保留原标题（已默认行为，留作兼容；不会再写 "
                         "zz表格验收-可删 这种测试标题）")
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
            if deg < 0:
                print("  %s %-20s 【已中止】清空正文未成功，绝不插入新内容"
                      "（远端仍是 %d表/%d格，本地目标 %d表/%d格）"
                      % (flag, c["id"], gt, gc, wt, wc))
            else:
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