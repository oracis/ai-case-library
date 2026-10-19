#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 29 条案例的 replicability 从旧维度键换成现行维度键。

背景（这是代码里早就记载过的坑，2026-10-15 又踩了一次）：
    score_solo_fit 要的是 {tech, capital, distribution, timing}，
    而 fill_case_text 的正文素材里一直写着旧键名
    {tech, data, sales, domain} —— 非空，所以 --check 报「缺 0 条」，
    下游却算不出 solo_fit / composite。

    上一次踩的是 harperai / easymix（见 fit_ready.missing_replicability
    的注释），这次是放宽门槛后新晋升的 29 条。教训：四维评分的键名
    不能靠「照着上一条抄」，抄之前先确认下游真正读的是哪套。

换算口径（1 分＝最容易，5 分＝最难）：
    tech          ← 旧 tech（技术门槛，语义一致，直接沿用）
    distribution  ← 旧 sales（销售/获客门槛）
    capital       ← 逐条判：要不要垫资/备货/预购算力或通道
    timing        ← 逐条判：这门生意是否只存在于当前这个窗口期
"""
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "data", "cases.json")

# id: (capital, timing)
NEW_DIMS = {
    # 纯软件、自筹、无备货 —— 资金门槛低；但吃 AI 窗口期
    "alchie": (2, 4),                  # 小语种 LinkedIn 工具：靠 AI 生成窗口
    "adelante-ltd": (2, 4),            # 托管式 AI 客服：不垫资，但吃 AI 落地潮
    "storyshort": (3, 4),              # 视频生成：算力要预付，赛道正热
    "analytics-mates": (2, 5),         # GA4 迁移窗口造出来的需求，窗口一过就没
    "brevilabs": (2, 5),               # AI 咨询：完全靠这一波企业 AI 预算
    "cartboss": (3, 2),                # 短信通道要预购，但弃单召回是长期刚需
    "based-labs-ai": (3, 4),           # 生成算力成本高，吃 AI 视频窗口
    "draftly": (2, 4),                 # AI 建站，窗口期明显
    "ae-intelligence": (2, 5),         # AI 检测对抗：规则一变需求就消失
    "abmaxx": (2, 3),                  # App + 模型调用，健身需求常青但有季节
    "backpedal-ltd": (4, 2),           # 要备 GPS 硬件 + 线下找回团队，重资金
    "adspirer": (2, 4),                # 广告 AI 托管，吃 AI 窗口
    "corsidia": (2, 2),                # 垂直行业服务，常青
    "avenue-ticketing-inc": (3, 2),    # 票务要过账/垫付，但需求常青
    "autocontent-api": (2, 4),         # AI 内容 API
    "chessigma": (2, 2),               # 国际象棋教学，常青
    "backlinker-ai": (2, 3),           # SEO 外链，规则会变但需求长期
    "ai-toolbox": (2, 4),              # AI 工具聚合
    "amicited": (2, 4),                # AI 引用/检索，吃 AI 窗口
    "angel-match": (2, 3),             # 投资人数据库，常青但小众
    "calendesk": (2, 2),               # 预约 SaaS，常青
    "capgo": (2, 2),                   # 开发者工具，常青
    "applyall": (2, 2),                # 求职服务，常青
    "audn-ai": (3, 4),                 # 音频 AI，算力 + 窗口
    "hypit-ai": (2, 4),                # AI 内容
    "apiframe": (3, 4),                # 视频生成 API，算力预付
    "chatseo": (2, 4),                 # AI SEO
    "brawler": (2, 4),                 # AI 内容
    "cod-dex": (2, 2),                 # 编程教育，常青
}


def main():
    with io.open(CASES, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    cases = json.loads(raw)
    changed, skipped = [], []
    for c in cases:
        rep = c.get("replicability") or {}
        if set(rep.keys()) != {"tech", "data", "sales", "domain"}:
            continue
        cid = c.get("id")
        dims = NEW_DIMS.get(cid)
        if not dims:
            skipped.append(cid)
            continue
        capital, timing = dims
        c["replicability"] = {
            "tech": rep.get("tech"),
            "distribution": rep.get("sales"),
            "capital": capital,
            "timing": timing,
        }
        changed.append(cid)

    # cases.json 原生格式：indent=2 + CRLF + 无末尾换行（与 fill_case_text 同规）
    out = json.dumps(cases, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with io.open(CASES, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("换算 %d 条；未匹配到人工判据（保持原样）%d 条：%s"
          % (len(changed), len(skipped), "、".join(skipped) or "无"))
    print("新维度示例：%s" % json.dumps(
        next(c["replicability"] for c in cases if c.get("id") == "alchie"),
        ensure_ascii=False))


if __name__ == "__main__":
    main()
