#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 阿里云三处错账补正（PRD #109）。

范围（严格，只改本地 SQLite，平台侧零写操作）：
  1. gojiberryai/aliyun  auditing → draft
     平台 1780408「抱歉,您所发布的内容未能通过审核」+ 匿名 404 ⇒ 未公开，
     auditing（审核中）语义错误。_STATE_RANK 中 auditing=2、draft=2（平级），
     不是状态回退。remote_id/url/published_at/title/draft_id 全不动。
  2. chessigma/aliyun    published → draft，清 published_at
     平台列表 12 条（滚动到底仍 12 条）只有 1777851「审核未通过」、匿名 404；
     longtail 里 aliyun 只有 11-01 建草稿、无 published 批次 ⇒ 假 published。
     ⚠ 状态 4→2 是**人工裁定纠偏**（有平台证据 + 一致性备份），
       不是 upsert_post 的自动状态回退；非 published 不许带 published_at。
  3. cod-dex/aliyun      补 remote_id=1778138
     平台 /article/1778138 匿名 200 + 标题逐字 + meta date 2026-11-03 00:28:52；
     longtail cod-dex-2026-11-03-1 明确 articleId=1778138 / published=true。
     published_at 保持既有值（#104「已 published 行不动」），title 不动。

⛔ 不碰：url / title / draft_id / attempt / updated_at；不删驳回旧稿；
   不重发；不改平台侧任何内容；不写 data/*.json 镜像。

证据（缺一 abort）：tmp/aliyun_3cases_evidence_v2.json（现场 9228 + 匿名 GET）
   + 本脚本现场匿名 GET 双证 + data/longtail_sync.json 批次交叉 + 写前状态断言。

用法：
    python -X utf8 tmp/aliyun_3cases_fix.py            # 干跑 + 副本模拟
    python -X utf8 tmp/aliyun_3cases_fix.py --yes      # 备份 + 单行 UPDATE + 复核
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

LEDGER_DB = os.path.join(ROOT, "data", "ledger.db")
SIM_DB = os.path.join(ROOT, "tmp", "aliyun_3cases_sim.db")
EVIDENCE = os.path.join(ROOT, "tmp", "aliyun_3cases_evidence_v2.json")
LONGTAIL = os.path.join(ROOT, "data", "longtail_sync.json")
PLAN = os.path.join(ROOT, "tmp", "aliyun_3cases_fix_plan.json")
REPORT = os.path.join(ROOT, "tmp", "aliyun_3cases_fix_report.json")
STAMP = time.strftime("%Y%m%d-%H%M%S")
BACKUP = os.path.join(ROOT, "tmp", "ledger_backup_aliyun_3cases_%s.db" % STAMP)

REJECTED = ["1780408", "1777853", "1777851"]     # 三篇被驳回
PUBLISHED_AID = "1778138"                         # cod-dex 真已发布
PUBLISHED_DATE = "2026-11-03 00:28:52"

FIXES = [
    {
        "case_id": "gojiberryai",
        "where": "state='auditing' and remote_id='1780408' and published_at is null",
        "args": (),
        "set": {"state": "draft"},
        "tag": ("[11-09 #109 错账纠偏] 平台 9228 带登录态文章页显示"
                "「抱歉,您所发布的内容未能通过审核」（列表标审核未通过 11-07 19:11:36），"
                "匿名 GET /article/1780408 = 404 ⇒ 未公开；auditing（审核中）语义错误，"
                "按现场证据改 draft（_STATE_RANK 中 auditing=2、draft=2 平级，非状态回退）。"
                "remote_id/url/published_at/title/draft_id 未动；按 11-08 裁定须在旧稿就地改稿再发。"),
    },
    {
        "case_id": "chessigma",
        "where": ("state='published' and remote_id='' and "
                  "published_at='2026-11-05 11:05:00' and url like '%edit=1700270%'"),
        "args": (),
        "set": {"state": "draft", "published_at": None},
        "tag": ("[11-09 #109 错账纠偏] 假 published：平台列表 12 条（滚动到底仍 12 条、无分页）"
                "只有 1777851 标「审核未通过」，带登录态文章页显示「未能通过审核」，匿名 GET 404；"
                "longtail 的 chessigma/aliyun 只有 11-01 建草稿 1700270、无 published 批次 ⇒ "
                "台账 published_at=2026-11-05 11:05:00 系当时「补 published_at 缺失」误补。"
                "人工裁定撤销假 published：published→draft、清 published_at（红线：published_at 只属于 published 行）。"
                "⚠ 状态 4→2 属人工纠偏，依据平台证据 + 一致性备份，非 upsert_post 自动覆盖；"
                "remote_id/url/title/draft_id 未动（url 为建稿编辑页、非公开页证据）。"),
    },
    {
        "case_id": "cod-dex",
        "where": ("state='published' and (remote_id='' or remote_id is null) "
                  "and url like '%1778138'"),
        "args": (),
        "set": {"remote_id": PUBLISHED_AID},
        "tag": ("[11-09 #109 真值回填] 平台 /article/1778138 匿名 GET 200、标题逐字、"
                "正文关键词 3/3、无审核横幅，<meta name=\"date\">=2026-11-03 00:28:52；"
                "longtail 的 cod-dex-2026-11-03-1 明确 articleId=1778138 / published=true ⇒ "
                "remote_id 空是漏填。published_at 保持既有值不动（#104 已 published 行不回改），"
                "title 为建稿镜像未动。列表另有被驳回旧稿 1777853 属同 case 另一篇，本轮未动。"),
    },
]

DUPE_SAMPLES = ["gojiberryai", "chessigma", "cod-dex",
                "insect-bite-id", "markdown-structure"]


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


def anon_get(aid):
    """现场独立匿名 GET（绕开本机代理污染），不复用证据文件里的结果。"""
    url = "https://developer.aliyun.com/article/" + aid
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/126 Safari/537.36")]
    try:
        r = op.open(url, timeout=12)
        body = r.read().decode("utf-8", "replace")
        code = r.status
    except urllib.error.HTTPError as e:
        body, code = "", e.code
    t = re.search(r"<title>(.*?)</title>", body, re.S)
    d = re.search(r'<meta[^>]*name=["\']date["\'][^>]*content=["\']([^"\']+)', body)
    return {
        "aid": aid, "http": code, "bytes": len(body),
        "title": re.sub(r"\s+", " ", t.group(1)).strip() if t else "",
        "meta_date": d.group(1) if d else "",
        "banner": "内容审核中" in body,
    }


def check_evidence():
    """硬校验：现场证据 + 本脚本现场匿名 GET + longtail 交叉，缺一 abort。"""
    ev = jload(EVIDENCE)
    if "live:9228" not in (ev.get("source") or ""):
        raise RuntimeError("证据不是现场 9228 回读：%r" % ev.get("source"))
    if not (ev.get("login") or {}).get("logged"):
        raise RuntimeError("现场证据显示未登录")
    if ev.get("persistent_tickets") != 4:
        raise RuntimeError("持久票据不是 4/4")

    lst = ev.get("list") or {}
    n1 = len(lst.get("links") or [])
    n2 = len(lst.get("links_after_scroll") or [])
    if not (n1 >= 12 and n1 == n2 and lst.get("complete")):
        raise RuntimeError("列表完整性不达标：links=%d after=%d complete=%s"
                           % (n1, n2, lst.get("complete")))
    hrefs = " ".join(x.get("href", "") for x in lst["links"])
    if "1777851" not in hrefs or "1780408" not in hrefs:
        raise RuntimeError("列表缺被驳回文章链接")

    pages = {p["aid"]: p for p in ev.get("pages") or []}
    for aid in REJECTED:
        p = pages.get(aid)
        if not p:
            raise RuntimeError("现场证据缺被驳回文章页 %s" % aid)
        if not any("未能通过审核" in x for x in p.get("audit_lines") or []):
            raise RuntimeError("%s 现场页没有「未能通过审核」文案" % aid)
        if not p.get("title_exact"):
            raise RuntimeError("%s 现场标题不逐字命中" % aid)
    pub = pages.get(PUBLISHED_AID)
    if not (pub and pub["title_exact"]
            and len(pub["keyword_hits"]) == pub["keyword_total"]
            and pub["meta_date"] == PUBLISHED_DATE):
        raise RuntimeError("1778138 现场已发布证据不达标：%s" % pub)

    # 现场匿名 GET 双证
    anon = {a["aid"]: a for a in (anon_get(x) for x in REJECTED + [PUBLISHED_AID])}
    for aid in REJECTED:
        if anon[aid]["http"] != 404:
            raise RuntimeError("%s 匿名 GET 不是 404：%s" % (aid, anon[aid]))
    p = anon[PUBLISHED_AID]
    if not (p["http"] == 200
            and "交互式编程课程的运行时设计" in p["title"]
            and p["meta_date"] == PUBLISHED_DATE
            and not p["banner"]):
        raise RuntimeError("1778138 匿名双证不达标：%s" % p)

    # longtail 批次交叉
    lt = jload(LONGTAIL)
    chess_pub = [k for k, v in lt.items()
                 if k.startswith("chessigma")
                 and ((v.get("platforms") or {}).get("aliyun") or {}).get("published")]
    if chess_pub:
        raise RuntimeError("longtail 里 chessigma/aliyun 有 published 批次，与「假 published」矛盾：%s"
                           % chess_pub)
    cd = lt.get("cod-dex-2026-11-03-1", {}).get("platforms", {}).get("aliyun", {})
    if not (cd.get("published") and str(cd.get("articleId")) == PUBLISHED_AID):
        raise RuntimeError("longtail 的 cod-dex-2026-11-03-1 与 1778138 对不上：%s" % cd)

    return {
        "evidence": os.path.relpath(EVIDENCE, ROOT), "at": ev.get("at"),
        "source": ev.get("source"), "logged": True,
        "persistent_tickets": ev.get("persistent_tickets"),
        "list_links": n1, "list_links_after_scroll": n2,
        "rejected_live_banner": {a: next(x for x in pages[a]["audit_lines"]
                                         if "未能通过审核" in x)
                                 for a in REJECTED},
        "rejected_anon": {a: anon[a] for a in REJECTED},
        "published_anon": anon[PUBLISHED_AID],
        "published_live_date": pub["meta_date"],
        "longtail_chessigma_aliyun_published_batches": chess_pub,
        "longtail_coddex_article": {"articleId": cd.get("articleId"),
                                    "published": cd.get("published")},
    }


def snapshot(db_path):
    """独立子进程算档期/档案/判重基线（LEDGER_DB 必须进程启动时就设好）。"""
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


def decide_semantic(d):
    """去掉 last.at 的精度字段（真值补账可能让它更精确，不算档期回归）。"""
    x = dict(d)
    last = dict(x.get("last") or {})
    last.pop("at", None)
    x["last"] = last
    return x


def records_change_ok(diff):
    """唯一允许的档案变化：移除 chessigma 的 11-05 假账（0 或 1 条）。

    gojiberryai 的 remote_id 保留 ⇒ _rows_yun 仍算 sent，records 不变；
    cod-dex 只补 remote_id、published_at 已有 ⇒ records 不变。
    """
    if set(diff) - {"aliyun"}:
        return False
    d = diff.get("aliyun") or {}
    added, removed = d.get("added") or [], d.get("removed") or []
    if added:
        return False
    return all(x[1] == "chessigma" and str(x[0]).startswith("2026-11-05")
               for x in removed)


def simulate(base):
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
    ddiff = [p for p in sim["decide"]
             if decide_semantic(sim["decide"][p]) != decide_semantic(base["decide"][p])]
    return {
        "sim_db": os.path.relpath(SIM_DB, ROOT),
        "records_diff": diff, "decide_diff": ddiff,
        "decide_aliyun_after": sim["decide"]["aliyun"],
        "dupe_after": sim["dupe"],
        "expected_records_change_ok": records_change_ok(diff),
        "dupe_still_blocks": all(v is False for v in sim["dupe"].values()),
    }


def apply_updates(path=LEDGER_DB):
    c = sqlite3.connect(path)
    try:
        for f in FIXES:
            row = c.execute(
                "select state,remote_id,published_at,note,url from platform_posts "
                "where case_id=? and platform='aliyun' and " + f["where"],
                (f["case_id"],) + f["args"]).fetchone()
            if not row:
                raise RuntimeError("%s 写前条件失效（可能已修过或并发改动）" % f["case_id"])
            old = row[3] or ""
            note = old.rstrip() + (" ｜ " if old.strip() else "") + f["tag"]
            sets = list(f["set"].keys())
            cur = c.execute(
                "update platform_posts set %s, note=? where case_id=? "
                "and platform='aliyun' and %s"
                % (", ".join("%s=?" % k for k in sets), f["where"]),
                tuple(f["set"][k] for k in sets) + (note, f["case_id"]) + f["args"])
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
    touched = {r["id"] for r in before_rows
               if r["case_id"] in [f["case_id"] for f in FIXES]}
    allowed = {
        "gojiberryai": {"state", "note"},
        "chessigma": {"state", "published_at", "note"},
        "cod-dex": {"remote_id", "note"},
    }
    drift, other = [], []
    for rid, r in by_id.items():
        o = old.get(rid)
        if not o:
            other.append("新增行 %s" % rid)
            continue
        for k, v in o.items():
            if rid in touched and k in allowed.get(o["case_id"], set()):
                continue
            if r.get(k) != v:
                (drift if rid in touched else other).append("%s.%s" % (rid, k))
    for rid in old:
        if rid not in by_id:
            other.append("行消失 %s" % rid)

    now = snapshot(LEDGER_DB)
    m = {r["case_id"]: r for r in after}
    checks = {}
    checks["only_allowed_fields_changed"] = not drift
    checks["other_rows_untouched"] = not other
    checks["gojiberryai_draft_remote_kept"] = (
        m["gojiberryai"]["state"] == "draft"
        and m["gojiberryai"]["remote_id"] == "1780408"
        and not m["gojiberryai"]["published_at"])
    checks["chessigma_draft_no_published_at"] = (
        m["chessigma"]["state"] == "draft"
        and not m["chessigma"]["published_at"]
        and not m["chessigma"]["remote_id"])
    checks["coddex_remote_id_1778138"] = (
        m["cod-dex"]["state"] == "published"
        and m["cod-dex"]["remote_id"] == PUBLISHED_AID
        and m["cod-dex"]["published_at"] == "2026-11-05 11:05:00")
    checks["nonpublished_has_no_published_at"] = all(
        not r["published_at"] for r in after if r["state"] != "published")
    checks["states_expected"] = (
        sum(1 for r in after if r["state"] == "published") == 18
        and sum(1 for r in after if r["state"] == "auditing") == 0)
    diff = {}
    for p, rows in now["records"].items():
        a = {tuple(x) for x in base["records"][p]}
        b = {tuple(x) for x in rows}
        if a != b:
            diff[p] = {"added": [list(x) for x in sorted(b - a)],
                       "removed": [list(x) for x in sorted(a - b)]}
    checks["records_change_expected"] = records_change_ok(diff)
    checks["decide_all_semantic_same"] = all(
        decide_semantic(now["decide"][p]) == decide_semantic(base["decide"][p])
        for p in base["decide"])
    checks["decide_aliyun_still_1of1"] = (
        now["decide"]["aliyun"].get("today_count") == 1
        and now["decide"]["aliyun"].get("quota") == 1)
    checks["dupe_still_blocks"] = all(v is False for v in now["dupe"].values())
    checks["simulate_expected"] = (sim["expected_records_change_ok"]
                                   and not sim["decide_diff"]
                                   and sim["dupe_still_blocks"])
    checks["no_platform_write"] = True          # 本脚本不含任何 CDP / POST
    return {"checks": checks, "records_diff": diff,
            "decide_aliyun": now["decide"]["aliyun"],
            "dupe": now["dupe"], "after_rows": {k: {
                "state": v["state"], "remote_id": v["remote_id"],
                "published_at": v["published_at"], "url": v["url"]}
                for k, v in m.items() if k in allowed},
            "drift": drift, "other": other}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真写盘（缺省只干跑）")
    args = ap.parse_args()

    print("[证据] 现场只读复核 + 本脚本现场匿名 GET + longtail 交叉", flush=True)
    ev = check_evidence()
    print("  9228 logged=%s 票据=%s 列表=%d/%d；三篇驳回匿名 404；"
          "1778138 匿名 200 @ %s"
          % (ev["logged"], ev["persistent_tickets"], ev["list_links"],
             ev["list_links_after_scroll"], ev["published_anon"]["meta_date"]),
          flush=True)

    before = sql_rows()
    pre = {f["case_id"]: next(r for r in before if r["case_id"] == f["case_id"])
           for f in FIXES}
    for cid, r in pre.items():
        print("[写前] %-14s state=%-10s remote_id=%-9s published_at=%s"
              % (cid, r["state"], r["remote_id"] or "(空)",
                 r["published_at"] or "(空)"), flush=True)

    base = snapshot(LEDGER_DB)
    sim = simulate(base)
    print("[副本模拟] records 差异=%s；decide 差异=%s；dupe=%s"
          % (sim["records_diff"], sim["decide_diff"], sim["dupe_after"]), flush=True)
    print("   预期内=%s dupe 仍拦=%s；aliyun 今日=%s/%s"
          % (sim["expected_records_change_ok"], sim["dupe_still_blocks"],
             sim["decide_aliyun_after"].get("today_count"),
             sim["decide_aliyun_after"].get("quota")), flush=True)

    plan = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scope": "只改本地 SQLite 三行；平台侧零写操作",
            "evidence": ev, "before": pre, "fixes": FIXES, "simulate": sim}
    jdump(PLAN, plan)

    if not args.yes:
        print("[干跑] 未写盘。确认无误后加 --yes。产物 %s"
              % os.path.relpath(PLAN, ROOT), flush=True)
        return 0

    src = sqlite3.connect(LEDGER_DB)
    dst = sqlite3.connect(BACKUP)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print("[备份] %s" % os.path.relpath(BACKUP, ROOT), flush=True)

    apply_updates(LEDGER_DB)
    print("[写盘] 3 条单行 UPDATE 完成", flush=True)
    result = verify(before, base, ev, sim)
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "backup": os.path.relpath(BACKUP, ROOT),
              "evidence": ev, "simulate": sim, "verify": result}
    jdump(REPORT, report)
    for k, v in result["checks"].items():
        print("   %-38s %s" % (k, "OK" if v else "FAIL"), flush=True)
    print("[复核] %d/%d 通过；产物 %s"
          % (sum(1 for v in result["checks"].values() if v),
             len(result["checks"]), os.path.relpath(REPORT, ROOT)), flush=True)
    if not all(result["checks"].values()):
        raise SystemExit("✗ 复核未全绿，请按备份回滚")
    return 0


if __name__ == "__main__":
    sys.exit(main())
