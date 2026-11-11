#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏厅系列「去站外推广 + 改讲技术」改造器（2026-11-02）。

## 为什么要改

11-02 22:26 头条因「包含营销推广类信息」判**禁言 30 天**（不是 7 天，从未在
11-09 到期），点名 2 篇游戏厅稿。诱因是行为＋内容双特征：19:49–22:10 约 2 小时
连发 11 篇同栏目、同结构、同一导流域名的稿子，且**正文在给自家索引站导流**：

    · 站点详情页：<a href="https://games.ydtgo.top/#/game/390-minetest">…</a>
    想自己翻？完整索引都在这里：<a href="https://games.ydtgo.top">…</a>

而且整个模板的叙事框架就是「源码索引站」——kicker 写「开源游戏源码索引」、
第一节标题写「这是源码索引，不是游戏下载站」、档案里带「档案编号」、版权里写
「本站只做索引与归类」。对读者（和平台审核）来说，这是一篇**推广稿**，不是
一篇讲游戏的文章。

用户 11-02 拍板：**不再推广个人网站，就介绍游戏、介绍游戏用到的技术。**

## 改什么（逐行确定性替换，不重写正文）

| 原样 | 改成 |
|---|---|
| kicker「开源游戏源码索引 · 每期只讲一款游戏」 | 「开源游戏拆解 · 它用的是什么技术」 |
| 第一节标题「先说清楚：这是源码索引，不是游戏下载站」 | 「它用什么技术做的」 |
| 该节正文（「本文介绍的项目指向一个真实存在的源码仓库…」） | 每款游戏的技术拆解段落（`TECH` 表） |
| 「· 档案编号：…」 | 删（站点内部编号，读者无用） |
| 「· 站点详情页：<a …ydtgo.top…>」 | 删 |
| 「想自己翻？完整索引都在这里：<a …ydtgo.top…>」 | 删 |
| 节标题「档案」 | 「基本信息」 |
| 「· 本站只做索引与归类，不托管…」 | 「· 本文只讨论项目与代码，不托管…」 |
| 「收录项目版权归各自作者所有；你是作者且不希望被收录，可以联系删除。」 | 「· 代码与素材版权归各自作者所有，本文仅作技术介绍。」 |

任何含 `ydtgo.top` 的行一律整行删（兜底：以后模板再加导流行，跑一遍就掉）。

## 用法

    python scripts/game_rebrand.py --dry     # 只看会改什么
    python scripts/game_rebrand.py --go      # 真改（先备份到 tmp/rebrand_bak_<ts>/）
    python scripts/game_rebrand.py --go --only ext-ep-011   # 只改某期
