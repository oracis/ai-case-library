#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书 `platform_posts.remote_id` 真值回填（PRD #107）。

背景 / 根因
------------
`xhs_publish.mark_published()` 发布时平台接口拿不到 noteId，`remote_id` 被硬
编码成 ""；`xhs_sync_ledger.py --apply` 虽能按线上快照回填，但它会整本重建
镜像（丢 `title_old` 等扩展字段）并回写全库 SQLite —— 红线不允许。结果就是
「镜像有 note_id、SQLite 真值没有」的长期裂口。

本脚本是发布后按**平台全量已发布列表**补 `remote_id` 的独立、可复跑入口。

严格范围
--------
* 只改 SQLite `platform_posts` 里 `state='published'` 且 `remote_id` 为空的行，
  逐行 `UPDATE ... SET remote_id=?, note=?`（rowcount 必须为 1）；
* `url` 一律不动（公开页被风控 ⇒ 留空不编）；`state` / `published_at` /
  `title` / `draft_id` / `attempt` / `updated_at` 一律不动，状态只前进；
* 镜像 `data/xhs_published.json` 只替换 note 里「本轮不回写真值」的过时表述，
  其余字段、顺序、LF / 无尾换行格式原样保留；
* 缺省干跑；`--yes` 才写盘；平台回读失败直接 abort，不静默回退。

配对判据（三方逐字，不许猜标题）
--------------------------------
1. 平台列表 noteId == 镜像该 case 的 `note_id`；
2. 平台 time == 镜像 `at` == SQLite `published_at`；
3. 该 noteId 未被 SQLite 任何其它行占用，且平台标题非空；
4. 平台 / 镜像 / SQLite published 三方总数与 noteId 集合完全闭合。

用法（9223 Chrome 必须前台同一条命令内起，见 `_launch_9223.py`）：
    python -X utf8 scripts/_launch_9223.py ; python -X utf8 scripts/xhs_backfill_remote_ids.py
    python -X utf8 scripts/_launch_9223.py ; python -X utf8 scripts/xhs_backfill_remote_ids.py --yes
    # 只读离线复核（证据文件必须自带 source 标注）：
    python -X utf8 scripts/xhs_backfill_remote_ids.py --offline-evidence tmp/xxx.json
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEDGER_DB = os.path.join(ROOT, "data", "ledger.db")
MIRROR = os.path.join(ROOT, "data", "xhs_published.json")
TMP = os.path.join(ROOT, "tmp")
EVIDENCE = os.path.join(TMP, "xhs_remote_backfill_evidence.json")
PLAN = os.path.join(TMP, "xhs_remote_backfill_plan.json")
REPORT = os.path.join(TMP, "xhs_remote_backfill_report.json")
NOW = time.strftime("%Y-%m-%d %H:%M")
STAMP = time.strftime("%Y%m%d-%H%M%S")
DB_BACKUP = os.path.join(TMP, "ledger_backup_backfill_%s.db" % STAMP)
MIRROR_BACKUP = os.path.join(TMP, "xhs_published_backfill_backup_%s.json" % STAMP)

OLD_NOTE = "SQLite remote_id 保持空（既有平台特性，本轮不回写真值）"
NEW_NOTE = "SQLite remote_id 已按 #107 回写真值（同一 noteId）"
MAX_ROWS = 8                      # 防失控：一次最多补 8 行


def jload(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def sql_rows():
    c = sqlite3.connect(LEDGER_DB)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "select * from platform_posts where platform='xhs' order by id")]
    finally:
        c.close()


def fetch_platform():
    """现场独立回读：user/posted?tab=0 全量已发布列表。"""
    import xhs_webapi as W
    cdp, _page = W.connect()
    if cdp is None:
        raise RuntimeError("9223 上没有 creator 页面（Chrome 未起 / 登录态失效）")
    try:
        notes, _ = W.posted_notes(pages=30, page_size=30, cdp=cdp)
    finally:
        try:
            cdp.close_target(cdp.tid)
        except Exception:
            pass
    out = []
    for n in notes:
        out.append({
            "note_id": n.get("note_id") or n.get("id") or "",
            "title": n.get("title") or "",
            "time": n.get("time") or "",
            "likes": n.get("likes"),
            "view_count": n.get("view_count"),
        })
    return out


