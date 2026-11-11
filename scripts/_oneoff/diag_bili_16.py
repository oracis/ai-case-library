#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐条诊断：B站 16 条带注记草稿的「平台正文 vs 本地母版」到底差在哪。

为什么必须先诊断再动手
------------------
干跑里只有 bustem 的 `只差注记=True`，其余 15 条摘注记剥符后仍与母版不等。
这个「不等」有两种截然不同的含义：
  (甲) 平台比母版**多**东西（多余块 / 旧的渲染残留 / 别的注记变体）
       ⇒ 以母版覆盖 = 把脏东西换掉，方向对。
  (乙) 平台比母版**少**东西（delete/replace）
       ⇒ 母版在草稿建好之后被改过（补句子、改数字），平台是旧版。
          这时「用母版覆盖」会**改变文章内容**，必须先人工裁定，
          不能当成「只是清注记」直接批量干。

判据（用 SequenceMatcher 逐字对齐后的 opcode 分类）
-------------------------------------------------
  del_chars  = delete + replace(母版侧) 的字符数之和
  ins_chars  = insert + replace(平台侧) 的字符数之和
  del_chars == 0            ⇒ (甲)，可安全覆盖
  0 < del_chars <= tiny     ⇒ (甲') 仅个别符号/标点差异，人工扫一眼
  del_chars > tiny          ⇒ (乙)，**停下来问人**

⛔ 只读：只 view_draft + 开编辑页读 innerText，不 save_draft、不写平台。
用法：
    python -X utf8 tmp/diag_bili_16.py
    python -X utf8 tmp/diag_bili_16.py --only bustem --verbose
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
EDITOR_GLYPHS = ("▶", "｜", "|", "►", "▸")
TINY = 40

JS_READ = r"""(function(){
  var el = document.querySelector('.tiptap.ProseMirror.eva3-editor');
  return JSON.stringify({ok: !!el, t: el ? (el.innerText||'') : ''});
})()"""


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def norm_editor(s):
    t = norm(s)
    for g in EDITOR_GLYPHS:
        t = t.replace(g, "")
    return t


def drop_note_lines(text):
    return "\n".join(ln for ln in str(text or "").splitlines() if MARK not in ln)


def build_body(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return tt.blocks_to_html(tt.md_to_blocks(md), footer=False)


def load_targets():
    import sqlite3
    log = os.path.join(ROOT, "tmp", "batch_clean_log.json")
    d = json.load(open(log, encoding="utf-8"))
    cids = [r[0].rsplit("/", 1)[-1][:-3] for r in d["results"] if r[1] != "skip"]
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(cids))
    cur.execute("SELECT case_id, remote_id, draft_id FROM platform_posts "
                "WHERE platform='bilibili' AND state='draft' AND case_id IN (%s)" % q,
                cids)
    ast = {r[0]: (r[1] or r[2]) for r in cur.fetchall()}
    conn.close()
    out = []
    for cid in cids:
        md = os.path.join(ROOT, "out", "articles", cid + ".md")
        if cid in ast and os.path.isfile(md):
            out.append((cid, md, str(ast[cid])))
    return out


def classify(m, e):
    sm = difflib.SequenceMatcher(None, m, e, autojunk=False)
    dele = repl_m = ins = 0
    segs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            dele += i2 - i1
        elif tag == "insert":
            ins += j2 - j1
        else:
            repl_m += i2 - i1
            ins += j2 - j1
        segs.append((tag, m[i1:i2], e[j1:j2]))
    return dele + repl_m, ins, segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--settle", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--dumpdir", default=os.path.join(ROOT, "tmp", "bili16"))
    a = ap.parse_args()
    port = a.port or bp.PUB_PORT

    targets = load_targets()
    if a.only:
        targets = [t for t in targets if t[0] == a.only]
    if not targets:
        print("无目标")
        return 0
    os.makedirs(a.dumpdir, exist_ok=True)

    cdp = wp.CDP(port)
    api._bili_tab(cdp)

    rows = []
    for cid, md, aid in targets:
        master = norm(build_body(md))
        sub = None
        tid = None
        try:
            tid = cdp.new_target(EDITOR_URL % aid)["id"]
            sub = wp.CDP(port)
            if not sub.connect_target(tid):
                print("%-26s ✗ 连不上编辑页" % cid)
                rows.append((cid, aid, -1, -1, "连不上", 0))
                continue
            sub.send("Emulation.setDeviceMetricsOverride",
                     {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                      "mobile": False})
            for _ in range(a.settle):
                time.sleep(1)
            raw = sub.eval(JS_READ, refresh_context=True)
            j = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as e:                              # noqa: BLE001
            print("%-26s ✗ %s" % (cid, str(e)[:60]))
            rows.append((cid, aid, -1, -1, "异常", 0))
            continue
        finally:
            if sub is not None:
                try:
                    sub.close()
                except Exception:                           # noqa: BLE001
                    pass
            if tid:
                try:
                    wp.CDP(port).close_target(tid)
                except Exception:                           # noqa: BLE001
                    pass

        et = j.get("t") or ""
        with open(os.path.join(a.dumpdir, cid + ".txt"), "w", encoding="utf-8") as f:
            f.write(et)
        e = norm_editor(drop_note_lines(et))
        dele, ins, segs = classify(master, e)
        same = (e == master)
        verdict = ("一致" if same else
                   "仅多内容" if dele == 0 else
                   "微小缺字" if dele <= TINY else
                   "平台旧版")
        rows.append((cid, aid, dele, ins, verdict, len(segs)))
        print("%-26s aid=%-8s 母版%4d 平台%4d ｜ 缺%4d 多%4d ｜ %-8s （%d 段）"
              % (cid, aid, len(master), len(e), dele, ins, verdict, len(segs)))
        if a.verbose or (dele > 0):
            for tag, ms, es in segs[:8]:
                print("      %-7s 母版=%r 平台=%r" % (tag, ms[:60], es[:60]))
            if len(segs) > 8:
                print("      … %d 段" % len(segs))

    print("\n--- 汇总 ---")
    for v in ("一致", "仅多内容", "微小缺字", "平台旧版", "连不上", "异常"):
        n = sum(1 for r in rows if r[4] == v)
        if n:
            print("  %-6s %d 条：%s" % (v, n, "、".join(r[0] for r in rows if r[4] == v)))
    print("\n判定规则：缺字=0 ⇒ 平台只是多脏东西，母版覆盖安全；"
          "缺字>%d ⇒ 母版建稿后被改过，覆盖会改内容，先问人。" % TINY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
