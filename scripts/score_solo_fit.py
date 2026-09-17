#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人可做性评分 + 双轴综合诊断。

一、solo_fit（个人可做性）
    把 cases.json 里已有的 replicability 四维（1=最容易，5=最难）翻成
    「越高越好做」的方向，归一化到 0-100，再补一个原数据没有、但对
    「一个人能不能做」最致命的维度：

        delivery  单人交付   1.4   不用团队 / 资质 / 7×24 值守

    五个维度和权重（权重按「一个人做」的杀手排序）：
        delivery   1.4   要团队要资质就是死刑
        reach      1.3   一个人卖不出去等于白做
        capital    1.1   能不能不烧钱熬到盈亏平衡
        build      1.0   技术栈在不在射程内（对全栈开发者不是瓶颈，故权重低）
        window     1.0   现在进场还有没有位置

    其中 build / reach / capital / window 直接由 replicability 反推
    （build = 6 - tech，以此类推），保证两套数据永不打架；
    只有 delivery 需要人工判断，写在 DELIVERY 表里。
    满分 = 5 × 5.8 = 29，归一化到 100。

二、composite（双轴综合分）
    solo_fit 和 china_fit 是**乘性关系**，不是加性关系：
    「一个人能做但国内没人买」= 0，「国内有需求但我做不了」= 0。
    所以不做算术平均，而用木桶式聚合并把短板权重放大：

        composite = 0.6 × min(solo, china) + 0.4 × mean(solo, china)

    这样 90/40 → 50，60/60 → 60：**双及格 > 单点突出**。

三、quadrant（四象限）
    以 70 分为「及格线」，把 24 条案例分进四个格子：
        go       双高          可以开干
        export   能做但没市场   做海外，别在国内卷
        partner  有市场但啃不动 值得找人 / 拿资质
        skip     双低          别碰

用法：
    python scripts/score_solo_fit.py              # 打分并写回 data/cases.json
    python scripts/score_solo_fit.py --dry-run    # 只看结果
    python scripts/score_solo_fit.py --top 10     # 只看前 10
    python scripts/score_solo_fit.py --threshold 75   # 换及格线
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# ------------------------------------------------------------------ 权重与维度

SOLO_WEIGHTS = {
    "delivery": 1.4,
    "reach": 1.3,
    "capital": 1.1,
    "build": 1.0,
    "window": 1.0,
}

SOLO_DIMS = [
    ("build", "造得出来", "技术栈在不在一个人射程内"),
    ("delivery", "单人交付", "不用团队 / 资质 / 7×24 值守"),
    ("reach", "够得着客户", "不靠销售团队就能触达"),
    ("capital", "启动轻", "不需要先烧钱就能开张"),
    ("window", "窗口还开着", "现在进场还有没有位置"),
]

SOLO_MAX = 5 * sum(SOLO_WEIGHTS.values())          # 29.0

# replicability(1=最容易) → solo_fit(5=最好做) 的反转映射
INVERT = {
    "build": "tech",
    "reach": "distribution",
    "capital": "capital",
    "window": "timing",
}

QUADRANTS = {
    "go": ("可以开干", "两边都过线：一个人能做，国内也有市场。"),
    "export": ("能做，但别在国内卖", "技术完全在手，卡在国内的需求或支付土壤上。出口做更顺。"),
    "partner": ("有市场，但一个人啃不动", "需求是真的，门槛在资质、大客户销售或团队交付上。"),
    "skip": ("别碰", "两个方向都不过线。"),
}

