#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 收尾第 2 项：把 data/xhs_published.json 镜像同步到平台/SQLite 真值。

范围（严格）：
  · 只写 data/xhs_published.json；SQLite ledger.db 一行都不动。
  · 给 7 条缺 note_id 的条目补 note_id（平台侧 user/posted?tab=0 全量列表配对）
  · 补 1 条整条缺失的 ai-prank-call（SQLite 与平台都有，镜像没有）
  · 其余字段/顺序/字节格式（LF、无尾换行）原样保留

用法：
    python -X utf8 tmp/xhs_mirror_sync.py --dry   # 现场回读 + 硬校验 + 副本模拟，不写盘
    python -X utf8 tmp/xhs_mirror_sync.py --go    # 备份 + 原子写盘 + 复核
现场回读失败时回退到同轮 14:14 的平台证据，并在证据文件里如实标注 source。
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEDGER_JSON = os.path.join(ROOT, "data", "xhs_published.json")
LEDGER_DB = os.path.join(ROOT, "data", "ledger.db")
FALLBACK_EVIDENCE = os.path.join(ROOT, "tmp", "xhs_audit_7_evidence.json")
EVIDENCE = os.path.join(ROOT, "tmp", "xhs_mirror_sync_evidence.json")
PLAN = os.path.join(ROOT, "tmp", "xhs_mirror_sync_plan.json")
REPORT = os.path.join(ROOT, "tmp", "xhs_mirror_sync_report.json")
SIM_DIR = os.path.join(ROOT, "tmp", "mirror_sync_sim_data")
BACKUP = os.path.join(ROOT, "tmp", "xhs_published_sync_backup_%s.json"
                      % time.strftime("%Y%m%d-%H%M%S"))

NOW = time.strftime("%Y-%m-%d %H:%M")


