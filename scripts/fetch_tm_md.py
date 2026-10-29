#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量抓剩余候选的 TrustMRR 官方 .md 档案，抽成写正文用的紧凑素材。

只走官方 AI 通道（llms.txt 认可的 /startup/<slug>.md），与 harvest 的
--source trustmrr_sitemap 同一入口；每 slug 只抓一次，落盘 tmp/tm_md/。
抽出来的字段是 candidates.json 里没有、但写「它为什么能成」用得上的：
创始人 X 粉丝数（分发信号）、客户/订阅数、挂牌信息（并购视角）。

用法::

    python scripts/fetch_tm_md.py        # 增量：缓存可用的跳过，只抓缺的/坏的

⚠ 缓存判据（2026-10-21 修）：此前只看「文件存在且 > 200 字节」—— 抓取被
  掐断留下的**半截档案**照样当有效缓存，于是摘要里一半字段是 None，写正文
  的模型就在空素材上发挥。现在两条一起判：长度 + 官方页脚（`is_complete`），
  且写盘走原子替换（`atomicio`），自己不会再产出残片。
"""

import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tmp", "tm_md")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atomicio  # noqa: E402  原子写盘（半截档案 = 后面的摘要全空）

MIN_MD_BYTES = 200
# 完整档案的**页脚**（2026-10-21 抓 private-venture.md 实测的结尾）：
#     ## Related TrustMRR Pages
#     - [Acquire startups](...)  …  - [llms.txt](https://trustmrr.com/llms.txt)
# 下载被掐断的残片不可能带这段页脚 —— 这是「完整」的唯一硬判据。
# 若站点改版去掉这段，本脚本会**每条都失败并点名原因**（响亮），
# 而不是把残片当档案用（静默）—— 那时改这个常量即可。
FOOTER_MARK = "## Related TrustMRR Pages"
FOOTER_WINDOW = 2000        # 页脚必在文末这一段里


def fetch(slug):
    """抓一份档案。返回 `(text, 失败原因)`：成功时原因为空串。"""
    url = "https://trustmrr.com/startup/%s.md" % slug
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", "replace"), ""
    except Exception as e:                                  # noqa: BLE001
        # 原因要带出来：404（slug 不存在）和 timeout（网络/限流）处置完全不同
        return None, "%s: %s" % (type(e).__name__, str(e)[:90])


def is_complete(md):
    """这份 .md 是不是一份**完整**档案（不是写了一半的残片）。"""
    if not md or len(md) < MIN_MD_BYTES:
        return False
    return FOOTER_MARK in md[-FOOTER_WINDOW:]


def load_cached(path):
    """读缓存；残片/读失败一律返回 None（当作没缓存，重新抓）。"""
    try:
        with open(path, encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return None
    return md if is_complete(md) else None


def grab(pat, text, cast=str):
    m = re.search(pat, text, re.M)
    if not m:
        return None
    try:
        return cast(m.group(1).replace(",", "").strip())
    except Exception:                                       # noqa: BLE001
        return m.group(1).strip()


def summarize(slug, md):
    """从 .md 里抽紧凑素材；抽不到的字段留 None，不猜。"""
    d = {"slug": slug}
    d["description"] = grab(r"^- Description: (.+)$", md, str)
    d["founder"] = grab(r"^- Founder: \[([^\]]+)\]", md)
    d["x_followers"] = grab(r"^- X followers: ([\d,]+)$", md, int)
    d["founded"] = grab(r"^- Founded date: (.+)$", md)
    d["provider"] = grab(r"^- Verified payment provider API source: (.+)$", md)
    d["merchant_of_record"] = grab(r"^- Merchant of record: (.+)$", md)
    d["revenue_synced"] = grab(r"^- Revenue last synced: (.+)$", md)
    d["ga"] = grab(r"^- Google Analytics API connected: (.+)$", md)
    d["datafast"] = grab(r"^- DataFast API key connected: (.+)$", md)
    d["gsc"] = grab(r"^- Google Search Console API connected: (.+)$", md)
    # 收入表（行格式：| Last 30 days | $9,994 |）
    for period, key in (("Last 24 hours", "rev_24h"), ("Last 7 days", "rev_7d"),
                        ("Last 30 days", "rev_30d"), ("All time", "all_time")):
        d[key] = grab(r"^\| %s \| \$?([\d,\.]+) \|$" % re.escape(period), md, float)
    # 指标快照
    # 增速的两个字段带上正负号。
    # ⚠ 2026-10-29 修：原正则第一组是 `([\d\.]+)`、第二组是 `\+?([\d\.]+)`,
    # 只要档案写成 `+1361.7%` 这种带正号的，MRR 增速就**静默抽成 None**
    # —— 而增速正是写正文时最容易被编出来的字段：素材里写着「未给」，
    # 模型只能从 .md 原文里捡，捡错标签（把收入增速写成 MRR 增速）也没人拦。
    # 实测 veloris-llc 就是这样：档案原文有 `+1361.7%`，summary 里却是 None。
    d["cur_mrr"] = grab(r"^- Current MRR: \$([\d,\.]+)", md, float)
    d["mrr_growth"] = grab(r"^- Last 30 days MRR growth: ([+-]?[\d\.]+)%", md, float)
    d["active_subs"] = grab(r"^- Current active subscriptions: ([\d,]+)", md, int)
    d["profit_margin"] = grab(r"^- Last 30 days profit margin: ([\d\.]+)%", md, float)
    # Startup Insights —— 写正文的硬素材
    d["value_prop"] = grab(r"^- Value proposition: (.+)$", md)
    d["problem"] = grab(r"^- Problem solved: (.+)$", md)
    d["pricing_model"] = grab(r"^- Pricing model: (.+)$", md)
    d["persona"] = grab(r"^- Target persona: (.+)$", md)
    d["audience"] = grab(r"^- Audience type: \[([^\]]+)\]", md)
    d["team_size"] = grab(r"^- Team size: (.+)$", md)
    d["funding"] = grab(r"^- Funding: (.+)$", md)
    d["user_count"] = grab(r"^- Estimated user count: ([\d,]+)", md, int)
    d["markets"] = grab(r"^- Markets: (.+)$", md)
    # ⚠ 不抓 askingPrice / revenueMultiple / isForSale：.md 文末内嵌的 JSON 是
    # 「相似项目」列表，第一条往往不是本条目 —— 2026-10-15 实测 orbio 的挂牌价
    # 就是这么从别人身上抓来的（$220K 属于列表中另一家公司）。归属分不清，
    # 宁可不要，也不能把别人的挂牌价写进本条案例。
    # 增长（正负号都要留：档案里有 `+185.8%` 也有 `-7.3%`）
    d["growth_30d"] = grab(r"^- Last 30 days revenue growth: ([+-]?[\d\.]+)%", md, float)
    # 分类/标签
    d["category"] = grab(r"^- Category: (.+)$", md)
    d["tags"] = grab(r"^- Tags?: (.+)$", md)
    return d


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(ROOT, "data", "candidates.json"),
              encoding="utf-8") as f:
        cands = json.load(f)
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    done_ids = {c.get("published_from") for c in cases}
    todo = [c for c in cands if c.get("id") not in done_ids]
    print("待抓候选 %d 条" % len(todo))

    summary = {}
    ok = fail = skip = 0
    for i, c in enumerate(todo, 1):
        slug = c.get("trustmrr_slug") or c.get("id")
        path = os.path.join(OUT, slug + ".md")
        md = load_cached(path)
        if md is not None:
            skip += 1
        else:
            md, why = fetch(slug)
            if not is_complete(md):
                fail += 1
                print("  [%d/%d] %-38s 抓取失败：%s"
                      % (i, len(todo), slug,
                         why or "内容不完整（缺 %s 页脚）" % FOOTER_MARK))
                time.sleep(0.4)
                continue
            atomicio.write_text(path, md)
            ok += 1
            time.sleep(0.35)
        summary[c["id"]] = summarize(slug, md)
        if i % 10 == 0:
            print("  [%d/%d] 进度（新抓 %d / 缓存 %d / 失败 %d）"
                  % (i, len(todo), ok, skip, fail))

    atomicio.write_json(os.path.join(OUT, "summary.json"), summary, indent=1)
    print("\n完成：新抓 %d，缓存 %d，失败 %d，素材 -> tmp/tm_md/summary.json"
          % (ok, skip, fail))
    # 失败清单单独落盘，方便重试
    bad = [c["id"] for c in todo if c["id"] not in summary]
    if bad:
        atomicio.write_text(os.path.join(OUT, "failed.txt"), "\n".join(bad))
        print("抓取失败 %d 条：%s" % (len(bad), ", ".join(bad[:10])))


if __name__ == "__main__":
    main()
