#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条台账三方对账：草稿箱 × 已发布列表 × 本地台账。

为什么必须三方（2026-10-19 定论）：
    只对一本台账会得出错误结论 ——「草稿箱里没有」既可能是「已群发升格」，
    也可能是「被删」，还可能是「从没建成」。三种情况在单侧视角下长得一样，
    而处置完全不同（前者不用管，后两者要重发）。
    实测代价：1030 之前一批 `publish --api` 在 headless 下跑过，服务端不认
    tt-anti-token（7050），代码只判 `code:0` 就记了「成功」⇒ 台账里出现
    幽灵条目，`pending_cases()` 把它们永久排除，内容再也发不出去。

判据（三态）：
    · 在草稿箱          → `draft`，正常
    · 在已发布列表      → `published`，正常
    · 两边都没有        → `ghost`，**台账虚记**，需要重排版
反向再查两类漏登：草稿箱有台账无、已发布有台账无。

用法：
    python toutiao_reconcile.py                       # 只读，打报告
    python toutiao_reconcile.py --json out/rc.json    # 顺带落一份 JSON
退出码：0=两侧全对上；1=有 ghost 或漏登（CI 可当门禁）。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tt  # noqa: E402
import wechat_publish as wp   # noqa: E402

DRAFT_FILE = os.path.join(ROOT, "data", "toutiao_drafts.json")
PUB_FILE = os.path.join(ROOT, "data", "toutiao_published.json")

# 台账 note 里用『』包住的远端标题
_ALIAS_RE = re.compile(r"『([^』]+)』")


