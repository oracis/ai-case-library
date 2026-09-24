#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读者不该看到的「元数据标注」清理。

背景：案例里的数字是从第三方页面（TrustMRR 榜单 / Stripe / RevenueCat / Lemon
Squeezy）抓的，写稿时顺手记一句核对备注，比如

    MRR $2,869；累计收入 $62,352（RevenueCat API 验证，2026-09-17 快照）

这句话是**写给自己的**，对读者没有任何价值：他不需要知道哪天抓的，也不会因为
一个日期更相信这个数字，但它会渗进文章标题、表格行和正文里 —— 把一句干净的
结论拖长，而且会随时间过期（读者看到「09-17 快照」只会想「那现在还准吗」）。

清理规则（刻意做窄）：
    删 —— 带日期的快照标注：
        · 「（Stripe 直连验证，2026-09-17 快照）」→「（Stripe 直连验证）」
        · 「（2026-09-17 快照）」整体删除
        · 「$424,368 MRR · Stripe 验证 · 2026-08-14 最后快照」→ 去掉尾段
        · 「并标注快照时间 Sep 17, 2026。」
    留 —— **不带日期**的「快照」：
        「该连接后来停止更新，所以这是固定快照而非实时值」
        「数字取自 TrustMRR 公开榜单快照」
        「无法从快照区分 MRR 还是累计」
        这些在**说明数字的性质**（是冻结值还是实时值、口径能否确认），是本库
        可信度分档的依据。删掉会改变含义，所以不碰。

用法：
    from text_clean import strip_snapshot_marks, clean_snapshot_marks
    strip_snapshot_marks("MRR $2,869（RevenueCat API 验证，2026-09-17 快照）")
    # => "MRR $2,869（RevenueCat API 验证）"
    clean_snapshot_marks(case)      # 就地清理嵌套 dict/list 里的所有字符串

    from text_clean import end_sentences
    end_sentences(cases)            # 给正文级列表项补结尾句号
                                    # 「卖的是…而不是服务」→「卖的是…而不是服务。」

    from text_clean import strip_placeholders, clean_placeholders
    strip_placeholders("客服类产品（具体定位未获取）")   # => "客服类产品"
    clean_placeholders(cases)       # 清掉 one_liner / what_it_does 的定位占位
