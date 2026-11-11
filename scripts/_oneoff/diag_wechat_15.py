#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号 15 条存量草稿：只读诊断「平台正文 vs 本地母版」。

为什么需要它
------------
11-10 清过 36 个本地产物（`tmp/batch_clean_verify_notes.py`），但草稿箱里的
旧副本是**建稿那一刻**抄进去的，本地干净 ≠ 平台干净。B站 15 条、小红书 13 条
已盘完（PRD #131），公众号这 15 条还没人看过。

判据（沿用 B站那套，不看 code=0 不看字数相等）
---------------------------------------------
`diff_stats(母版, 平台) -> (dele, ins)`：
  · `dele` = 平台缺母版多少字 ⇒ **平台是旧版/残缺**
  · `ins`  = 平台比母版多多少字 ⇒ 渲染符号或平台侧手改
`MARK` 在平台正文里出现 = **带内部注记，必改**。

⚠ 公众号正文读的是编辑页 DOM（`wp.read_draft_body`），返回纯文本。本地母版是
`out/articles/<cid>.html`（`build` 子命令的素材源），两边都是「可见文字」，
归一后可直接比。

用法
----
    python -X utf8 tmp/diag_wechat_15.py                # 干跑（默认，只读）
    python -X utf8 tmp/diag_wechat_15.py --limit 3       # 分块（前台硬限）
    python -X utf8 tmp/diag_wechat_15.py --only mort
"""
import argparse
import difflib
import html as H
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import wechat_publish as wp                                   # noqa: E402

MARK = "我去核了一遍"
DUMP = os.path.join(ROOT, "tmp", "wx15")
DONE = os.path.join(DUMP, "_done.json")


def base(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", H.unescape(t)).strip()


def diff_stats(a, b):
    """返回 (a 被删字数, b 新增字数)。"""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    dele = ins = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            dele += i2 - i1
        if tag in ("insert", "replace"):
            ins += j2 - j1
    return dele, ins


def master_text(cid):
    p = os.path.join(ROOT, "out", "articles", cid + ".html")
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return base(f.read())


def drafts_from_ledger():
    """台账里 wechat 且 state=draft 的 36 个 case 的子集（本批 15 条）。"""
    log = json.load(open(os.path.join(ROOT, "tmp", "batch_clean_log.json"),
                         encoding="utf-8"))
    changed = [os.path.splitext(os.path.basename(r[0]))[0]
               for r in log["results"] if r[1] != "skip"]
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(changed))
    cur.execute("SELECT case_id, remote_id, draft_id, title FROM platform_posts "
                "WHERE platform='wechat' AND state='draft' "
                "AND case_id IN (%s) ORDER BY case_id" % q, changed)
    rows = [{"cid": r[0], "aid": (r[1] or r[2] or ""), "title": r[3] or ""}
            for r in cur.fetchall()]
    conn.close()
    return rows


def load_done():
    try:
        return json.load(open(DONE, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def save_done(d):
    os.makedirs(DUMP, exist_ok=True)
    tmp = DONE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DONE)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--redo", action="store_true", help="忽略已查记录")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    port = a.port or wp.CDP_PORT

    rows = drafts_from_ledger()
    if a.only:
        rows = [r for r in rows if r["cid"] == a.only]
    done = load_done()
    if not a.redo:
        rows = [r for r in rows if r["cid"] not in done]
    print("待查 %d 条（已查 %d）" % (len(rows), len(done)))
    if not rows:
        return 0

    os.makedirs(DUMP, exist_ok=True)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=port)
    cdp = wp.CDP(port)
    _tid, tok = wp._connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认 9222 里 mp.weixin.qq.com 已登录")
    print("token ok\n")

    n = 0
    for r in rows:
        if a.limit and n >= a.limit:
            print("… 已达 --limit %d" % a.limit)
            break
        n += 1
        cid, aid, title = r["cid"], r["aid"], r["title"]
        if not aid:
            print("%-26s ✗ 台账没有 appmsgid，跳过" % cid)
            continue
        t0 = time.time()
        try:
            body = wp.read_draft_body(cdp, tok, aid, expect_title=title)
        except Exception as e:                                 # noqa: BLE001
            print("%-26s ✗ 读编辑器失败：%s" % (cid, e))
            continue
        if not body:
            print("%-26s ✗ 读到空正文（appmsgid=%s）" % (cid, aid))
            continue
        with open(os.path.join(DUMP, cid + ".txt"), "w", encoding="utf-8") as f:
            f.write(body)
        p = base(body)
        m = master_text(cid)
        if not m:
            print("%-26s 平台 %4d 字 ｜ 无本地母版，只落盘" % (cid, len(p)))
        else:
            dele, ins = diff_stats(m, p)
            tag = []
            if MARK in body:
                tag.append("带注记")
            if dele > 20:
                tag.append("平台缺%4d字" % dele)
            if ins > 40:
                tag.append("平台多%4d字" % ins)
            verdict = "、".join(tag) if tag else "干净"
            print("%-26s 平台 %4d / 母版 %4d ｜ 缺%4d 多%4d ｜ %s（%.0fs）"
                  % (cid, len(p), len(m), dele, ins, verdict, time.time() - t0))
        done[cid] = {"aid": aid, "len": len(p), "at": time.strftime("%H:%M")}
        save_done(done)
    print("\n本轮 %d 条已落盘 → tmp/wx15/" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