def load_ledger():
    """读两本台账，统一成 [{id, at, note?}]（兼容老的纯 id 列表）。"""
    def _r(p):
        if not os.path.isfile(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            return [e if isinstance(e, dict) else {"id": e}
                    for e in json.load(f) if e]
    return _r(DRAFT_FILE), _r(PUB_FILE)


def collect_alias(ledger_rows):
    """从 note 里挖历史远端标题（=别名），返回 {id: [标题, ...]}。

    🚨 为什么不能只按产品名匹配：早期文章标题是 `月收$X：一句话`，现行是
       `产品名：一句话`，**同一篇文章两种标题**。2026-10-19 首轮纯 name
       匹配把 13 篇老文章误判成幽灵（其中 4 条已进了 ghost 名单）。
       note 里的『…』是历次人工对账的结论，是最可靠的映射源。
    """
    out = {}
    for e in ledger_rows:
        for m in _ALIAS_RE.findall(e.get("note") or ""):
            out.setdefault(e["id"], []).append(m)
    return out


def match_titles(cid, case, alias_for_id, titles):
    """返回**全部**命中的远端标题。

    同一 case 可能发过两篇（bustem / cometly 都是新旧口径各一篇），
    只取第一条会让第二篇变成假孤儿，所以这里返回 list。
    """
    hit = []
    for a in alias_for_id or []:
        if a in titles and a not in hit:
            hit.append(a)
    if case:
        one = tt.draft_title_match(cid, case, titles)
        if one and one not in hit:
            hit.append(one)
    return hit


def build_rows(led_d, led_p, cases, d_titles, p_titles):
    led_d_ids = {e["id"] for e in led_d}
    led_p_ids = {e["id"] for e in led_p}
    alias = collect_alias(led_d + led_p)
    rows = {}
    for cid in led_d_ids | led_p_ids:
        c = cases.get(cid)
        dt = match_titles(cid, c, alias.get(cid), d_titles)
        pt = match_titles(cid, c, alias.get(cid), p_titles)
        rows[cid] = {
            "id": cid,
            "name": (c or {}).get("name", ""),
            "in_draft_ledger": cid in led_d_ids,
            "in_pub_ledger": cid in led_p_ids,
            "draft_title": dt[0] if dt else None,
            "pub_title": pt[0] if pt else None,
            "all_titles": dt + pt,
            "state": ("published" if pt else "draft" if dt else "ghost"),
        }
    return rows


def pull_drafts():
    """草稿箱全量（draft_list，一次拿全，无需翻页）。"""
    cdp, t = tt._open(tt.TT_DRAFT, wait=8)
    try:
        tt._check_login(cdp)
        raw = tt.fetch_draft_api_raw(cdp)
        if raw is None:
            raise SystemExit("draft_list 不通（登录态过期或接口变了？）")
        out = []
        for x in raw:
            if not isinstance(x, dict):
                continue
            out.append({"title": (x.get("title") or "").strip(),
                        "gid": str(x.get("gid") or ""),
                        "update_time": x.get("update_time") or 0})
        return out
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:                                # noqa: BLE001
            pass


def pull_published(max_page=6):
    """已发布图文全量。⚠ 只能 Page.navigate 导航式读（同源 fetch 被风控挡成 HTML）。"""
    out = []
    cdp = wp.CDP(tt.CDP_PORT)
    tid = cdp.new_target(tt.TT_ARTICLES_PAGE)["id"]
    try:
        cdp.connect_target(tid)
        time.sleep(4)
        for pg in range(1, max_page + 1):
            off = (pg - 1) * 10
            cdp.send("Page.navigate", {"url": tt._article_api_url(off, pg)})
            time.sleep(3.5)
            txt = str(cdp.eval("(document.body.innerText||'')") or "")
            if not txt.startswith("{"):
                break
            try:
                d = json.loads(txt)
            except ValueError:
                break
            data = d.get("data") or []
            for it in data:
                cell = (it.get("assembleCell") or {}).get("itemCell") or {}
                base = cell.get("articleBase") or {}
                title = (base.get("title") or "").strip()
                if not title:
                    continue
                out.append({"title": title,
                            "gid": str(base.get("gidStr") or ""),
                            "status": base.get("itemStatus")})
            if not d.get("has_more") or not data:
                break
    finally:
        try:
            cdp.close_target(tid)
        except Exception:                                # noqa: BLE001
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="把对账结果写到这个路径")
    a = ap.parse_args()

    print("[1/3] 拉草稿箱 …")
    drafts = pull_drafts()
    print("      %d 条" % len(drafts))
    print("[2/3] 拉已发布列表 …")
    pubs = pull_published()
    print("      %d 条" % len(pubs))
    print("[3/3] 读本地台账 …")
    led_d, led_p = load_ledger()
    print("      草稿台账 %d 条 / 已发台账 %d 条" % (len(led_d), len(led_p)))

    cases = {c["id"]: c for c in tt.load_cases()}
    d_titles = [x["title"] for x in drafts if x["title"]]
    p_titles = [x["title"] for x in pubs if x["title"]]
    rows = build_rows(led_d, led_p, cases, d_titles, p_titles)

    ghost = [r for r in rows.values() if r["state"] == "ghost"]
    known = {t for r in rows.values() for t in r["all_titles"]}
    orphan_d = [x for x in drafts if x["title"] and x["title"] not in known]
    orphan_p = [x for x in pubs if x["title"] and x["title"] not in known]

    print("\n=== 汇总 ===")
    print("台账 %d 条 ⇒ 已发布 %d / 仍是草稿 %d / 两边都无 %d"
          % (len(rows),
             sum(1 for r in rows.values() if r["state"] == "published"),
             sum(1 for r in rows.values() if r["state"] == "draft"),
             len(ghost)))
    print("线上草稿箱 %d 条，其中台账没登记 %d 条" % (len(drafts), len(orphan_d)))
    print("线上已发布 %d 条，其中台账没登记 %d 条" % (len(pubs), len(orphan_p)))

    if ghost:
        print("\n=== 幽灵（台账有、线上两侧都没有）===")
        for r in sorted(ghost, key=lambda x: x["id"]):
            tag = []
            if r["in_draft_ledger"]:
                tag.append("草稿台账")
            if r["in_pub_ledger"]:
                tag.append("已发台账")
            print("  ✗ %-28s %-20s [%s]" % (r["id"], r["name"][:18],
                                             "+".join(tag)))

    if orphan_d:
        print("\n=== 草稿箱有、台账没登记 ===")
        for x in orphan_d:
            print("  · %s   gid=%s" % (x["title"][:44], x["gid"]))

    if orphan_p:
        print("\n=== 已发布有、台账没登记 ===")
        for x in orphan_p:
            print("  · %s   gid=%s  status=%s"
                  % (x["title"][:44], x["gid"], x["status"]))

    if a.json:
        p = a.json if os.path.isabs(a.json) else os.path.join(ROOT, a.json)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"drafts": drafts, "published": pubs,
                       "rows": list(rows.values()),
                       "ghost": ghost,
                       "orphan_draft": orphan_d,
                       "orphan_published": orphan_p},
                      f, ensure_ascii=False, indent=2)
        print("\n已写 %s" % p)

    bad = bool(ghost or orphan_d or orphan_p)
    print("\n%s" % ("✗ 有 %d 项对不上" % (len(ghost) + len(orphan_d) + len(orphan_p))
                    if bad else "✓ 两侧全对上"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
