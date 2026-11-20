#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站已发侧台账对账：线上已发表 × 本地 `bili_published.json`。

## 治的三类病（都出现在 2026-11-20 那次全平台对账里）

1. **STALE（id 记错）** 本地 `remote_id` 不在线上已发表列表 —— 典型是
   草稿 id（`342371` 六位）被当成已发 id 记下来。处置：按标题在线上唯一
   命中后换成真 id。
2. **ORPHAN（漏登）** 线上有这篇，本地压根没登记。处置：按标题在本地
   case 里唯一命中后补一条 published。
3. **幽灵草稿 id** 已是 published，但 `draft_remote_id` 指向的稿子已经
   不在线上草稿箱（发布后就没了）。不清它，`_bucket_item` 会一直往
   `bili_drafts.json` 里写一行，于是体检永远报「双 ID 并存」。处置：清掉。

## 认领判据（唯一命中才动，绝不按相似度投票）

归一化后（去空白/标点、全角转半角）满足其一：
  · 两边标题**完全相等**
  · 一方是另一方的前缀，且长度差 ≤ `MAX_PREFIX_GAP`
并且候选**只有一个**。否则列进 `unclaimed` 等人看（正文为证，见
`bili_draft_sync.py` 的教训：harperai 的标题里一个产品名都没有）。

用法：
    python scripts/bili_published_reconcile.py            # 只读报告（默认）
    python scripts/bili_published_reconcile.py --apply    # 写盘
    python scripts/bili_published_reconcile.py --json out/rc.json
退出码：0=没有待处理；1=有 unclaimed 或写盘失败。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
from title_match import MAX_GAP as MAX_PREFIX_GAP, title_match  # noqa: E402

PLAT = "bilibili"
TIP_STALE = "｜11-20 对账：id 由草稿 id 纠正为线上真 id %s"
TIP_ORPHAN = "｜11-20 对账：线上已发表但本地漏登，按标题唯一命中补登"
TIP_GHOST = "｜11-20 对账：草稿 id %s 已不在线上草稿箱（发布后消失），不再记"
TIP_PROMOTE = "｜11-20 对账：台账原记 draft（草稿 id %s），线上已发表 %s ⇒ 改 published"


def pull():
    import bilibili_publish as bp
    import bili_draft_api as ba
    import wechat_publish as wp
    cdp = wp.CDP(bp.PUB_PORT)
    ba._bili_tab(cdp)
    drafts = ba.list_drafts(cdp) or []
    arts = ba.list_published_articles(cdp) or []
    return ({str(d.get("article_id")): (d.get("title") or "")
             for d in drafts if d.get("article_id")},
            {str(a.get("id")): (a.get("title") or "")
             for a in arts if a.get("id")})


