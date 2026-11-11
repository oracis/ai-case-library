#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读分析：编辑器 innerText 与「母版重建 HTML」的差异**到底是什么**。

为什么需要这一步
--------------
`clean_bili1_draft_notes.py` 的验收是 `norm(draft_view.content) == norm(母版html)`，
实测恒假。两个可能：
  (a) draft/view.content 被截断 ⇒ 该换编辑器全文读
  (b) 两边**表示法不同**（`<h2>` → `▶ 标题`、`<hr>` → 空、markdown 强调符被吃）
已实测 content == 编辑器 innerText 且都是 1059 字 ⇒ **不是截断**，是 (b)。

那就要把 (b) 摸清：逐字 diff，缺什么补什么，补出来的归一函数才是可判定的验收判据。

做法
----
不猜符号表，直接算 diff 段：把两边归一后按最长公共子序列对齐，打印「母版有/编辑端没有」
的片段（截 200 条）。看到实际符号后再决定映射规则。

⛔ 只读：只读草稿 + 本地母版，不写平台、不写盘（除 --dump 指定）。
用法：
    python -X utf8 tmp/analyze_bili_editortext.py --cid bustem
"""
import argparse
import difflib
import html
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import bili_draft_api as api            # noqa: E402
import bilibili_publish as bp           # noqa: E402
import toutiao_publish as tt            # noqa: E402
import wechat_publish as wp             # noqa: E402

MARK = "我去核了一遍"
EDITOR_URL = "https://member.bilibili.com/york/read-editor?aid=%s"

JS_READ = r"""(function(){
  var el = document.querySelector('.tiptap.ProseMirror.eva3-editor');
  return JSON.stringify({ok: !!el, t: el ? (el.innerText||'') : ''});
})()"""


def build_body(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return tt.blocks_to_html(tt.md_to_blocks(md), footer=False)


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def strip_note(text):
    """把「我去核了一遍…」那段整块摘掉（到下一个空行为界，实测注记是独立引用块）。"""
    lines = str(text or "").splitlines()
    out, dropping = [], False
    for ln in lines:
        if MARK in ln:
            dropping = True
            # 注记块可能就在同一行结束（后面还有正文时用 ｜ 分段）；保守起见只丢该行
            continue
        if dropping:
            # 注记块后续行：直到遇到空行或明显正文（以句号/编号开场）才恢复
            if not ln.strip():
                dropping = False
                continue
            # 仍在引用块里的续行（B站引用块行常以 4 空格或「>」或紧跟）
            continue
        out.append(ln)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="bustem")
    ap.add_argument("--aid", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--settle", type=int, default=8)
    ap.add_argument("--maxdiff", type=int, default=60)
    ap.add_argument("--dump", default="")
    a = ap.parse_args()
    port = a.port or bp.PUB_PORT

    md = os.path.join(ROOT, "out", "articles", a.cid + ".md")
    body = build_body(md)
    mplain = norm(body)
    print("母版：HTML %d / 纯文本 %d" % (len(body), len(mplain)))

    aid = a.aid
    if not aid:
        import sqlite3
        conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
        cur = conn.cursor()
        cur.execute("SELECT remote_id, draft_id FROM platform_posts "
                    "WHERE platform='bilibili' AND state='draft' AND case_id=?",
                    (a.cid,))
        r = cur.fetchone()
        conn.close()
        aid = str((r[0] or r[1]) if r else "")

    cdp = wp.CDP(port)
    api._bili_tab(cdp)
    tid, sub = None, None
    try:
        tid = cdp.new_target(EDITOR_URL % aid)["id"]
        sub = wp.CDP(port)
        sub.connect_target(tid)
        sub.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                  "mobile": False})
        for _ in range(a.settle):
            time.sleep(1)
        raw = sub.eval(JS_READ, refresh_context=True)
        j = json.loads(raw) if isinstance(raw, str) else (raw or {})
    finally:
        for conn in (sub, cdp):
            try:
                if conn is not None:
                    conn.close()
            except Exception:                               # noqa: BLE001
                pass
        if tid:
            try:
                wp.CDP(port).close_target(tid)
            except Exception:                               # noqa: BLE001
                pass

    if not j.get("ok"):
        print("✗ 读不到编辑器")
        return 2
    etext = j["t"]
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as f:
            f.write(etext)
        print("编辑器原文已写 %s" % a.dump)

    eplain = norm(etext)
    print("编辑器：纯文本 %d ｜ 含注记=%s" % (len(eplain), MARK in etext))

    # 摘掉注记块后再比
    e_no = norm(strip_note(etext))
    print("编辑器摘注记后：%d 字；母版 %d 字；相等=%s"
          % (len(e_no), len(mplain), e_no == mplain))

    print("\n--- 未摘注记的直接 diff（母版 vs 编辑器，归一后） ---")
    sm = difflib.SequenceMatcher(None, mplain, eplain, autojunk=False)
    shown = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        print("  %-7s 母版[%s]=%r  编辑器[%s]=%r"
              % (tag, "%d:%d" % (i1, i2), mplain[i1:i2][:70],
                 "%d:%d" % (j1, j2), eplain[j1:j2][:70]))
        shown += 1
        if shown >= a.maxdiff:
            print("  …（截断）")
            break
    print("  diff 段数=%d（已显示 %d）" % (len([1 for o in
          difflib.SequenceMatcher(None, mplain, eplain, autojunk=False).get_opcodes()
          if o[0] != "equal"]), shown))

    print("\n--- 摘注记后的 diff（这才是「除注记外是否逐字一致」的答案） ---")
    sm2 = difflib.SequenceMatcher(None, mplain, e_no, autojunk=False)
    shown = 0
    for tag, i1, i2, j1, j2 in sm2.get_opcodes():
        if tag == "equal":
            continue
        print("  %-7s 母版[%s]=%r  编辑器[%s]=%r"
              % (tag, "%d:%d" % (i1, i2), mplain[i1:i2][:70],
                 "%d:%d" % (j1, j2), e_no[j1:j2][:70]))
        shown += 1
        if shown >= a.maxdiff:
            print("  …（截断）")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
