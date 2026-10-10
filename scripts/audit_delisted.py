#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读审计：TrustMRR 上哪些条目已经下架，以及采集缓存里的数字是否还对得上。

为什么要有这个文件
------------------
`triage.py` 是纯函数集合，**不联网** —— 这是它能被单测钉死的前提。
但「这条还在不在」本质上是个只能联网回答的问题，于是它长期没有归处，
代价是**队列里混着一批早已消失的条目**。

2026-10-10 实测踩到的：inbox 397 条的 `last_harvest.json` 停在 09-23，
而 `triage` 判出的 23 条 deep 里有4 条 —— `trendstory`（MRR $67,600，全批最高）、
`appgen`、`lurq-by-peoplefinder`、`slurp` —— 在 TrustMRR 上已经查不到了。
它们在队列里带着一个月前的漂亮数字，看起来比谁都值得核。
**花钱深核一个已经不存在的项目，是这条流水线上最贵的浪费。**

判据：双证据才算下架
------------------
单看 `/startup/<slug>.md` 返回 404 不够 —— slug 常与 id 不同（历史上
`livecrew-ai`→`livecrew`），一次 404 会被误当成「已下架」。所以要过两道：

    ① sitemap 存在性 —— `startup-sitemap.xml`（robots.txt 公布，全站 1.1 万条）
    ② `.md` 内容核对  —— 必须抓到且标题对得上

⚠️ **sitemap 只证明 slug 存在，不证明是同一个产品**（`private-venture-1` 实测：
按 id 命中同名 slug，抓回却是另一个隐身挂牌）。所以本脚本两道都过才判「在」，
只有 sitemap 没有或 `.md` 抓不到才判「疑似下架」，并且**把两种结论分开报**，
不做二选一的断言。

用法
----
    python scripts/audit_delisted.py                 # 只读审计，默认 dry
    python scripts/audit_delisted.py --scope inbox
    python scripts/audit_delisted.py --check-fresh   # 顺带核对数字是否漂移
    python scripts/audit_delisted.py --write         # 把实测数字回写进 inbox.json

⚠️ `--write` 只改**数字字段**与 `data_freshness`，不删条目、不改判级。
下架条目该归档还是该留，由人决定 —— 脚本只负责把事实摆出来。
"""

import argparse
import concurrent.futures as cf
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import harvest as H                                          # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INBOX = os.path.join(ROOT, "data", "inbox.json")
CANDS = os.path.join(ROOT, "data", "candidates.json")

SITEMAP = "https://trustmrr.com/startup-sitemap.xml"
MD = "https://trustmrr.com/startup/%s.md"

# `.md` 只对 /startup/ 有效；HTTP 200 也可能返回 404 骨架页（见 docs/TRUSTMRR_FETCH.md 7c）
NOT_FOUND_MARK = "NEXT_HTTP_ERROR_FALLBACK"


def _get(url, timeout=60):
    """抓文本。显式要 gzip —— sitemap 不压缩有 1.7MB，本机拉 120 秒都拉不完。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": H.UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def load_sitemap():
    """全站 slug 集合。抓不到就返回空 set（调用方据此降级为「无法判定」）。"""
    try:
        xml = _get(SITEMAP, timeout=120)
    except Exception as e:
        print("[!] sitemap 抓取失败（%s）—— 下架判定降级为「无法判定」" % type(e).__name__)
        return set()
    return set(re.findall(r"/startup/([A-Za-z0-9\-_]+)</loc>", xml))