def load_evidence(args):
    if args.offline_evidence:
        ev = jload(args.offline_evidence)
        if not ev.get("notes"):
            raise RuntimeError("离线证据无 notes：%s" % args.offline_evidence)
        ev["source"] = "offline:%s (原 source=%s)" % (
            os.path.basename(args.offline_evidence), ev.get("source"))
        return ev
    notes = fetch_platform()
    if len(notes) < 30:
        raise RuntimeError("现场回读只有 %d 条，疑似分页 / 风控异常，拒绝回填" % len(notes))
    ev = {"fetched_at": NOW, "source": "live:user/posted?tab=0",
          "total": len(notes), "notes": notes}
    jdump(EVIDENCE, ev)
    return ev


def build_plan(ev, rows, mirror):
    notes = ev["notes"]
    plat_by_id = {}
    for n in notes:
        nid = n.get("note_id") or ""
        if not nid or nid in plat_by_id:
            raise RuntimeError("平台列表 note_id 为空或重复：%r" % nid)
        plat_by_id[nid] = n

    mirror_by_id = {e.get("id"): e for e in mirror}
    if len(mirror_by_id) != len(mirror):
        raise RuntimeError("镜像里 case_id 有重复")
    published = [r for r in rows if (r.get("state") or "").strip() == "published"]
    empty = [r for r in published if not (r.get("remote_id") or "").strip()]
    if not empty:
        raise RuntimeError("没有 remote_id 为空的 published 行，无需回填")
    if len(empty) > MAX_ROWS:
        raise RuntimeError("待回填 %d 行 > 上限 %d，拒绝批量改动" % (len(empty), MAX_ROWS))

    # 三方总数 / noteId 集合闭合
    plat_ids = set(plat_by_id)
    mir_ids = {e.get("note_id") for e in mirror}
    if mir_ids != plat_ids:
        raise RuntimeError("镜像 noteId 集合与平台不一致：镜像多 %s / 平台多 %s"
                           % (sorted(mir_ids - plat_ids)[:3],
                              sorted(plat_ids - mir_ids)[:3]))
    if len(published) != len(notes) or len(published) != len(mirror):
        raise RuntimeError("三方总数不等：SQLite published=%d 平台=%d 镜像=%d"
                           % (len(published), len(notes), len(mirror)))
    occupied = {(r.get("remote_id") or "").strip()
                for r in rows if (r.get("remote_id") or "").strip()}
    bad = occupied - plat_ids
    if bad:
        raise RuntimeError("SQLite 有 %d 个 remote_id 平台查无：%s"
                           % (len(bad), sorted(bad)[:3]))

    fills = []
    claimed = set()
    for r in sorted(empty, key=lambda x: x["published_at"] or ""):
        cid = r["case_id"]
        m = mirror_by_id.get(cid)
        if not m or not m.get("note_id"):
            raise RuntimeError("%s 镜像缺 note_id" % cid)
        nid = m["note_id"]
        n = plat_by_id.get(nid)
        if not n:
            raise RuntimeError("%s 的镜像 noteId %s 平台查无" % (cid, nid))
        if not (n.get("title") or "").strip():
            raise RuntimeError("%s 平台标题为空" % cid)
        if nid in occupied or nid in claimed:
            raise RuntimeError("%s 的 noteId %s 已被别的行占用" % (cid, nid))
        if not (n.get("time") == m.get("at") == r.get("published_at")):
            raise RuntimeError("%s 三时点不一致：平台=%s 镜像=%s SQLite=%s"
                               % (cid, n.get("time"), m.get("at"), r.get("published_at")))
        if (r.get("url") or "").strip():
            raise RuntimeError("%s 已有 url，超出「url 留空不编」范围" % cid)
        claimed.add(nid)
        fills.append({
            "id": r["id"], "case_id": cid, "note_id": nid,
            "platform_time": n["time"], "platform_title": n["title"],
            "mirror_at": m.get("at"), "sqlite_published_at": r.get("published_at"),
            "url_before": r.get("url") or "", "state": r.get("state"),
            "note_tag": ("[11-09 #107 真值回填] remote_id=%s 来自平台 "
                         "user/posted?tab=0 全量列表（noteId 与镜像逐字一致，"
                         "平台 time=%s == 镜像 at == SQLite published_at，标题「%s」）；"
                         "url 留空不编；state/published_at/title 不动。"
                         % (nid, n["time"], n["title"])),
        })
    rest = plat_ids - occupied - claimed
    if rest:
        raise RuntimeError("平台还有 %d 条 noteId 无归属：%s"
                           % (len(rest), sorted(rest)[:3]))
    return {"at": NOW, "evidence_source": ev["source"],
            "platform_total": len(notes), "mirror_total": len(mirror),
            "sqlite_published_total": len(published),
            "sqlite_rows_total": len(rows), "fills": fills,
            "scope": "SQLite：逐行 UPDATE 只 SET remote_id / note；镜像：只改 4 条 note 的过时表述；"
                     "url / state / published_at / title / draft_id / attempt / updated_at 一律不动"}