# ------------------------------------------------------------------ 人工判断表
# 只需填 delivery（单人交付，1-5）和理由。其余四维从 replicability 自动反推。
DELIVERY = {
    "gojiberryai": (3, "自动触达要天天盯：发送限频、封号申诉、话术迭代，客户还会追着问"
                       "线索质量——售后省不掉。它自己就经历过「支持过载、几乎不睡」那一阵。"),
    "sierra": (1, "交付对象是美国财富 500 强：要 SLA、要合规审计、要 7×24 待命。"
                  "这不是「忙一点」，是要养一个交付团队。"),
    "genius-ai": (3, "多租户 SaaS 单人能维护，但门店客户会打电话、要上门教，"
                     "客服人力省不掉。"),
    "nitra": (1, "医疗软件要过资质、数据不出院、HIS 对接，"
                 "合规和技术支持都必须有团队和法人主体。"),
    "viktor": (2, "产品本身一个人写得出来，但企业客户要采购流程、要约演示、"
                  "要有人在群里响应。"),
    "chatbase": (4, "自助式 SaaS，用户自己注册自己配。人工交付几乎为零。"),
    "rezi": (5, "纯自助工具，付钱就能用，没有交付环节。"),
    "postiz": (4, "开源自部署，社区和文档替你承担了大量售后。"),
    "shipfast": (5, "数字商品，买完就走。零交付、零运维。"),
    "trustmrr": (5, "纯信息展示站，没有交付。"),
    "comp-ai": (2, "合规审计结论要有资质背书，客户买的是「谁来签字」，不是工具。"),
    "aeo-engine": (5, "纯 SaaS 自助，客户自己看报表。"),
    "lancer-app": (4, "数据订阅，只需定期维护采集管道。"),
    "speel-co": (4, "SaaS 自助，但生成质量偶尔要人工兜底，留一点人力。"),
    "visualizee-ai": (4, "渲染服务自助化，出图不满意客户会来找，但不算重人力。"),
    "checkvibe": (5, "工具型产品，跑完出报告，没有交付环节。"),
    "storyshort-ai": (4, "自助生成，人工介入很少。"),
    "meerkats-ai": (2, "自动外呼要呼叫基础设施 + 合规审核 + 话术迭代，"
                      "而且出问题要有人兜。"),
    "coral": (4, "开源可自部署，售后被社区分担。"),
    "startclaw": (4, "托管平台，自助开通。"),
    "outrank": (5, "SaaS 自助。"),
    "marc-lou-portfolio": (5, "全是数字产品和轻 SaaS，天然无交付。"),
    "bustem": (4, "投诉流程可以自动化，但平台申诉要按规则人工调整策略。"),
    "kibu": (1, "客户是机构，采购要走关系、走评审，还要有人驻场培训。"),
    "pieter-levels": (5, "Photo AI 这类纯自助，付钱即用。"),
}


# ------------------------------------------------------------------ 工具

