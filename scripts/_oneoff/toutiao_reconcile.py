#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条三方对账：草稿箱（draft_list API）× 已发布列表（feed API）× 本地台账。

做这件事的动机（2026-10-19）：1030 之前 `publish --api` 有一批是在 headless
下跑的，服务端不认 tt-anti-token（7050），当时代码只判 `code:0` 就记了「成功」。
`code:0` 在头条只代表「请求已受理」，不代表落库 ⇒ 台账里可能存在幽灵记录。

判据（三态）：
  · 在草稿箱            → 还是草稿，正常
  · 在已发布列表        → 真发出去了，正常
  · 两边都没有          → **幽灵**：台账说有，线上没有 ⇒ 需要确认是否重发
另外反向找「线上有、台账没有」的漏登。

用法：
  python _oneoff/toutiao_reconcile.py            # 只读，打印报告
  python _oneoff/toutiao_reconcile.py --json out/reconcile.json
"""
import argparse
import json
import re
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)

import toutiao_publish as tt  # noqa: E402
import wechat_publish as wp   # noqa: E402


def pull_drafts():
    """草稿箱全量（draft_list），返回 [{title, gid, update_time}]。"""
    cdp, t = tt._open(tt.TT_DRAFT, wait=8)
    try:
        tt._check_login(cdp)
        raw = tt.fetch_draft_api_raw(cdp)
        if raw is None:
            raise SystemExit("draft_list 不通（登录态/接口变了？）")
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
        except Exception:                                    # noqa: BLE001
            pass


def pull_published(max_page=6):
    """已发布图文列表全量，返回 [{title, gid, status}]。"""
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
        except Exception:                                    # noqa: BLE001
            pass
    return out


def load_ledger():
    def _r(p):
        if not os.path.isfile(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            return [e if isinstance(e, dict) else {"id": e}
                    for e in json.load(f) if e]
    return (_r(os.path.join(ROOT, "data", "toutiao_drafts.json")),
            _r(os.path.join(ROOT, "data", "toutiao_published.json")))


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

    # 台账 note 里的『远端标题』= 历史别名。早期文章用「月收$X：一句话」口径，
    # 跟现在的「产品名：一句话」完全不同，纯 name 匹配必然失败（2026-10-17
    # 的 P1 对账已经人工映射过一遍，结论就写在 note 里，这里直接复用）。
    alias = {}
    for e in led_d + led_p:
        for m in re.findall(r"『([^』]+)』", e.get("note") or ""):
            alias.setdefault(e["id"], []).append(m)

    def match(cid, titles):
        """返回**全部**命中的远端标题（同一 case 可能发过两篇：新旧口径各一篇，
        如 bustem / cometly）。只取第一条会让第二篇变成假孤儿。"""
        hit = []
        for a in alias.get(cid, []):
            if a in titles and a not in hit:
                hit.append(a)
        c = cases.get(cid)
        if c:
            one = tt.draft_title_match(cid, c, titles)
            if one and one not in hit:
                hit.append(one)
        return hit

    rows = {}
    for cid in {e["id"] for e in led_d} | {e["id"] for e in led_p}:
        dt = match(cid, d_titles)
        pt = match(cid, p_titles)
        rows[cid] = {
            "id": cid,
            "name": (cases.get(cid) or {}).get("name", ""),
            "in_draft_ledger": any(e["id"] == cid for e in led_d),
            "in_pub_ledger": any(e["id"] == cid for e in led_p),
            "draft_title": dt[0] if dt else None,
            "pub_title": pt[0] if pt else None,
            "all_titles": dt + pt,
        }
    for cid, r in rows.items():
        r["state"] = ("published" if r["pub_title"]
                      else "draft" if r["draft_title"]
                      else "ghost")

    ghost = [r for r in rows.values() if r["state"] == "ghost"]
    # 线上有、台账没登记
    known_titles = {t for r in rows.values() for t in r.get("all_titles", [])}
    orphan_d = [x for x in drafts if x["title"] and x["title"] not in known_titles]
    orphan_p = [x for x in pubs if x["title"] and x["title"] not in known_titles]

    print("\n=== 汇总 ===")
    print("台账 %d 条 ⇒ 已发布 %d / 仍是草稿 %d / 两边都无 %d"
          % (len(rows),
             sum(1 for r in rows.values() if r["state"] == "published"),
             sum(1 for r in rows.values() if r["state"] == "draft"),
             len(ghost)))
    print("线上草稿箱 %d 条，其中台账没登记 %d 条" % (len(drafts), len(orphan_d)))
    print("线上已发布 %d 条，其中台账没登记 %d 条" % (len(pubs), len(orphan_p)))

    if ghost:
        print("\n=== 幽灵（台账有、线上两边都没有）===")
        for r in sorted(ghost, key=lambda x: x["id"]):
            tag = []
            if r["in_draft_ledger"]:
                tag.append("草稿台账")
            if r["in_pub_ledger"]:
                tag.append("已发台账")
            print("  ✗ %-28s %-22s [%s]" % (r["id"], r["name"][:20], "+".join(tag)))

    if orphan_d:
        print("\n=== 草稿箱有、台账没登记 ===")
        for x in orphan_d:
            print("  · %s   gid=%s" % (x["title"][:44], x["gid"]))

    if orphan_p:
        print("\n=== 已发布有、台账没登记 ===")
        for x in orphan_p:
            print("  · %s   gid=%s  status=%s" % (x["title"][:44], x["gid"], x["status"]))

    if a.json:
        p = a.json if os.path.isabs(a.json) else os.path.join(ROOT, a.json)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"drafts": drafts, "published": pubs,
                       "rows": list(rows.values()),
                       "ghost": ghost,
                       "orphan_draft": orphan_d,
                       "orphan_published": orphan_p},
                      f, ensure_ascii=False, indent=2)
        print("\n已写 %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
