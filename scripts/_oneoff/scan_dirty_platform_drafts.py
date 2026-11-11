#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读扫描：平台侧草稿箱里到底有几条正文带「我去核了一遍」内部核验注记。

⛔ 纯读，不写盘、不改稿、不发布。
   平台侧判据只有一条：**把草稿正文真的取下来**看有没有那段引用块。

为什么必须先扫再改
------------------
本地 `out/articles/*.md` 清干净 ≠ 平台草稿干净。平台草稿是**建稿那一刻的副本**，
之后本地怎么改都影响不到它。所以「要不要批量改平台稿」这个问题，
只能靠把平台正文读下来才有答案；没读就报数属于凭猜测。

用法：
    python -X utf8 tmp/scan_dirty_platform_drafts.py --platform bilibili
    python -X utf8 tmp/scan_dirty_platform_drafts.py --platform bilibili --all
"""
import argparse
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

MARK = "我去核了一遍"


def load_clean_ids():
    """本地被清过注记的 36 个案例 id（从批量清理日志读，不硬编码）。"""
    p = os.path.join(ROOT, "tmp", "batch_clean_log.json")
    if not os.path.exists(p):
        return set()
    d = json.load(open(p, encoding="utf-8"))
    return {r[0].rsplit("/", 1)[-1][:-3] for r in d["results"] if r[1] != "skip"}


def ledger_draft_ids(platform, case_ids):
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(case_ids))
    cur.execute(
        "SELECT case_id, remote_id, draft_id, title FROM platform_posts "
        "WHERE platform=? AND state='draft' AND case_id IN (%s)" % q,
        [platform] + sorted(case_ids))
    rows = cur.fetchall()
    conn.close()
    return rows


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def scan_bilibili(args, case_ids):
    import wechat_publish as wp
    import bili_draft_api as ba
    import bilibili_publish as bp

    cdp = wp.CDP(args.port or bp.PUB_PORT)
    ba._bili_tab(cdp)          # 先开 member.bilibili.com 的 target，否则 eval 无页面可挂
    drafts = ba.list_drafts(cdp)
    print("draft/list 返回 %d 条草稿" % len(drafts))

    rows = ledger_draft_ids("bilibili", case_ids)
    by_title = {}
    for cid, rid, did, title in rows:
        if title:
            by_title[norm(title)] = (cid, rid or did)
    print("台账里这批案例的 bilibili draft 行：%d" % len(rows))

    out = []
    for d in drafts:
        aid = d.get("article_id") or d.get("aid") or d.get("id")
        title = (d.get("title") or "").strip()
        try:
            v = ba.view_draft(cdp, aid)
        except Exception as e:                             # noqa: BLE001
            print("  [err] %s %s → %s" % (aid, title[:20], str(e)[:80]))
            continue
        content = v.get("content") or ""
        hit = MARK in content
        cid, led_rid = by_title.get(norm(title), ("", ""))
        out.append({
            "aid": str(aid), "title": title, "hit": hit,
            "len": len(content), "case_id": cid, "ledger_remote": str(led_rid),
        })
    return out


def report(out, platform):
    hit = [r for r in out if r["hit"]]
    clean = [r for r in out if not r["hit"]]
    print()
    print("=" * 78)
    print("%s 平台侧草稿扫描：共 %d 条｜带注记 %d 条｜干净 %d 条"
          % (platform, len(out), len(hit), len(clean)))
    print("=" * 78)
    if hit:
        print("%-12s %-6s %-8s %s" % ("article_id", "字数", "台账case", "标题"))
        for r in hit:
            print("%-12s %-6d %-8s %s"
                  % (r["aid"], r["len"], r["case_id"] or "—", r["title"][:42]))
    return len(hit), len(clean)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="bilibili")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()

    case_ids = load_clean_ids()
    print("本地被清过注记的案例：%d 个" % len(case_ids))

    if a.platform == "bilibili":
        out = scan_bilibili(a, case_ids)
    else:
        raise SystemExit("暂只实现 bilibili")
    report(out, a.platform)


if __name__ == "__main__":
    main()
