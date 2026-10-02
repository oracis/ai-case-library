#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站草稿封面 URL 基线（换图前抓一次）+ 换图后比对。

⚠ 必须在 `cover --force` **之前**跑基线，否则没有可比对的旧值。
   判据只有一条：远端 `image_urls[0]` 的**完整 URL 字符串**
   （铁律 17：cover-status 只给「有/无」布尔，证明不了换图）。

用法：
  python -X utf8 scripts/bili_cover_baseline.py --grab     # 抓基线
  python -X utf8 scripts/bili_cover_baseline.py --check    # 换完比对
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import chrome_cdp_launch as _L  # noqa: E402
import wechat_publish as wp  # noqa: E402
import bilibili_publish as bp  # noqa: E402

BASE = os.path.join(ROOT, "out", "bili_cover_base.json")


def all_titles():
    """全部案例的 B站标题（含远端草稿里可能存在的）。"""
    out = {}
    for c in bp.load_cases() if hasattr(bp, "load_cases") else []:
        out[c["id"]] = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grab", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--case")
    args = ap.parse_args()

    wp._autostart_chrome_if_needed(bp.PUB_PORT, profile=_L.PLAT_PROFILE)
    cdp = wp.CDP(bp.PUB_PORT)
    tid = cdp.new_target(bp.BILI_DRAFT_LIST_PAGE)["id"]
    if not cdp.connect_target(tid):
        print("✗ connect_target 失败")
        return 1
    time.sleep(6)

    base = {}
    if os.path.exists(BASE):
        base = json.load(open(BASE, encoding="utf-8"))

    # 收集案例：优先用 cases.json 里能生成 B站标题的
    import json as _j
    cases = _j.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]

    targets = []
    for c in cases:
        html = bp.load_article_html(c["id"])
        if not html:
            continue
        targets.append((c["id"], bp.make_title(c["id"], html)))

    res = {}
    for i, (cid, title) in enumerate(targets, 1):
        try:
            aid, drafts = bp._pick_draft(cdp, tid, title)
        except Exception as e:                # noqa: BLE001
            print("[%d/%d] %-22s ERR %s" % (i, len(targets), cid, e), flush=True)
            continue
        if not aid:
            print("[%d/%d] %-22s 草稿箱无此稿" % (i, len(targets), cid), flush=True)
            continue
        url = bp._banner_of(cdp, tid, aid) or ""
        res[cid] = {"aid": aid, "url": url, "title": title}
        print("[%d/%d] %-22s aid=%s %s"
              % (i, len(targets), cid, aid, (url[-30:] if url else "(无封面)")), flush=True)
        time.sleep(0.8)

    try:
        cdp.close_target(tid)
    except Exception:                        # noqa: BLE001
        pass

    if args.grab:
        json.dump(res, open(BASE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        got = sum(1 for v in res.values() if v["url"])
        print("\n基线已存 %s：%d 条有封面 / 共 %d 条"
              % (BASE, got, len(res)))
        return 0

    # check
    changed, same, miss = [], [], []
    for cid, v in res.items():
        old = (base.get(cid) or {}).get("url", "")
        new = v["url"]
        if not old:
            miss.append(cid)
        elif old != new:
            changed.append(cid)
        else:
            same.append(cid)
    print("\n==== B站封面：换成功 %d / 未变 %d / 无基线 %d ===="
          % (len(changed), len(same), len(miss)))
    if changed:
        print("已换：", "、".join(changed))
    if same:
        print("未变：", "、".join(same))
    if miss:
        print("无基线：", "、".join(miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())