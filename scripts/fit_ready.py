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

    # ---- 2026-09-25 补的一批（TrustMRR 挂牌标的为主）----

    # 跨境云通信：难点不在写客户端，在接上游运营商、拿号码资源与过合规；
    # 获客靠「收不到验证码」这类被动搜索，不投广告；启动要先备号码与预付费；
    # 需求长期存在，不存在窗口问题。
    "voklit": {"tech": 4, "distribution": 3, "capital": 3, "timing": 2},

    # LinkedIn AI 外呼：技术难度在反封禁与拟人化节奏，不在 AI 话术；
    # 获客靠创始人 X 上的垂直影响力与产品口碑；启动轻；
    # 但 LinkedIn 自动化赛道已经很挤，晚进者要付更多封号成本。
    "prosp": {"tech": 3, "distribution": 3, "capital": 2, "timing": 4},

    # 圣经小组件 App：widget 开发门槛低，内容是现成经文；获客靠 ASO 自然量；
    # 启动成本只有一个开发者账号；时机不早不晚（信仰类小组件已有一批，
    # 但垂直到「女性」这一格仍有位置）。
    "divine-widgets": {"tech": 2, "distribution": 2, "capital": 1, "timing": 3},

    # AI 可见性监测：要对接多模型、做提示词跑批与报告，工程量中等；
    # 获客靠 SEO 与新话题红利；启动轻；时机正好——品牌刚意识到这个问题。
    "promptmonitor-io": {"tech": 3, "distribution": 3, "capital": 2, "timing": 2},

    # LinkedIn 内容代写 + 自动发布：web + iOS + Android 三端，工程量不小；
    # 获客要打进「教练/顾问」这个分散人群，是最重的一维；
    # 启动养了 2–5 人的团队；赛道已晚。
    "uplinked-b-v": {"tech": 4, "distribution": 4, "capital": 3, "timing": 4},

    # 单品 DTC：建站与支付都是现成能力，技术不是门槛；
    # 获客完全靠 Meta 投放，可复制但要天天调；
    # 启动要备货与广告金；礼品有季节性，进场时机中等。
    "le19emetrou": {"tech": 2, "distribution": 3, "capital": 2, "timing": 3},

    # AI agent 的搜索/爬取 API：聚合上游搜索源 + MCP 接入，工程量中等；
    # 获客靠开发者社区与 SEO；启动只有服务器与上游调用费；
    # 窗口正开着——agent 联网需求刚起量。
    "search1api": {"tech": 3, "distribution": 3, "capital": 2, "timing": 2},

    # ---- 2026-09-30 补的两条 ----

    # AI 求职代理：爬 14 家 ATS / 5 万+ career page 是主要工程量，还要做
    # 简历解析与 0-100 打分；获客靠「海投无效」这个搜索入口，不投广告；
    # 启动只有模型调用费与爬虫带宽；求职赛道已有 Teal/Simplify/Huntr 在前，
    # 窗口偏晚，但它切的是 career page 这段脏数据。
    "mort": {"tech": 3, "distribution": 3, "capital": 2, "timing": 4},

    # 信仰类打卡 App：React Native 双端 + Firebase + RevenueCat，一个人能做完；
    # 获客全靠 App Store / Google Play 自然量 + 社区分发，是四维里最轻的；
    # 启动只有一个开发者账号；内容是现成经文，边际成本为零。时机不早不晚。
    "quran-unlock": {"tech": 2, "distribution": 2, "capital": 1, "timing": 2},

    # ---- 2026-10-11 补：Stripe 深核通过的 7 条 ----
    #
    # 这批的共同点：数字全部由支付网关 API 直读，不靠自报截图，
    # 但 `mrr` 跨度从 $1,521 到 $202,060，**四维评分不能看体量**，
    # 要看「一个人能不能接住这个生意」—— 有的体量大恰恰因为它需要团队。

    # 营销归因（cometly）：技术不难（接各广告平台 API + 多表归因），
    # 真正吃人的是distribution —— 它靠 2 位创始人 + 2 CSM + AE + 实施专员
    # 共 9 人做客户成功，Enterprise 还要专属 solutions engineer。
    # 🚨 四维里 capital 判 5 不是因为要烧钱，而是因为「要养一支交付团队」
    # 本身就是最大的启动成本。timing 判 2：投放规模越大归因越痛，长期存在。
    # ⇒ 这一条是「赛道成立但个人做不了」的典型，四维必须把交付成本算进去。
    "cometly": {"tech": 3, "distribution": 5, "capital": 5, "timing": 2},

    # AI 短视频（vid-ai）：管线全是现成能力拼装（脚本/TTS/素材/合成），
    # 技术不是门槛；获客靠内容平台本身，粘在平台生态里；
    # 启动只需模型调用费；但窗口判5 —— 峰值留存已腰到46%，
    # 且平台自带 AI 剪辑就会关窗，这位置的时间价值在快速归零。
    "vid-ai": {"tech": 3, "distribution": 4, "capital": 2, "timing": 5},

    # LinkedIn 内容增长（podawaa）：技术是常规SaaS；获客要打进
    # 「用 LinkedIn 做获客」这个分散人群，是最重的一维；
    # 启动轻（无付金/资质）；timing 判 5 —— 平台红利在退，
    # 最近完整月已 -15.2%，且平台随时会把分析做进原生。
    "podawaa": {"tech": 3, "distribution": 5, "capital": 2, "timing": 5},

    # KDP 作者工具（publbee）：接关键词数据 + 调大模型，技术轻；
    # 获客全靠 SEO 打「kindle 出版怎么做」这类长尾词，不用投广告；
    # 启动只有模型费与少量带宽；时机判 4 —— 这个位置已经有一批同类工具，
    # 窗口不算早也不算晚，但付费人群的总量本身就有限。
    "publbee": {"tech": 2, "distribution": 3, "capital": 2, "timing": 4},

    # 位图转 SVG（vectosolve）：纯计算 + 格式转换，技术门槛最低；
    # 获客靠搜索长尾（单价 $7 的东西只能靠自然流量）；
    # 启动几乎零成本（计算/存储便宜）；timing 判 2：需求一直在，
    # 但也永远不会变大 —— 天花板等于流量天花板。
    "vectosolve": {"tech": 2, "distribution": 3, "capital": 1, "timing": 2},

    # Shopify 服务端追踪（augora-ai）：技术是像素/转化/去重的工程活，
    # 不难但琐碎；获客靠跨境电商社群与「ROAS 算不准」这个搜索入口；
    # 启动轻；timing 判 3 —— iOS 隐私政策一直在收紧，
    # 需求被政策持续喂养，但政策哪天松了这个优势就没了。
    "augora-ai": {"tech": 3, "distribution": 3, "capital": 2, "timing": 3},

    # AI 建站转 WordPress（wpconvert）：**四维里最轻的一条**——
    # 它是纯工程转换：解析 → 重写 → 部署，一个人能接住（页面标注团队 1 人，
    # 也是这批唯一拿到 `solo_possible` 的）；获客靠「AI 建的站搬不走」
    # 这个明确痛点；启动只有服务器。
    # ⚠️ timing 判 5：需求正绑在上游 AI 建站工具身上 ——
    # v0 / Replicate 自己把导出做好，需求就被上游吃掉。
    "wpconvert-ai-convert-ai-sites-to-wordpress": {"tech": 2, "distribution": 2,
                                                 "capital": 1, "timing": 5},
}