def baseline(rows):
    import publish_slot as PS
    import publish_guard as PG
    import fifo_dedupe as FD
    b = {"decide": {}, "records_xhs": PS.records("xhs"),
         "union": sorted(FD.published_case_ids("xhs")),
         "dupe": {}, "rows": rows}
    for p, _label in PS.PLATFORMS:
        b["decide"][p] = PS.decide(p)
    for cid in ["ai-prank-call", "speel-co", "autoreels-ai", "coral",
                "search1api", "insect-bite-id"]:
        r = PG.check_dupe("xhs", cid)
        b["dupe"][cid] = r.get("ok") if isinstance(r, dict) else bool(r)
    return b


def write_db(plan):
    """一致性备份 → 单行 UPDATE（只两个字段）→ commit。"""
    src = sqlite3.connect(LEDGER_DB)
    dst = sqlite3.connect(DB_BACKUP)
    try:
        src.backup(dst)                       # 在线一致性备份，非裸 cp
    finally:
        dst.close()
        src.close()
    c = sqlite3.connect(LEDGER_DB)
    try:
        for f in plan["fills"]:
            old = c.execute(
                "select note from platform_posts where id=? and case_id=? "
                "and platform='xhs' and state='published' "
                "and (remote_id is null or remote_id='')",
                (f["id"], f["case_id"])).fetchone()
            if not old:
                raise RuntimeError("写前条件失效：%s" % f["case_id"])
            note = ((old[0] or "").rstrip() + (" ｜ " if (old[0] or "").strip() else "")
                    + f["note_tag"])
            cur = c.execute(
                "update platform_posts set remote_id=?, note=? where id=? "
                "and case_id=? and platform='xhs' and state='published' "
                "and (remote_id is null or remote_id='')",
                (f["note_id"], note, f["id"], f["case_id"]))
            if cur.rowcount != 1:
                raise RuntimeError("单行 UPDATE rowcount=%d：%s"
                                   % (cur.rowcount, f["case_id"]))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def write_mirror(mirror, plan):
    """只替换 note 里过时分句；其它字段逐项不动。"""
    shutil.copy2(MIRROR, MIRROR_BACKUP)
    targets = {f["case_id"] for f in plan["fills"]}
    hit = 0
    out = []
    for e in mirror:
        item = dict(e)
        if item.get("id") in targets and OLD_NOTE in (item.get("note") or ""):
            item["note"] = item["note"].replace(OLD_NOTE, NEW_NOTE)
            hit += 1
        out.append(item)
    if hit != len(targets):
        raise RuntimeError("镜像过时表述命中 %d 条，预期 %d 条"
                           % (hit, len(targets)))
    import atomicio
    atomicio.write_json(MIRROR, out, indent=2, newline="",
                        trailing_newline=False)
    return hit


