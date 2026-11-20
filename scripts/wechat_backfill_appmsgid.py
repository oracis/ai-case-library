#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号已发台账补 `appmsgid`：台账记着 published 却没有远端 id 时用。

## 为什么这件单独做（`wechat_sync.apply_fixes` 不管）

`wechat_sync.apply_fixes` 的注释写得很清楚：「只改 status，不动 title/appmsgid」。
它能判「这条其实发过了」，但补 id 得自己按已发表列表认亲 —— 而已发表侧
**不能靠 id 认**：群发之后 appmsgid 会变成新的（本地记的常是草稿 id
100000xxx，线上是 2247xxxxxx），只能靠标题。

## 判据

`title_match`（归一化后相等，或一方是另一方前缀且差 ≤ 12 字）**且唯一命中**，
并且该 `appmsgid` 当前没被其它 case 占用。撞多个一律进 `unclaimed` 等人看 ——
公众号标题在后台被改过是常事（visualizee-ai 就被改过），宁可不认也不认错。

用法：
    python scripts/wechat_backfill_appmsgid.py           # 只读报告
    python scripts/wechat_backfill_appmsgid.py --apply   # 写盘
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"

import ledger as L  # noqa: E402
import wechat_publish as wp  # noqa: E402
import wechat_sync as ws  # noqa: E402
from title_match import title_match  # noqa: E402

PLAT = "wechat"
TIP = "｜11-20 对账：按已发表列表标题唯一命中补 appmsgid=%s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    recs = L.load(PLAT)
    noid = {c: r for c, r in recs.items()
            if r["state"] == L.PUBLISHED and not r.get("remote_id")}
    print("台账：%d 条，其中 published 缺 appmsgid %d 条" % (len(recs), len(noid)))
    if not noid:
        return 0

    cdp = wp.CDP(wp.CDP_PORT)
    _tid0, tok = wp._connect_mp(cdp)      # 打开后台页 + 取 token 一步到位
    if not tok:
        print("⛔ 拿不到 token：调试窗口里 mp.weixin.qq.com 没登录")
        return 2
    remote, ok = ws.fetch_published_remote(cdp, tok)
    if not ok:
        print("⛔ 已发表列表读不到（登录态/接口），不猜，退出")
        return 2
    print("线上已发表：%d 条" % len(remote))

    taken = {str(r.get("remote_id")): c for c, r in recs.items() if r.get("remote_id")}
    hit, unclaimed = [], []
    for cid, r in noid.items():
        t = r.get("title") or ""
        cand = [x for x in remote
                if title_match(x.get("title"), t)
                and str(x.get("appmsg_id")) not in taken]
        if len(cand) == 1:
            hit.append((cid, str(cand[0]["appmsg_id"]), cand[0].get("title") or ""))
            taken[str(cand[0]["appmsg_id"])] = cid
        else:
            unclaimed.append((cid, t, len(cand),
                              [str(x.get("appmsg_id")) for x in cand[:3]]))

    print("\n[可补] %d 条" % len(hit))
    for cid, aid, t in hit:
        print("  %-28s ← %-12s %s" % (cid[:28], aid, t[:34]))
    print("\n[不敢认] %d 条" % len(unclaimed))
    for cid, t, n, sample in unclaimed:
        print("  %-28s 线上候选 %d 个 %s | %s" % (cid[:28], n, sample, (t or "(空标题)")[:30]))

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 0

    for cid, aid, _t in hit:
        r = recs[cid]
        r["remote_id"] = aid
        r["note"] = (r.get("note") or "") + TIP % aid
    L.save(PLAT, recs)

    after = L.load(PLAT)
    bad = [cid for cid, aid, _t in hit
           if str((after.get(cid) or {}).get("remote_id")) != aid]
    print("\n写盘 %d 处；回读核验 %s" % (len(hit), "全部对上" if not bad else "失败:" + str(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
