#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阿里云 6 条 auditing 真相回填（2026-11-25）。

背景：台账里 6 条 aliyun 行挂 `auditing`，实际匿名公开页全部 200 + 标题命中 +
无审核横幅 —— 其中 3 条是 11-17/11-20/11-24 的历史遗留（一直没人回读），
3 条是今天改稿重发的（stan/autoreels-ai/flodesk）。

做法：逐条调 `yun_state.record_publish()`（唯一规范入口，内部 judge 匿名回读 →
单行 UPDATE/INSERT），不自己拼 SQL，不调 Manifest.save()。

用法::

    python scripts/_oneoff/mark_aliyun_published_20261125.py          # 干跑
    python scripts/_oneoff/mark_aliyun_published_20261125.py --yes    # 落库
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db                      # noqa: E402
import yun_state as Y          # noqa: E402

CASES = ["upvoty", "ext-ep-007-openttd", "ext-ep-009-lichess",
         "stan", "autoreels-ai", "flodesk"]


def rows():
    conn = db.get_conn()
    conn.row_factory = None
    cur = conn.cursor()
    q = ",".join("?" * len(CASES))
    cur.execute("select case_id,state,remote_id,draft_id,title,published_at "
                "from platform_posts where platform='aliyun' and case_id in (%s)" % q,
                CASES)
    return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真落库（默认干跑）")
    a = ap.parse_args()

    rs = {r["case_id"]: r for r in rows()}
    for case in CASES:
        r = rs.get(case)
        if not r:
            print("%-24s ✗ 台账无此 case" % case)
            continue
        if r["state"] == "published":
            print("%-24s 已是 published，跳过" % case)
            continue
        aid, title = r["remote_id"], r["title"] or ""
        if not aid:
            print("%-24s ✗ remote_id 为空，无法裁定" % case)
            continue
        j = Y.judge(aid, title, platform="aliyun")
        print("%-24s %-6s → %-9s | %s" % (case, aid, j["state"], j["evidence"]))
        if not a.yes:
            continue
        if j["state"] != "published":
            print("     ↳ 裁定非 published，不动台账（需人工看原因）")
            continue
        try:
            Y.record_publish("aliyun", case, r["draft_id"] or "", aid, title,
                             verdict="published")
        except Exception as e:                                # noqa: BLE001
            print("     ↳ ✗ 落库失败：%s: %s" % (type(e).__name__, e))
            continue
        back = Y._row(db.get_conn(), case, "aliyun") or {}
        print("     ↳ 回读 state=%s published_at=%s url=%s"
              % (back.get("state"), back.get("published_at"),
                 (back.get("url") or "")[:60]))


if __name__ == "__main__":
    main()
