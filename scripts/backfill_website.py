#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存量回填：只用「官网」这一个字段，把 TrustMRR 两个公开端点（/api/ai 与
/api/ai/discovery）的 slug -> website 映射写回已有数据。

背景：候选池 28 条**没有任何一条带 URL**，导致 ai_verify.known_urls() 恒为空、
每次都退化成 cn.bing 搜索（小众外文站基本搜不到，le19emetrou 那轮抓回 5 个
百度知道/作业帮页）。而 TrustMRR 两个端点 90+ 条里绝大多数都带 website ——
问题不是「没数据」，是「提升进候选时把 website 丢了」。

这个脚本只补 website（最多顺手记一下 trustmrr_slug 与 source_url），
不碰收入/分类/地区等其它字段（那些由 backfill_trustmrr.py 负责），
不覆盖已经有的人工内容。

用法：
    python scripts/backfill_website.py --dry-run    # 先看会改什么
    python scripts/backfill_website.py             # 实际写入
    python scripts/backfill_website.py --only candidates

依赖：仅 Python 标准库。可重复执行（幂等）。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest as H                                          # noqa: E402

ROOT = H.ROOT
DATA_DIR = H.DATA_DIR


def norm_key(s):
    """归一化名称，用于模糊匹配（去标点、小写）。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def fetch_website_map():
    """拉两个端点，返回 {slug: {name, website, page_url}} 与 {归一名: slug}。

    覆盖两个端点是因为它们按 slug 只重叠 4 条：
      /api/ai         → recentlyListedStartups + bestDeals（40 字段，含 website）
      /api/ai/discovery → recentlyAddedStartups + fastestGrowingStartups（含 website）
    合起来 ~90 个唯一 slug，比只用 /api/ai 多约 41 个。
    """
    slug_map, name_idx = {}, {}
    for endpoint in (H.TRUSTMRR_API, H.TRUSTMRR_DISCOVERY_API):
        try:
            data = H.http_json(endpoint, timeout=30)
        except Exception as e:                               # noqa: BLE001
            print("[!] %s 拉取失败：%s" % (endpoint, e))
            continue
        if not isinstance(data, dict):
            continue
        for key in ("recentlyListedStartups", "bestDeals",
                    "recentlyAddedStartups", "fastestGrowingStartups"):
            for it in (data.get(key) or []):
                if not isinstance(it, dict):
                    continue
                slug = (it.get("slug") or "").strip()
                if not slug:
                    continue
                website = (it.get("website") or "").strip()
                if "trustmrr.com" in website.lower():        # 隐身/未填官网
                    website = ""
                info = slug_map.get(slug) or {
                    "slug": slug, "name": (it.get("name") or "").strip(),
                    "website": "", "page_url": it.get("url") or ""}
                if website and not info["website"]:
                    info["website"] = website
                if not info["page_url"]:
                    info["page_url"] = it.get("url") or ""
                slug_map[slug] = info
                k = norm_key(it.get("name"))
                if k and k not in name_idx:
                    name_idx[k] = slug
    print("[*] 两端点合并：%d 个唯一 slug，其中带 website 的 %d 个"
          % (len(slug_map), sum(1 for v in slug_map.values() if v["website"])))
    return slug_map, name_idx


def detect_slug(rec):
    """从一条已有记录里推断 TrustMRR slug。"""
    src = rec.get("source_url") or ""
    m = re.search(r"trustmrr\.com/startup/([\w.\-]+)", src)
    if m:
        return m.group(1)
    if rec.get("trustmrr_slug"):
        return rec["trustmrr_slug"]
    name = (rec.get("name_en") or rec.get("name") or "").strip()
    if name:
        guess = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if guess:
            return guess
    return ""


def is_trustmrr_record(rec):
    """只给 TrustMRR 相关的条目补网站，避免给别来源的条目乱填。"""
    if H.infer_source(rec) == "trustmrr":
        return True
    return bool(rec.get("trustmrr_slug"))


def merge_website(rec, info):
    """补 website（以及缺的 slug / 来源页 / 来源性质）。返回改动说明列表。

    来源性质必须同步成 verified：这条确认是 TrustMRR 标的，但候选池有不变量
    「凡是带 TrustMRR 来源的，source_kind 都为 verified」——只补 source_url 不补
    source_kind 会打破它（test_harvest_sources 有断言钉着）。
    """
    changes = []
    website = info.get("website") or ""
    page_url = info.get("page_url") or ""

    if website and not rec.get("website"):
        rec["website"] = website
        changes.append("补官网 %s" % website)
    if info.get("slug") and not rec.get("trustmrr_slug"):
        rec["trustmrr_slug"] = info["slug"]
        changes.append("记 slug")
    if page_url and not rec.get("source_url"):
        rec["source_url"] = page_url
        changes.append("补来源页")
    if rec.get("source_kind") != "verified":
        rec["source_kind"] = "verified"
        changes.append("来源性质→verified")
    if not rec.get("harvest_source"):
        rec["harvest_source"] = "trustmrr"
        changes.append("harvest_source→trustmrr")
    return changes


def find_info(rec, slug_map, name_idx):
    """按 slug / id / 名称三层取 TrustMRR 映射。命中即说明这条就是某 TrustMRR 标的。"""
    slug = detect_slug(rec)
    info = slug_map.get(slug) if slug else None
    if not info and rec.get("id"):
        info = slug_map.get(rec["id"])
    if not info:
        k = norm_key(rec.get("name_en") or rec.get("name"))
        if k:
            info = slug_map.get(name_idx.get(k))
    return info


def process_file(name, slug_map, name_idx, args):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        print("[i] 没有 %s，跳过" % path)
        return 0

    records = H.load_json(name, []) or []
    if not isinstance(records, list):
        print("[!] %s 不是列表，跳过" % name)
        return 0

    touched = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        # 官网已填且来源性质已是 verified —— 这条完全合规，跳过
        if rec.get("website") and rec.get("source_kind") == "verified":
            continue

        info = find_info(rec, slug_map, name_idx)
        if not info:
            continue

        changes = merge_website(rec, info)
        if changes:
            touched += 1
            print("   · %-24s %s" % (rec.get("id"), "；".join(changes)))

    print("[*] %s：%d 条补上官网" % (name, touched))
    if touched and not args.dry_run:
        H.save_json(name, records)
        print("    已写入 data/%s.json" % name)
    return touched


def main():
    ap = argparse.ArgumentParser(description="用 TrustMRR 真数据回填官网")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--only", default="", choices=["", "cases", "candidates", "inbox"],
                    help="只处理某一个文件")
    args = ap.parse_args()

    print("=" * 64)
    print("  TrustMRR 官网回填%s" % ("（dry-run）" if args.dry_run else ""))
    print("=" * 64)

    slug_map, name_idx = fetch_website_map()
    if not slug_map:
        print("[!] 两个端点都没拿到数据，检查网络后重试。")
        return 1

    targets = (["cases", "candidates", "inbox"]
               if not args.only else [args.only])
    total = 0
    for name in targets:
        total += process_file(name, slug_map, name_idx, args)

    print()
    if args.dry_run:
        print("--dry-run：共 %d 条会补上官网，未写入。" % total)
    else:
        print("完成：共 %d 条已更新。" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