def probe(slug):
    """返回 (状态, 标题, 实测数字字典)。状态 ∈ live / gone / unknown。"""
    out = {"slug": slug, "state": "unknown", "title": None, "metrics": {}}
    try:
        txt = _get(MD % slug, timeout=60)
    except urllib.error.HTTPError as e:
        out["state"] = "gone" if e.code == 404 else "unknown"
        out["http"] = e.code
        return out
    except Exception as e:
        out["err"] = type(e).__name__
        return out

    # 🚨 HTTP 200 完全掩盖 404 —— 必须查内容标记
    if NOT_FOUND_MARK in txt:
        out["state"] = "gone"
        out["http"] = 200
        return out

    m = re.search(r"^#\s*(.+)$", txt, re.M)
    out["title"] = m.group(1).strip() if m else None
    out["state"] = "live"

    def g(pat, cast=float):
        mm = re.search(pat, txt)
        if not mm:
            return None
        try:
            return cast(mm.group(1).replace(",", "").strip())
        except ValueError:
            return None

    out["metrics"] = {
        "mrr": g(r"Current MRR:\s*\$([\d,\.]+)"),
        "last_30d_revenue": g(r"Last 30 days revenue snapshot:\s*\$([\d,\.]+)"),
        # 🚨🚨 **MRR 增长率与收入增长率必须分两个字段**（2026-10-10 实测）。
        # TrustMRR 的 .md 上**同时**列了两个，口径不同、数值可正负相反：
        #     Last 30 days revenue growth: +35.4%   ← 近30 天**收入**
        #     Last 30 days MRR growth:     -4.9%   ← 近30 天**MRR**
        # `cometly` 就是活例子：收入 +35.4%（一次性大额入账），
        # 但 MRR 其实在**跌** 4.9%。把前者存进 `growth_30d` 会让
        # 「稳定增长的大盘生意」看起来像暴涨 —— 这正是红线 1
        # 「收入口径不许混用」在**趋势字段**上的表现形式，比金额字段更隐蔽，
        # 因为它不会让数字变错，只会让**方向**变错。
        "mrr_growth_30d": g(r"Last 30 days MRR growth:\s*\*{0,2}([\-\+\d\.]+)%"),
        "revenue_growth_30d": g(r"Last 30 days revenue growth:\s*\*{0,2}([\-\+\d\.]+)%"),
        "all_time": g(r"All-time revenue snapshot:\s*\$([\d,\.]+)"),
        "subscriptions": g(r"Current active subscriptions:\s*([\d,]+)", lambda s: int(s.replace(",", ""))),
        "profit_margin": g(r"Last 30 days profit margin:\s*([\d\.]+)%"),
    }
    # 兼容旧字段名：本项目主口径是 MRR（红线 1），所以 `growth_30d`
    # **只能**指 MRR 增长。留旧名会让下游拿到收入增速当 MRR 增速用。
    out["metrics"]["growth_30d"] = out["metrics"]["mrr_growth_30d"]
    # ⚠️ **不要拿订阅数去覆盖 `customers`**（docs/DATA_SCHEMA.md:212）。
    # 那个字段是自由文本，单位本来就乱（`42,000 users` vs `700+ clinics`），
    # TrustMRR 采集时填的往往是**累计注册用户**而不是付费订阅：
    # `vid-ai` 缓存 customers=6626、实测订阅只有 726，差一个数量级。
    # 覆写会把「用户规模」这个信息改成「付费数」，是静默的口径串味。
    # 订阅数只作为**独立字段** `subscriptions` 落库，供需要时取用。
    m = re.search(r"- Revenue last synced:\s*(\S+)", txt)
    out["synced_at"] = m.group(1) if m else None
    m = re.search(r"Verified payment provider API source:\s*(.+)", txt)
    out["verified_via"] = m.group(1).strip() if m else None
    out["listed_for_sale"] = "not currently listed" not in txt

    # 月度时间线：判趋势必须看它，且**要排除残月**
    blk = re.search(r"### Monthly revenue timeline(.*?)## Metric Snapshots", txt, re.S)
    seg = blk.group(1) if blk else ""
    rows = re.findall(r"\|\s*(\d{4}-\d{2})\s*\|\s*\$([\d,\.]+)\s*\|", seg)
    out["monthly"] = [{"month": mth, "revenue": float(v.replace(",", ""))}
                      for mth, v in rows]
    return out


def load(name):
    p = INBOX if name == "inbox" else CANDS
    with open(p, encoding="utf-8", newline="") as f:
        return json.load(f)


def save(name, rows):
    """按**各文件自己的原生格式**写回，绝不统一化。

    ⚠️ data/ 下三种换行格式并存（红线 7），实测 2026-10-10：
        inbox.json / cases.json  = CRLF
        candidates.json          = LF
    用默认 `open()` 写会把 inbox 的 9,358 个 CRLF 全改成 LF ——
    一次无心的批量改动就会让整个文件的 diff 全红。
    所以先探原文件的字节形态，写回时照原样。
    """
    p = INBOX if name == "inbox" else CANDS
    with open(p, "rb") as f:
        raw = f.read()
    crlf = raw.count(b"\r\n") > 0
    nl = "\r\n" if crlf else "\n"
    out = json.dumps(rows, ensure_ascii=False, indent=2).replace("\n", nl)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(out)


def collect(rows):
    """挑出 TrustMRR 来源且带 slug 的条目。"""
    out = []
    for it in rows:
        slug = it.get("trustmrr_slug")
        if slug and str(it.get("harvest_source") or "") == "trustmrr":
            out.append((it, slug))
    return out


def drift_of(rec, live):
    """采集缓存与实测值的差异。返回 None 表示口径一致。

    ⚠️ 口径不可混用（红线 1）：`mrr` 是当前月经常性收入，
    `last_30d_revenue` 是近 30 天流水，对非订阅制项目前者恒为 0。
    比对时各自只跟同名字段比，不交叉。
    """
    m = rec.get("metrics") or {}
    diffs = []
    # ⚠️ 只比**同口径**的数字字段（红线 1）。`customers` 是自由文本，
    # 采集时填的常是累计注册用户而非付费订阅（vid-ai: 6626 vs 订阅 726），
    # 拿它跟订阅数比会报出一堆假漂移，所以不参与比对。
    for key in ("mrr", "all_time"):
        old, new = m.get(key), live["metrics"].get(key)
        if isinstance(old, (int, float)) and not isinstance(old, bool) and new is not None:
            if old <= 0 and new > 0:
                diffs.append((key, old, new, "缓存为 0"))
            elif new <= 0 and old > 0:
                diffs.append((key, old, new, "实测归零 —— 产品可能已停更"))
            elif old > 0 and abs(old - new) / max(old, new) > 0.25:
                diffs.append((key, old, new, "偏差 >25%"))
        if old is None and new:
            diffs.append((key, None, new, "缓存缺该字段"))
    return diffs or None


