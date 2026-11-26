#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 easymix 腾讯云台账的非法状态（2026-11-26）。

背景：上一轮把 state 写成 'rejected'（`platform_posts` 没有这个态，
`yun_state.record_publish` 的 verdict="rejected" 明确落 draft），且
remote_id=2794842 残留 ⇒ `publish_guard.check_dupe` 按 bool(remote_id)
判「已发过」硬拦，改好的稿再也发不出去。

修法（两条都只动这一行/这一个子键，note 只追加不覆盖）：
1. db: `UPDATE platform_posts SET state='draft', remote_id='', ...`
   ⛔ 不走 db.upsert_post（它会把空字段用旧值救回，PRD #82）。
2. manifest: 单子键改 state/remote_id/note，indent=2 + 尾换行（与 HEAD 实际格式一致，避免整本重排）；
   ⛔ 不调 Manifest.save()、不整本 dumps（PRD #203 / #231 洗字段事故）。
article 2794842 的历史留在 note 里可追溯，不丢。

用法::

    python scripts/_oneoff/fix_tc_easymix_state_rejected_20261126.py          # 干跑
    python scripts/_oneoff/fix_tc_easymix_state_rejected_20261126.py --yes    # 落库
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

DB = os.path.join(ROOT, "data", "ledger.db")
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
BAK = os.path.join(ROOT, "tmp", "ledger_backup_easymix_20261126.db")
CASE, PLAT = "easymix", "tencentcloud"
ARTICLE = "2794842"

ADD = ("[2026-11-26 改稿] 产品名写法已改（首句品牌定义→中性工程切入，"
       "正文不再出现 easyMix 品牌名）；修正上一轮误写的非法 state=rejected→draft、"
       "清残留 remote_id=%s（article 号留在本行追溯）。"
       "改稿后可重发，是否真发由用户确认。" % ARTICLE)


def db_row():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute("select state,remote_id,draft_id,title,note "
                  "from platform_posts where platform=? and case_id=?",
                  (PLAT, CASE)).fetchone()
    c.close()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    print("=== db 改前 ===")
    old = db_row()
    print(json.dumps({k: (v[:160] + "…" if k == "note" and v and len(v) > 160 else v)
                      for k, v in old.items()}, ensure_ascii=False, indent=2) if old else "(无行)")
    m = json.load(open(MANIFEST, encoding="utf-8"))
    print("manifest 改前:", json.dumps(
        {k: m[CASE][PLAT].get(k) for k in ("state", "remote_id")}, ensure_ascii=False))

    if not a.yes:
        print("\n（干跑）加 --yes 才落库")
        return 0

    # 备份 db（真值在 SQLite，先留保险）
    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    shutil.copy2(DB, BAK)
    print("\n已备份 db ->", BAK)

    # 1. db 单行 UPDATE（note 只追加，⛔ 不走 upsert_post）。幂等：已是 draft 则跳过。
    if old["state"] == "draft" and not old["remote_id"]:
        print("db 已是合法 draft 且 remote_id 为空 ⇒ 跳过 db UPDATE（幂等）")
    else:
        c = sqlite3.connect(DB)
        cur = c.execute(
            "update platform_posts set state='draft', remote_id='', "
            "note=coalesce(note,'') || ? || char(10) || ?, "
            "updated_at=datetime('now','localtime') "
            "where platform=? and case_id=? and state='rejected'",
            (old["note"] or "", ADD, PLAT, CASE))
        print("db UPDATE 影响行:", cur.rowcount)
        c.commit()
        c.close()
        if cur.rowcount != 1:
            raise SystemExit("✗ db 更新行数 != 1，停手检查")

    # 2. manifest 单子键（保留所有既有键，note 追加，不删）
    with atomicio.file_lock(MANIFEST + ".lock"):
        before = json.load(open(MANIFEST, encoding="utf-8"))
        after = json.loads(json.dumps(before))
        entry = dict(after[CASE][PLAT])
        entry.update({"state": "draft", "remote_id": ""})
        old_note = entry.get("note") or ""
        if ADD not in old_note:
            entry["note"] = (old_note + " ‖ " + ADD) if old_note else ADD
        after[CASE][PLAT] = entry
        changed = ["%s.%s" % (cc, pp)
                   for cc in set(before) | set(after)
                   for pp in set(before.get(cc) or {}) | set(after.get(cc) or {})
                   if (before.get(cc) or {}).get(pp) != (after.get(cc) or {}).get(pp)]
        lost = [k for k in before if k not in after]
        print("manifest 变更项:", sorted(changed), "| 丢键:", lost)
        # 允许两种：① 首次修 state/remote_id（只动 easymix.tencentcloud）；
        #          ② 幂等重排格式（语义无变化，changed=[]）。
        allowed = ([], ["%s.%s" % (CASE, PLAT)])
        if lost or changed not in allowed:
            raise SystemExit("✗ manifest 变更超出预期，停手")
        # HEAD 实际格式 = indent=2 + 尾换行（实测 git show 原文末字节是 \n）
        atomicio.write_text(MANIFEST, json.dumps(after, ensure_ascii=False, indent=2) + "\n")

    # 3. 回读
    print("\n=== 回读 ===")
    back = db_row()
    print("db:", json.dumps({k: back[k] for k in ("state", "remote_id", "draft_id")},
                            ensure_ascii=False))
    m2 = json.load(open(MANIFEST, encoding="utf-8"))
    print("manifest:", json.dumps(
        {k: m2[CASE][PLAT][k] for k in ("state", "remote_id")}, ensure_ascii=False))
    ok = (back["state"] == "draft" and not back["remote_id"]
          and m2[CASE][PLAT]["state"] == "draft" and not m2[CASE][PLAT]["remote_id"])
    print("✓ 状态已回到合法 draft 且清 remote_id" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