"""
import argparse
import glob
import os
import re
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "out", "articles")
TT = os.path.join(ROOT, "out", "toutiao")

# ---------------------------------------------------------------- 技术段落
# 口径：只写**可核实的技术事实**（语言/引擎/渲染方式/数据结构/内容怎么定义），
# 不写版本号、不写收入、不写「多少行代码」这类无法核对的数字。
TECH = {
    "ext-ep-001-super-mario-bros":
        "这是跑在浏览器里的 JavaScript 复刻：用 Backbone 拆分游戏对象与状态，画面靠 HTML5 "
        "Canvas 逐帧重绘，跳跃物理、碰撞判定、关卡数据全在前端代码里。好处是没有原生二进制、"
        "打开网页就能跑，也意味着整个游戏循环都在源码里摊开给你看——想改重力或跑速，改一个常量就行。",
    "ext-ep-002-2048":
        "原版是一位意大利开发者用 JavaScript 写的网页小游戏：4×4 棋盘是 DOM 元素，滑动动画交给 "
        "CSS transition，真正有分量的只有「合并同类项 + 计分 + 判负」这几百行逻辑。它常被当作"
        "「读懂一个完整游戏循环」的样本——状态、输入、动画、重开，一样不缺。",
    "ext-ep-003-freedoom":
        "要注意：它本身**不是引擎**，而是一整套自由授权的游戏资源（WAD：贴图、音效、关卡地图）。"
        "想跑起来得配一个 Doom 引擎，比如 PrBoom+ 或 GZDoom。技术看点正是这种「资源与引擎彻底"
        "分离」的老架构——同一份 WAD 能在十几个不同引擎里复用，这也是它能在没有源码的情况下"
        "被重制出来的前提。",
    "ext-ep-004-quake":
        "id Software 用 C 写成的引擎，2000 年前后以 GPL 公开源码。它把渲染、网络同步、BSP 地图"
        "编译拆成几块相对独立的代码：地图预编译成可见性数据，联机时只同步增量状态。后来大量"
        "联机 FPS 的网络模型都能追到这套实现，读它就是读 3D 联机游戏的祖宗代码。",
    "ext-ep-005-supertux":
        "C++ 写的 2D 横版游戏，窗口、图形、声音、手柄输入交给 SDL 处理，游戏逻辑自己实现。"
        "关卡不是硬编码的，而是用 S-expression 文本格式描述（`.stl` 文件），项目还自带关卡"
        "编辑器——想加一关，不用碰代码，摆完存盘即可。这种「内容与逻辑分离」是它方便二创的关键。",
    "ext-ep-006-nethack":
        "纯 C 实现，画面是终端里的 ASCII 字符而不是贴图，地牢在每次开局时由算法现场生成。它的"
        "复杂不在渲染，而在**规则引擎**：物品、怪物、地形、状态几乎可以任意组合，两两之间都有"
        "交互。几十年来一直有人拿它做 AI 实验（比如让程序自动通关），就是因为规则够规整。",
    "ext-ep-007-openttd":
        "用 C++ 从《运输大亨豪华版》重写而来：游戏逻辑全部开源，但原版美术与音效有版权，社区"
        "另做了一套自由素材（OpenGFX / OpenSFX）顶上。技术看点是那张超大地图上的寻路——列车"
        "信号、车站调度、货运路径都要在毫秒级算完，读这块代码等于读一遍图搜索的工程实现。",
    "ext-ep-008-mindustry":
        "Java + libGDX：同一份代码可以打包成桌面版、安卓版，也能在浏览器里跑。地图是网格，"
        "传送带、管道、工厂构成有向图，物流与生产在每帧里推一遍；塔防的敌人寻路跑在同一套网格"
        "上。想学「怎么让几千个实体同时跑而不卡」，它的调度与数据结构是很实用的参考。",
    "ext-ep-009-lichess":
        "后端 Scala（Play 框架）+ MongoDB，前端 TypeScript；对局靠 WebSocket 双向推消息，异常"
        "处理与棋规判定都在服务端。它的架构把「实时对局」「AI 分析」「开局库」拆成互相独立的"
        "部分，所以能同时撑住大量在线对局——想学实时 Web 应用的工程分层，这份代码很值得抄结构。",
    "ext-ep-010-cataclysm-dark-days-ahead":
        "C++ 写的 Roguelike，最大的特点是**内容全部用 JSON 定义**：物品、怪物、配方、地图、"
        "变异，代码只提供规则引擎与加载器。想加一把新武器，写一段 JSON 就行，不必改源码。"
        "这种「引擎小、数据大」的做法让它积累了惊人的内容量，也是长期社区维护能撑住的原因。",
    "ext-ep-011-minetest":
        "引擎用 C++：体素世界的数据结构、区块加载、渲染、网络同步都在这一层；玩法层用 Lua——"
        "加方块、加工具、写一整套新游戏模式，全都通过 Lua API 挂进去。所以同一个引擎能跑出"
        "完全不同的游戏。想学「怎么把引擎和玩法解耦」，这是开源沙盒里最典型的样本。",
}

# ---------------------------------------------------------------- 替换规则
KICKER_OLD = "开源游戏源码索引 · 每期只讲一款游戏"
KICKER_NEW = "开源游戏拆解 · 它用的是什么技术"
SEC1_OLD = "先说清楚：这是源码索引，不是游戏下载站"
SEC1_NEW = "它用什么技术做的"
SEC2_OLD = ">档案<"
SEC2_NEW = ">基本信息<"
LEGAL_OLD = "· 本站只做索引与归类，不托管任何游戏文件，也不提供素材包。"
LEGAL_NEW = "· 本文只讨论项目与代码，不托管任何游戏文件，也不提供素材包。"
CR_OLD = "· 收录项目版权归各自作者所有；你是作者且不希望被收录，可以联系删除。"
# 页脚那句「全部指向源码仓库」还是索引站口气，一并中性化
FOOT_OLD = "全部指向源码仓库，需自行编译"
FOOT_NEW = "源码都在各自仓库里，需自行编译"
CR_NEW = "· 代码与素材版权归各自作者所有，本文仅作技术介绍。"

# 需要整行删掉的（自家域名 + 站点内部编号）
DROP_PAT = [
    re.compile(r"ydtgo\.top", re.I),                 # 站外导流：整行
    re.compile(r"·\s*档案编号[：:]"),
    re.compile(r"·\s*站点详情页[：:]"),
]
# 本节样板段（技术段要替换掉它）
BOILER_PLATE = "本文介绍的项目指向一个真实存在的源码仓库"


def slug_of(path):
    b = os.path.basename(path)
    b = re.sub(r"\.html?$", "", b)
    b = re.sub(r"-b2$", "", b)
    if b in ("article", "index"):                 # out/toutiao/<cid>/article.html
        b = os.path.basename(os.path.dirname(path))
    return b


def tech_for(path):
    return TECH.get(slug_of(path))


def rebrand_arcade(s):
    """「街机 78 款」篇（ext-top-arcade-78）专用：它不是单款游戏，而是一份名单。

    它的问题是**通篇以「索引站」自我指涉**：kicker 写「全书 576 条 26 类」、
    正文写「完整 78 款、全书 576 条见文末站点」、结尾挂一条「完整 576 条 · 26 类，
    都在这里：<a href=…>」。名单本身是内容，保留；指向站点的表述全部拆掉。
    """
    reps = [
        ("开源游戏源码索引 · 街机 78 款 · 全书 576 条 26 类",
         "开源街机游戏 · 每一款都能读到源码"),
        ("先说清楚：这是源码索引，不是游戏下载站",
         "先说清楚：这里讲的是代码，不是下载站"),
        ("这不是一份「下载即玩」的游戏合集，而是一份<strong>开源游戏源码索引</strong>："
         "576 个开源项目，按玩法与引擎分成 26 类，每一条都指向一个真实存在的源码仓库"
         "（绝大多数在 GitHub）。",
         "这不是一份「下载即玩」的游戏合集，而是一批<strong>能读到源码的开源街机游戏</strong>："
         "按玩法与引擎分类，每一条都指向一个真实存在的源码仓库（绝大多数在 GitHub）。"),
        ("按原文编号排序；完整 78 款、全书 576 条见文末站点。", "按编号排序。"),
        ("怎么用这份索引", "怎么读这些代码"),
        ("（开源游戏源码索引 · 街机 78 款）", "（开源街机游戏 78 款）"),
        (SEC1_OLD, SEC1_NEW),          # 兜底（同形但文案不同的情况）
        (LEGAL_OLD, LEGAL_NEW),
        (CR_OLD, CR_NEW),
        (FOOT_OLD, FOOT_NEW),
    ]
    out, stat = [], {"drop": 0, "kicker": 0, "sec": 0, "tech": 0,
                     "item": 0, "legal": 0}
    for line in s.splitlines():
        raw = line
        if any(p.search(raw) for p in DROP_PAT) or "example.com" in raw:
            stat["drop"] += 1
            continue
        for old, new in reps:
            if old in raw:
                raw = raw.replace(old, new)
                stat["kicker" if old.startswith("开源游戏源码索引") else "legal"] += 1
        out.append(raw)
    return "\n".join(out) + ("\n" if s.endswith("\n") else ""), stat


def rebrand_text(s, cid):
    """返回 (新文本, 改动计数 dict)。逐行处理，不碰正文其他内容。

    结构上做两件事：
    1. 删掉「先说清楚：这是源码索引…」这节（标题 + 样板段）——那是索引站的口径；
    2. 在「基本信息」之前插入「它用什么技术做的」一节（标题样式沿用原 h2、
       段落样式沿用原样板段的 <p style>），让叙述顺序变成
       **它是什么 → 怎么玩 → 它用什么技术做的 → 基本信息**。
    """
    tech = TECH.get(cid)
    out, stat = [], {"drop": 0, "kicker": 0, "sec": 0, "tech": 0,
                     "item": 0, "legal": 0}
    h2_style, p_style = None, None
    for line in s.splitlines():
        raw = line
        # 1) 站外导流 / 站点内部编号：整行删
        if any(p.search(raw) for p in DROP_PAT):
            stat["drop"] += 1
            continue
        # 2) kicker
        if KICKER_OLD in raw:
            raw = raw.replace(KICKER_OLD, KICKER_NEW)
            stat["kicker"] += 1
        # 3) 第一节标题：整行删（内容挪到后面「它用什么技术做的」）
        if SEC1_OLD in raw:
            m = re.match(r'\s*(<h2[^>]*>)', raw)
            if m:
                h2_style = m.group(1)
            stat["sec"] += 1
            continue
        # 4) 第一节样板段：整行删（替换成技术段，位置挪到「基本信息」前）
        if BOILER_PLATE in raw and tech:
            m = re.match(r'\s*(<p[^>]*>)', raw)
            if m:
                p_style = m.group(1)
            continue
        # 5) 节标题「档案」→「基本信息」，并在它**前面**插入技术节
        if SEC2_OLD in raw and tech:
            out.append((h2_style or '<h2 style="font-size:18px;line-height:1.5;'
                                    'color:#111;margin:30px 0 12px;'
                                    'padding-left:10px;'
                                    'border-left:4px solid #2f6fed">')
                       + SEC1_NEW + "</h2>")
            out.append((p_style or '<p style="line-height:1.75;font-size:16px;'
                                   'color:#333;margin:0 0 16px">') + tech + "</p>")
            stat["tech"] += 1
            raw = raw.replace(SEC2_OLD, SEC2_NEW)
            stat["item"] += 1
        # 6) 版权两句
        if LEGAL_OLD in raw:
            raw = raw.replace(LEGAL_OLD, LEGAL_NEW)
            stat["legal"] += 1
        if CR_OLD in raw:
            raw = raw.replace(CR_OLD, CR_NEW)
            stat["legal"] += 1
        if FOOT_OLD in raw:
            raw = raw.replace(FOOT_OLD, FOOT_NEW)
            stat["legal"] += 1
        out.append(raw)
    return "\n".join(out) + ("\n" if s.endswith("\n") else ""), stat


def targets(only=None):
    fs = sorted(glob.glob(os.path.join(ART, "ext-ep-*.html")))
    fs += sorted(glob.glob(os.path.join(ART, "ext-top-arcade-78.html")))
    fs += sorted(glob.glob(os.path.join(TT, "ext-ep-*", "article.html")))
    fs += sorted(glob.glob(os.path.join(TT, "ext-top-arcade-78", "article.html")))
    if only:
        fs = [f for f in fs if only in os.path.basename(os.path.dirname(f))
              or only in os.path.basename(f)]
    return fs


def main(argv=None):
    ap = argparse.ArgumentParser(description="游戏厅：去站外推广 + 改讲技术")
    ap.add_argument("--go", action="store_true", help="真改（默认 dry-run）")
    ap.add_argument("--only", default=None, help="只处理某期（如 ext-ep-011）")
    ap.add_argument("--bak", default=None, help="备份目录（默认 tmp/rebrand_bak_<ts>）")
    args = ap.parse_args(argv)

    files = targets(args.only)
    if not files:
        print("没有匹配的文件。")
        return 1
    bak = args.bak or os.path.join(ROOT, "tmp",
                                   "rebrand_bak_" + time.strftime("%H%M%S"))
    tot = {}
    changed = []
    for f in files:
        try:
            s = open(f, encoding="utf-8").read()
        except OSError as e:
            print("  ✗ 读不到 %s (%s)" % (f, e))
            continue
        cid = slug_of(f)
        new, stat = (rebrand_arcade(s) if cid.startswith("ext-top-arcade")
                     else rebrand_text(s, cid))
        same = (new == s)
        flag = "·" if same else "→"
        rel = os.path.relpath(f, ROOT)
        print("%s %-52s drop=%d kicker=%d sec=%d tech=%d item=%d legal=%d"
              % (flag, rel, stat["drop"], stat["kicker"], stat["sec"],
                 stat["tech"], stat["item"], stat["legal"]))
        for k, v in stat.items():
            tot[k] = tot.get(k, 0) + v
        if not same:
            changed.append((f, new))
        if not same and not TECH.get(cid):
            print("    ⚠ %s 没有技术段落（TECH 表缺这一条），只做了去推广" % cid)

    print("\n合计：%d 个文件待改，改动 %s"
          % (len(changed), {"整行删": tot.get("drop", 0),
                            "kicker": tot.get("kicker", 0),
                            "节标题": tot.get("sec", 0),
                            "技术段": tot.get("tech", 0),
                            "档案": tot.get("item", 0),
                            "版权": tot.get("legal", 0)}))
    if not changed:
        print("都已经是新口径了。")
        return 0
    if not args.go:
        print("\n（dry-run，没写盘。加 --go 执行）")
        return 0

    os.makedirs(bak, exist_ok=True)
    for f, new in changed:
        rel = os.path.relpath(f, ROOT)
        dst = os.path.join(bak, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(f, dst)
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(new)
    print("\n✓ 已改 %d 个文件，原文件备份在 %s" % (len(changed), bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