def load_json(name, default=None):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name, payload):
    path = os.path.join(DATA_DIR, name + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def to_score(raw, max_raw):
    return round(raw / max_raw * 100, 1)


def medal_for(rank):
    return {1: "gold", 2: "silver", 3: "bronze"}.get(rank)


def composite(solo, china):
    """木桶式聚合：短板占 6 成权重。两者缺一，总分就塌。"""
    lo, hi = min(solo, china), max(solo, china)
    return round(0.6 * lo + 0.4 * (solo + china) / 2, 1)


def quadrant_of(solo, china, threshold):
    if solo >= threshold and china >= threshold:
        return "go"
    if solo >= threshold:
        return "export"
    if china >= threshold:
        return "partner"
    return "skip"


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser(description="个人可做性评分 + 双轴综合诊断")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--top", type=int, default=0, help="只打印前 N 名（0 = 全部）")
    ap.add_argument("--threshold", type=float, default=70.0, help="四象限及格线，默认 70")
    args = ap.parse_args()

    cases = load_json("cases", [])
    if not cases:
        print("[!] data/cases.json 为空")
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    no_rep, no_delivery, no_china = [], [], []
    rows = []

    for c in cases:
        cid = c.get("id")
        rep = c.get("replicability") or {}

        # 四维反推
        dims = {}
        ok = True
        for key, src in INVERT.items():
            v = rep.get(src)
            if not isinstance(v, int):
                ok = False
                break
            dims[key] = 6 - v
        if not ok:
            no_rep.append(cid)
            continue

        # 单人交付：人工判断
        d = DELIVERY.get(cid)
        if not d:
            no_delivery.append(cid)
            continue
        dims["delivery"] = d[0]
        delivery_note = d[1]

        raw = round(sum(dims[k] * SOLO_WEIGHTS[k] for k in SOLO_WEIGHTS), 1)
        solo = to_score(raw, SOLO_MAX)

        china = (c.get("china_fit") or {}).get("score")
        if china is None:
            no_china.append(cid)

        rows.append({
            "case": c, "dims": dims, "raw": raw, "solo": solo,
            "delivery_note": delivery_note, "china": china,
        })

    # --- solo_fit 排名 ---
    rows.sort(key=lambda x: (-x["solo"], x["case"].get("name") or ""))
    for i, r in enumerate(rows, 1):
        r["solo_rank"] = i
        r["solo_medal"] = medal_for(i)

    # --- composite 排名（只算有 china_fit 的）---
    both = [r for r in rows if r["china"] is not None]
    for r in both:
        r["composite"] = composite(r["solo"], r["china"])
        r["quadrant"] = quadrant_of(r["solo"], r["china"], args.threshold)
    both.sort(key=lambda x: (-x["composite"], x["case"].get("name") or ""))
    for i, r in enumerate(both, 1):
        r["composite_rank"] = i
        r["composite_medal"] = medal_for(i)

    # --- 写入 ---
    for r in rows:
        c = r["case"]
        c["solo_fit"] = {
            "score": r["solo"],
            "raw": r["raw"],
            "max_raw": SOLO_MAX,
            "rank": r["solo_rank"],
            "medal": r["solo_medal"],
            "dims": r["dims"],
            "weights": SOLO_WEIGHTS,
            "delivery_note": r["delivery_note"],
            "derived_from": "replicability (%s)" % ", ".join(
                "%s=6-%s" % (k, v) for k, v in INVERT.items()),
            "scored_at": today,
        }
        if r["china"] is None:
            c.pop("composite", None)
            continue
        c["composite"] = {
            "score": r["composite"],
            "solo": r["solo"],
            "china": r["china"],
            "rank": r["composite_rank"],
            "medal": r["composite_medal"],
            "quadrant": r["quadrant"],
            "quadrant_label": QUADRANTS[r["quadrant"]][0],
            "formula": "0.6×min(solo,china) + 0.4×mean(solo,china)",
            "threshold": args.threshold,
            "scored_at": today,
        }

    # --- 打印 ---
    W = 90
    print("=" * W)
    print("  一、个人可做性排行（solo_fit）· 满分 %.1f → 归一化 100" % SOLO_MAX)
    print("  权重：单人交付 1.4｜够得着客户 1.3｜启动轻 1.1｜造得出来 1.0｜窗口 1.0")
    print("  ※ build/reach/capital/window 由 replicability 反推，只有 delivery 是新判断")
    print("=" * W)
    lbl = {1: "🥇 金牌", 2: "🥈 银牌", 3: "🥉 铜牌"}
    shown = rows[:args.top] if args.top > 0 else rows
    for r in shown:
        c = r["case"]
        print("\n%s  %2d. %-24s %5.1f / 100" % (
            lbl.get(r["solo_rank"], "   "), r["solo_rank"], c.get("name", "")[:24], r["solo"]))
        print("      " + "  ".join("%s %d" % (lab, r["dims"][k]) for k, lab, _ in SOLO_DIMS))
        print("      交付：%s" % r["delivery_note"])
    if len(shown) < len(rows):
        print("\n  … 其余 %d 条略（--top 0 看全部）" % (len(rows) - len(shown)))

    print("\n\n" + "=" * W)
    print("  二、双轴综合排行（composite）· 0.6×短板 + 0.4×均值")
    print("  及格线 %.0f 分：双高＝可以开干，单高＝要么做海外、要么找人合伙" % args.threshold)
    print("=" * W)
    shown2 = both[:args.top] if args.top > 0 else both
    for r in shown2:
        c = r["case"]
        qlabel, _ = QUADRANTS[r["quadrant"]]
        print("\n%s  %2d. %-24s %5.1f" % (
            lbl.get(r["composite_rank"], "   "), r["composite_rank"],
            c.get("name", "")[:24], r["composite"]))
        print("      个人可做性 %5.1f  ×  国内移植性 %5.1f   → [%s] %s" % (
            r["solo"], r["china"], r["quadrant"], qlabel))
    if len(shown2) < len(both):
        print("\n  … 其余 %d 条略" % (len(both) - len(shown2)))

    print("\n\n" + "=" * W)
    print("  三、四象限分布")
    print("=" * W)
    for key in ("go", "partner", "export", "skip"):
        grp = [r for r in both if r["quadrant"] == key]
        label, desc = QUADRANTS[key]
        print("\n  [%s] %s（%d 条）— %s" % (key, label, len(grp), desc))
        for r in grp:
            print("      · %-24s solo %5.1f  china %5.1f" % (
                r["case"].get("name", "")[:24], r["solo"], r["china"]))

    print("\n" + "=" * W)
    if both:
        t3 = both[:3]
        print("  综合金银铜：%s" % " / ".join(
            "%s %s" % (n, r["case"].get("name"))
            for n, r in zip(["金", "银", "铜"], t3)))
    print("=" * W)

    # --- 问题提示 ---
    if no_rep:
        print("\n[!] 缺 replicability，无法算 solo_fit：%s" % ", ".join(no_rep))
    if no_delivery:
        print("\n[!] DELIVERY 表里没填（将跳过）：%s" % ", ".join(no_delivery))
    if no_china:
        print("\n[i] 以下案例还没跑 score_china_fit.py，本次不算综合分：%s"
              % ", ".join(no_china))

    if args.dry_run:
        print("\n--dry-run：未写入 data/cases.json")
        return 0

    save_json("cases", cases)
    print("\n已写入 data/cases.json（solo_fit %d 条，composite %d 条）" % (len(rows), len(both)))
    print("页面刷新即可看到「综合排行」视图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
