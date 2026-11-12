#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `data/` 下的 JSON 台账导入 SQLite 统一台账 `data/ledger.db`。

导入来源（按优先级，同一 (case_id, platform) 以最后写入为准）：

1. cases.json          → cases 表（type='case'）
2. graveyard.json      → cases 表（type='graveyard'）
3. anti_cases.json     → cases 表（type='anti_case'；仅导入能解析出 id 的条目）
4. publish_manifest.json → platform_posts 表
5. longtail_sync.json    → platform_posts 表
6. verifications.json    → verifications 表
7. wechat_published.json → platform_posts 表（platform='wechat'）
8. xhs_published.json    → platform_posts 表（platform='xhs'）
9. bili_published.json   → platform_posts 表（platform='bilibili'）
10. toutiao_published.json → platform_posts 表（platform='toutiao'）

导入是幂等的：重复运行会覆盖同一记录，不会重复插入。

发表时点口径（2026-11-12 起）
------------------------------
`publish_manifest.json` 的 `at` 是**记账时点**（发布脚本落账时刻，有的还带
「草稿箱→发布（send-draft）」这类说明文字），不是远端真值；而 `data/ledger.db`
不入库，按远端补正过的 `published_at` 换机即失。为此 manifest 增加独立字段
`published_at`：有它时优先用它，没有才退回 `at` / `ts`，历史文件仍可重建。
`_to_platform_post()` 的 `updated_at` 与 `published_at` 共用这个时点。

现有 JSON 文件**不删除、不改动**，仅作为导出/备份保留。

用法::

    python scripts/import_to_db.py          # 全量导入
    python scripts/import_to_db.py --cases  # 仅导入 cases 相关
    python scripts/import_to_db.py --posts  # 仅导入 platform_posts
    python scripts/import_to_db.py --verify # 仅导入 verifications
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import db  # noqa: E402


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _to_platform_post(case_id, platform, row):
    """把各平台 JSON 里的一条记录统一映射成 platform_posts 字段 dict。"""
    if not isinstance(row, dict):
        return None

    # 2026-11-12：published_at = 远端真值时点（独立字段）；at = 记账时点。
    # 优先读独立字段，缺失时退回旧口径，保证历史 JSON 仍可重建。
    at = row.get("published_at") or row.get("at") or row.get("ts") or ""
    state = row.get("state") or row.get("status") or ("published" if row.get("ok") else "draft")
    if state == "draft_saved":
        state = "draft"

    return {
        "case_id": case_id,
        "platform": platform,
        "state": state,
        "remote_id": row.get("remote_id") or row.get("article_id") or row.get("gid") or row.get("note_id") or "",
        "url": row.get("url") or "",
        "title": row.get("title") or "",
        "draft_id": row.get("draft_id") or row.get("draftId") or "",
        "attempt": row.get("attempt", 0),
        "published_at": at,
        "updated_at": at or _now(),
        "note": row.get("note") or row.get("sync_note") or row.get("evidence") or "",
    }


# ================================================================
# cases 表
# ================================================================

def import_cases(conn):
    """导入 cases.json 到 `case_library`（真值表），graveyard / anti_cases 仍进老表 `cases`。

    2026-11-05：案例库本体迁到 `case_library`，一表对一文件。graveyard 与
    anti_cases 不是案例库本体（前者是已停运复盘、后者是「活得好但搬不动」），
    继续留在老表，避免三份数据的生命周期互相污染。
    """
    count = 0

    # --- cases.json → case_library ---
    path = os.path.join(DATA, "cases.json")
    data = _load_json(path, [])
    if isinstance(data, list):
        for r in data:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            db.upsert_case(conn, dict(r))          # 默认写 CASE_TABLE = case_library
            count += 1
    conn.commit()
    print(f"  cases.json → case_library {count} 条")

    # --- graveyard.json → 老表 cases ---
    count_g = 0
    path = os.path.join(DATA, "graveyard.json")
    data = _load_json(path, [])
    if isinstance(data, list):
        for r in data:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            row = dict(r)
            row["type"] = "graveyard"
            db.upsert_case(conn, row, table="cases")
            count_g += 1
    conn.commit()
    print(f"  graveyard.json → cases {count_g} 条")

    # --- anti_cases.json ---
    count_a = 0
    path = os.path.join(DATA, "anti_cases.json")
    data = _load_json(path, {})
    if isinstance(data, dict):
        # anti_cases 是按 volumes 嵌套的，先尝试扁平化：遍历所有以 id 为 key 的条目
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and v.get("id"):
                row = dict(v)
                row["type"] = "anti_case"
                db.upsert_case(conn, row, table="cases")
                count_a += 1
            elif isinstance(v, dict) and isinstance(v.get("cases"), list):
                for c in v["cases"]:
                    if isinstance(c, dict) and c.get("id"):
                        row = dict(c)
                        row["type"] = "anti_case"
                        db.upsert_case(conn, row, table="cases")
                        count_a += 1
    conn.commit()
    print(f"  anti_cases.json → {count_a} 条")

    return count + count_g + count_a


# ================================================================
# platform_posts 表
# ================================================================