def jload(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def db_rows():
    c = sqlite3.connect(LEDGER_DB)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "select id,case_id,state,remote_id,published_at,title,url,note "
            "from platform_posts where platform='xhs' and state='published' "
            "order by published_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def db_hash():
    h = hashlib.sha256()
    with open(LEDGER_DB, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_platform():
    """现场独立回读：前台同命令内自起的 9223 Chrome。"""
    import xhs_webapi as W
    cdp, page = W.connect()
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


def load_evidence():
    """现场回读优先；失败则回退同轮 14:14 平台全量证据（如实标注）。"""
    try:
        notes = fetch_platform()
        if len(notes) < 30:
            raise RuntimeError("现场回读只有 %d 条，疑似分页/风控异常" % len(notes))
        ev = {"fetched_at": NOW, "source": "live:user/posted?tab=0",
              "total": len(notes), "notes": notes}
    except Exception as e:                                     # noqa: BLE001
        old = jload(FALLBACK_EVIDENCE)
        ev = {"fetched_at": NOW,
              "source": "fallback:%s (fetched_at=%s); live_error=%s"
                        % (os.path.basename(FALLBACK_EVIDENCE),
                           old.get("fetched_at"), str(e)[:200]),
              "total": old.get("total"), "notes": old.get("notes")}
    jdump(EVIDENCE, ev)
    return ev


def build_plan(ev, js, rows):
    """三方硬校验；任何一条不满足直接 abort，不写盘。"""
    notes = ev["notes"]
    by_nid = {}
    for n in notes:
        nid = n.get("note_id") or ""
        if not nid or nid in by_nid:
            raise RuntimeError("平台列表 note_id 为空或重复：%r" % nid)
        by_nid[nid] = n
    by_time = {}
    for n in notes:
        by_time.setdefault(n.get("time") or "", []).append(n)

    sql = {r["case_id"]: r for r in rows}
    js_ids = [e.get("id") for e in js]
    if len(set(js_ids)) != len(js_ids):
        raise RuntimeError("镜像里 case_id 有重复")

    used = {e["note_id"] for e in js if e.get("note_id")}
    missing = [e for e in js if not e.get("note_id")]
    if len(missing) != 7:
        raise RuntimeError("预期 7 条缺 note_id，实测 %d 条" % len(missing))

    # ① 已有 note_id 的 28 条：必须全部在平台列表里逐字命中
    by_id_hits = []
    for e in js:
        nid = e.get("note_id")
        if not nid:
            continue
        if nid not in by_nid:
            raise RuntimeError("镜像 note_id 平台上查无：%s / %s" % (e["id"], nid))
        by_id_hits.append({"id": e["id"], "note_id": nid,
                           "platform_time": by_nid[nid].get("time"),
                           "json_at": e.get("at")})

    # ② 7 条缺 note_id：平台 time == json.at 的唯一未被占用候选
    fills = []
    for e in missing:
        cand = [n for n in by_time.get(e.get("at") or "", [])
                if n["note_id"] not in used]
        if len(cand) != 1:
            raise RuntimeError("%s 的时点 %s 候选 %d 个（要求恰好 1 个）"
                               % (e["id"], e.get("at"), len(cand)))
        n = cand[0]
        s = sql.get(e["id"]) or {}
        if s.get("remote_id") and s["remote_id"] != n["note_id"]:
            raise RuntimeError("%s 平台配对 %s 与 SQLite remote_id %s 冲突"
                               % (e["id"], n["note_id"], s["remote_id"]))
        if not s:
            raise RuntimeError("%s 在 SQLite xhs published 里不存在" % e["id"])
        fills.append({"id": e["id"], "at": e.get("at"),
                      "note_id": n["note_id"], "platform_title": n.get("title"),
                      "platform_time": n.get("time"),
                      "sqlite_remote_id": s.get("remote_id") or "",
                      "evidence": "平台全量列表 time 唯一精确配对"
                                  + (" + SQLite remote_id 一致" if s.get("remote_id")
                                     else "；SQLite remote_id 保持空（既有平台特性，不回写）")})
        used.add(n["note_id"])

    # ③ 剩余未配对平台条目：应恰好是 ai-prank-call
    rest = [n for n in notes if n["note_id"] not in used]
    extra = None
    if "ai-prank-call" not in js_ids:
        s = sql.get("ai-prank-call")
        if not s or not s.get("remote_id"):
            raise RuntimeError("SQLite 缺 ai-prank-call 的 remote_id，无法补镜像")
        hit = [n for n in rest if n["note_id"] == s["remote_id"]]
        if len(hit) != 1 or len(rest) != 1:
            raise RuntimeError("剩余平台条目 %d 条（要求恰好 1 条且 == ai-prank-call）"
                               % len(rest))
        n = hit[0]
        extra = {"id": "ai-prank-call", "at": n.get("time"),
                 "note_id": n["note_id"], "title": n.get("title"),
                 "sqlite_published_at": s.get("published_at"),
                 "evidence": "平台全量列表唯一未被镜像配对的条目，noteId 与 SQLite "
                             "remote_id 逐字一致；at 用平台 time（SQLite 为 %s，状态只前进不动）"
                             % s.get("published_at")}
        used.add(n["note_id"])

    if len(used) != len(notes):
        raise RuntimeError("配对后仍有平台条目未用：%d / %d" % (len(used), len(notes)))

    return {"at": NOW, "evidence": os.path.relpath(EVIDENCE, ROOT),
            "evidence_source": ev["source"], "platform_total": len(notes),
            "json_total_before": len(js), "sqlite_total": len(rows),
            "by_id_hits": len(by_id_hits), "fills": fills, "extra": extra,
            "scope": "只改 data/xhs_published.json；SQLite 零改动；"
                     "title/url/metrics/title_old 等其它字段一律不动"}


def apply_plan(js, plan):
    """生成新镜像：浅拷贝 + 只补 note_id/note + 按 at 升序插 1 条。"""
    fills = {f["id"]: f for f in plan["fills"]}
    out = []
    for e in js:
        item = dict(e)
        f = fills.get(item.get("id"))
        if f:
            item["note_id"] = f["note_id"]
            tail = ("SQLite remote_id 逐字一致（#104 已补正）"
                    if f["sqlite_remote_id"] else
                    "SQLite remote_id 保持空（既有平台特性，本轮不回写真值）")
            item["note"] = ("[11-09 镜像同步] note_id 按平台 user/posted?tab=0 "
                            "全量列表补齐；配对依据 json.at == 平台 time 唯一候选；" + tail)
        out.append(item)
    ex = plan.get("extra")
    if ex:
        rec = {"id": ex["id"], "at": ex["at"], "note_id": ex["note_id"],
               "title": ex["title"],
               "note": ("[11-09 镜像同步] 补登镜像缺失记录；平台全量列表唯一剩余条目，"
                        "noteId 与 SQLite remote_id 逐字一致；at 用平台 time，"
                        "SQLite published_at=%s 按 #104 裁定保持不动"
                        % ex["sqlite_published_at"])}
        out.append(rec)
        out.sort(key=lambda x: str(x.get("at") or ""))
    return out


def baseline():
    import publish_slot as PS
    import publish_guard as PG
    import fifo_dedupe as FD
    b = {"decide": {}, "records_xhs": PS.records("xhs"),
         "union": sorted(FD.published_case_ids("xhs")),
         "dupe": {}}
    for p, _label in PS.PLATFORMS:
        b["decide"][p] = PS.decide(p)
    for cid in ["ai-prank-call", "wpconvert-ai-convert-ai-sites-to-wordpress",
                "speel-co", "autoreels-ai"]:
        r = PG.check_dupe("xhs", cid)
        b["dupe"][cid] = r.get("ok") if isinstance(r, dict) else bool(r)
    return b


def simulate(new_js, base):
    """副本模拟：新镜像放进临时 data_dir，比对判重并集；档期走真库（SQLite 优先）。"""
    os.makedirs(SIM_DIR, exist_ok=True)
    sim = os.path.join(SIM_DIR, "xhs_published.json")
    jdump(sim, new_js)
    import fifo_dedupe as FD
    sim_union = sorted(FD.published_case_ids("xhs", data_dir=SIM_DIR))
    return {"sim_dir": os.path.relpath(SIM_DIR, ROOT),
            "sim_union_total": len(sim_union),
            "union_delta": sorted(set(sim_union) ^ set(base["union"])),
            "note": "publish_slot._rows_list 优先读 SQLite state=published，"
                    "SQLite 有 36 行时 JSON 只是 fallback，故 records/decide 预期零差异"}


def verify(new_js, plan, base, before_db_hash, before_bytes):
    import publish_slot as PS
    import publish_guard as PG
    import fifo_dedupe as FD
    after = jload(LEDGER_JSON)
    rows = db_rows()
    notes = jload(EVIDENCE)["notes"]
    plat_ids = {n["note_id"] for n in notes}
    sql_ids = {r["case_id"] for r in rows}
    js_ids = {e["id"] for e in after}
    checks = {}
    checks["json_total_36"] = len(after) == 36
    checks["json_note_id_missing_0"] = sum(1 for e in after if not e.get("note_id")) == 0
    checks["json_ids_eq_sqlite"] = js_ids == sql_ids
    checks["json_note_ids_eq_platform"] = ({e["note_id"] for e in after} == plat_ids)
    checks["sqlite_unchanged"] = (db_hash() == before_db_hash and len(rows) == 36)
    checks["lf_no_crlf"] = b"\r\n" not in open(LEDGER_JSON, "rb").read()
    checks["no_trailing_newline"] = not open(LEDGER_JSON, "rb").read().endswith(b"\n")
    checks["records_xhs_same"] = PS.records("xhs") == base["records_xhs"]
    checks["decide_all_same"] = all(
        PS.decide(p) == base["decide"][p] for p, _label in PS.PLATFORMS)
    union = sorted(FD.published_case_ids("xhs"))
    checks["fifo_union_same"] = union == base["union"]
    checks["dupe_still_blocks"] = all(
        (PG.check_dupe("xhs", cid).get("ok") is False)
        for cid in base["dupe"])
    # 原有 35 条：除新增 note_id / note 外，其它字段逐项不变
    old = {e["id"]: e for e in jload(BACKUP_LATEST[0])}
    drift = []
    for e in after:
        o = old.get(e["id"])
        if not o:
            continue
        for k, v in o.items():
            if k in ("note_id", "note"):
                continue
            if e.get(k) != v:
                drift.append("%s.%s" % (e["id"], k))
    checks["old_fields_no_drift"] = not drift
    return {"at": NOW, "checks": checks, "old_field_drift": drift,
            "sqlite_total": len(rows), "json_total": len(after),
            "platform_total": len(notes),
            "bytes_before": len(before_bytes),
            "bytes_after": os.path.getsize(LEDGER_JSON),
            "backup": os.path.relpath(BACKUP_LATEST[0], ROOT)}


BACKUP_LATEST = [None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--from-original", default=None,
                    help="从写盘前的原始备份重建（幂等重放，修正文案用）")
    args = ap.parse_args()
    if not (args.dry or args.go):
        ap.error("必须给 --dry 或 --go")

    js = jload(args.from_original or LEDGER_JSON)
    rows = db_rows()
    before_bytes = open(LEDGER_JSON, "rb").read()
    before_db_hash = db_hash()
    base = baseline()

    ev = load_evidence()
    print("[证据] source=%s total=%s" % (ev["source"], ev["total"]))
    plan = build_plan(ev, js, rows)
    jdump(PLAN, plan)
    print("[硬校验] 平台 %d / 镜像 %d / SQLite %d；已有 noteId 命中 %d；"
          "补齐 %d；新增 %s"
          % (plan["platform_total"], plan["json_total_before"], plan["sqlite_total"],
             plan["by_id_hits"], len(plan["fills"]),
             plan["extra"]["id"] if plan["extra"] else "无"))
    for f in plan["fills"]:
        print("   + %-46s %s  %s" % (f["id"], f["note_id"], f["evidence"]))

    new_js = apply_plan(js, plan)
    sim = simulate(new_js, base)
    print("[副本模拟] 并集 %d 条，差集 %s" % (sim["sim_union_total"], sim["union_delta"]))
    print("[范围] %s" % plan["scope"])

    if args.dry:
        jdump(os.path.join(ROOT, "tmp", "xhs_mirror_sync_preview.json"), new_js)
        print("[干跑] 预览 tmp/xhs_mirror_sync_preview.json；未写盘。")
        return 0

    # 写盘前再确认一遍现场证据未过期、计划未被外部改动
    assert jload(PLAN) == plan, "计划文件与内存不一致，拒绝写盘"
    shutil.copy2(LEDGER_JSON, BACKUP)
    BACKUP_LATEST[0] = BACKUP
    import atomicio
    atomicio.write_json(LEDGER_JSON, new_js, indent=2, newline="",
                        trailing_newline=False)
    rep = verify(new_js, plan, base, before_db_hash, before_bytes)
    rep["plan"] = os.path.relpath(PLAN, ROOT)
    rep["evidence_source"] = ev["source"]
    rep["sim"] = sim
    jdump(REPORT, rep)
    ok = all(rep["checks"].values())
    for k, v in rep["checks"].items():
        print("  %-28s %s" % (k, "OK" if v else "FAIL"))
    if rep["old_field_drift"]:
        print("  drift:", rep["old_field_drift"])
    print("[备份] %s" % os.path.relpath(BACKUP, ROOT))
    print("[报告] %s" % os.path.relpath(REPORT, ROOT))
    print("[结果] %s" % ("全部复核通过" if ok else "有 FAIL，需排查"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