"""

import re

# 「2026-09-17 快照」/「2026-08-14 最后快照」这类日期锚点
_DATE = r"\d{4}-\d{2}-\d{2}"
_SNAP = r"(?:最后|最终|最新)?\s*快照"

# 顺序有讲究：先处理句子内部的（逗号相连），再处理独立的括号/分隔符形态，
# 最后兜底任何残留的「日期 + 快照」。
_RULES = (
    # 1) 括号内、逗号后的「，2026-09-17 快照」
    (re.compile(r"[，,]\s*" + _DATE + r"\s*" + _SNAP), ""),
    # 2) 独立括号「（2026-09-17 快照）」整体删除（含前置空格）
    (re.compile(r"\s*[（(]\s*" + _DATE + r"\s*" + _SNAP + r"\s*[）)]"), ""),
    # 3) 分隔符后的「· 2026-08-14 最后快照」
    (re.compile(r"\s*[·•・]\s*" + _DATE + r"\s*" + _SNAP), ""),
    # 4) 兜底：任何残留的「日期 + 快照」（含前置逗号/顿号/空格）
    (re.compile(r"\s*[、，,]?\s*" + _DATE + r"\s*" + _SNAP), ""),
    # 5) 英文日期形态：「并标注快照时间 Sep 17, 2026。」
    (re.compile(r"[，,]?\s*并?标注\s*(?:快照时间|了快照时间)?\s*"
                r"[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}\s*[。.]?"), ""),
)

# 清理删完之后的残骸：空括号、悬空分隔符、多余空格、标点前的空格
_TIDY = (
    (re.compile(r"[（(]\s*[）)]"), ""),                     # 「（）」→ 空
    (re.compile(r"[·•・]\s*(?=[）)。，,；;]|$)"), ""),      # 悬空「·」
    (re.compile(r"\s+([，。；、）)])"), r"\1"),             # 标点前空格
    (re.compile(r"[（(]\s+"), "（"),                        # 括号后空格
    (re.compile(r"\s+[）)]"), "）"),                        # 括号前空格
    (re.compile(r"[ \t]{2,}"), " "),                       # 连续空格
    # 注：不给「——」补前导空格 —— 语料里破折号一律紧接前文（「$62,352—— 一个人」），
    # 中文破折号自带两个字宽，补空格反而和其余 30 条案例的写法不一致。
)


def strip_snapshot_marks(text):
    """删掉带日期的快照标注。非字符串或没提到「快照」的原样返回。"""
    if not isinstance(text, str) or "快照" not in text:
        return text
    s = text
    for rx, rep in _RULES:
        s = rx.sub(rep, s)
    for rx, rep in _TIDY:
        s = rx.sub(rep, s)
    return s.strip()


def clean_snapshot_marks(obj):
    """就地清理嵌套 dict / list 里所有字符串，返回同一个对象。"""

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = strip_snapshot_marks(v)
            elif isinstance(v, (dict, list)):
                clean_snapshot_marks(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = strip_snapshot_marks(v)
            elif isinstance(v, (dict, list)):
                clean_snapshot_marks(v)
    return obj


# ---------------------------------------------------------------- 占位文案
# 2026-09-24 用户反馈：公众号标题上直接印着「Voklit：客服类产品（具体定位未获取）」。
# 这句括号是**采集侧的兜底**——初筛没查到产品定位就填它，好让内部一眼看出
# 「这条缺料」。但它挂在 one_liner 上，一路流进了标题、摘要、封面文案和正文
# 首段：等于把「我们没查到」印在读者面前，比不写更难看。
#
# 清理刻意做窄，只认两类「没有内容」的措辞，括号内还必须是短句：
#   · 定位查不到：「（具体定位未获取）」「（待补充：产品定位未明）」
#   · 干脆没料：「（主动匿名，无公开信息）」「（暂无公开信息）」
#   · 「（含 Figma 插件）」「（Lemon Squeezy 侧验证）」→ 留（那是真信息）
# 删完只剩标点/空白的，返回空串 —— 渲染层靠 `if text:` 整行不输出，
# 所以不会留下一个孤零零的「Voklit：」，标题会退回只写产品名。
#
# 2026-09-24 用户第二次强调：「空的占位就不要用了，没有内容就不要显示」——
# 原先只清「定位未获取」一族，把「（主动匿名，无公开信息）」当成了真信息保留，
# 结果 stealth-company 的标题是「Stealth Company：（主动匿名，无公开信息）」，
# 等于把「这条没料」印成标题。所以补上第二族。
_PLACE_HINT = re.compile(
    r"定位未获取|定位未明|产品定位|待补充|未获取|未明"          # 定位查不到
    r"|无公开信息|暂无公开信息|不公开|未公开|信息不详|不详"      # 干脆没料
)
_PLACE_BRACKET = re.compile(r"[（(]([^（）()]{0,24})[）)]")
# 裸占位（没带括号的形态，数据里也有：「具体定位未获取」）。
# 第二族（没料的措辞）只按**括号形态**清 —— 裸写「无公开信息」如果出现在
# 一句真话里（「官网无公开信息，故未采信」），整句是有意义的，不能删。
_PLACE_BARE = re.compile(
    r"(?:具体|产品|整体)?定位(?:信息)?(?:未获取|未明|不详|待补充)"
    r"|待补充[：:]?"
    r"|未获取")
# 判断「还有没有内容」：有一个中文/字母/数字就算有。
# **不能**靠 strip 标点集来判断 —— 那样会把正常句尾的「。」也吃掉
# （2026-09-24 踩过：rezi 的 what_it_does 结尾句号被 strip 掉，30 条老案例
#   的正文全被改了一遍，diff 一片红）。
_MEANINGFUL = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")
# 删掉占位后可能留在**句尾**的孤立连接符：「客服、」→「客服」。
# 刻意不含「。！？」—— 句号是正文的收尾，不是占位残留。
_DANGLING_TAIL = re.compile(r"[，,、：:；;·\-—]+$")


def strip_placeholders(text):
    """删掉「没有内容」的占位（带括号或裸写）；只剩占位时返回空串。

    非字符串原样返回。
    """
    if not isinstance(text, str):
        return text
    s = _PLACE_BRACKET.sub(
        lambda m: "" if _PLACE_HINT.search(m.group(1)) else m.group(0), text)
    s = _PLACE_BARE.sub("", s)
    s = s.strip()
    if not _MEANINGFUL.search(s):
        return ""
    return _DANGLING_TAIL.sub("", s)


def clean_placeholders(obj, fields=("one_liner", "what_it_does"),
                       nested=("claim",)):
    """就地清掉案例里**会外显**的定位占位字段，返回同一个对象。

    只动两处：
      · `fields` —— 直接进标题 / 摘要 / 封面 / 正文首段的定位字段；
      · `corrections[].claim` —— 会被当成「流传的说法」原文引用出来
        （voklit 那条的 claim 里就嵌着「（客服类产品，具体定位未获取）」）。
    `verdict` / `note` / `signals` 里的同类措辞是**写给内部看的**（说明这条
    没查到什么），各有各的渲染判断，不在这里抹掉。
    """
    if isinstance(obj, dict):
        obj = [obj]
    for c in obj or []:
        if not isinstance(c, dict):
            continue
        for f in fields:
            if isinstance(c.get(f), str):
                c[f] = strip_placeholders(c[f])
        for it in (c.get("corrections") or []):
            if not isinstance(it, dict):
                continue
            for f in nested:
                if isinstance(it.get(f), str):
                    it[f] = strip_placeholders(it[f])
    return obj


# ---------------------------------------------------------------- 结尾句号
# 2026-09-24：正文里「它为什么能成 / 能搬走的部分」两条列表，每一条都是没有
# 句号的半截句。四条并排放一起，既没有收尾、也看不出彼此关系，读起来很涩
# （用户原话：「每句都没有结束的句号…每句都不关联，读起来很不流畅」）。
# 补一个句号，每条才像一句说完了的话；这一层是渲染前的最后一道，data/ 里
# 以后新入库的条目漏写句号也能兜住。
_END_PUNCT = ("。", "！", "？", "…", "；")

# 只补**正文级句子列表**。models / tags 是标签词（「订阅制。」是怪东西），
# signals 是数据行，都不是句子，一律不碰。
SENTENCE_LISTS = ("why_it_works", "playbook")


def end_sentence(text):
    """一句列表项：缺结尾标点的补一个「。」，已经有的原样返回。"""
    if not isinstance(text, str):
        return text
    s = text.rstrip()
    if not s or s.endswith(_END_PUNCT):
        return s
    return s + "。"


def end_sentences(obj, fields=SENTENCE_LISTS):
    """就地给正文级列表项补结尾句号，返回同一个对象（dict 或 list 都认）。"""
    if isinstance(obj, dict):
        obj = [obj]
    for c in obj or []:
        if not isinstance(c, dict):
            continue
        for f in fields:
            v = c.get(f)
            if isinstance(v, list):
                c[f] = [end_sentence(x) if isinstance(x, str) else x for x in v]
    return obj


if __name__ == "__main__":
    # 自测（python scripts/text_clean.py）：把真实语料和边界都过一遍
    W = strip_snapshot_marks
    CASES = [
        # 应删：带日期
        ("MRR $2,869（RevenueCat API 验证，2026-09-17 快照）", "MRR $2,869（RevenueCat API 验证）"),
        ("累计收入约 $62,352（2026-09-17 快照）—— 一个人可以维护的体量",
         "累计收入约 $62,352—— 一个人可以维护的体量"),
        ("$424,368 MRR · Stripe 验证 · 2026-08-14 最后快照",
         "$424,368 MRR · Stripe 验证"),
        ("累计收入约 $714,537（Stripe API 验证，2026-09-17 快照）",
         "累计收入约 $714,537（Stripe API 验证）"),
        ("入库时应以页面明示的 $244,029 MRR 为准并标注快照时间 Sep 17, 2026。",
         "入库时应以页面明示的 $244,029 MRR 为准"),
        ("MRR $1,358、54 个活跃订阅（2026-09-17 快照）", "MRR $1,358、54 个活跃订阅"),
        # 应留：不带日期（在说明数字性质）
        ("该连接后来停止更新，所以这是固定快照而非实时值。",
         "该连接后来停止更新，所以这是固定快照而非实时值。"),
        ("数字取自 TrustMRR 公开榜单快照。", "数字取自 TrustMRR 公开榜单快照。"),
        ("MRR 与累计两种口径无法从快照区分。", "MRR 与累计两种口径无法从快照区分。"),
        ("NeoDrop：四个 AI wrapper 拆解（含 TrustMRR 快照）",
         "NeoDrop：四个 AI wrapper 拆解（含 TrustMRR 快照）"),
        # 无关文本不该被碰
        ("MRR $11,332、825 个活跃订阅", "MRR $11,332、825 个活跃订阅"),
        ("", ""),
    ]
    bad = 0
    for src, want in CASES:
        got = W(src)
        flag = "ok " if got == want else "BAD"
        if got != want:
            bad += 1
        print("%s %r\n    -> %r" % (flag, src, got))
        if got != want:
            print("    期望 %r" % want)
    print("\n%d/%d 通过" % (len(CASES) - bad, len(CASES)))

    # ---- 占位文案 ----
    P = strip_placeholders
    PCASES = [
        # 应删：定位没查到的兜底括号
        ("客服类产品（具体定位未获取）", "客服类产品"),
        ("网页小组件（具体定位未获取）", "网页小组件"),
        ("（待补充：产品定位未明）", ""),
        ("AI 产品（具体定位未获取）", "AI 产品"),
        ("具体定位未获取", ""),                      # 裸占位（无括号）也清
        # 应留：括号里是**真信息**
        ("（含 Figma 插件）", "（含 Figma 插件）"),
        ("一套面向设计师的工具（含 Figma 插件）", "一套面向设计师的工具（含 Figma 插件）"),
        ("（Lemon Squeezy 侧验证）", "（Lemon Squeezy 侧验证）"),
        # 应删：「没料」也该当空占位，不该印成标题（2026-09-24 用户第二次强调）
        ("（主动匿名，无公开信息）", ""),
        ("Stealth Company（暂无公开信息）", "Stealth Company"),
        # 句尾句号必须留住（占位清理不是「洗标点」）
        ("客服类产品（具体定位未获取）。", "客服类产品。"),
        ("AI 简历生成器。", "AI 简历生成器。"),
        ("监测品牌在各类 AI 模型回答中的曝光情况",
         "监测品牌在各类 AI 模型回答中的曝光情况"),
        ("搜索类 API 服务", "搜索类 API 服务"),
        ("一套面向设计师的工具（含 Figma 插件）", "一套面向设计师的工具（含 Figma 插件）"),
        ("", ""),
    ]
    pbad = 0
    for src, want in PCASES:
        got = P(src)
        flag = "ok " if got == want else "BAD"
        if got != want:
            pbad += 1
        print("%s %r\n    -> %r" % (flag, src, got))
        if got != want:
            print("    期望 %r" % want)
    print("\n%d/%d 通过（占位文案）" % (len(PCASES) - pbad, len(PCASES)))
    bad += pbad

    one = {"one_liner": "客服类产品（具体定位未获取）",
           "what_it_does": "（待补充：产品定位未明）",
           "verdict": "（主动匿名，无公开信息）",
           "corrections": [{"claim": "Voklit 属「客户服务」类产品（客服类产品，具体定位未获取）",
                            "truth": "它是云通信服务。"}]}
    clean_placeholders(one)
    scope_ok = (one["one_liner"] == "客服类产品" and one["what_it_does"] == ""
                and one["verdict"] == "（主动匿名，无公开信息）"
                and one["corrections"][0]["claim"] == "Voklit 属「客户服务」类产品")
    if not scope_ok:
        bad += 1
    print("%s 清 one_liner / what_it_does / corrections[].claim，"
          "verdict 等字段不动\n    -> %r"
          % ("ok " if scope_ok else "BAD", one))

    # ---- 结尾句号 ----
    E = end_sentence
    ECASES = [
        ("卖的是「产量」而不是「创意」：天然适合做成工具而不是服务",
         "卖的是「产量」而不是「创意」：天然适合做成工具而不是服务。"),
        ("已经收尾的。", "已经收尾的。"),          # 已有的不重复补
        ("感叹句收尾！", "感叹句收尾！"),
        ("带括号收尾（Lemon Squeezy 侧验证）", "带括号收尾（Lemon Squeezy 侧验证）。"),
        ("末尾有空格、句子 ", "末尾有空格、句子。"),  # 尾部空白先去掉再补
        ("", ""),
    ]
    ebad = 0
    for src, want in ECASES:
        got = E(src)
        flag = "ok " if got == want else "BAD"
        if got != want:
            ebad += 1
        print("%s %r\n    -> %r" % (flag, src, got))
        if got != want:
            print("    期望 %r" % want)
    print("\n%d/%d 通过（结尾句号）" % (len(ECASES) - ebad, len(ECASES)))

    # 只动句子列表，标签/数据行不碰
    one = {"why_it_works": ["卖的是产量"], "models": ["订阅制"],
           "tags": ["批量内容"], "signals": ["MRR 约 $1,358"],
           "playbook": ["把创意型需求包装成产量型交付"]}
    end_sentences(one)
    scope_ok = (one["why_it_works"] == ["卖的是产量。"]
                and one["playbook"] == ["把创意型需求包装成产量型交付。"]
                and one["models"] == ["订阅制"]
                and one["tags"] == ["批量内容"]
                and one["signals"] == ["MRR 约 $1,358"])
    if not scope_ok:
        ebad += 1
    print("%s 只补 why_it_works / playbook，models / tags / signals 不动\n    -> %r"
          % ("ok " if scope_ok else "BAD", one))
    raise SystemExit(1 if (bad or ebad) else 0)
