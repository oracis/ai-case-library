#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TrustMRR slug 反查回填：给缺 trustmrr_slug 的存量条目找回挂牌页。

问题（2026-09-24）：候选池里16 条缺 slug，深核时 `known_urls()` 拿不到挂牌页，
退化成纯搜索抓回垃圾（promptmonitor 那轮抓回巴赫乐谱页，AI 判 3% 置信度 /
disputed / 0 来源，过不了入库闸门）。当时以为是「这些条目没有挂牌页」，
实际是**两张官方索引没被用上**：

  1. `https://trustmrr.com/startup-sitemap.xml`（robots.txt 公布）
     —— 全站 10,691 条 slug，解压后 1.7MB。有它就能确认「id/名称对应的 slug
     到底存不存在」，不用一个个 404 试探。
  2. `https://trustmrr.com/api/ai`（llms.txt 公布）
     —— 25 条新挂牌 + 25 条 bestDeals，每条带 slug/name/website/askingPrice/
     multiple，比 /api/ai/discovery 多一批，是「名称/官网 → slug」的匹配表。

反查命中后写回 `trustmrr_slug`（并把来源页与来源性质一并补齐），深核下次就能
直接抓到 `.md` 一手原文。反查不到**不等于不能复核** —— 那就走官网/创始人等
其它一手来源（2026-09-24 voklit 实测：挂牌页已撤、只靠官网照样过闸入库），
本脚本会把这些条目单独列出来并给出建议路径。

用法：
    python scripts/resolve_slug.py                  # 只报告（dry-run）
    python scripts/resolve_slug.py --write          # 写回 data/*.json
    python scripts/resolve_slug.py --only candidates
    python scripts/resolve_slug.py --verify         # 顺手 HEAD 一下 .md 是否真的在

依赖：仅 Python 标准库。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_verify as A                                            # noqa: E402
import harvest as H                                              # noqa: E402

DATA_DIR = H.DATA_DIR

# 这些不是真实产品，是分析口径的基准/对比条目，本就查不到挂牌页。
BENCHMARK_HINT = ("benchmark", "avg", "audit", "hygiene", "competitors",
                  "marketplace-avg", "caliber")


def is_benchmark(rec):
    rid = (rec.get("id") or "").lower()
    return any(h in rid for h in BENCHMARK_HINT)


def merge_slug(rec, slug):
    """写回 slug 相关字段，返回改动说明列表。"""
    changes = []
    if rec.get("trustmrr_slug") != slug:
        rec["trustmrr_slug"] = slug
        changes.append("记 slug=%s" % slug)
    page = "%s/startup/%s" % (A.TRUSTMRR_SITE, slug)
    if not rec.get("source_url"):
        rec["source_url"] = page
        changes.append("补来源页")
    if rec.get("source_kind") != "verified":
        rec["source_kind"] = "verified"
        changes.append("来源性质→verified")
    return changes


def process(name, args, index, slugs):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        print("[i] 没有 %s，跳过" % path)
        return 0, 0, 0

    records = H.load_json(name, []) or []
    if not isinstance(records, list):
        return 0, 0, 0

    hit = miss = bench = 0
    for rec in records:
        if not isinstance(rec, dict) or rec.get("trustmrr_slug"):
            continue
        if is_benchmark(rec):
            bench += 1
            continue

        slug, how = A.resolve_trustmrr_slug(rec, index=index, slugs=slugs,
                                            confirm=True)
        if not slug:
            miss += 1
            print("   ✗ %-24s 反查无果 —— 走官网/创始人等一手来源复核" % rec.get("id"))
            continue

        hit += 1
        note = how
        if args.verify:
            # sitemap 只证明「这个 slug 存在」，不证明「就是同一个产品」——
            # id 命中不代表身份对得上（private-venture-1 就踩过：同名 slug 抓回来
            # 是另一个隐身挂牌，创始人/收入全对不上）。所以这里再用 .md 的
            # `# 标题` 与记录名称核一遍，不符的明确标出来。
            m = re.search(r"^#\s*(.+)$", A.fetch_text(
                "%s/startup/%s.md" % (A.TRUSTMRR_SITE, slug)) or "", re.M)
            title = (m.group(1).strip() if m else "")
            rec_name = rec.get("name_en") or rec.get("name") or ""
            if not title:
                note += "；.md 抓不到（可能被限流，隔一会儿再试）"
            elif A._norm_key(title) != A._norm_key(rec_name):
                note += "；⚠ .md 标题「%s」与名称不符，请人工确认" % title
            else:
                note += "；.md 标题一致（%s）" % title
        changes = merge_slug(rec, slug)
        print("   ✓ %-24s → %-28s 【%s】%s"
              % (rec.get("id"), slug, note, "；".join(changes) or "无改动"))

    print("[*] %s：命中 %d · 无果 %d · 基准条目 %d"
          % (name, hit, miss, bench))
    if hit and not args.dry_run:
        H.save_json(name, records)
        print("    已写入 data/%s.json" % name)
    return hit, miss, bench


def main():
    ap = argparse.ArgumentParser(description="TrustMRR slug 反查回填")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写入")
    ap.add_argument("--write", action="store_true", help="写回 data/*.json")
    ap.add_argument("--only", default="", choices=["", "cases", "candidates", "inbox"],
                    help="只处理某一个文件")
    ap.add_argument("--verify", action="store_true",
                    help="顺手抓一次 .md 确认挂牌页真的在（慢一点）")
    args = ap.parse_args()
    args.dry_run = not args.write

    print("=" * 70)
    print("  TrustMRR slug 反查%s" % ("（dry-run，加 --write 才写回）" if args.dry_run else ""))
    print("=" * 70)

    slugs = A.trustmrr_sitemap_slugs()
    print("[*] sitemap：%d 条全站 slug" % len(slugs))
    index = A.trustmrr_index()
    print("[*] /api/ai 索引：%d 条（含名称/官网/挂牌价）" % len(index))
    if not slugs and not index:
        print("[!] 两张索引都没拿到，检查网络后重试。")
        return 1

    # 默认只处理 cases / candidates：这两处的 id 本来就来自 TrustMRR slug，
    # 命中即同一产品。inbox 里混着 HN 条目（id 是帖子标题 slug），短词撞名
    # 概率高（radio / salt / flow 这类），要处理得显式 --only inbox --verify。
    targets = (["cases", "candidates"] if not args.only else [args.only])
    total = (0, 0, 0)
    for name in targets:
        h, m, b = process(name, args, index, slugs)
        total = (total[0] + h, total[1] + m, total[2] + b)

    print()
    print("命中 %d · 无果 %d · 基准条目 %d" % total)
    if args.dry_run and total[0]:
        print("--dry-run：未写入。确认无误后加 --write。")
    if total[1]:
        print("反查无果的条目：用 AI 深核时挑官网 / App Store / 创始人自述当一手来源，"
              "闸门只要求「有登记来源」，不要求来源必须是 TrustMRR。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
