# -*- coding: utf-8 -*-
"""2026-11-11 第二轮收尾：补正 CSDN 实际已发表却仍记 draft 的脏行。

判据：发布后独立双回读已通过（列表 170501859 标题逐字 + 公开页 8931 字/5 词）。
只做单行 UPDATE，⛔ 不用 Manifest.save() 整本落盘。
"""
import os
import shutil
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
p = os.path.join(ROOT, "data", "ledger.db")
bak = os.path.join(ROOT, "tmp", "ledger_backup_round2_%s.db"
                   % time.strftime("%Y%m%d_%H%M%S"))
shutil.copy2(p, bak)
print("备份:", os.path.basename(bak))

CASE, PLAT, RID = "ext-ep-007-openttd", "csdn", "170501859"
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row
before = c.execute(
    "select case_id,platform,state,remote_id,published_at,title "
    "from platform_posts where case_id=? and platform=?", (CASE, PLAT)).fetchone()
print("改前:", dict(before) if before else "MISSING")
assert before and before["state"] == "draft", "不是预期的 draft 脏行，拒绝改"
assert before["published_at"] == "2026-11-11 10:43:10", "时点与发布记录不符，拒绝改"

c.execute("update platform_posts set state='published', remote_id=? "
          "where case_id=? and platform=? and state='draft'",
          (RID, CASE, PLAT))
c.commit()
after = c.execute(
    "select case_id,platform,state,remote_id,published_at,title "
    "from platform_posts where case_id=? and platform=?", (CASE, PLAT)).fetchone()
print("改后:", dict(after))

bad = list(c.execute(
    "select case_id,platform,state,published_at from platform_posts "
    "where state!='published' and published_at is not null and published_at!=''"))
print("非 published 却带 published_at 的条数:", len(bad))
assert not bad, "仍有脏行"
assert after["state"] == "published" and after["remote_id"] == RID
print("✓ 补正完成")
