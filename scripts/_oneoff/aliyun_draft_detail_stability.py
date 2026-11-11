#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立只读诊断：1699820 的 draftDetail 是否稳定为空 + 15 个 aid 两轮一致性。

前台一条命令内自起 9228，全程只读，不打开任何编辑器。
"""
from __future__ import annotations
import html
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y          # noqa: E402
import yun_login_keep as K         # noqa: E402

OUT = os.path.join(ROOT, "tmp", "aliyun_draft_detail_stability.json")
AIDS = ["1699820", "1709459", "1699818", "1710840", "1710229", "1710636",
        "1711205", "1710231"]

DETAIL_JS = """
fetch("/developer/api/articleDraft/draftDetail?aid=%s", {credentials:"include"})
  .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})"""

LIST_JS = """
fetch("/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1",
      {credentials:"include"})
  .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})"""


def detail(cdp, aid):
    raw = cdp.eval(DETAIL_JS % aid)
    code, _, payload = (raw or "").partition("|")
    try:
        j = json.loads(payload)
    except Exception:
        return {"aid": aid, "http": code, "parse_error": (payload or "")[:120]}
    d = j.get("data") or {}
    if not isinstance(d, dict):
        d = {}
    body = html.unescape(d.get("content") or "")
    return {"aid": aid, "http": code, "success": j.get("success"),
            "code": j.get("code"), "message": j.get("message"),
            "data_keys": sorted(d.keys()),
            "title": d.get("title") or "", "content_len": len(body),
            "raw_head": payload[:180]}


def main():
    _, cdp = Y._connect()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "source": "live:9228 draftDetail 重复回读（只读）", "rounds": []}
    try:
        login = K.probe_page(cdp)
        out["login"] = login
        print("[登录态]", login.get("logged"), flush=True)
        cdp.open("https://developer.aliyun.com/my#/article")
        time.sleep(8)

        for rd in (1, 2, 3):
            time.sleep(2)
            rows = [detail(cdp, a) for a in AIDS]
            out["rounds"].append({"round": rd, "rows": rows})
            print("--- 第 %d 轮" % rd, flush=True)
            for r in rows:
                print("   %-9s HTTP=%-4s title=%-38s len=%-6s success=%s code=%s"
                      % (r["aid"], r.get("http"), (r.get("title") or "(空)")[:38],
                         r.get("content_len"), r.get("success"), r.get("code")),
                      flush=True)

        # 列表视图：1699820 是否仍在
        raw = cdp.eval(LIST_JS)
        payload = (raw or "").partition("|")[2]
        try:
            j = json.loads(payload)
            d = j.get("data") or []
            if isinstance(d, dict):
                d = d.get("list") or []
            rows = [{"aid": str(x.get("aid")), "title": x.get("title") or "",
                     "len": len(html.unescape(x.get("content") or ""))}
                    for x in d if isinstance(x, dict)]
        except Exception as e:
            rows = [{"error": str(e)[:100]}]
        out["list_view"] = rows
        out["list_has_1699820"] = any(x.get("aid") == "1699820" for x in rows)
        print("[列表视图] 共 %d 条，含 1699820=%s"
              % (len(rows), out["list_has_1699820"]), flush=True)
        for x in rows:
            if x.get("aid") == "1699820":
                print("   1699820 列表条目：%s" % json.dumps(x, ensure_ascii=False), flush=True)

        r1 = {x["aid"]: x for x in out["rounds"][0]["rows"]}
        r3 = {x["aid"]: x for x in out["rounds"][2]["rows"]}
        out["stable"] = {a: (r1[a].get("content_len"), r3[a].get("content_len"))
                         for a in AIDS}
        out["conclusion"] = {
            "1699820_detail_all_empty": all(
                rd["rows"][0].get("content_len") == 0 for rd in out["rounds"]),
            "1699820_still_in_list": out["list_has_1699820"],
            "other_aids_stable": all(
                r1[a].get("content_len") == r3[a].get("content_len") and
                r1[a].get("title") == r3[a].get("title")
                for a in AIDS if a != "1699820"),
        }
        print("[结论] %s" % json.dumps(out["conclusion"], ensure_ascii=False), flush=True)
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
