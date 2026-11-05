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
import db  # noqa: E402  SQLite 统一台账：读端切到 platform_posts 表

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


def _load_json(path):
    """读 JSON 快照，容错：缺失/损坏返回 {}。快照只是合并底座，db 才是真值。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_fieldwise(json_rec, db_rec):
    """逐字段合并：db 非空值胜出，db 空值回落 JSON ⇒ JSON 独有记录/字段不丢。

    2026-11-05 bug ③ 的修复点：db 是唯一事实源，但 JSON 快照里存在 db
    从没收录过的记录（如 UI 手动建的草稿、游戏厅稿）。旧逻辑「db 有数据
    就直接 return」把这些记录整条丢掉——既不参与对账合并，--apply 时也
    落不进 db，永远停在快照里。合并后它们随 _write 的 upsert 补录进 db。
    """
    rec = dict(json_rec or {})
    for k, v in (db_rec or {}).items():
        if v not in (None, ""):
            rec[k] = v
        elif k not in rec:
            rec[k] = v
    return rec


def _read(path):
    """db 真值 ∪ JSON 快照：取两边 case 并集逐字段合并。

    db 非空字段覆盖 JSON（db 是真值），JSON 独有的 case / 字段保留，
    借 _write 的 upsert 补录进 db。迁移期 JSON 快照不再会整条丢记录。
    """
    name = os.path.basename(path)
    is_draft = name == os.path.basename(DRAFT_FILE)
    is_manifest = name == os.path.basename(MANIFEST)
    if not (is_draft or is_manifest):
        # 其他文件：维持旧行为，直接读 JSON
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    json_data = _load_json(path)
    db_rows = []
    try:
        conn = db.get_conn()
        try:
            db_rows = db.get_posts_by_platform(conn, "zhihu") or []
        finally:
            db.close_conn()
    except Exception:  # noqa: BLE001  db 不可用时回落纯 JSON（旧行为）
        return json_data if json_data else _load_json_fallback(path)

    if is_draft:
        db_map = {}
        for p in db_rows:
            db_map[p["case_id"]] = {
                "article_id": p.get("remote_id") or p.get("draft_id") or "",
                "title": p.get("title") or "",
                "url": p.get("url") or "",
                "status": p.get("state") or "draft",
                "ts": p.get("updated_at") or p.get("published_at") or "",
                # 发表时点单独带出（published 才有意义），否则同步循环写的
                # published_ts 在 _write 里落不了盘 ⇒ 永远 dirty。
                "published_ts": (p.get("published_at") or "")
                    if (p.get("state") == "published") else "",
            }
        return {cid: _merge_fieldwise(json_data.get(cid), db_map.get(cid))
                for cid in set(json_data) | set(db_map)}

    # MANIFEST：{cid: {zhihu: {...}}}，逐 cid 在 zhihu 子层合并；
    # JSON 条目里其他平台的键原样保留（本脚本只动 zhihu）。
    db_map = {}
    for p in db_rows:
        db_map[p["case_id"]] = {"zhihu": {
            "state": p.get("state") or "draft",
            "remote_id": p.get("remote_id") or "",
            "title": p.get("title") or "",
            "draft_id": p.get("draft_id") or "",
            "updated_at": p.get("updated_at") or "",
            # 带出 published_at，使 main() 的 diff 判据稳定（不带则每轮都误报变更）
            "published_ts": p.get("published_at") or "",
            "url": p.get("url") or "",
        }}
    merged = {}
    for cid in set(json_data) | set(db_map):
        jent = json_data.get(cid)
        ent = dict(jent) if isinstance(jent, dict) else {}
        jz = ent.get("zhihu") if isinstance(ent.get("zhihu"), dict) else None
        dz = (db_map.get(cid) or {}).get("zhihu")
        merged_z = _merge_fieldwise(jz, dz)
        # 两边都没有 zhihu 记录的条目（纯其他平台）不造空 zhihu 层——
        # 否则 _write 会给没发过知乎的 case 批量 upsert 出空 draft 行。
        if merged_z:
            ent["zhihu"] = merged_z
        merged[cid] = ent
    return merged


def _load_json_fallback(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, obj):
    """写 SQLite ledger（platform_posts, platform='zhihu'）。

    不再写 JSON 快照——db 是唯一事实源。
    """
    conn = db.get_conn()
    try:
        if os.path.basename(path) == os.path.basename(DRAFT_FILE):
            for cid, v in obj.items():
                if not isinstance(v, dict):
                    continue
                db.upsert_post(conn, cid, "zhihu", {
                    "state": v.get("status") or "draft",
                    "remote_id": v.get("article_id") or "",
                    "url": v.get("url") or "",
                    "title": v.get("title") or "",
                    "draft_id": v.get("article_id") or "",
                    "attempt": 0,
                    # 2026-11-05 修：published_at 必须取 published_ts（真实发表时点），
                    # 旧写法取 ts（=updated_at）⇒ 每跑一次同步就把发表时间刷成补账时间。
                    "published_at": v.get("published_ts") or v.get("ts") or "",
                    "updated_at": v.get("ts") or "",
                    "note": "status=" + (v.get("status") or "draft"),
                })
        elif os.path.basename(path) == os.path.basename(MANIFEST):
            for cid, v in obj.items():
                if not isinstance(v, dict):
                    continue
                z = v.get("zhihu")
                if not isinstance(z, dict):
                    continue
                db.upsert_post(conn, cid, "zhihu", {
                    "state": z.get("state") or "draft",
                    "remote_id": z.get("remote_id") or z.get("article_id") or "",
                    "url": z.get("url") or "",
                    "title": z.get("title") or "",
                    "draft_id": z.get("draft_id") or "",
                    "attempt": 0,
                    # 2026-11-05 修：published_at 取 published_ts（真实发表时点），
                    # 旧写法取 updated_at（=补账运行时刻）⇒ 发表时间全变成同步时间。
                    "published_at": z.get("published_ts") or z.get("updated_at") or "",
                    "updated_at": z.get("updated_at") or "",
                    "note": "",
                })
    finally:
        db.close_conn()


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
            written.append(os.path.basename(path))
    if written:
        # ⚠ 实际落盘的是 SQLite platform_posts（_write 只写 db），
        # 这里报的文件名只是「哪本台账有变动」的标签，别当 JSON 写盘证据。
        print("\n✓ 已写台账（SQLite platform_posts）：%s" % "、".join(written))
    else:
        print("\n✓ 两本台账无需改动")
    return 1 if unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