def verify(plan, base, ev):
    import publish_slot as PS
    import publish_guard as PG
    import fifo_dedupe as FD
    rows = sql_rows()
    mirror = jload(MIRROR)
    mirror_by_id = {e.get("id"): e for e in mirror}
    by_id = {r["id"]: r for r in rows}
    plat_ids = {n["note_id"] for n in ev["notes"]}
    checks = {}

    # ① 4 行回填正确、url / 状态 / 时间 / 标题零漂移
    drift = []
    for f in plan["fills"]:
        r = by_id.get(f["id"])
        if not r:
            drift.append("%s 行消失" % f["case_id"])
            continue
        for k, expect in (("remote_id", f["note_id"]), ("url", f["url_before"]),
                          ("state", f["state"]), ("published_at", f["sqlite_published_at"])):
            if (r.get(k) or "") != expect:
                drift.append("%s.%s %r!=%r" % (f["case_id"], k, r.get(k), expect))
        if "#107 真值回填" not in (r.get("note") or ""):
            drift.append("%s.note 缺证据" % f["case_id"])
        m = mirror_by_id.get(f["case_id"]) or {}
        if m.get("note_id") != f["note_id"]:
            drift.append("%s 镜像 note_id 漂移" % f["case_id"])
        if OLD_NOTE in (m.get("note") or ""):
            drift.append("%s 镜像 note 仍有过时表述" % f["case_id"])
    checks["fills_exact_and_no_drift"] = not drift

    # ② 其它行逐字段零漂移（允许本次 4 行的 remote_id / note 变化）
    touched = {f["id"] for f in plan["fills"]}
    old_by_id = {r["id"]: r for r in base["rows"]}
    other = []
    for rid, r in by_id.items():
        o = old_by_id.get(rid)
        if not o:
            other.append("新增行 %s" % rid)
            continue
        for k, v in o.items():
            if rid in touched and k in ("remote_id", "note"):
                continue
            if r.get(k) != v:
                other.append("%s.%s" % (rid, k))
    for rid in old_by_id:
        if rid not in by_id:
            other.append("行消失 %s" % rid)
    checks["other_rows_untouched"] = not other

    # ③ 全库口径不变
    published = [r for r in rows if (r.get("state") or "").strip() == "published"]
    checks["published_total_same"] = len(published) == plan["sqlite_published_total"]
    checks["rows_total_same"] = len(rows) == plan["sqlite_rows_total"]
    checks["remote_id_all_on_platform"] = all(
        (r.get("remote_id") or "") in plat_ids for r in published)
    checks["published_at_only_on_published"] = not any(
        r.get("published_at") for r in rows
        if (r.get("state") or "").strip() != "published")
    checks["mirror_note_ids_eq_platform"] = (
        {e.get("note_id") for e in mirror} == plat_ids)
    checks["ids_eq_sqlite"] = set(mirror_by_id) == {
        r["case_id"] for r in published}

    # ④ 档期 / 判重 / FIFO 不变
    checks["records_xhs_same"] = PS.records("xhs") == base["records_xhs"]
    checks["decide_all_same"] = all(
        PS.decide(p) == base["decide"][p] for p, _label in PS.PLATFORMS)
    checks["fifo_union_same"] = (
        sorted(FD.published_case_ids("xhs")) == base["union"])
    checks["dupe_still_blocks"] = all(
        (PG.check_dupe("xhs", cid).get("ok") is False)
        for cid in base["dupe"])

    # ⑤ 镜像字节格式
    b = open(MIRROR, "rb").read()
    checks["mirror_lf_no_crlf"] = b"\r\n" not in b
    checks["mirror_no_trailing_newline"] = not b.endswith(b"\n")
    checks["mirror_total_36"] = len(mirror) == 36
    return {"at": NOW, "checks": checks, "drift": drift, "other_drift": other,
            "evidence_source": ev["source"], "platform_total": len(ev["notes"]),
            "sqlite_published_total": len(published), "mirror_total": len(mirror),
            "db_backup": os.path.relpath(DB_BACKUP, ROOT),
            "mirror_backup": os.path.relpath(MIRROR_BACKUP, ROOT)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真写盘（缺省只干跑）")
    ap.add_argument("--offline-evidence", default=None,
                    help="只读复核：用已有平台证据，不做现场回读")
    args = ap.parse_args()

    rows = sql_rows()
    mirror = jload(MIRROR)
    base = baseline(rows)
    ev = load_evidence(args)
    print("[证据] source=%s total=%s fetched_at=%s"
          % (ev["source"], len(ev["notes"]), ev.get("fetched_at")))
    plan = build_plan(ev, rows, mirror)
    jdump(PLAN, plan)
    print("[硬校验] 平台 %d / 镜像 %d / SQLite published %d，待回填 %d 行："
          % (plan["platform_total"], plan["mirror_total"],
             plan["sqlite_published_total"], len(plan["fills"])))
    for f in plan["fills"]:
        print("   + %-16s %-26s time=%s  %s"
              % (f["case_id"], f["note_id"], f["platform_time"], f["platform_title"]))
    print("[范围] %s" % plan["scope"])

    if not args.yes:
        print("[干跑] 计划 tmp/xhs_remote_backfill_plan.json；未写盘。")
        return 0
    if args.offline_evidence:
        ap.error("--yes 不允许用离线证据，必须现场回读")

    write_db(plan)
    hit = write_mirror(mirror, plan)
    rep = verify(plan, base, ev)
    rep["mirror_note_replaced"] = hit
    rep["plan"] = os.path.relpath(PLAN, ROOT)
    jdump(REPORT, rep)
    for k, v in rep["checks"].items():
        print("  %-32s %s" % (k, "OK" if v else "FAIL"))
    if rep["drift"] or rep["other_drift"]:
        print("  drift:", rep["drift"], rep["other_drift"])
    print("[备份] %s | %s" % (os.path.relpath(DB_BACKUP, ROOT),
                              os.path.relpath(MIRROR_BACKUP, ROOT)))
    print("[报告] %s" % os.path.relpath(REPORT, ROOT))
    ok = all(rep["checks"].values())
    print("[结果] %s" % ("全部复核通过" if ok else "有 FAIL，需排查（备份可回滚）"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
