#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存量回填：把 TrustMRR 的官网 / 收入 / 分类 / 地区等字段补进已有数据。

背景：早先 harvest_trustmrr() 只读首页 JSON-LD 的 ItemList，那里 url 恰好是
榜单自身页，于是「官网」这个概念根本没进采集器。结果候选池 36 条**没有一条**
带 source_url 或 website，页面上看着像「这些产品都找不到官网」。

这个脚本按已有记录的 slug / 名称去 TrustMRR 取真数据回填，两种来源：
  1. /api/ai 批量列表（快，含 44 条去重后的富字段）
  2. /startup/<slug> 详情页 JSON-LD（慢，但任何 slug 都能查）

用法：
    python scripts/backfill_trustmrr.py --dry-run    # 先看会改什么
    python scripts/backfill_trustmrr.py             # 实际写入
    python scripts/backfill_trustmrr.py --only inbox

依赖：仅 Python 标准库。可重复执行（幂等）。
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest as H                                          # noqa: E402

ROOT = H.ROOT
DATA_DIR = H.DATA_DIR


def norm_key(s):
    """归一化名称，用于模糊匹配（去掉标点、大小写、常见后缀）。"""
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|gmbh|sp\.?\s*z\.?\s*o\.?\s*o|corp|co)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def build_name_index(batch):
    """{归一化名称: slug}，用来把已有记录对到 API 条目上。"""
    idx = {}
    for slug, it in batch.items():
        for field in ("name", "slug"):
            k = norm_key(it.get(field))
            if k and k not in idx:
                idx[k] = slug
    return idx


def detect_slug(rec):
    """从一条已有记录里推断 TrustMRR slug。"""
    # 1) source_url 里已经有的，直接用
    src = rec.get("source_url") or ""
    m = re.search(r"trustmrr\.com/startup/([\w.\-]+)", src)
    if m:
        return m.group(1)

    # 2) 显式记过的 slug
    if rec.get("trustmrr_slug"):
        return rec["trustmrr_slug"]

    # 3) 按名称猜。TrustMRR 的 slug 基本是名字小写去符号。
    name = (rec.get("name_en") or rec.get("name") or "").strip()
    if name:
        guess = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if guess:
            return guess
    return ""


def is_trustmrr_record(rec):
    """这条记录是不是来自 TrustMRR。

    注意候选池里混了别的来源（比如微信公众号二手转述），它们没有
    harvest_source 字段，不能拿 TrustMRR 的数据去覆盖。
    """
    # 隐身公司的假名条目：官网与域名都不公开，回填没有意义
    name = (rec.get("name") or "").strip().lower()
    if name in ("anonymous startup", "stealth company", "unnamed company"):
        return False

    if rec.get("harvest_source") == "trustmrr":
        return True
    if "trustmrr" in (rec.get("source_url") or "").lower():
        return True
    # 榜单快照的手写 headline 也认
    hl = (rec.get("metrics") or {}).get("headline") or ""
    return "TrustMRR" in hl


def fetch_batch():
    """拉一次 /api/ai，返回 {slug: 富字段 dict}。"""
    try:
        data = H.http_json(H.TRUSTMRR_API, timeout=30)
    except Exception as e:                                   # noqa: BLE001
        print("[!] /api/ai 拉取失败：%s" % e)
        return {}
    out = {}
    for key in ("recentlyListedStartups", "bestDeals"):
        for it in (data.get(key) or []):
            if not isinstance(it, dict):
                continue
            slug = (it.get("slug") or "").strip()
            if slug:
                out[slug] = it
    print("[*] /api/ai 拉到 %d 条（去重前的键数）" % len(out))
    return out


