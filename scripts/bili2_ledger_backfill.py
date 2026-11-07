#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本仓 db 里 `bilibili2` 的 published 记录回填到**游戏仓台账**（默认干跑）。

用途（2026-11-06）
------------------
二号进 FIFO 后，发布链路已由 `bilibili_publish._mark_manifest_published()`
在「已发表列表按 aid 回读命中」之后自动补写游戏仓台账。本脚本是两件事的
兜底，不是日常发布的一部分：

1. **核对两本账**：本仓 `platform_posts(platform='bilibili2')` 与游戏仓
   `data/publish_state.json` 的 bilibili2 列是否一致（默认干跑就干这个）。
2. **补写失败 / 历史缺口时回填**：`_mark_manifest_published` 里补写抛异常
   会打印本脚本的 `--go` 命令，按它补账即可。

⚠ 只认本仓 db 里 `state=published` 且**带 remote_id** 的记录 —— 那条记录
  写在回读命中之后，是回读证据的落点。没有 remote_id 的绝不回填。
⚠ 没有 `published_at` / `updated_at` 的记录也跳过：回填拿不出真实发布时点，
  不许用 now() 冒充（否则档期会把上周的稿算成今天发的）。

用法::

    python -X utf8 scripts/bili2_ledger_backfill.py             # 干跑，只报告
    python -X utf8 scripts/bili2_ledger_backfill.py --go        # 真回填
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import db                       # noqa: E402  本仓 SQLite 台账
import games_state as GS        # noqa: E402  游戏仓台账唯一读口 + 写口


def gaps(path=None):
    """返回 `(本仓全部 published 记录, 游戏仓缺的记录)`。

    缺 = 游戏仓里没有同一 slug 的 `state=published`。已有 `drafted` 也算缺，
    补写时 `record_published` 会把它升级成 published 并换上发布 aid
    （B站草稿发布后 aid 会重分配，旧 aid 留在 note 里）。
    """
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, "bilibili2", state="published") or []
    finally:
        db.close_conn()
    todo = []
    for p_ in posts:
        cid = GS.norm(p_.get("case_id"))
        rid = str(p_.get("remote_id") or "").strip()
        at = str(p_.get("published_at") or p_.get("updated_at") or "").strip()
        if not cid or not rid:
            continue                        # 无远端 ID ⇒ 不是回读证据，不补
        if not at:
            continue                        # 无发布时点 ⇒ 不许拿 now() 冒充
        old = GS.find("bilibili2", cid, path=path)
        if old.get("state") == "published":
            continue
        todo.append((cid, rid, at, str(p_.get("title") or ""), old))
    return posts, todo


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--go", action="store_true", help="真写盘；默认只报告")
    ap.add_argument("--state-path", default=None,
                    help="覆盖游戏仓台账路径（单测用；默认读 GAMES_STATE/GAMES_ROOT）")
    args = ap.parse_args()
    path = args.state_path

    posts, todo = gaps(path)
    print("本仓 db bilibili2 published：%d 条 ｜ 游戏仓缺口：%d 条"
          % (len(posts), len(todo)))
    print("游戏仓台账：%s" % GS.state_path(path))
    for cid, rid, at, _title, old in todo:
        print("  %-38s aid=%-12s at=%s  游戏仓现状态=%s"
              % (cid, rid, at, old.get("state") or "无"))
    if not todo:
        print("✓ 无缺口，两本账一致（不写盘）")
        return 0
    if not args.go:
        print("\n（干跑）确认无误后加 --go 才写盘")
        return 0

    done = 0
    for cid, rid, at, title, _old in todo:
        action, _entry = GS.record_published(
            "bilibili2", cid, remote_id=rid, title=title, at=at,
            note="回填：本仓 db 已记 published（回读证据）", path=path)
        print("  %s %s" % (action, cid))
        done += (action == "written")
    print("回填完成：新写 %d 条" % done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