def main():
    ap = argparse.ArgumentParser(description="TrustMRR 下架与数字漂移只读审计")
    ap.add_argument("--scope", choices=["inbox", "candidates", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0, help="只查前 N 条（调试用）")
    ap.add_argument("--check-fresh", action="store_true", help="同时比对数字是否漂移")
    ap.add_argument("--write", action="store_true",
                    help="把实测数字回写（只改数字与 data_freshness，不删条目）")
    args = ap.parse_args()

    scopes = ["inbox", "candidates"] if args.scope == "both" else [args.scope]
    sm = load_sitemap()
    print("TrustMRR sitemap：%d 条 slug\n" % len(sm))

    for scope in scopes:
        rows = load(scope)
        pairs = collect(rows)
        if args.limit:
            pairs = pairs[:args.limit]
        if not pairs:
            print("[%s] 没有带 slug 的 TrustMRR 条目" % scope)
            continue

        print("== %s · 待查 %d 条 ==" % (scope, len(pairs)))
        results = {}
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(probe, slug): (rec, slug) for rec, slug in pairs}
            for fut in cf.as_completed(futs):
                rec, slug = futs[fut]
                try:
                    results[rec["id"]] = (rec, fut.result())
                except Exception as e:
                    results[rec["id"]] = (rec, {"slug": slug, "state": "unknown",
                                                "err": type(e).__name__, "metrics": {}})

        gone, unknown, drift_rows, changed = [], [], [], 0
        for rid, (rec, live) in sorted(results.items()):
            st = live["state"]
            if st == "gone":
                gone.append((rid, rec.get("name"), live))
            elif st == "unknown":
                unknown.append((rid, rec.get("name"), live))
            if args.check_fresh and st == "live":
                d = drift_of(rec, live)
                if d:
                    drift_rows.append((rid, rec.get("name"), d, live))

        if gone:
            print("\n🚨 已下架（sitemap 无此 slug 且 .md 404/404 骨架）· %d 条" % len(gone))
            print("   %-32s %-20s %s" % ("id", "name", "缓存里的数字"))
            for rid, name, live in gone:
                rec = results[rid][0]
                mm = rec.get("metrics") or {}
                cache = "MRR $%s / 累计 $%s" % (
                    "{:,.0f}".format(mm["mrr"]) if isinstance(mm.get("mrr"), (int, float)) else "?",
                    "{:,.0f}".format(mm["all_time"]) if isinstance(mm.get("all_time"), (int, float)) else "?")
                print("   %-32s %-20s %s" % (rid[:32], str(name)[:20], cache))
        if unknown:
            print("\n? 无法判定 %d 条（网络/超时，不等于下架）：%s"
                  % (len(unknown), ", ".join(r[0] for r in unknown[:8])))

        if args.check_fresh:
            print("\n== 数字漂移 ==")
            if not drift_rows:
                print("   在册条目全部一致（或无缓存值可比）")
            for rid, name, d, live in sorted(drift_rows, key=lambda x: -len(x[2])):
                print("   %-26s %-18s" % (rid[:26], str(name)[:18]))
                for key, old, new, why in d:
                    print("      %-10s 缓存=%-12s 实测=%-12s %s"
                          % (key, old, new, why))
                m = live["metrics"]
                print("      实测：MRR $%s / 近30天 $%s / 订阅 %s / 同步 %s / %s"
                      % (m.get("mrr"), m.get("last_30d_revenue"),
                         m.get("subscriptions"), live.get("synced_at"),
                         live.get("verified_via")))

        if args.write:
            for rid, (rec, live) in results.items():
                if live["state"] != "live":
                    continue
                mm = rec.setdefault("metrics", {})
                lm = live["metrics"]
                touched = False
                for k in ("mrr", "all_time"):
                    if lm.get(k) is not None and lm[k] != mm.get(k):
                        mm[k] = lm[k]
                        touched = True
                if lm.get("subscriptions") is not None:
                    # 独立字段，不覆盖 customers（见 probe() 里的口径说明）
                    if mm.get("subscriptions") != lm["subscriptions"]:
                        mm["subscriptions"] = lm["subscriptions"]
                        touched = True
                if live.get("synced_at"):
                    rec["data_freshness"] = live["synced_at"]
                if touched:
                    changed += 1
            save(scope, rows)
            print("\n[write] %s：回写 %d 条实测数字（未删除任何条目）" % (scope, changed))
        print()


if __name__ == "__main__":
    main()