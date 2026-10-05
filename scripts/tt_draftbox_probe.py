#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条草稿箱真值探测：绕开 `fetch_draft_titles` 的关键字过滤，直接读原始文本。

⚠⚠ 为什么必须另写一个探针（2026-10-05 实测）：

`fetch_draft_titles()` 有一行**静默过滤**：

    if len(title) < 3 or not any(k in title for k in ("拆解", "：", "$")):
        continue

也就是说**标题里不含「拆解 / 全角冒号 / 美元符」的草稿会被当噪声丢掉**。
历史 39 条标题都是「月收$X：…」这种格式，恰好都命中；
但2026-10-05 新入库的两条标题是
  「法国录音棚收 250 欧一单，有人把混音做成了订阅」
  「把英国公开抵押记录连成一张网，然后卖给贷款经纪」
——**一个关键字都不含** ⇒ 回读工具永远看不见它们。

⇒ 「工具读不到」≠「远端没有」。这跟记忆里 B站 `draft/list` 标题不可信、
公众号 `NO_CARD` 是分页导致，是同一类陷阱：**校验工具的判据本身有洞**。

所以这里读`document.body.innerText` 原文，用**案例标题里的稳定片段**去搜。

用法：
    python -X utf8 scripts/tt_draftbox_probe.py
    python -X utf8 scripts/tt_draftbox_probe.py --case easymix,harperai
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 稳定片段：从标题里取，避开标点与数字。
PROBE = {
    "easymix": ["法国录音棚", "混音做成了订阅"],
    "harperai": ["把英国公开抵押", "卖给贷款经纪"],
}


def load_cases():
    p = os.path.join(ROOT, "data", "cases.json")
    with io.open(p, encoding="utf-8", newline="") as f:
        return json.loads(f.read())


def titles_of():
    import toutiao_publish as tp
    import wechat_publish as wp
    m = {}
    for c in load_cases():
        t = None
        try:
            t = wp.make_wechat_title(c)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            t2 = tp.make_toutiao_title(c)
            if len(t2 or "") >= 6:
                t = t2
        except Exception:                                     # noqa: BLE001
            pass
        if t:
            m[c["id"]] = t
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="", help="逗号分隔 id")
    ap.add_argument("--port", type=int, default=9223)
    args = ap.parse_args()

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)

    import toutiao_publish as tp
    import wechat_publish as wp

    # ⚠ 必须自启动：Chrome 活不过一次父进程调用（同进程起 + 驱动才稳）。
    #   ⚠ profile 常量住在 `chrome_cdp_launch` 里（`PLAT_PROFILE`），
    #   `toutiao_publish` **不导出** 它（它只在函数内部 import 成 `_L`），
    #   写 `tp._L.PLAT_PROFILE` 会 AttributeError（2026-10-05 实测）。
    try:
        import chrome_cdp_launch as _L
        wp._autostart_chrome_if_needed(args.port, profile=_L.PLAT_PROFILE)
    except BaseException as e:                # CDP.__init__ 抛 SystemExit
        print("[X] Chrome 起不来：%s" % str(e)[:200])
        return 2

    want = [x.strip() for x in (args.case or "").split(",") if x.strip()]
    tt_titles = titles_of()

    cdp, t = tp._open(tp.TT_DRAFT, wait=10)
    try:
        tp._check_login(cdp)
        time.sleep(3)
        txt = cdp.eval("(function(){return document.body.innerText||'';})()") or ""
        segs = txt.split("编辑删除")
        rows = []
        for s in segs:
            lines = [l.strip() for l in s.split("\n") if l.strip()]
            if lines:
                rows.append((lines[0], " ".join(lines[1:3])))
        print("草稿箱原始段数：%d" % len(segs))
        print("过滤后 `fetch_draft_titles` 只认：%d 条（这就是它看不见新稿的原因）"
              % len([r for r in rows
                     if len(r[0]) >= 3
                     and any(k in r[0] for k in ("拆解", "：", "$"))]))
        print("-" * 66)
        ids = want or list(PROBE)
        found = {}
        for cid in ids:
            probes = PROBE.get(cid) or [tt_titles.get(cid, cid)[:8]]
            hit = [p for p in probes if p and p in txt]
            found[cid] = bool(hit)
            print("%-12s %s   片段=%s" % (cid, "FOUND" if hit else "MISS ",
                                          probes))
            if hit:
                for title, info in rows:
                    if any(p in title for p in probes):
                        print("     标题：%s" % title[:60])
                        print("     时间：%s" % info[:40])
                        break
        print("-" * 66)
        print("结论：远端命中 %d / %d"
              % (sum(found.values()), len(ids)))
        missing = [c for c, v in found.items() if not v]
        if missing:
            print("未命中：%s（可能真没存，也可能标题与本地不一致）" % missing)
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:                                     # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())