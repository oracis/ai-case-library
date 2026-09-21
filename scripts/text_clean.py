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
    raise SystemExit(1 if bad else 0)
