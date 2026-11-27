#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""easymix 第四次驳回止损：台账记账 + 不再重试（2026-11-26）。

事实：新草稿 353117 真发 → 文章 2794871，创作中心独立回读 state=rejected，
理由「内容包含广告/引流信息」，摘要引用**改后新首句**「上传人声与伴奏分轨…」。
发布命令被 SIGTERM（本机 DETACHED 回收）⇒ 平台已发但台账仍是 draft（未记账）。

红线：四次驳回（2792910/2793275/2794842/2794871），不再重试写端点。
修法（合法状态，不自创 rejected）：
  - db 单行 UPDATE：draft_id→353117（真实提交的那份），remote_id 清空（本就为空），
    note 只追加第四次驳回证据；⛔ 不走 upsert_post、不写非法 state。
  - manifest 单子键同步，indent=2 + 尾换行（与 HEAD 一致），⛔ 不整本 dumps。

幂等：note 里已有标记则跳过写入（重跑安全）。

用法::

    python scripts/_oneoff/mark_tc_easymix_rejected4_20261126.py          # 干跑
    python scripts/_oneoff/mark_tc_easymix_rejected4_20261126.py --yes    # 落库
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
BAK = os.path.join(ROOT, "tmp", "ledger_backup_easymix_rejected4_20261126.db")
CASE, PLAT = "easymix", "tencentcloud"
ARTICLE = "2794871"
DRAFT = "353117"
TAG = "[2026-11-26 第四次驳回]"

ADD_DB = (
    TAG + " 草稿 %s → 文章 %s；管理态独立回读 state=rejected，"
    "理由「内容包含广告/引流信息」，摘要引用改后新首句「上传人声与伴奏分轨…」"
    "⇒ 去品牌名后仍被判引流（触发点不只是产品名，是整段功能介绍写法）。"
    "四次驳回（2792910/2793275/2794842/2794871），不再重试写端点；"
    "发布命令 SIGTERM 未记账，本条补账。要再发须先删 2794871（幂等闸）并彻底改写法。"
    % (DRAFT, ARTICLE))
ADD_M = (
    TAG + " 草稿 %s → 文章 %s 第四次驳回（广告/引流，摘要引用改后新首句）⇒ 不再重试；"
    "补账（发布命令 SIGTERM 未写台账）。要再发须先删 2794871 并彻底改写法。"
    % (DRAFT, ARTICLE))


def db_row():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute("select state,remote_id,draft_id,note from platform_posts "
                  "where platform=? and case_id=?", (PLAT, CASE)).fetchone()
    c.close()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    old = db_row()
    print("db 改前:", json.dumps(
        {k: old[k] for k in ("state", "remote_id", "draft_id")}, ensure_ascii=False))
    m = json.load(open(MANIFEST, encoding="utf-8"))
    e = m[CASE][PLAT]
    print("manifest 改前:", json.dumps(
        {k: e.get(k) for k in ("state", "remote_id", "draft_id")}, ensure_ascii=False))

    if not a.yes:
        print("\n（干跑）加 --yes 落库")
        return 0

    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    shutil.copy2(DB, BAK)
    print("已备份 db ->", BAK)

    # 1. db 单行 UPDATE（note 只追加，幂等）
    if TAG in (old["note"] or ""):
        print("db 已有第四次驳回标记 ⇒ 跳过 UPDATE")
    else:
        c = sqlite3.connect(DB)
        cur = c.execute(
            "update platform_posts set state='draft', remote_id='', draft_id=?, "
            "note=coalesce(note,'') || ? || char(10) || ?, "
            "updated_at=datetime('now','localtime') "
            "where platform=? and case_id=?",
            (DRAFT, old["note"] or "", ADD_DB, PLAT, CASE))
        print("db UPDATE 影响行:", cur.rowcount)
        c.commit(); c.close()
        if cur.rowcount != 1:
            raise SystemExit("✗ db 更新行数 != 1，停手")

    # 2. manifest 单子键（保留所有键，note 追加）
    with atomicio.file_lock(MANIFEST + ".lock"):
        before = json.load(open(MANIFEST, encoding="utf-8"))
        after = json.loads(json.dumps(before))
        entry = dict(after[CASE][PLAT])
        entry.update({"state": "draft", "remote_id": "", "draft_id": DRAFT})
        on = entry.get("note") or ""
        if TAG not in on:
            entry["note"] = (on + " ‖ " + ADD_M) if on else ADD_M
        after[CASE][PLAT] = entry
        changed = ["%s.%s" % (cc, pp)
                   for cc in set(before) | set(after)
                   for pp in set(before.get(cc) or {}) | set(after.get(cc) or {})
                   if (before.get(cc) or {}).get(pp) != (after.get(cc) or {}).get(pp)]
        lost = [k for k in before if k not in after]
        print("manifest 变更项:", sorted(changed), "| 丢键:", lost)
        if lost or (changed and changed != ["%s.%s" % (CASE, PLAT)]):
            raise SystemExit("✗ manifest 变更超出预期，停手")
        atomicio.write_text(MANIFEST, json.dumps(after, ensure_ascii=False, indent=2) + "\n")

    # 3. 回读
    back = db_row()
    m2 = json.load(open(MANIFEST, encoding="utf-8"))
    e2 = m2[CASE][PLAT]
    print("\n回读 db:", {k: back[k] for k in ("state", "remote_id", "draft_id")})
    print("回读 manifest:", {k: e2.get(k) for k in ("state", "remote_id", "draft_id")})
    ok = (back["state"] == "draft" and not back["remote_id"] and back["draft_id"] == DRAFT
          and TAG in back["note"] and e2.get("draft_id") == DRAFT and TAG in e2.get("note", ""))
    print("✓ 第四次驳回已补账、台账合法 draft、不再重试" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
