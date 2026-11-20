#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条已发侧台账对账：线上已发表 × 本地 `toutiao_published.json`。

和 `bili_published_reconcile.py` 同一套判据（`title_match.py` 唯一命中），
治的是同一批病，只是头条多一种形态：

  · **NO_ID**  台账记了 published，`gid` 却是空的（10-25 那批只写了标题）
  · **STALE**  `gid` 不在线上已发表列表（重发/换 id 后就对不上了）
  · **ORPHAN** 线上有这篇，本地压根没登记
  · **PROMOTE** 台账记成 draft，线上其实已经发了（最危险：会重发）

⚠ 一个 case 的已发 id 只认唯一的线上命中。已经有一条在线真 id 的 case，
  线上再冒出同标题文章 ⇒ 那是重复稿，进 `unclaimed` 交人裁定，绝不覆盖。

用法：
    python scripts/toutiao_published_reconcile.py            # 只读报告
    python scripts/toutiao_published_reconcile.py --apply    # 写盘
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import toutiao_reconcile as tr  # noqa: E402
from title_match import title_match  # noqa: E402

PLAT = "toutiao"
TIP_STALE = "｜11-20 对账：gid 纠正为线上真 id %s"
TIP_NOID = "｜11-20 对账：按标题唯一命中补 gid=%s"
TIP_ORPHAN = "｜11-20 对账：线上已发表但本地漏登，按标题唯一命中补登"
TIP_PROMOTE = "｜11-20 对账：台账原记 draft，线上已发表 %s ⇒ 改 published"


PACK_DIR = os.path.join(ROOT, "out", "toutiao")


def _title_of(cid, r):
    """台账 title → 内容包 meta.json 的 title。

    头条台账早期只记了 `id/at/gid`，**title 字段是空的**（10-25 那批），
    只看台账会得出「线上候选 0 个」的假结论 —— 不是线上没有，是压根没标题
    可比。标题的真值在 `out/toutiao/<case>/meta.json`。
    """
    t = (r or {}).get("title") or ""
    if t:
        return t
    p = os.path.join(PACK_DIR, cid, "meta.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return (json.load(f) or {}).get("title") or ""
        except (ValueError, OSError):
            return ""
    return ""


def pull():
    drafts = tr.pull_drafts() or []
    pubs = tr.pull_published() or []
    return ({d["gid"]: d["title"] for d in drafts if d.get("gid")},
            {p["gid"]: p["title"] for p in pubs if p.get("gid")})


def plan(recs, remote_pubs):
    noid, stale, orphan, promote, unclaimed = [], [], [], [], []
    local_gid = {str(r.get("remote_id")) for r in recs.values() if r.get("remote_id")}

    # 1. NO_ID / STALE：本地记了已发布，但 gid 空或不在线上
    for cid, r in recs.items():
        if r["state"] != L.PUBLISHED:
            continue
        rid = str(r.get("remote_id") or "")
        if rid and rid in remote_pubs:
            continue
        lt = _title_of(cid, r)
        cand = [g for g, t in remote_pubs.items()
                if title_match(t, lt) and g not in local_gid]
        if len(cand) != 1:
            tag = "NO_ID" if not rid else "STALE"
            unclaimed.append((tag, cid, lt or "(无标题)",
                              "线上候选 %d 个：%s" % (len(cand), cand)))
            continue
        (noid if not rid else stale).append((cid, rid, cand[0], remote_pubs[cand[0]]))
        local_gid.add(cand[0])

    # 2. ORPHAN / PROMOTE
    for gid, gtitle in remote_pubs.items():
        if gid in local_gid:
            continue
        cand = [cid for cid, r in recs.items()
                if title_match(gtitle, _title_of(cid, r))]
        if len(cand) != 1:
            unclaimed.append(("ORPHAN", "", gtitle,
                              "本地候选 %d 个：%s" % (len(cand), cand)))
            continue
        cid = cand[0]
        r = recs[cid]
        cur = str(r.get("remote_id") or "")
        if cur and r["state"] == L.PUBLISHED:
            unclaimed.append(("DUP_PUB", cid, gtitle,
                              "该 case 已有已发 gid=%s，线上 %s 疑似重复稿"
                              % (cur, gid)))
            continue
        if r["state"] == L.DRAFT:
            promote.append((cid, gid, gtitle))
        else:
            orphan.append((cid, gid, gtitle))
        local_gid.add(gid)
    return noid, stale, orphan, promote, unclaimed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    _drafts, remote_pubs = pull()
    print("线上：已发表 %d 条；本地台账 %d 条" % (len(remote_pubs), len(recs)))

    noid, stale, orphan, promote, unclaimed = plan(recs, remote_pubs)

    for label, rows, fmt in (
            ("[1] NO_ID  已发布但 gid 空 → 按标题补", noid,
             lambda c, o, n, t: "    %-30s %-22s ← %-22s %s" % (c[:30], "(空)", n, t[:26])),
            ("[2] STALE  gid 不在线上 → 换真 id", stale,
             lambda c, o, n, t: "    %-30s %-22s → %-22s %s" % (c[:30], o, n, t[:26])),
            ("[3] ORPHAN 线上有本地没登 → 补 published", orphan,
             lambda c, g, t: "    %-30s ← %-22s %s" % (c[:30], g, t[:26])),
            ("[4] PROMOTE 台账记 draft、线上已发 → 改 published", promote,
             lambda c, g, t: "    %-30s ← %-22s %s" % (c[:30], g, t[:26]))):
        print("\n%s  (%d)" % (label, len(rows)))
        for row in rows:
            print(fmt(*row))

    print("\n[!] unclaimed 不敢认  (%d)" % len(unclaimed))
    for kind, cid, t, why in unclaimed:
        print("    [%-7s] %-26s %s | %s" % (kind, str(cid)[:26], str(t)[:26], why))

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 1 if unclaimed else 0

    changed = 0
    for cid, old, new, _t in noid + stale:
        r = recs[cid]
        r["remote_id"] = new
        r["note"] = (r.get("note") or "") + (TIP_NOID % new if not old else TIP_STALE % new)
        changed += 1
    for cid, gid, gtitle in orphan:
        r = recs.get(cid) or {"id": cid, "at": ""}
        r.update({"state": L.PUBLISHED, "remote_id": gid,
                  "evidence": "published_reconcile",
                  "title": r.get("title") or gtitle,
                  "note": (r.get("note") or "") + TIP_ORPHAN})
        recs[cid] = r
        changed += 1
    for cid, gid, _t in promote:
        r = recs[cid]
        r.update({"state": L.PUBLISHED, "remote_id": gid,
                  "note": (r.get("note") or "") + TIP_PROMOTE % gid})
        changed += 1
    L.save(PLAT, recs)

    after = L.load(PLAT)
    bad = [cid for cid, _o, new, _t in noid + stale
           if str((after.get(cid) or {}).get("remote_id")) != new]
    bad += [cid for cid, g, _t in orphan + promote
            if str((after.get(cid) or {}).get("remote_id")) != g]
    print("\n写盘 %d 处；回读核验 %s" % (changed, "全部对上" if not bad else "失败:" + str(bad)))
    return 0 if (not bad and not unclaimed) else 1


if __name__ == "__main__":
    sys.exit(main())