def plan(recs, remote_drafts, remote_pubs):
    """算出三类改动。纯函数，不写盘。

    ⚠ 顺序敏感：STALE 必须先算，它决定了某条 case 的「真已发 id」是什么；
      ORPHAN 再补时，已经有 id 的 case **不许再补一条** —— 一个 case 只能
      有一条已发记录（trustmrr 线上躺着 3 篇同标题稿，第二三篇是重复稿，
      补进来会把第一条覆盖掉，正是台账被洗的经典姿势）。
    """
    stale, orphan, promote, ghost, unclaimed = [], [], [], [], []
    local_rid = {str(r.get("remote_id")) for r in recs.values() if r.get("remote_id")}

    # ---- 1. STALE：本地登记了已发，线上没这个 id
    for cid, r in recs.items():
        rid = str(r.get("remote_id") or "")
        if r["state"] != L.PUBLISHED or not rid or rid in remote_pubs:
            continue
        cand = [i for i, t in remote_pubs.items()
                if title_match(t, r.get("title")) and i not in local_rid]
        if len(cand) == 1:
            stale.append((cid, rid, cand[0], remote_pubs[cand[0]]))
            local_rid.add(cand[0])
        else:
            unclaimed.append(("STALE", cid, r.get("title"),
                              "线上候选 %d 个：%s" % (len(cand), cand)))

    # ---- 2. ORPHAN：线上有，本地没有任何一条指着它
    for aid, atitle in remote_pubs.items():
        if aid in local_rid:
            continue
        cand = [cid for cid, r in recs.items()
                if title_match(atitle, r.get("title"))]
        if len(cand) != 1:
            unclaimed.append(("ORPHAN", "", atitle,
                              "本地候选 %d 个：%s" % (len(cand), cand)))
            continue
        cid = cand[0]
        cur = str((recs.get(cid) or {}).get("remote_id") or "")
        cur_state = (recs.get(cid) or {}).get("state")
        if cur and cur_state == L.PUBLISHED:
            # 已发布且已有**在线**的真 id ⇒ 线上这条同标题的是**重复稿**，
            # 不补登、不覆盖，交人裁定（trustmrr 线上躺着 3 篇同标题稿）
            unclaimed.append(("DUP_PUB", cid, atitle,
                              "该 case 已有已发 id=%s，线上这条 %s 疑似重复稿"
                              % (cur, aid)))
            continue
        if cur and cur_state == L.DRAFT:
            # 台账记成草稿、主 id 就是草稿 id，而线上**已经发布了**这篇
            # ⇒ 「草稿 id 误记 + 状态错」两件病一起犯。这条最危险：FIFO 只
            #   认 published，会把它当成没发过、再发一遍（11-20 已出过事故）。
            promote.append((cid, cur, aid, atitle))
            local_rid.add(aid)
            continue
        orphan.append((cid, aid, atitle))
        local_rid.add(aid)

    # ---- 3. 幽灵草稿 id：已发布 + 草稿已不在线上草稿箱
    for cid, r in recs.items():
        did = str(r.get("draft_remote_id") or "")
        if r["state"] == L.PUBLISHED and did and did not in remote_drafts:
            ghost.append((cid, did))
    return stale, orphan, promote, ghost, unclaimed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    recs = L.load(PLAT)
    remote_drafts, remote_pubs = pull()
    print("线上：草稿 %d 条 / 已发表 %d 条；本地台账 %d 条"
          % (len(remote_drafts), len(remote_pubs), len(recs)))

    stale, orphan, promote, ghost, unclaimed = plan(recs, remote_drafts, remote_pubs)

    print("\n[1] STALE  本地 id 在线上找不到 → 换真 id  (%d)" % len(stale))
    for cid, old, new, t in stale:
        print("    %-30s %-10s → %-10s  %s" % (cid[:30], old, new, t[:34]))
    print("\n[2] ORPHAN 线上有本地没登 → 补一条 published  (%d)" % len(orphan))
    for cid, aid, t in orphan:
        print("    %-30s ← %-10s  %s" % (cid[:30], aid, t[:34]))
    print("\n[2b] PROMOTE 台账记成草稿、线上已发布 → 改 published  (%d)" % len(promote))
    for cid, old, aid, t in promote:
        print("    %-30s %-10s(draft) → %-10s  %s" % (cid[:30], old, aid, t[:30]))
    print("\n[3] GHOST  草稿 id 已不在线上草稿箱 → 清掉  (%d)" % len(ghost))
    for cid, did in ghost:
        print("    %-30s draft %s" % (cid[:30], did))
    print("\n[!] unclaimed 不敢认（要读正文）  (%d)" % len(unclaimed))
    for kind, cid, t, why in unclaimed:
        print("    [%s] %-28s %s | %s" % (kind, str(cid)[:28], str(t)[:30], why))

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"stale": stale, "orphan": orphan, "promote": promote,
                       "ghost": ghost, "unclaimed": unclaimed},
                      f, ensure_ascii=False, indent=2)
        print("\nJSON → %s" % a.json)

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 1 if unclaimed else 0

    # ---- 写盘：一次 load 一次 save，避免中间态
    changed = 0
    for cid, old, new, _t in stale:
        r = recs[cid]
        r["remote_id"] = new
        r["note"] = (r.get("note") or "") + TIP_STALE % new
        changed += 1
    for cid, aid, atitle in orphan:
        r = recs.get(cid) or {"id": cid, "at": ""}
        r["state"] = L.PUBLISHED
        r["remote_id"] = aid
        r["title"] = r.get("title") or atitle
        r["evidence"] = "published_reconcile"
        r["note"] = (r.get("note") or "") + TIP_ORPHAN
        recs[cid] = r
        changed += 1
    for cid, old, aid, atitle in promote:
        r = recs[cid]
        r["state"] = L.PUBLISHED
        r["remote_id"] = aid
        r["note"] = (r.get("note") or "") + TIP_PROMOTE % (old, aid)
        # 草稿 id 已被真 id 取代；线上草稿箱里也没有它了（发布即消失）
        if str(r.get("draft_remote_id") or "") == old:
            r.pop("draft_remote_id", None)
        recs[cid] = r
        changed += 1
    for cid, did in ghost:
        r = recs[cid]
        r["note"] = (r.get("note") or "") + TIP_GHOST % did
        r.pop("draft_remote_id", None)
        changed += 1
    L.save(PLAT, recs)

    # ---- 回读核验（不信自报）
    after = L.load(PLAT)
    bad = []
    for cid, _o, new, _t in stale:
        if str((after.get(cid) or {}).get("remote_id")) != new:
            bad.append(cid)
    for cid, aid, _t in orphan:
        if str((after.get(cid) or {}).get("remote_id")) != aid:
            bad.append(cid)
    for cid, _old, aid, _t in promote:
        r = after.get(cid) or {}
        if str(r.get("remote_id")) != aid or r.get("state") != L.PUBLISHED:
            bad.append(cid)
    for cid, _d in ghost:
        if (after.get(cid) or {}).get("draft_remote_id"):
            bad.append(cid)
    print("\n写盘 %d 处；回读核验 %s" % (changed, "全部对上" if not bad else "失败:" + str(bad)))
    return 0 if (not bad and not unclaimed) else 1


if __name__ == "__main__":
    sys.exit(main())
