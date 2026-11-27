#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修正阿里云 6 条的 published_at（2026-11-25）。

为什么会错：`yun_state.record_publish()` 落库时用 `now` 写 `published_at`，
而这次回填的 6 条里，3 条是历史遗留（11-17 / 11-20 / 11-24 就已公开），
全被写成 11-25 12:46 ⇒ 阿里云「今日已发」从 3 篇虚增到 6 篇。

判据：日期取阿里云「我的文章」列表卡片上的发布日期（时刻平台未披露，
按时分秒 00:00:00 记，并在 note 注明来源）。今天三篇用台账 updated_at 的实际时刻。

用法::

    python scripts/_oneoff/fix_aliyun_published_at_20261125.py          # 干跑
    python scripts/_oneoff/fix_aliyun_published_at_20261125.py --yes    # 落库
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db                      # noqa: E402

# case -> (published_at, 依据说明)
FIX = {
    "upvoty":             ("2026-11-17 00:00:00", "阿里云列表卡片日期 2026-11-17"),
    "ext-ep-007-openttd": ("2026-11-20 00:00:00", "阿里云列表卡片日期 2026-11-20"),
    "ext-ep-009-lichess": ("2026-11-24 00:00:00", "阿里云列表卡片日期 2026-11-24"),
    "stan":               ("2026-11-25 10:59:00", "改稿重发，取台账 updated_at"),
    "autoreels-ai":       ("2026-11-25 11:02:00", "改稿重发，取台账 updated_at"),
    "flodesk":            ("2026-11-25 11:03:00", "改稿重发，取台账 updated_at"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    conn = db.get_conn()
    cur = conn.cursor()
    for case, (at, why) in FIX.items():
        cur.execute("select published_at from platform_posts where case_id=? "
                    "and platform='aliyun'", (case,))
        r = cur.fetchone()
        old = r[0] if r else None
        print("%-22s %s → %s  (%s)" % (case, old, at, why))
        if not a.yes:
            continue
        cur.execute("update platform_posts set published_at=?, updated_at=datetime('now','localtime') "
                    "where case_id=? and platform='aliyun'", (at, case))
        rc = cur.rowcount
        cur.execute("select published_at from platform_posts where case_id=? "
                    "and platform='aliyun'", (case,))
        print("     ↳ 回读 %s（影响 %d 行）" % (cur.fetchone()[0], rc))
    if a.yes:
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