def fetch_detail(slug):
    """拉 /startup/<slug> 详情页，解析 JSON-LD 里的 Organization 节点。"""
    url = "%s/startup/%s" % (H.TRUSTMRR_SITE, slug)
    try:
        page = H._fetch_html(url, timeout=30)
    except Exception:                                        # noqa: BLE001
        return None
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                            page, re.S):
        try:
            data = json.loads(block)
        except Exception:                                    # noqa: BLE001
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        nodes = graph if isinstance(graph, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "Organization":
                return node
    return None


def props_from_detail(node):
    """把详情页 Organization 节点转成与 /api/ai 同形的字段。"""
    def prop(name):
        for p in (node.get("additionalProperty") or []):
            if isinstance(p, dict) and p.get("name") == name:
                return p.get("value")
        return None

    founder = None
    f = node.get("founder")
    if isinstance(f, list) and f:
        founder = f[0] if isinstance(f[0], dict) else None
    elif isinstance(f, dict):
        founder = f

    x_handle = ""
    if founder:
        same = founder.get("sameAs") or ""
        m = re.search(r"(?:x|twitter)\.com/([\w_]+)", str(same))
        if m:
            x_handle = m.group(1)

    # 国家：foundingLocation 给的是 ISO 码
    country = ""
    loc = node.get("foundingLocation")
    if isinstance(loc, dict):
        country = H.country_cn(loc.get("identifier") or "")

    return {
        "name": node.get("name"),
        "slug": node.get("identifier"),
        "url": "%s/startup/%s" % (H.TRUSTMRR_SITE, node.get("identifier") or ""),
        "website": node.get("url") or "",
        "description": node.get("description") or "",
        "category": node.get("category") or "",
        "country": country,
        "foundedDate": node.get("foundingDate") or "",
        "revenue": {
            "total": prop("Verified revenue, all time"),
            "last30Days": prop("Verified revenue, last 30 days"),
            "mrr": prop("Current MRR"),
        },
        "customers": None,
        "activeSubscriptions": None,
        "xHandle": x_handle,
        "xFounderName": (founder or {}).get("name") or "",
        "_from": "detail",
    }


def clean_website(u):
    """清掉不能当官网的地址。

    TrustMRR 详情页的 Organization.url 有时会落回成它自己的页面
    （隐身公司、或卖家没填官网），那种不算官网，宁可留空。
    """
    u = (u or "").strip()
    if not u:
        return ""
    host = u.lower()
    if "trustmrr.com" in host:
        return ""
    if host in ("https://", "http://"):
        return ""
    return u


def merge_record(rec, rich):
    """把富字段并进一条已有记录；只补空、不覆盖已有的人工内容。

    返回 (是否改动, 改动说明列表)。
    """
    # rich 为空说明这条没对上 API/详情页。调用方虽然会先挡一次，但这个函数是
    # 纯函数、会被单独测试和复用，不该因为一个空输入就抛 AttributeError。
    if not isinstance(rich, dict):
        return False, []

    changes = []
    api = H._unwrap_api_item(rich) if isinstance(rich, dict) else None
    a = (api or {}).get("_api") or {}

    website = clean_website(a.get("website") or rich.get("website"))
    page_url = rich.get("url") or rec.get("source_url") or ""

    # 官网
    if website and not rec.get("website"):
        rec["website"] = website
        changes.append("补官网 %s" % website)

    # 来源页
    if page_url and not rec.get("source_url"):
        rec["source_url"] = page_url
        changes.append("补来源页")

    # slug（方便以后重查）
    if a.get("slug") and not rec.get("trustmrr_slug"):
        rec["trustmrr_slug"] = a["slug"]
        changes.append("记 slug")

    # 分类：只在原来是空 / 未分类时改
    cat = a.get("category") or rich.get("category") or ""
    if cat and rec.get("category") in ("", None, "未分类"):
        rec["category"] = cat
        changes.append("分类→%s" % cat)

    # 地区
    country = a.get("country") or H.country_cn(rich.get("country"))
    if country and not rec.get("origin"):
        rec["origin"] = country
        changes.append("地区→%s" % country)

    # 收入：写进 metrics（前端详情弹窗认这几个键）
    m = rec.setdefault("metrics", {})
    rev = rich.get("revenue") or {}
    mrr = rev.get("mrr") or 0
    last30 = rev.get("last30Days") or 0
    total = rev.get("total") or 0

    if mrr and not m.get("mrr"):
        m["mrr"] = mrr
        changes.append("MRR %s" % H._money_cell(mrr))
    if total and not m.get("all_time"):
        m["all_time"] = total
        changes.append("累计 %s" % H._money_cell(total))
    # 近 30 天收入：键名必须与 data/sources.json 的字段契约一致（last_30d_revenue）。
    # 别改回 harvest.py 内部暂存用的 revenue_last30d —— triage.revenue_of() 按契约名
    # 取数，取不到就会把「月流水几万」的条目当成「没有数字」直接埋掉。
    if last30 and not m.get("last_30d_revenue"):
        m["last_30d_revenue"] = last30
        changes.append("近30天 %s" % H._money_cell(last30))
    subs = a.get("subscriptions") or rich.get("activeSubscriptions")
    if subs and not m.get("customers"):
        m["customers"] = subs
        changes.append("订阅 %s" % subs)

    # 金额待补的旧 headline 换成真实数字。
    #
    # 兜底顺序很关键：TrustMRR 详情页的「Current MRR」对非订阅制项目恒为 0，
    # 这时只有 last30 有值。老实现要求 mrr 为真才替换 headline，于是这些条目
    # 永远停在「具体数字待补」——数字其实就在同一个响应里。
    # 口径照实写：MRR 就写 MRR，只有近 30 天流水就写「近 30 天收入」。
    old_hl = m.get("headline") or ""
    stale = ("待补" in old_hl or "未获取" in old_hl or "仅见于榜单" in old_hl
             or "未公开" in old_hl or not old_hl)
    if stale:
        if mrr:
            head = "MRR %s" % H._money_cell(mrr)
        elif last30:
            head = "近 30 天收入 %s" % H._money_cell(last30)
        else:
            head = ""
        if head:
            rank = a.get("rank")
            if rank:
                head += "，TrustMRR 第 %s 名" % rank
            m["headline"] = head
            changes.append("主指标→%s" % head)

    if a.get("founded") and not rec.get("founded_at"):
        rec["founded_at"] = a["founded"]
    if a.get("x_handle") and not rec.get("founder_x"):
        rec["founder_x"] = a["x_handle"]

    return bool(changes), changes


def process_file(name, batch, name_idx, args):
    """处理一个数据文件，返回改动条数。"""
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        print("[i] 没有 %s，跳过" % path)
        return 0

    records = H.load_json(name, []) or []
    if not isinstance(records, list):
        print("[!] %s 不是列表，跳过" % name)
        return 0

    touched = 0
    detail_cache = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if not is_trustmrr_record(rec):
            continue

        slug = detect_slug(rec)
        rich = batch.get(slug)

        # 猜的 slug 没命中，先用名称在 API 列表里找一遍
        if not rich:
            k = norm_key(rec.get("name_en") or rec.get("name"))
            hit = name_idx.get(k)
            if hit:
                slug = hit
                rich = batch.get(hit)

        # 还不行就走详情页（能查到榜单外/已下榜的条目，比如 stan）
        if not rich and not args.no_detail and slug:
            if slug not in detail_cache:
                node = fetch_detail(slug)
                detail_cache[slug] = props_from_detail(node) if node else None
                time.sleep(0.25)                             # 别把人家站点打疼
            rich = detail_cache.get(slug)

        if not rich:
            print("   · %-24s [找不到 %s]" % (rec.get("id"), slug or "?"))
            continue

        changed, why = merge_record(rec, rich)
        if changed:
            touched += 1
            print("   · %-24s %s" % (rec.get("id"), "；".join(why)))

    print("[*] %s：%d/%d 条有更新" % (name, touched, len(records)))
    if touched and not args.dry_run:
        H.save_json(name, records)
        print("    已写入 data/%s.json" % name)
    return touched


def main():
    ap = argparse.ArgumentParser(description="用 TrustMRR 真数据回填存量记录")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--only", default="", choices=["", "inbox", "candidates"],
                    help="只处理某一个文件")
    ap.add_argument("--no-detail", action="store_true",
                    help="只用 /api/ai 批量列表，不逐个查详情页（快但覆盖少）")
    args = ap.parse_args()

    print("=" * 64)
    print("  TrustMRR 存量回填%s" % ("（dry-run）" if args.dry_run else ""))
    print("=" * 64)

    batch = fetch_batch()
    name_idx = build_name_index(batch)

    targets = ["inbox", "candidates"] if not args.only else [args.only]
    total = 0
    for name in targets:
        total += process_file(name, batch, name_idx, args)

    print()
    if args.dry_run:
        print("--dry-run：共 %d 条会改动，未写入。" % total)
    else:
        print("完成：共 %d 条已更新。" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
