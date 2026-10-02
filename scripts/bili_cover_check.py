#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""逐条读远端草稿的封面状态，落盘到 out/bili_cover_state.json。

⚠ **必须边读边落盘**：75 次 `draft/view` 每条约 2 秒，一个进程跑完
容易被环境回收（实测跑到一半日志空、进程消失）。每条 append 一次，
被中断也能从断点续。

用法：
    python -u -X utf8 scripts/bili_cover_check.py            # 全量
    python -u -X utf8 scripts/bili_cover_check.py --case voklit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠⚠ 必须先清代理再连接：父进程环境里的 http_proxy=127.0.0.1:2662 会掐断 CDP 的
# WebSocket —— 连 /json/version 走 HTTP 没事，一发 Runtime.evaluate 就
# ConnectionAbortedError 10053。本脚本只连 localhost，代理纯属污染。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

import bili_title_push as tp        # noqa: E402
import bili_draft_api as api         # noqa: E402

OUT = os.path.join(tp.bp.ROOT, "out", "bili_cover_state.json")


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save(d):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--fresh", action="store_true",
                    help="清空已有结果重扫")
    args = ap.parse_args()

    cdp = tp.connect()
    ds = api.list_drafts(cdp, pn=1, ps=200)
    st = {} if args.fresh else load()
    # ⚠ 丢弃错误记录：Chrome 被回收（10061）那批的 cover=None 是假的，
    # 混进结果会得出「全部无封面」的错误结论。
    bad = [k for k, v in st.items() if v.get("err")]
    for k in bad:
        st.pop(k)
    todo = []
    for d in ds:
        aid = d.get("article_id")
        t = (d.get("title") or "").strip()
        cid = tp._cid_by_title_any(t) or ("unknown:%s" % aid)
        if args.case and cid != args.case:
            continue
        todo.append((cid, aid, t))
    # ⚠⚠ 台账键必须是 article_id，不能用 cid —— 一个 case 现在有**两条**草稿
    # （新标题版 + 旧标题版），按 cid 存会互相覆盖：75 条只能存下 39 个 key，
    # 「哪些有封面」这个问题直接被掩盖。
    todo = [t for t in todo if args.fresh or str(t[1]) not in st]
    print("远端 %d 条｜待查 %d｜已有 %d｜丢弃错误记录 %d"
          % (len(ds), len(todo), len(st), len(bad)), flush=True)
    ok = nov = 0
    cdp = None
    for i, (cid, aid, t) in enumerate(todo, 1):
        for attempt in range(3):
            try:
                if cdp is None:
                    cdp = tp.connect()
                v = api.view_draft(cdp, aid)
                iu = v.get("image_urls") or []
                paras = ((v.get("opus") or {}).get("content") or {}) \
                            .get("paragraphs") or []
                st[str(aid)] = {"cid": cid, "aid": aid, "title": t,
                                "cover": iu[0] if iu else None,
                                "paras": len(paras),
                                "mtime": v.get("mtime"),
                                "at": time.strftime("%H:%M:%S")}
                if iu:
                    ok += 1
                else:
                    nov += 1
                    print("[%d/%d] %-22s aid=%s 无封面"
                          % (i, len(todo), cid, aid), flush=True)
                break
            except Exception as exc:                      # noqa: BLE001
                msg = str(exc)
                # Chrome 被回收：重连一次再试，别把这条记成「无封面」
                if "10061" in msg or "10054" in msg or "Bad" in msg:
                    cdp = None
                    time.sleep(3)
                    continue
                st[str(aid)] = {"cid": cid, "aid": aid, "title": t,
                                "cover": None, "err": msg[:80],
                                "at": time.strftime("%H:%M:%S")}
                print("[%d/%d] %-22s aid=%s ERR %s"
                      % (i, len(todo), cid, aid, msg[:60]), flush=True)
                break
        if i % 5 == 0:
            save(st)
    save(st)
    print("=" * 60, flush=True)
    print("有封面 %d ｜ 无封面 %d ｜ 共 %d" % (ok, nov, len(st)), flush=True)
    print("结果已存", OUT, flush=True)


if __name__ == "__main__":
    main()