def import_platform_posts(conn):
    """导入发布台账到 platform_posts 表。"""
    total = 0

    def upsert_many(items):
        nonlocal total
        for item in items:
            if not item:
                continue
            db.upsert_post(conn, item["case_id"], item["platform"], item)
            total += 1
        conn.commit()

    # --- publish_manifest.json ---
    path = os.path.join(DATA, "publish_manifest.json")
    data = _load_json(path, {})
    items = []
    if isinstance(data, dict):
        for case_id, plats in data.items():
            if not isinstance(plats, dict):
                continue
            for platform, row in plats.items():
                p = _to_platform_post(case_id, platform, row)
                if p:
                    items.append(p)
    upsert_many(items)
    print(f"  publish_manifest.json → {len(items)} 条（累计 {total}）")

    # --- longtail_sync.json ---
    count_before = total
    path = os.path.join(DATA, "longtail_sync.json")
    data = _load_json(path, {})
    items = []
    if isinstance(data, dict):
        for import_key, row in data.items():
            if not isinstance(row, dict):
                continue
            case_id = row.get("case") or import_key
            plats = row.get("platforms") or {}
            for platform, prow in plats.items():
                if not isinstance(prow, dict):
                    continue
                p = _to_platform_post(case_id, platform, prow)
                if p:
                    p["note"] = (p.get("note") or "") + f" | longtail_key={import_key}"
                    items.append(p)
    upsert_many(items)
    print(f"  longtail_sync.json → {len(items)} 条（累计 {total}）")

    # --- wechat_published.json ---
    count_before = total
    path = os.path.join(DATA, "wechat_published.json")
    data = _load_json(path, {})
    items = []
    if isinstance(data, dict):
        for case_id, row in data.items():
            if not isinstance(row, dict):
                continue
            p = _to_platform_post(case_id, "wechat", row)
            if p:
                items.append(p)
    upsert_many(items)
    print(f"  wechat_published.json → {len(items)} 条（累计 {total}）")

    # --- xhs_published.json ---
    count_before = total
    path = os.path.join(DATA, "xhs_published.json")
    data = _load_json(path, [])
    items = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            p = _to_platform_post(row["id"], "xhs", row)
            if p:
                items.append(p)
    upsert_many(items)
    print(f"  xhs_published.json → {len(items)} 条（累计 {total}）")

    # --- bili_published.json ---
    count_before = total
    path = os.path.join(DATA, "bili_published.json")
    data = _load_json(path, [])
    items = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            p = _to_platform_post(row["id"], "bilibili", row)
            if p:
                items.append(p)
    upsert_many(items)
    print(f"  bili_published.json → {len(items)} 条（累计 {total}）")

    # --- toutiao_published.json ---
    count_before = total
    path = os.path.join(DATA, "toutiao_published.json")
    data = _load_json(path, [])
    items = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            p = _to_platform_post(row["id"], "toutiao", row)
            if p:
                items.append(p)
    upsert_many(items)
    print(f"  toutiao_published.json → {len(items)} 条（累计 {total}）")

    return total


# ================================================================
# verifications 表
# ================================================================

def import_verifications(conn):
    """导入 verifications.json 到 verifications 表。"""
    count = 0
    path = os.path.join(DATA, "verifications.json")
    data = _load_json(path, {})
    if isinstance(data, dict):
        for case_id, row in data.items():
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row["case_id"] = case_id
            row.setdefault("checked_at", row.get("verified_at") or _now())
            db.upsert_verification(conn, case_id, row)
            count += 1
        conn.commit()
    print(f"  verifications.json → {count} 条")
    return count


# ================================================================
# main
# ================================================================

def main():
    ap = argparse.ArgumentParser(description="JSON 台账导入 SQLite")
    ap.add_argument("--cases", action="store_true", help="仅导入 cases 表")
    ap.add_argument("--posts", action="store_true", help="仅导入 platform_posts 表")
    ap.add_argument("--verify", action="store_true", help="仅导入 verifications 表")
    args = ap.parse_args()

    only_cases = args.cases and not (args.posts or args.verify)
    only_posts = args.posts and not (args.cases or args.verify)
    only_verify = args.verify and not (args.cases or args.posts)
    do_all = not (args.cases or args.posts or args.verify)

    conn = db.get_conn()
    try:
        if do_all or only_cases:
            print(">>> 导入 cases 表 ...")
            n = import_cases(conn)
            print(f"    cases 表共导入 {n} 条记录")

        if do_all or only_posts:
            print(">>> 导入 platform_posts 表 ...")
            n = import_platform_posts(conn)
            print(f"    platform_posts 表共导入 {n} 条记录")

        if do_all or only_verify:
            print(">>> 导入 verifications 表 ...")
            n = import_verifications(conn)
            print(f"    verifications 表共导入 {n} 条记录")

        # 统计
        lib_n = conn.execute("SELECT COUNT(*) FROM case_library WHERE deleted=0").fetchone()[0]
        cases_n = conn.execute("SELECT COUNT(*) FROM cases WHERE deleted=0").fetchone()[0]
        posts_n = conn.execute("SELECT COUNT(*) FROM platform_posts").fetchone()[0]
        ver_n = conn.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
        print(f"\n=== 导入完成 ===")
        print(f"  case_library:  {lib_n} 条（真值）")
        print(f"  cases(老表):   {cases_n} 条（graveyard/anti + 历史归档）")
        print(f"  platform_posts: {posts_n} 条")
        print(f"  verifications:  {ver_n} 条")
        print(f"  数据库文件: {db.current_path()}")
    finally:
        db.close_conn()


if __name__ == "__main__":
    main()
