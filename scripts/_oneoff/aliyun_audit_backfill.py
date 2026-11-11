#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云两条已过审文章补账（PRD #108 第 ① 项）。

范围（严格）：
  · 只把 insect-bite-id/1780780、markdown-structure/1776574 从
    auditing → published；published_at 用公开页 <meta name="date">（非 now）。
  · markdown-structure 顺带补 remote_id=1776574（原空）。
  · url / title / draft_id / attempt / updated_at 一律不动；note 只追加证据。
  · ⛔ 不动 gojiberryai/1780408（平台列表标「审核未通过」、匿名 404），
    不动 cod-dex/chessigma 的驳回旧稿（属另一轮裁定）。

证据（双证，缺一 abort）：
  A. tmp/aliyun_audit_platform_evidence.json：9228 带登录态真实浏览器回读
     （title 逐字 / 正文关键词全命中 / 无审核横幅 / meta date）
  B. 本脚本现场匿名 GET：HTTP 200 + title 命中 + meta date 命中 + 无审核横幅

用法：
    python -X utf8 tmp/aliyun_audit_backfill.py           # 干跑 + 副本模拟
    python -X utf8 tmp/aliyun_audit_backfill.py --yes     # 备份 + 单行 UPDATE + 复核
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEDGER_DB = os.path.join(ROOT, "data", "ledger.db")
SIM_DB = os.path.join(ROOT, "tmp", "aliyun_audit_sim.db")
EVIDENCE = os.path.join(ROOT, "tmp", "aliyun_audit_platform_evidence.json")
REPORT = os.path.join(ROOT, "tmp", "aliyun_audit_backfill_report.json")
STAMP = time.strftime("%Y%m%d-%H%M%S")
BACKUP = os.path.join(ROOT, "tmp", "ledger_backup_aliyun_audit_%s.db" % STAMP)
NOW = time.strftime("%Y-%m-%d %H:%M")

FIXES = [
    {"case_id": "insect-bite-id", "aid": "1780780",
     "expect_date": "2026-11-09 11:18:23",
     "expect_title": "识别虫咬类型应用：基于云资源与成本结构的工程实践",
     "set": {"state": "published", "published_at": "2026-11-09 11:18:23"}},
    {"case_id": "markdown-structure", "aid": "1776574",
     "expect_date": "2026-10-29 13:51:34",
     "expect_title": "同一份 Markdown 分发到 4 个技术社区",
     "set": {"state": "published", "remote_id": "1776574",
             "published_at": "2026-10-29 13:51:34"}},
]

DUPE_SAMPLES = ["insect-bite-id", "markdown-structure", "gojiberryai",
                "cod-dex", "chessigma"]


def decide_semantic(d):
    """档期判定语义：去掉 last.at 的秒级精度（真值补账会让它更精确）。

    11-09 副本模拟实测：补 published_at 后 `last.at` 从补记日志的
    11:17:20 变成公开页 meta date 的 11:18，同日同 case，today_count /
    reason / quota / today_kinds 逐项不变 ⇒ 这是真值精度提升，不是档期回归。
    """
    x = dict(d)
    last = dict(x.get("last") or {})
    last.pop("at", None)
    x["last"] = last
    return x


def records_change_ok(diff):
    """允许的档案变化（多一条不多）：
      + markdown-structure @ 2026-10-29（历史补登，之前 auditing 且 remote_id 空）
      + insect-bite-id @ 2026-11-09 11:18（真值，替换补记日志的 11:17:20）
      - insect-bite-id @ 2026-11-09 11:17:20（同一篇的补记时点）
    今日按 (日期, case_id) 去重后仍 1 篇，由 decide.today_count=1 交叉锁死。
    """
    if set(diff) - {"aliyun"}:
        return False
    d = diff.get("aliyun") or {}
    added, removed = d.get("added") or [], d.get("removed") or []
    if len(added) != 2 or len(removed) != 1:
        return False
    if not any(x[1] == "markdown-structure"
               and str(x[0]).startswith("2026-10-29") for x in added):
        return False
    if not any(x[1] == "insect-bite-id"
               and str(x[0]).startswith("2026-11-09") for x in added):
        return False
    return all(x[1] == "insect-bite-id"
               and str(x[0]).startswith("2026-11-09") for x in removed)


