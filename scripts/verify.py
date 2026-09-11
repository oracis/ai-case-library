#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核实助手：写进案例库之前，先把一个公司的数字搜一遍。

这一步不能交给工具。工具不会因为「数字看起来合理」而警觉，但你会。
这个脚本只做一件事：把该查的东西、该问的六个口径，摊在你面前。

用法：
    python scripts/verify.py "Nitra"                  # 打印核查清单与搜索入口
    python scripts/verify.py "Nitra" --open           # 顺手在浏览器打开几个入口
    python scripts/verify.py "Nitra" --log            # 记一条到核实日志
    python scripts/verify.py --checklist              # 只看通用核查清单

依赖：仅 Python 标准库。
"""

import argparse
import json
import os
import sys
import urllib.parse
import webbrowser
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
LOG_FILE = os.path.join(DATA_DIR, "verification_log.json")

# 六个口径 —— 混一个就差十倍
CALIBERS = [
    ("ARR", "年化经常性收入。是「如果客户不变，一年会收到多少」，不是已经收到的现金。"),
    ("MRR", "月度经常性收入。ARR ≈ MRR × 12，但一次性收入不进来。"),
    ("run-rate", "把某个月或某季度乘以 12 得来的估计值。是估计，不是已发生。"),
    ("累计收入", "lifetime revenue。跟「年收入」完全是两回事——Chatbase 就栽在这。"),
    ("平台流水 / GMV", "客户通过平台过的钱，不是平台自己的收入。Nitra 的「$10 亿」是处理量。"),
    ("毛利 / 净利", "91% 毛利不等于 91% 利润。扣掉人力、算力、支付通道之后才是净利。"),
]

# 该搜的关键组合
SEARCH_TEMPLATES = [
    ('"{c}" 融资 / 收入 官方披露', 'https://www.bing.com/search?q={q}'),
    ('"{c}" ARR revenue verified', 'https://www.google.com/search?q={q}'),
    ('"{c}" on TrustMRR', 'https://trustmrr.com/search?q={q}'),
    ('"{c}" site:indiehackers.com', 'https://www.bing.com/search?q={q}'),
    ('"{c}" site:news.ycombinator.com', 'https://hn.algolia.com/?q={q}'),
]

DIRECT_ENTRIES = [
    ('官网首页', 'https://{d}'),
    ('定价页（判断客单价与计费模式）', 'https://{d}/pricing'),
    ('关于 / 团队页', 'https://{d}/about'),
]


def load_log():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                          # noqa: BLE001
        return []


def save_log(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def print_checklist(company=None):
    bar = "=" * 64
    print(bar)
    print("  核实清单" + (" · %s" % company if company else ""))
    print(bar)

    print("\n【第 1 步】六个口径，逐个问一遍这个数字是哪个\n")
    for i, (name, desc) in enumerate(CALIBERS, 1):
        print("  %d. %-14s %s" % (i, name, desc))

    print("\n【第 2 步】找一手证据，优先级从高到低\n")
    tiers = [
        ("A", "支付网关直连（TrustMRR / Stripe 客户案例）", "数字由支付数据自动验证，不接受截图"),
        ("A", "公司官方通稿或财报", "愿意署名的数字，说错了要担责"),
        ("B", "可信媒体的独立报道", "注意是否只是转述通稿"),
        ("C", "创始人自己的推文 / 帖子", "可以做方向参考，不能当收入依据"),
        ("D", "中文二手转述（公众号 / 知识星球）", "本库发现的两个错误都出在这一层"),
    ]
    for tag, what, why in tiers:
        print("  [%s] %-38s %s" % (tag, what, why))

    print("\n【第 3 步】对照检查——这几个组合最容易出错\n")
    traps = [
        ("客户数", "「7000 家」和「700 家」差一个数量级（Nitra 真实案例）"),
        ("累计 vs 年", "「收入 2000 万」是累计还是年化（Chatbase 真实案例）"),
        ("流水 vs 收入", "「年处理 10 亿」不是「年收入 10 亿」"),
        ("时间点", "数字是哪一年的？2023 年的 $50K MRR 和今天的不是一回事（ShipFast）"),
        ("口径范围", "「12,000 团队」和「2,000 组织」是两个不同的数（Viktor）"),
        ("是否还在增长", "收入真实 ≠ 还在增长（Speel.co 增长 0%）"),
    ]
    for k, v in traps:
        print("  · %-12s %s" % (k, v))

    print("\n【第 4 步】写下你的结论，并且写清不确定在哪\n")
    print("  库里每条案例都有 verification 字段，取值：")
    for v, lab in [("stripe", "支付网关验证"), ("official", "官方披露"),
                   ("partial", "口径待核"), ("founder", "创始人自报"),
                   ("disputed", "数字有出入"), ("unverified", "未核实")]:
        print("    %-11s %s" % (v, lab))

    print("\n  以及 corrections 字段 —— 如果你发现了别人的错误，记下来。")
    print("  这是这个库最有价值的部分：它让别人不会抄错。")
    print()


def print_entries(company, domain=None):
    q = urllib.parse.quote(company)
    print("【搜索入口】\n")
    links = []
    for label, tmpl in SEARCH_TEMPLATES:
        url = tmpl.replace("{q}", q)
        links.append((label.format(c=company), url))
    for label, url in links:
        print("  · %s\n    %s" % (label, url))

    if domain:
        print("\n【直接打开】\n")
        for label, tmpl in DIRECT_ENTRIES:
            url = tmpl.replace("{d}", domain)
            print("  · %s\n    %s" % (label, url))
            links.append((label, url))
    else:
        print("\n  （提示：加上 --domain example.com 可以生成官网/定价页的直接入口）")

    return links


def main():
    ap = argparse.ArgumentParser(description="案例核实助手")
    ap.add_argument("company", nargs="?", help="公司名，例如 Nitra")
    ap.add_argument("--domain", default="", help="官网域名，例如 nitra.com")
    ap.add_argument("--open", action="store_true", help="在浏览器打开搜索入口")
    ap.add_argument("--log", action="store_true", help="记录一条核实日志")
    ap.add_argument("--note", default="", help="配合 --log 使用的备注")
    ap.add_argument("--verification", default="", help="配合 --log：核实结论等级")
    ap.add_argument("--checklist", action="store_true", help="只打印通用核查清单")
    args = ap.parse_args()

    if args.checklist or not args.company:
        print_checklist(args.company)
        return 0

    print_checklist(args.company)
    links = print_entries(args.company, args.domain or None)

    if args.open:
        print("\n[*] 正在打开 %d 个入口…" % len(links))
        for _, url in links:
            try:
                webbrowser.open(url)
            except Exception:                                  # noqa: BLE001
                pass

    if args.log:
        rows = load_log()
        rows.insert(0, {
            "company": args.company,
            "domain": args.domain,
            "verification": args.verification or "unverified",
            "note": args.note,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        save_log(rows)
        print("\n[✓] 已记录到 data/verification_log.json（共 %d 条）" % len(rows))

    print("\n提醒：核完再去改 data/cases.json。写之前先搜一遍——这条规矩值钱。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
