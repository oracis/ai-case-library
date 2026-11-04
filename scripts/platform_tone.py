#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九平台调性单一源（2026-11-04 用户：「按照每个平台的调性编写文稿」）。

此前 `gen_platform_md.py` 五个 builder 只是把同一批字段换个栏目名重排，
开头共用 `_head()`（`# 标题 / verdict / 公开口径：…`），结尾共用 `_tail_note()`；
跨五平台读起来是同一篇稿换皮 —— 这正是 CSDN《社区内容创作规范》一.3
「批量低质」、知乎 2026-04 治理「AI 批量拼凑」、腾讯云「技术无关内容」
驳回单共同的形状。实测 11-04：`tencentcloud/cod-dex.md` 与
`aliyun/cod-dex.md` 逐字相同，只差水印里的平台名。

本模块把「调性」写成**可执行规范**：每个平台有自己的
  · title_style  标题句式（问句 / 判断句 / 工程取舍 / 成本账 / 白描）
  · opening      开场怎么说（结论先行 / 第一人称 / 场景 / 工程约束）
  · sections     按什么顺序讲、小标题怎么起
  · closing      落在哪里（工程结论 / 边界 / 判断）
  · forbid       该平台绝不能出现的语气或词

渲染器只有一个（`gen_platform_md.render`），平台差异全在这张表里；
`retone_drafts.py` 用同一张表体检存量稿，避免「生成器改了、存量稿还是老调」。

