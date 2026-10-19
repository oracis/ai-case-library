#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给完全缺 replicability 的案例补四维评分。

为什么单独一个脚本：
    fix_replicability_dims.py 只处理「键名写错」的（{tech,data,sales,domain}），
    它对「整个字段缺失」的条目不生效（set(rep.keys()) != 旧四键 → continue）。
    这 12 条是连字段都没有，必须四维全判，没法靠换算。

维度口径（**1 分＝最容易，5 分＝最难**，与 fix_replicability_dims.py 一致）：
    tech          技术门槛：一个人能不能把东西造出来
    distribution  获客门槛：不靠销售团队能不能触达客户
    capital       资金门槛：要不要垫资 / 备货 / 预购算力 / 先烧钱
    timing        时机门槛：这门生意是不是只存在于当前这个窗口

⚠️ 下游 score_solo_fit 读的键就是这四个，写成 {tech,data,sales,domain}
   会「非空但算不出来」—— 这是本项目踩过两次的坑，改键名先确认下游。

用法：
    python scripts/fill_missing_replicability.py            # 写入
    python scripts/fill_missing_replicability.py --dry-run   # 只看会补哪些
"""
import argparse
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "data", "cases.json")

DIMS = ("tech", "distribution", "capital", "timing")

# id: (tech, distribution, capital, timing) —— 每条都写了判断依据
NEW = {
    # 双端 AI 修图 App：风格化重渲染不难，难在风格模型要持续做；
    # 美颜赛道买量贵；算力按单计费；这一波 AI 修图窗口，大厂随时内置。
    "boothie-ai-photo-editor": (3, 3, 3, 4),

    # 卡路里追踪：模型不是门槛，食物营养库才是；健康类获客极贵；
    # 要买量 + 建库；需求常青但 AI 卡路里这一波正在退潮（它自己掉 25%）。
    "nutria-9e3eaa": (3, 4, 3, 3),

    # 学术写作改写：套壳 + 提示工程；长尾词现成；纯软件不垫资；
    # 完全寄生在「检测器 vs 反检测」的军备竞赛上，规则一变就归零 → timing 5。
    "fastwrite-io": (2, 3, 2, 5),

    # SketchUp 版本转换：要吃透各版本文件格式（硬功夫，但不涉及 AI）；
    # 极窄长尾，SEO 精确匹配就够，量小但精准 → distribution 只判 2；
    # 零成本纯工具；只要 SketchUp 还在就有需求 → 常青。
    "downconvert-sketchup-files": (3, 2, 1, 2),

    # Mac 动态壁纸：内容制作；壁纸获客极卷且付费转化低 → distribution 4；
    # 内容制作有成本但不垫资；需求常青，只是长期没人付费。
    "macwall": (2, 4, 2, 2),

    # 照片变 AI 变脸视频：人脸动画生成；情感类消费靠投放 → 4；
    # 视频生成算力是真成本；这一波 AI 视频窗口。
    "growingupvideo": (3, 4, 3, 4),

    # Mac 文件搜索：文件系统索引 + 性能优化，这批里技术最硬的一条 → 4；
    # 极窄的 Mac 效率工具市场，靠小众口碑 → 4；一个人一台电脑 → capital 1；
    # Spotlight 一直不够用，常青。
    "homy": (4, 4, 1, 2),

    # VPS 托管：运维自动化；获客只能靠低价 → 4；
    # 要先备机器与带宽 → capital 5（全库最高）；需求常青但被云厂商挤压。
    "stealth-rdp": (3, 4, 5, 2),

    # 无脸视频代运营：技术不难（AI + 剪辑流水线）；B2B 靠案例与口碑 → 3；
    # 算力 + 人力；AI 内容降本这一波窗口。
    "white-glove-content": (2, 3, 2, 4),

    # AI 人宠肖像：成熟图像方案 → 2；宠物内容自带流量，但电商转化要打磨 → 3；
    # 算力按单走，不垫资；AI 肖像窗口（淘宝早有人工版）。
    "pet-plus-us": (2, 3, 2, 4),

    # 戒除习惯社区：社区 + 打卡技术简单 → 2；靠宗教社群裂变，自带传播 → 2；
    # 不垫资；需求常青，与文化绑定。
    "wagiff-app": (2, 2, 2, 2),

    # 保险报价工具：费率引擎 + 行业规则 → 3；
    # 保险代理人是极难触达的下沉群体，且要机构自上而下推 → distribution 5；
    # 不垫资；常青。
    "quotedeck": (3, 5, 2, 2),
}


def main():
    ap = argparse.ArgumentParser(description="补缺失的 replicability 四维")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    args = ap.parse_args()

    with io.open(CASES, "r", encoding="utf-8", newline="") as f:
        cases = json.loads(f.read())

    patched, no_rule = [], []
    for c in cases:
        if c.get("replicability"):
            continue
        cid = c.get("id")
        dims = NEW.get(cid)
        if not dims:
            no_rule.append(cid)
            continue
        c["replicability"] = dict(zip(DIMS, dims))
        patched.append(cid)

    print("补了 %d 条：%s" % (len(patched), ", ".join(patched)))
    if no_rule:
        print("[!] 以下 %d 条没写判定，仍然缺：%s" % (len(no_rule), ", ".join(no_rule)))

    if args.dry_run:
        print("\n--dry-run：未写入")
        return 0

    # 与 cases.json 现有格式一致：indent=2、CRLF、无末尾换行
    with io.open(CASES, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(cases, ensure_ascii=False, indent=2).replace("\n", "\r\n"))
    print("已写入 data/cases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
