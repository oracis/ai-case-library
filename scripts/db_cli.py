#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 统一台账查询 CLI。

用法示例::

    # 查单条 case
    python scripts/db_cli.py case flowmance-templates

    # 查某案例在所有平台的发布记录（跨平台一条 SQL）
    python scripts/db_cli.py posts flowmance-templates

    # 某平台最旧的 draft（FIFO 草稿箱语义）
    python scripts/db_cli.py drafts bilibili
    python scripts/db_cli.py drafts xhs --limit 3

    # 某平台的已发布记录
    python scripts/db_cli.py published wechat
    python scripts/db_cli.py published toutiao --state published --limit 10

    # 按 tier 聚合
    python scripts/db_cli.py tiers

    # 执行自定义 SQL（只读，SELECT only）
    python scripts/db_cli.py sql "SELECT platform, COUNT(*) FROM platform_posts GROUP BY platform"

    # 导出回 JSON（备份用）
    python scripts/db_cli.py export cases data/cases_export.json
    python scripts/db_cli.py export posts data/posts_export.json
    python scripts/db_cli.py export verifications data/verifications_export.json

输出默认为人类可读的表格；加 --json 可得机器可读的 JSON。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import db  # noqa: E402


def _fmt_rows(rows, title=None):
    """把 list[dict] 格式化为对齐的文本表格。"""
    if not rows:
        if title:
            print(f"(无 {title})")
        return
    keys = list(rows[0].keys())
    widths = {}
    for k in keys:
        col_vals = [len(str(r.get(k, ""))) for r in rows]
        widths[k] = max(len(str(k)), max(col_vals))
    line = " | ".join(k.ljust(widths[k]) for k in keys)
    sep = "-+-".join("-" * widths[k] for k in keys)
    if title:
        print(f"=== {title} ({len(rows)} 条) ===")
    print(line)
    print(sep)
    for r in rows:
        print(" | ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys))


def cmd_case(args):
    conn = db.get_conn()
    try:
        c = db.get_case(conn, args.id)
        if not c:
            print(f"未找到 case: {args.id}")
            return 1
        if args.json:
            for jf in ("metrics", "models", "replicability", "why_it_works", "playbook",
                       "signals", "traffic_sources", "corrections", "sources", "tags"):
                c[jf] = db.json_field(c.get(jf))
            print(json.dumps(c, ensure_ascii=False, indent=2))
        else:
            # 只打印关键字段，避免太长
            show = ("id", "type", "name", "category", "status", "verification", "caliber",
                    "tier", "updated_at")
            for k in show:
                print(f"{k}: {c.get(k)}")
    finally:
        db.close_conn()
    return 0


def cmd_posts(args):
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_case(conn, args.id)
        if args.json:
            print(json.dumps(posts, ensure_ascii=False, indent=2))
        else:
            _fmt_rows(posts, title=f"{args.id} 的平台发布记录")
    finally:
        db.close_conn()
    return 0


def cmd_drafts(args):
    conn = db.get_conn()
    try:
        drafts = db.get_drafts_fifo(conn, args.platform, limit=args.limit)
        if args.json:
            print(json.dumps(drafts, ensure_ascii=False, indent=2))
        else:
            _fmt_rows(drafts, title=f"{args.platform} 最旧的 {len(drafts)} 条 draft (FIFO)")
    finally:
        db.close_conn()
    return 0


def cmd_published(args):
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, args.platform, state=args.state, limit=args.limit)
        if args.json:
            print(json.dumps(posts, ensure_ascii=False, indent=2))
        else:
            _fmt_rows(posts, title=f"{args.platform} 已发布记录 (state={args.state})")
    finally:
        db.close_conn()
    return 0


def cmd_tiers(args):
    conn = db.get_conn()
    try:
        # 真值是 case_library（2026-11-05 起）；老表 cases 只剩 graveyard/anti_cases
        rows = conn.execute(
            "SELECT tier, COUNT(*) AS n FROM case_library WHERE deleted=0 "
            "GROUP BY tier ORDER BY tier"
        ).fetchall()
        data = [{"tier": r[0], "count": r[1]} for r in rows]
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            _fmt_rows(data, title="按 tier 聚合 case_library")
    finally:
        db.close_conn()
    return 0


def cmd_sql(args):
    sql = args.sql.strip()
    if not sql.lower().startswith("select"):
        print("⚠ 出于安全，仅支持 SELECT 语句。")
        return 1
    conn = db.get_conn()
    try:
        cur = conn.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        # 把 JSON 字段反序列化（db.py 的 json_field 只对单值有效，这里统一处理常见 JSON 列）
        json_cols = set()
        for r in rows:
            for k, v in r.items():
                if isinstance(v, str) and v.startswith(("{", "[")):
                    json_cols.add(k)
        for r in rows:
            for k in json_cols:
                try:
                    r[k] = json.loads(r[k])
                except (json.JSONDecodeError, TypeError):
                    pass
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _fmt_rows(rows, title=f"SQL 查询结果 ({len(rows)} 条)")
    finally:
        db.close_conn()
    return 0


def cmd_export(args):
    conn = db.get_conn()
    try:
        if args.target == "cases":
            n = db.export_cases_to_json(conn, args.path)
        elif args.target == "posts":
            n = db.export_posts_to_json(conn, args.path)
        elif args.target == "verifications":
            n = db.export_verifications_to_json(conn, args.path)
        else:
            print(f"未知导出目标: {args.target}")
            return 1
        print(f"已导出 {n} 条到 {args.path}")
    finally:
        db.close_conn()
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="SQLite 统一台账查询 CLI")
    ap.add_argument("--json", action="store_true", help="机器可读的 JSON 输出")
    sub = ap.add_subparsers(dest="command", required=True)

    p_case = sub.add_parser("case", help="查单条 case")
    p_case.add_argument("id")
    p_case.set_defaults(func=cmd_case)

    p_posts = sub.add_parser("posts", help="查某案例在所有平台的发布记录")
    p_posts.add_argument("id")
    p_posts.set_defaults(func=cmd_posts)

    p_drafts = sub.add_parser("drafts", help="某平台最旧的 draft (FIFO)")
    p_drafts.add_argument("platform")
    p_drafts.add_argument("--limit", type=int, default=5)
    p_drafts.set_defaults(func=cmd_drafts)

    p_pub = sub.add_parser("published", help="某平台的已发布记录")
    p_pub.add_argument("platform")
    p_pub.add_argument("--state", default="published")
    p_pub.add_argument("--limit", type=int, default=20)
    p_pub.set_defaults(func=cmd_published)

    sub.add_parser("tiers", help="按 type/tier 聚合 cases").set_defaults(func=cmd_tiers)

    p_sql = sub.add_parser("sql", help="执行只读 SELECT SQL")
    p_sql.add_argument("sql")
    p_sql.set_defaults(func=cmd_sql)

    p_exp = sub.add_parser("export", help="导出回 JSON 备份")
    p_exp.add_argument("target", choices=["cases", "posts", "verifications"])
    p_exp.add_argument("path")
    p_exp.set_defaults(func=cmd_export)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
