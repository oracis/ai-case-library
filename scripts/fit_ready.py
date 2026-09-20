#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给新发布的案例补上三套人工判断：replicability / solo_fit / china_fit。

为什么会有这个脚本
------------------
发布链路（promote）只搬运**候选上已有的字段**，它能搬数据（数字、来源、口径），
搬不了判断 —— 判断表历来写在 score_solo_fit / score_china_fit 里，
replicability 更是没有任何脚本生成，一直靠人工写进 cases.json。

于是每次发布新案例都会留下同一个缺口：

    · make_article 生成公众号草稿时，缺 china_fit / solo_fit 的案例
      写不出「能不能搬回国内」「一个人能不能做」这两段，文章从 5 段掉到 3 段
    · 首页的适配度排序、四象限图里，新案例是空白

2026-09-20 发布 5 条后这两个失败同时出现，所以补这一道。

分工
----
    replicability  ← 本脚本写入（没有任何脚本生成它，历来靠人工填 cases.json）
    solo_fit       ← score_solo_fit.py 写（判断表 DELIVERY 在本脚本里补）
    china_fit      ← score_china_fit.py 写（判断表 SCORES 在本脚本里补）

本脚本不自己算分 —— 归一化公式和排名只有那两处实现，在这里重写一遍
就会多出一套口径，两边迟早算出不同结论。

用法
----
    python scripts/fit_ready.py --check     # 看哪些案例还缺判断（只读）
    python scripts/fit_ready.py --dry-run   # 只报要改什么，不写盘
    python scripts/fit_ready.py             # 写入 data/cases.json

写完必须接着跑这两步，否则 solo_fit / china_fit 还是空的：
    python scripts/score_solo_fit.py        # 重算 solo_fit + composite + quadrant
    python scripts/score_china_fit.py       # 重算 china_fit
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import score_china_fit as CF          # noqa: E402
import score_solo_fit as SF           # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES_PATH = os.path.join(ROOT, "data", "cases.json")

# ---------------------------------------------------------------------------
# 1) replicability 四维：1 = 最容易复制，5 = 最难。
#    口径与老案例一致（见 chatbase {tech:3, distribution:3, capital:1, timing:5}）。
#    tech 技术门槛 / distribution 获客难度 / capital 启动资金 / timing 时机窗口
# ---------------------------------------------------------------------------
REPLICABILITY = {
    # 校验 API：技术不复杂（现成数据源 + 缓存），难点在数据的准确率和反欺诈对抗；
    # 获客靠开发者口碑与 SEO，不烧钱；窗口不重要（基础设施类需求长期存在）。
    "1lookup": {"tech": 3, "distribution": 3, "capital": 3, "timing": 2},

    # 创作者收银台：技术是支付 + 分账 + 多端，工程量不小；获客极难（要抢创作者）；
    # 启动要备付金、支付资质和合规团队，烧钱重；窗口已被 Linktree/Beacons 占住。
    "stan": {"tech": 4, "distribution": 5, "capital": 5, "timing": 4},

    # AI 生成 PPT：接三方大模型 + Slides API，一个人写得出来；靠 SEO 长尾获客；
    # 启动几乎零成本；但 2023 那个「早半年」的窗口已经关了。
    "magicslides-app": {"tech": 2, "distribution": 3, "capital": 1, "timing": 4},

    # 批量短视频：管线长（脚本/TTS/素材/合成）但都是现成能力拼装；获客靠内容平台；
    # 启动成本低（按量付模型费）；竞品已密集，窗口偏晚。
    "autoreels-ai": {"tech": 3, "distribution": 4, "capital": 2, "timing": 4},

    # iOS 单点识别：Core ML / 视觉 API 够用，一个人能做完；获客完全靠 App Store
    # 自然搜索，不用投广告；启动只有开发者账号和模型调用费 —— 四维里最轻的一档。
    "insect-bite-id": {"tech": 2, "distribution": 2, "capital": 1, "timing": 2},
}


def load_cases():
    with open(CASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def missing_replicability(cases, only=None):
    out = []
    for c in cases:
        cid = c.get("id")
        if only and cid not in only:
            continue
        if not (c.get("replicability") or {}):
            out.append(cid)
    return out


def missing_solo(cases, only=None):
    out = []
    for c in cases:
        cid = c.get("id")
        if only and cid not in only:
            continue
        if cid not in SF.DELIVERY and not (c.get("solo_fit") or {}):
            out.append(cid)
    return out


def missing_china(cases, only=None):
    out = []
    for c in cases:
        cid = c.get("id")
        if only and cid not in only:
            continue
        if cid not in CF.SCORES and not (c.get("china_fit") or {}):
            out.append(cid)
    return out


def cmd_check():
    cases = load_cases()
    rep = missing_replicability(cases)
    solo = missing_solo(cases)
    china = missing_china(cases)
    print("案例共 %d 条\n" % len(cases))
    print("  缺 replicability : %d 条  %s" % (len(rep), "、".join(rep) or "-"))
    print("  缺 solo_fit 判断 : %d 条  %s" % (len(solo), "、".join(solo) or "-"))
    print("  缺 china_fit 判断: %d 条  %s" % (len(china), "、".join(china) or "-"))
    print()
    for cid in REPLICABILITY:
        if cid in rep:
            print("  本文件已备好 replicability：%s → %s" % (cid, REPLICABILITY[cid]))
    return 0


def cmd_apply(dry_run=False):
    cases = load_cases()
    today = datetime.now().strftime("%Y-%m-%d")
    touched = []

    for c in cases:
        cid = c.get("id")
        rep = REPLICABILITY.get(cid)
        if rep and not (c.get("replicability") or {}):
            touched.append((cid, dict(rep)))
            c["replicability"] = dict(rep)

    # solo_fit / china_fit 不在这里算。那两个脚本的入口是 main()（读盘→算→写盘），
    # 没有可复用的单条函数；在这里重算等于把归一化公式抄第二遍，
    # 而多一套口径正是这个库反复吃亏的地方。交给它们自己跑。
    if not touched:
        print("没有需要补的 replicability。")
    else:
        for cid, rep in touched:
            print("  [补] %-18s %s" % (cid, rep))

    if dry_run:
        print("\n（--dry-run，没有写盘）")
        return

    if touched:
        with open(CASES_PATH, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("\n已写入 data/cases.json（%s）" % today)

    print("\n接着跑这两步把派生分算出来：")
    print("  python scripts/score_solo_fit.py")
    print("  python scripts/score_china_fit.py")


def main():
    ap = argparse.ArgumentParser(description="给新发布的案例补三套人工判断")
    ap.add_argument("--check", action="store_true", help="只看缺什么（只读）")
    ap.add_argument("--dry-run", action="store_true", help="算一遍但不写盘")
    args = ap.parse_args()

    if args.check:
        raise SystemExit(cmd_check())
    cmd_apply(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