def jload(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def jdump(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def sql_rows(path=LEDGER_DB):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "select * from platform_posts where platform='aliyun' order by id")]
    finally:
        c.close()


def anon_get(url):
    """匿名 GET（绕开本机代理污染）：双证里的独立一路。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", "Mozilla/5.0 Chrome/126")]
    try:
        r = op.open(url, timeout=10)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def check_evidence():
    ev = jload(EVIDENCE)
    if ev.get("source") != "live:9228 chrome-yun-profile":
        raise RuntimeError("证据不是现场 9228 回读：%r" % ev.get("source"))
    if not (ev.get("login") or {}).get("logged"):
        raise RuntimeError("现场证据显示未登录")
    if ev.get("persistent_tickets") != 4:
        raise RuntimeError("持久票据不是 4/4")
    pages = {p["aid"]: p for p in ev["pages"]}
    anon = {}
    checks = []
    for f in FIXES:
        aid, url = f["aid"], "https://developer.aliyun.com/article/" + f["aid"]
        p = pages.get(aid)
        if not p:
            raise RuntimeError("现场证据缺 %s" % aid)
        if not (p["title_exact"] and len(p["keyword_hits"]) == p["keyword_total"]
                and not p["audit_banner"] and p["meta_date"] == f["expect_date"]):
            raise RuntimeError("%s 带登录态回读不达标：%s" % (aid, p))
        code, body = anon_get(url)
        title = re.search(r"<title>(.*?)</title>", body, re.S)
        date_m = re.search(r'<meta name="date" content="([^"]+)"', body)
        ok = (code == 200 and title and f["expect_title"] in title.group(1)
              and date_m and date_m.group(1) == f["expect_date"]
              and "内容审核中" not in body)
        anon[aid] = {"http": code, "bytes": len(body),
                     "title": title.group(1)[:90] if title else "",
                     "meta_date": date_m.group(1) if date_m else "",
                     "ok": bool(ok)}
        if not ok:
            raise RuntimeError("%s 匿名双证不达标：%s" % (aid, anon[aid]))
        checks.append({"case_id": f["case_id"], "aid": aid,
                       "live_date": p["meta_date"], "live_words":
                       "%d/%d" % (len(p["keyword_hits"]), p["keyword_total"]),
                       "live_banner": p["audit_banner"],
                       "anon": anon[aid]})
    return {"source": ev["source"], "at": ev["at"],
            "login": ev["login"].get("logged"),
            "persistent_tickets": ev["persistent_tickets"],
            "checks": checks}


def snapshot(db_path):
    """独立子进程算档期/档案基线（LEDGER_DB 必须进程启动时就设好）。"""
    code = r"""
