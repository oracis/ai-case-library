#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知乎台账对账：以「远端已发表列表」为唯一真相源，回填本地两本台账。

**为什么需要这个脚本。** 知乎发文章有两条路：UI 手动发、API 发。走 UI 那条时
没人回写台账，于是「文章确实发了、台账里查不到」——2026-10-31 实测：本号
已发表 3 篇，`publish_manifest.json` 里只有 1 条 zhihu 记录，
`zhihu_drafts.json` 里还有一条挂着 `draft`。
判据只有一条：**远端 `members/<token>/articles` 列表里有它，才算 published。**
本地 `status` 字段是历史遗留，它会骗人。

两件事分开做：
  1. `zhihu_drafts.json`（逐篇明细：article_id / title / status / published_ts）
  2. `publish_manifest.json`（平台状态机：{cid: {zhihu: {state, remote_id, ...}}}）

用法::

    python scripts/zhihu_ledger_sync.py            # 只报告，默认 dry-run
    python scripts/zhihu_ledger_sync.py --apply    # 写回两本台账

退出码：0 = 一致或已修复；1 = 存在无法自动归位的情况（远端有、本地认不出）。
"""

from __future__ import print_function

import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import atomicio                                                       # noqa: E402
import zhihu_creator as ZC                                            # noqa: E402

DRAFT_FILE = os.path.join(ROOT, "data", "zhihu_drafts.json")
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")


def _ts(v):
    """远端 unix 秒 → 'YYYY-MM-DD HH:MM:SS'；已是字符串就原样返回。"""
    if isinstance(v, str):
        return v
    try:
        return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def fetch_published(limit=50, max_pages=10):
    """读远端已发表列表，返回 {article_id: {title, created, published_ts, url}}。"""
    st, me = ZC.call_json("GET", "/api/v4/me")
    if st != 200:
        raise SystemExit("读不出账号信息（cookie 可能又过期了）：%s" % (me,))
    token = me.get("url_token")
    out, seen = {}, set()
    for page in range(max_pages):
        offset = page * limit
        path = ("/api/v4/members/%s/articles?limit=%d&offset=%d&sort_by=created"
                % (token, limit, offset))
        st, d = ZC.call_json("GET", path)
        if st != 200:
            raise SystemExit("读已发表失败（offset=%d）：%s" % (offset, d))
        items = d.get("data") or []
        added = 0
        for it in items:
            aid = str(it.get("id"))
            if not aid or aid in seen:
                continue
            seen.add(aid)
            added += 1
            out[aid] = {
                "title": it.get("title") or "",
                "created": it.get("created"),
                "published_ts": _ts(it.get("created")),
                "url": "https://zhuanlan.zhihu.com/p/%s" % aid,
            }
        totals = (d.get("paging") or {}).get("totals")
        if added == 0 or not items:
            break
        if totals is not None and offset + len(items) >= int(totals):
            break
    return token, out


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, obj):
    """按各台账自己的写入口径落盘，别用自己的审美改格式。

    `publish_manifest.json` 的写入口是 `multiplatform.state.Manifest.save`
    （`indent=1` + `sort_keys=True`）；`zhihu_drafts.json` 一直是 `indent=2`。
    2026-10-31 实测：用 indent=2 重写 manifest 会让 git diff 从 ~110 行
    涨到 3887 行（整文件重排），改了什么反而看不出来。
    """
    if os.path.basename(path) == os.path.basename(MANIFEST):
        atomicio.write_json(path, obj, indent=1, sort_keys=True)
    else:
        atomicio.write_json(path, obj, indent=2)


def _render(path, obj):
    if os.path.basename(path) == os.path.basename(MANIFEST):
        return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _write_if_changed(path, obj):
    """内容或格式与磁盘不一致才落盘 —— 顺手把历史跑歪的缩进纠回写入口径。

    为什么要比较格式：manifest 曾被别处用 `indent=2` 重写过一次，之后每次
    git diff 都是整文件重排，真改动淹没在 3800 行噪音里。判据是**字节**，
    不是「我这次改了几个字段」。
    """
    try:
        with open(path, encoding="utf-8", newline="") as f:
            old = f.read()
    except OSError:
        old = ""
    new = _render(path, obj)
    if old.rstrip("\r\n") == new.rstrip("\r\n"):
        return False
    _write(path, obj)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回两本台账")
    args = ap.parse_args()

    token, remote = fetch_published()
    drafts = _read(DRAFT_FILE)
    manifest = _read(MANIFEST)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    changes_draft, changes_manifest, unmatched, ghosts = [], [], [], []

    # --- 1. 本地明细 → 以远端为准 ------------------------------------------
    by_id = {str(v.get("article_id")): (cid, v) for cid, v in drafts.items()
             if v.get("article_id")}
    remote_seen = set()

    for aid, info in remote.items():
        hit = by_id.get(aid)
        if not hit:
            # 远端有、本地认不出：先按标题认亲（article_id 可能在重建时变过）
            hit = next(((c, v) for c, v in drafts.items()
                        if (v.get("title") or "").strip() == info["title"].strip()), None)
            if not hit:
                unmatched.append((aid, info["title"]))
                continue
        cid, rec = hit
        remote_seen.add(aid)
        if rec.get("status") != "published":
            changes_draft.append((cid, rec.get("status"), "published"))
            rec["status"] = "published"
        if rec.get("published_ts") != info["published_ts"]:
            changes_draft.append((cid, "published_ts", info["published_ts"]))
            rec["published_ts"] = info["published_ts"]
        rec["article_id"] = aid
        rec["url"] = rec.get("url") or info["url"]
        rec["verified_at"] = now
        rec["title"] = rec.get("title") or info["title"]

        # --- 2. 状态机台账补录 -------------------------------------------
        ent = manifest.setdefault(cid, {})
        cur = ent.get("zhihu") or {}
        want = {
            "state": "published",
            "remote_id": aid,
            "published_ts": info["published_ts"],
            "url": info["url"],
            "title": info["title"],
            "updated_at": now,
            "note": ("2026-10-31 补记：远端已发表列表回读命中（%s）。"
                     "此前 UI 发表后未回写台账，属漏记。" % info["published_ts"]),
        }
        if cur.get("state") != "published" or cur.get("remote_id") != aid:
            changes_manifest.append((cid, cur.get("state"), "published", aid))
        changed = {k: v for k, v in want.items() if cur.get(k) != v}
        if changed:
            ent["zhihu"] = dict(cur, **want)

    # 本地记 published、远端查不到 ⇒ 真下架（不是「没发」，别混）
    for cid, rec in drafts.items():
        if rec.get("status") == "published" and str(rec.get("article_id")) not in remote_seen:
            ghosts.append((cid, rec.get("article_id"), rec.get("title")))

    # --- 报告 --------------------------------------------------------------
    print("远端已发表：%d 篇（账号 %s）" % (len(remote), token))
    for aid, info in sorted(remote.items(), key=lambda kv: kv[1]["published_ts"]):
        print("  %-22s %s  %s" % (aid, info["published_ts"], info["title"][:40]))
    print()

    print("zhihu_drafts.json 待改 %d 处" % len(changes_draft))
    for cid, old, new in changes_draft:
        print("  %-22s %s → %s" % (cid, old, new))
    print("publish_manifest.json 待改 %d 条" % len(changes_manifest))
    for cid, old, new, aid in changes_manifest:
        print("  %-22s %s → %s (%s)" % (cid, old, new, aid))
    if ghosts:
        print("⚠ 本地记 published 但远端不存在（真下架，需人工核对）：")
        for cid, aid, title in ghosts:
            print("  %-22s %s %s" % (cid, aid, (title or "")[:36]))
    if unmatched:
        print("⚠ 远端有、本地认不出（需人工映射）：")
        for aid, title in unmatched:
            print("  %-22s %s" % (aid, title))

    if not args.apply:
        print("\n(dry-run) 加 --apply 写回台账")
        return 1 if unmatched else 0

    written = []
    for path, obj in ((DRAFT_FILE, drafts), (MANIFEST, manifest)):
        if _write_if_changed(path, obj):
            written.append(os.path.relpath(path, ROOT))
    if written:
        print("\n✓ 已写 %s" % "、".join(written))
    else:
        print("\n✓ 两本台账无需改动")
    return 1 if unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
