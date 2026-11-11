#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 第 1 项「提交发布」只读前置体检（不发布、不打开编辑器）。

⛔ 绝不打开 /article/new 或 ?publish=（每开一次平台自动新建一条草稿）。
只用 draftDetail / listDraft 只读 API + 本地档期，确认：
  1. 两条正本草稿标题/正文/摘要/分类等字段是否齐（发布子社区空值会静默拦发布）
  2. 今日配额是否仍为 1/1（真硬闸，--force 绕不过）
  3. 台账三行现状（发布成功后需按发布链同步）
  4. 输出 11-10 执行清单（不执行）

用法：python -X utf8 tmp/aliyun_publish_precheck.py
"""
from __future__ import annotations
import html
import io
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y          # noqa: E402
import yun_login_keep as K         # noqa: E402
import publish_slot as PS          # noqa: E402
import publish_guard as PG         # noqa: E402

OUT = os.path.join(ROOT, "tmp", "aliyun_publish_precheck.json")
KEEP = {"gojiberryai": "1710840", "chessigma": "1711205"}

DETAIL_JS = """
fetch("/developer/api/articleDraft/draftDetail?aid=%s", {credentials:"include"})
  .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})"""


def main():
    _, cdp = Y._connect()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "source": "live:9228 draftDetail 只读 + 本地档期/台账",
           "scope": "全程只读；不发布、不改台账、不打开编辑器"}
    try:
        login = K.probe_page(cdp)
        tickets = K.ticket_state(K.all_cookies(cdp))
        out["login"] = login
        out["persistent_tickets"] = sum(1 for v in tickets.values() if not v[0])
        cdp.open("https://developer.aliyun.com/my#/article")
        time.sleep(8)

        fields = {}
        for cid, aid in KEEP.items():
            raw = cdp.eval(DETAIL_JS % aid)
            code, _, payload = (raw or "").partition("|")
            j = json.loads(payload)
            d = j.get("data") or {}
            if not isinstance(d, dict):
                d = {}
            body = html.unescape(d.get("content") or "")
            # 只留字段名 + 值摘要，正文不落盘（避免副本）
            flat = {}
            for k, v in d.items():
                if k == "content":
                    flat[k] = {"len": len(body), "head": re.sub(r"\s+", " ", body)[:80]}
                elif isinstance(v, (str, int, float, bool, type(None))):
                    flat[k] = v if not isinstance(v, str) or len(v) < 160 else v[:160]
                else:
                    flat[k] = {"type": type(v).__name__, "preview": str(v)[:160]}
            fields[cid] = {"aid": aid, "http": code, "fields": flat,
                           "keys": sorted(d.keys())}
            print("--- %s / %s HTTP=%s" % (cid, aid, code), flush=True)
            for k in sorted(d.keys()):
                v = flat.get(k)
                print("    %-22s %s" % (k, str(v)[:110]), flush=True)
        out["draft_fields"] = fields

        # 本地档期与台账
        dec = PS.decide("aliyun")
        out["decide"] = {k: dec.get(k) for k in
                         ("day", "quota", "today_count", "reason", "today_kinds")}
        c = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
        c.row_factory = sqlite3.Row
        out["ledger_rows"] = [dict(r) for r in c.execute(
            "select case_id,state,remote_id,draft_id,published_at,title "
            "from platform_posts where platform='aliyun' "
            "and case_id in ('gojiberryai','chessigma','cod-dex') order by case_id")]
        c.close()
        out["dupe"] = {c: PG.check_dupe("aliyun", c).get("ok") for c in KEEP}

        out["publish_ready"] = {
            "both_drafts_intact": all(
                f["http"] == "200" and f["fields"].get("content", {}).get("len", 0) > 500
                for f in fields.values()),
            "both_titles_present": all(
                bool(f["fields"].get("title")) for f in fields.values()),
            "both_abstract_present": all(
                bool(f["fields"].get("abstractContent")) for f in fields.values()),
            "logged_in": bool(login.get("logged")),
            "quota_allows": not (dec.get("today_count", 0) >= dec.get("quota", 0)),
            "captcha_manual_required": True,
        }
        print("\n[发布前置] %s" % json.dumps(out["publish_ready"], ensure_ascii=False),
              flush=True)
        print("[配额] %s %s/%s | %s" % (dec.get("day"), dec.get("today_count"),
                                       dec.get("quota"), dec.get("reason")), flush=True)
        out["next_run"] = {
            "earliest": "2026-11-10（配额按自然日重置）",
            "need_user": "本人在电脑前拖 aliyunCaptcha 滑块（--headed + CHROME_HEADED=1，一条前台命令内起 9228）",
            "drafts": KEEP,
            "after_publish": "按发布链同步台账 title/remote_id/published_at，并按阿里云三态等过审再补 published",
            "blocked_today": ["今日配额 1/1 已满（真硬闸，--force 只绕 dedupe）",
                              "滑块必须人工", "打开编辑器会自动新建草稿，会污染刚清理的草稿箱"],
        }
        with io.open(OUT, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(out, ensure_ascii=False, indent=2))
        print("[产物] %s" % os.path.relpath(OUT, ROOT), flush=True)
    finally:
        try:
            cdp.bws.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