def load_cases():
    with open(CASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def missing_replicability(cases, only=None):
    """缺 replicability 的案例。

    判据必须与 score_solo_fit 的INVERT 完全一致（四个维度齐全且都是 int），
    不能只判非空。2026-10-05 实测过这个洞：harperai / easymix 晋升时写的是
    旧维度键名 {tech, data, sales, domain}，非空所以 --check 报「缺 0 条」，
    但 score_solo_fit 要的是 {tech, capital, distribution, timing}，
    于是它俩算不出 solo_fit、composite 只有 39 条，测试挂了 4 项。

    校验工具报「齐」而下游说「缺」，比直接报错更坏 —— 它让人以为不用管。
    """
    need = set(SF.INVERT.values())
    out = []
    for c in cases:
        cid = c.get("id")
        if only and cid not in only:
            continue
        rep = c.get("replicability") or {}
        if not rep or any(not isinstance(rep.get(k), int) for k in need):
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
        # ⚠️ 原格式保护（2026-10-11 补）。实测 data/cases.json 是
        # **CRLF + indent=2 + 无末尾换行**，而这里原来写的是
        # 默认文本模式 + LF + indent=2 + 末尾补 \n
        # ⇒ Windows 下整个 cases.json 的 diff 全红，而数据只多了几个字段。
        # 同一个坑 harvest.save_json() 犯过、contentpack_ready.py 也犯过。
        # ⚠️ cases.json 有四万行，格式噪音会把真实改动彻底淹没 ——
        # 下次真出问题时就分不清是数据变了还是换行变了。
        raw = open(CASES_PATH, "rb").read()
        nl = "\r\n" if b"\r\n" in raw[:8192] else "\n"
        tail_nl = raw.endswith(b"\n")
        body = json.dumps(cases, ensure_ascii=False, indent=2)
        text = (body + ("\n" if tail_nl else "")).replace("\n", nl)
        with open(CASES_PATH, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print("\n已写入 data/cases.json（%s，格式保持 %s /末尾换行=%s）"
              % (today, "CRLF" if nl == "\r\n" else "LF", tail_nl))

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