import json,sys,os
sys.path.insert(0, os.path.join(%r, 'scripts'))
import publish_slot as PS, publish_guard as PG
out = {}
out['records'] = {p: [list(x) for x in PS.records(p)] for p,_ in PS.PLATFORMS}
out['decide'] = {p: PS.decide(p) for p,_ in PS.PLATFORMS}
out['dupe'] = {c: PG.check_dupe('aliyun', c, db_path=os.environ.get('LEDGER_DB')).get('ok') for c in %r}
print(json.dumps(out, ensure_ascii=False, default=str))
""" % (ROOT, DUPE_SAMPLES)
    env = dict(os.environ)
    env["LEDGER_DB"] = db_path
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=90)
    if r.returncode != 0:
        raise RuntimeError("档期子进程失败：%s" % r.stderr[-500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def simulate(base):
    """副本模拟：在 SQLite 副本上应用同样 UPDATE，再独立算档期。"""
    src = sqlite3.connect(LEDGER_DB)
    dst = sqlite3.connect(SIM_DB)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    apply_updates(SIM_DB)
    sim = snapshot(SIM_DB)
    diff = {}
    for p, rows in sim["records"].items():
        a = {tuple(x) for x in base["records"][p]}
        b = {tuple(x) for x in rows}
        if a != b:
            diff[p] = {"added": [list(x) for x in sorted(b - a)],
                       "removed": [list(x) for x in sorted(a - b)]}
    decide_diff = [p for p in sim["decide"]
                   if decide_semantic(sim["decide"][p])
                   != decide_semantic(base["decide"][p])]
    only_ok = records_change_ok(diff)
    return {"sim_db": os.path.relpath(SIM_DB, ROOT),
            "records_diff": diff, "decide_diff": decide_diff,
            "decide_aliyun_after": sim["decide"]["aliyun"],
            "last_at_before": (base["decide"]["aliyun"].get("last") or {}).get("at"),
            "last_at_after": (sim["decide"]["aliyun"].get("last") or {}).get("at"),
            "dupe_after": sim["dupe"],
            "expected_records_change_ok": only_ok,
            "dupe_still_blocks": all(v is False for v in sim["dupe"].values())}


def apply_updates(path=LEDGER_DB):
    c = sqlite3.connect(path)
    try:
        for f in FIXES:
            row = c.execute(
                "select state,remote_id,published_at,note from platform_posts "
                "where case_id=? and platform='aliyun'", (f["case_id"],)).fetchone()
            if not row or row[0] != "auditing" or row[2]:
                raise RuntimeError("%s 写前条件失效：%s" % (f["case_id"], row))
            note = ((row[3] or "").rstrip()
                    + (" ｜ " if (row[3] or "").strip() else "")
                    + "[11-09 #108 过审补账] 带登录态浏览器 + 匿名 GET 双证："
                      "公开页 HTTP 200、标题逐字命中、正文关键词全命中、无审核横幅，"
                      "<meta name=\"date\">=%s ⇒ 按三态 auditing→published；"
                      "published_at 用平台 meta date（非 now）；"
                      "url/title/draft_id/attempt/updated_at 未动。"
                      % f["expect_date"])
            sets = list(f["set"].keys())
            cur = c.execute(
                "update platform_posts set %s, note=? where case_id=? "
                "and platform='aliyun' and state='auditing' "
                "and published_at is null"
                % ", ".join("%s=?" % k for k in sets),
                tuple(f["set"][k] for k in sets) + (note, f["case_id"]))
            if cur.rowcount != 1:
                raise RuntimeError("单行 UPDATE rowcount=%d：%s"
                                   % (cur.rowcount, f["case_id"]))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def verify(before_rows, base, ev_info, sim):
    after = sql_rows()
    by_id = {r["id"]: r for r in after}
    old = {r["id"]: r for r in before_rows}
    touched = {r["id"] for r in before_rows if r["case_id"] in
               [f["case_id"] for f in FIXES]}
    allowed = {"state", "published_at", "remote_id", "note"}
    drift, other = [], []
    for rid, r in by_id.items():
        o = old.get(rid)
        if not o:
            other.append("新增行 %s" % rid)
            continue
        for k, v in o.items():
            if rid in touched and k in allowed:
                continue
            if r.get(k) != v:
                if rid in touched:
                    drift.append("%s.%s" % (rid, k))
                else:
                    other.append("%s.%s" % (rid, k))
    for rid in old:
        if rid not in by_id:
            other.append("行消失 %s" % rid)

    now = snapshot(LEDGER_DB)
    checks = {}
    checks["only_allowed_fields_changed"] = not drift
    checks["other_rows_untouched"] = not other
    checks["state_published_time_set"] = all(
        (by_id[r["id"]]["state"] == "published"
         and by_id[r["id"]]["published_at"] == f["expect_date"])
        for f in FIXES
        for r in before_rows if r["case_id"] == f["case_id"])
    checks["markdown_remote_id_1776574"] = next(
        r["remote_id"] for r in after if r["case_id"] == "markdown-structure") == "1776574"
    checks["url_title_untouched"] = all(
        by_id[r["id"]]["url"] == r["url"]
        and by_id[r["id"]]["title"] == r["title"]
        for r in before_rows if r["case_id"] in [f["case_id"] for f in FIXES])
    checks["published_at_only_on_published"] = not any(
        r["published_at"] for r in after
        if r["state"] != "published")
    checks["decide_semantics_all_same"] = all(
        decide_semantic(now["decide"][p]) == decide_semantic(base["decide"][p])
        for p in base["decide"])
    checks["decide_aliyun_last_same_day_case"] = (
        (now["decide"]["aliyun"].get("last") or {}).get("case_id")
        == (base["decide"]["aliyun"].get("last") or {}).get("case_id")
        and str((now["decide"]["aliyun"].get("last") or {}).get("at"))[:10]
        == str((base["decide"]["aliyun"].get("last") or {}).get("at"))[:10])
    checks["decide_aliyun_still_1of1"] = (
        now["decide"]["aliyun"].get("today_count") == 1
        and now["decide"]["aliyun"].get("quota") == 1)
    for p in base["records"]:
        a = {tuple(x) for x in base["records"][p]}
        b = {tuple(x) for x in now["records"][p]}
        if p == "aliyun":
            checks["records_aliyun_only_expected_addition"] = records_change_ok(
                {"aliyun": {"added": [list(x) for x in sorted(b - a)],
                            "removed": [list(x) for x in sorted(a - b)]}})
        else:
            checks["records_%s_same" % p] = a == b
    checks["dupe_still_blocks"] = all(v is False for v in now["dupe"].values())
    checks["gojiberryai_untouched"] = next(
        r["state"] for r in after if r["case_id"] == "gojiberryai") == "auditing"
    return {"at": NOW, "evidence": ev_info, "checks": checks,
            "drift": drift, "other_drift": other,
            "sim": {k: v for k, v in sim.items() if k != "dupe_after"},
            "backup": os.path.relpath(BACKUP, ROOT),
            "aliyun_today": now["decide"]["aliyun"],
            "aliyun_total_rows": len(after),
            "published_total": sum(1 for r in after if r["state"] == "published")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    ev = check_evidence()
    print("[双证] 现场 %s 登录=%s 持久票据=%d/4；匿名 GET："
          % (ev["source"], ev["login"], ev["persistent_tickets"]))
    for c in ev["checks"]:
        print("   %-20s %s meta_date=%s 词=%s 横幅=%s 匿名HTTP=%s"
              % (c["case_id"], c["aid"], c["live_date"], c["live_words"],
                 c["live_banner"], c["anon"]["http"]))

    before_rows = sql_rows()
    base = snapshot(LEDGER_DB)
    sim = simulate(base)
    print("[副本模拟] decide 语义差异=%s；档案差异平台=%s；aliyun 今日=%s/%s"
          % (sim["decide_diff"], list(sim["records_diff"]),
             sim["decide_aliyun_after"].get("today_count"),
             sim["decide_aliyun_after"].get("quota")))
    print("   last.at %s → %s（同日同 case 的真值精度提升）；允许变化=%s；dupe 仍拦=%s"
          % (sim["last_at_before"], sim["last_at_after"],
             sim["expected_records_change_ok"], sim["dupe_still_blocks"]))
    if not (sim["expected_records_change_ok"] and not sim["decide_diff"]
            and sim["dupe_still_blocks"]):
        raise SystemExit("✗ 副本模拟不达标，拒绝写盘")

    if not args.yes:
        print("[干跑] 未写盘。")
        return 0

    src = sqlite3.connect(LEDGER_DB)
    dst = sqlite3.connect(BACKUP)
    try:
        src.backup(dst)                     # 在线一致性备份
    finally:
        dst.close()
        src.close()
    apply_updates(LEDGER_DB)
    rep = verify(before_rows, base, ev, sim)
    jdump(REPORT, rep)
    for k, v in rep["checks"].items():
        print("  %-38s %s" % (k, "OK" if v else "FAIL"))
    if rep["drift"] or rep["other_drift"]:
        print("  drift:", rep["drift"], rep["other_drift"])
    print("[备份] %s" % os.path.relpath(BACKUP, ROOT))
    print("[报告] %s" % os.path.relpath(REPORT, ROOT))
    ok = all(rep["checks"].values())
    print("[结果] %s" % ("全部复核通过" if ok else "有 FAIL，需排查"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