⚠ 全部只读：不改台账、不调 LLM、不编事实。所有事实字段来自 cases.json。
"""

# 每个平台的规范。sections 的 value 是 ctx 的键，不是 cases.json 的字段名，
# 由 gen_platform_md.build_ctx() 统一映射（字段可能是 list / None）。
TONE = {
    "wechat": {
        "label": "公众号「万物解释者」",
        "voice": "长文解释体：从一个具体反差讲起，把机制讲透，有作者在场但克制",
        "title_style": "plain",
        "opening": "今天拆一个海外小生意，收入数字都是公开可核验的。\n\n{verdict}\n\n它干什么、钱从哪来、做到多大、值不值得抄，下面一篇讲完。",
        "sections": [("它是干什么的", "what"), ("钱从哪来", "money"),
                     ("它到底做到多大", "metric"), ("它为什么能成", "why"),
                     ("能搬走的部分", "playbook")],
        "closing": "公开信息能支撑的判断就到这里；数字都要带时点看，别只拿一个数讲故事。",
        "forbid": ["索引站口气", "自家域名", "批量/栏目水印", "把事实写成作者的业绩"],
        "limit": 64,
    },
    "toutiao": {
        "label": "今日头条",
        "voice": "结论和数字前置、口语短句、一个完整分句；禁标题党但要有钩子",
        "title_style": "plain",
        "opening": "{verdict}\n\n下面把这笔账拆开说。",
        "sections": [("它是干什么的", "what"), ("钱从哪来", "money"),
                     ("做到多大", "metric"), ("为什么能成", "why")],
        "closing": "数字都带时点，看增长别混用口径。",
        "forbid": ["悬空尾巴", "口径旁注", "标题党"],
        "limit": 30,
    },
    "xhs": {
        "label": "小红书",
        "voice": "第一人称、轻、短段落；⚠ 不能把案例事实写成「我赚了」",
        "title_style": "plain",
        "opening": "翻到一个挺有意思的小产品，记一下。\n\n{verdict}",
        "sections": [("它做什么", "what"), ("钱从哪来", "money"),
                     ("我觉得能学的点", "playbook")],
        "closing": "数字都是公开口径，带时点看～",
        "forbid": ["第一人称编造业绩", "金额堆砌", "长段落"],
        "limit": 20,
    },
    "bilibili": {
        "label": "B站专栏",
        "voice": "面向开发者的拆解感，可以有梗但克制，讲清楚「为什么这么设计」",
        "title_style": "plain",
        "opening": "这次拆一个结构挺干净的小产品。\n\n{verdict}",
        "sections": [("它到底是什么", "what"), ("怎么收到的钱", "money"),
                     ("值得拆的地方", "playbook"), ("边界在哪", "why")],
        "closing": "结论就这些，数字带时点，别拿单个数字下判断。",
        "forbid": ["索引站口气", "自家域名", "喊口号"],
        "limit": 80,
    },
    "zhihu": {
        "label": "知乎",
        "voice": "先给结论再展开论证，克制、不煽情，不写「你也能做」",
        "title_style": "question",
        "opening": "先说结论：{verdict}\n\n下面分三层讲：它替代了什么、钱从哪里来、这套打法在什么前提下才成立。",
        "sections": [("它替代了什么", "what"),
                     ("收入结构怎么成立", "money"),
                     ("哪些做法经得起推敲", "playbook"),
                     ("什么情况下搬不动", "why")],
        "closing": "以上是公开信息能支撑的判断。{name} 的口径旁注和时点差异，正文里都标了，引用时别只截一个数。",
        "forbid": ["导流/联系方式/二维码", "营销 CTA", "栏目名式空壳标题", "同一个海外小产品怎么赚钱"],
        "limit": 100,
    },
    "cnblogs": {
        "label": "博客园",
        "voice": "技术随笔，第一人称「我」，平实、不喊口号；先记形态，再记边界",
        "title_style": "cost",
        "opening": "翻公开资料的时候顺手记一下这个产品。我关心的不是它卖多少钱，是它的形态里有哪些地方值得留意。\n\n{verdict}",
        "sections": [("先记形态", "what"),
                     ("我留意到的几个约束", "playbook"),
                     ("公开数字怎么读", "metric"),
                     ("成立条件与边界", "why")],
        "closing": "记到这里。{name} 的数字是带时点的快照，不是现值；要引用最好回源核一遍。",
        "forbid": ["标题带金额", "营销套话", "把 `$` 转义（博客园按原样渲染）"],
        "limit": 100,
    },
    "csdn": {
        "label": "CSDN",
        "voice": "工程向：问题 → 实现动作 → 复盘；商业叙述压到最后一句背景",
        "title_style": "engineering",
        "opening": "这篇不谈增长故事，只看**如果真要把这类产品做出来，工程上要处理哪些细节**。\n\n公开资料能核实的形态是：{what_short}",
        "sections": [("先看约束", "what"),
                     ("拆几个能复用的设计动作", "playbook"),
                     ("前提不成立就抄不了", "why"),
                     ("数据口径与商业化", "money")],
        "closing": "小结：上面几处都是实现层面的取舍，不是玩法。{name} 未公开的内部实现不作推断。",
        "forbid": ["标题带金额/MRR", "两个 `$` 夹一段正文", "与别平台同骨架（一.3 批量低质）"],
        "limit": 100,
    },
    "tencentcloud": {
        "label": "腾讯云开发者社区",
        "voice": "纯技术向：架构选择 / 协议格式 / 并发幂等 / 成本对选型的约束，可贴代码",
        "title_style": "engineering",
        "opening": "把公开可核实的形态当作一组工程约束来看：{what_short}\n\n下面按实现环节拆，商业数据只作一句背景。",
        "sections": [("问题边界", "what"),
                     ("可复用的设计动作", "playbook"),
                     ("适用前提", "why"),
                     ("数据与口径", "metric")],
        "closing": "本文只讨论公开可核实的形态；{name} 未公开的技术实现不作推断。收尾落在工程结论上，不落在「所以你也能做」。",
        "forbid": ["标题带金额/MRR/月入", "变现算术", "可复制的玩法", "与阿里云同命题同骨架"],
        "limit": 100,
    },
    "aliyun": {
        "label": "阿里云开发者社区",
        "voice": "云原生视角：弹性 / 可观测 / 成本 / 多租户，从架构约束切入，不与腾讯云同题",
        "title_style": "cost",
        "opening": "换个角度，从资源与弹性的账本看这个形态：{what_short}\n\n这里不谈定价策略，只谈负载形状会怎么反过来约束架构。",
        "sections": [("负载长什么样", "what"),
                     ("资源与成本的取舍", "playbook"),
                     ("公开数字的时点", "metric"),
                     ("判断与适用边界", "why")],
        "closing": "结论：负载形状决定成本结构，成本结构再决定哪些设计能长期扛住。{name} 的数字是快照，引用带时点。",
        "forbid": ["标题带金额", "与腾讯云稿逐字相同", "编造未公开的内部实现"],
        "limit": 100,
    },
}

# 五个文本平台由 gen_platform_md 生成；另四个（社交/图文）各有独立生成器，
# 调性规范写在上面，供体检与人工改稿对照。
TEXT_PLATFORMS = ("zhihu", "cnblogs", "csdn", "tencentcloud", "aliyun")


def profile(platform):
    return TONE.get(platform, TONE["zhihu"])


def skeletons():
    """返回 {platform: 小标题元组}，给「跨平台不得同骨架」的回归锁用。"""
    import re
    return {p: tuple(re.sub(r"\{.*?\}", "", h).strip()
                     for h, _ in TONE[p]["sections"])
            for p in TONE}


def tone_issues(platform, title, body):
    """只读体检：把平台调性的硬禁忌落成可判定项（补 publish_guard 的空白）。

    只查「一定不该出现」的形状；文风好坏不做机器判定。
    """
    import re
    p = profile(platform)
    out = []
    if platform == "zhihu":
        if re.search(r"加我|私信我|扫码|二维码|加群|进群|关注我|领取资料", body):
            out.append("知乎导流/联系方式")
        if re.search(r"^#\s*一个海外小产品怎么赚钱", body, re.M):
            out.append("知乎空壳模板标题")
    if platform in ("csdn", "tencentcloud", "aliyun", "cnblogs", "zhihu"):
        if re.search(r"[$¥]\s*\d|\d[\d,.]*\s*(?:万美元|美元|万元|MRR|ARR|月入|月收入)", title):
            out.append("技术平台标题带金额")
    if platform == "tencentcloud":
        if re.search(r"变现算术|可复制的玩法|一个人能不能做|月入", body):
            out.append("腾讯云商业拆解体裁（有驳回单）")
    if platform == "cnblogs" and "\\$" in body:
        out.append("博客园不该转义 `$`")
    if len(title) > p["limit"]:
        out.append("标题超 %d 字" % p["limit"])
    return out


def body_fingerprint(text):
    """跨平台同稿检测：去掉标题/水印/空白后的稳定指纹。

    11-04 实测 `tencentcloud/cod-dex.md` 与 `aliyun/cod-dex.md` 只差水印里的
    平台名，正文指纹完全一致 —— 这类「换皮复用」必须用机器查，不靠肉眼。
    """
    import hashlib
    import re
    s = re.sub(r"^#{1,3}\s+.*$", "", text or "", flags=re.M)
    s = re.sub(r"^>\s*平台[:：].*$", "", s, flags=re.M)
    s = re.sub(r"\s+", "", s)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    for k, v in TONE.items():
        print("%-14s %-10s %-8s %s" % (k, v["title_style"], v["limit"], v["voice"][:34]))
    print("\n各平台骨架：")
    for k, s in skeletons().items():
        print("  %-14s %s" % (k, " / ".join(s)))
