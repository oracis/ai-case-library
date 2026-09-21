#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/cases.json 里的精写案例转成公众号文章草稿。

用法：
    python scripts/make_article.py --list                 # 只看传播力排名，不写文件
    python scripts/make_article.py --id nitra             # 只生成一条
    python scripts/make_article.py --top 10               # 传播力前 10 条
    python scripts/make_article.py --all                  # 全部精写案例
    python scripts/make_article.py --all --format html    # 输出可直接粘进公众号编辑器的 HTML
    python scripts/make_article.py --top 5 --out out/drafts

产物：
    out/articles/<id>.md     公众号草稿（Markdown，带占位符与核对用来源注释）
    out/articles/<id>.html   内联样式 HTML，粘进公众号编辑器基本能保住样式

三条设计原则（写在代码里，别绕过）：
    1. 传播力不纯自动算 —— 脚本给基础分，EDITORIAL_WEIGHT 是人工覆盖表
    2. 每个数字都带原始来源 —— 来源只写进 HTML 注释，不进入正文（公众号正文不能放外链）
    3. 不生成「我的判断」—— 留占位符。判断是你唯一不可替代的资产，机器别碰

只读 data/，不写任何数据文件。
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):                      # Windows 控制台默认不是 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES_PATH = os.path.join(ROOT, "data", "cases.json")
DEFAULT_OUT = os.path.join(ROOT, "out", "articles")

# ------------------------------------------------------------------ 维度标签
# 与 score_china_fit.py / score_solo_fit.py 保持一致；导入失败则用这里的副本兜底
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from text_clean import clean_snapshot_marks               # noqa: E402

try:
    from score_china_fit import DIMS as CHINA_DIMS       # noqa: E402
    from score_solo_fit import SOLO_DIMS, QUADRANTS      # noqa: E402
except Exception:                                          # noqa: BLE001
    CHINA_DIMS = [
        ("demand", "付费意愿", ""), ("payment", "支付可达", ""),
        ("compliance", "合规空间", ""), ("acquisition", "获客迁移", ""),
        ("localization", "改造成本", ""), ("competition", "竞争空位", ""),
    ]
    SOLO_DIMS = [
        ("build", "造得出来", ""), ("delivery", "单人交付", ""),
        ("reach", "够得着客户", ""), ("capital", "启动轻", ""),
        ("window", "窗口还开着", ""),
    ]
    QUADRANTS = {
        "go": ("可以开干", ""), "export": ("能做，但别在国内卖", ""),
        "partner": ("有市场，但一个人啃不动", ""), "skip": ("别碰", ""),
    }

# 注：这里**故意不放 price_point**。价格属于「钱从哪来」那一节，正文那句
# 通常已经写了；表格里再来一行「定价」，加上原来单独一行的「定价：」，
# 同一件事会在正文里出现三遍（2026-09-21 实测命中 25/30 篇）。
# 价格改由 build_sections 的 money 段兜底：只有正文没提过的数字才补一行。
METRIC_LABELS = [
    ("headline", "官方口径"), ("arr", "年化收入"), ("customers", "客户"),
    ("team", "团队"), ("funding", "融资"), ("growth", "增长"),
    ("metric_note", "备注"),
]

# ------------------------------------------------------------------ 人工覆盖表
# 传播力首先是编辑判断，公式只是辅助。基础分只认三件可计算的事：
#   纠错条数（每条 +2）、metrics 里的最大金额量级、四象限（go/export 加分）。
# 它算不出「哪条更值得讲」，所以加减权都在这里手工调。建议范围 -3 ~ +5。
# 表里没写的案例权重为 0，只吃基础分。
# 这个表的顺序 = 发布优先级，改动它就是在改你的选题排期。
EDITORIAL_WEIGHT = {
    # —— 首发十篇 ——
    "nitra": 3.5,           # 客户数被写多一个量级，全库最强钩子
    "shipfast": 4.5,        # 爆款衰退，情绪张力最大
    "chatbase": 3.5,        # 累计 ≠ 年入，概念纠偏类转发率最高
    "speel-co": 5.0,        # 收入真、增长归零：反向结论
    "pieter-levels": 5.0,   # 名人效应，用来撬第一波外部流量
    "meerkats-ai": 4.0,     # 反面教材，立「核不到就不写」的公信力
    "comp-ai": 2.5,         # 口径陷阱：月 ×12 不等于年入
    "visualizee-ai": 3.5,   # 第一个正向样本（go），得给希望
    "rezi": 2.5,            # 反直觉：技术含量最低反而最赚
    "aeo-engine": 3.0,      # 中国移植分全库第一，天然引流社群
    # —— 第二批 ——
    "bustem": 0.4, "outrank": 1.3, "kibu": 1.7,
    "storyshort-ai": 0.5, "postiz": 0.8, "marc-lou-portfolio": 2.2,
    "lancer-app": 1.6, "coral": 1.8, "startclaw": 1.7, "trustmrr": 1.6,
    "checkvibe": 2.9,       # 基础分算出来是 0（无纠错、金额小、无象限加分），手工补一点
    # —— 压后：数字很大但钩子弱 ——
    "sierra": -1.4,         # $200M ARR 很响，但「定价被传错」撑不起一篇
    "viktor": -0.6,         # $15M/10 周，同类叙事已经被讲透
}

# 已手改好的标题（三条备选）。没写的走模板，并在草稿里标注「建议手改」。
TITLE_OVERRIDES = {
    "nitra": [
        "中文媒体把这家美国医疗公司的客户数，写多了 10 倍",
        "一年从 $4M 做到 $33M，但它真正的门槛不是技术，是牌照",
        "「7000 家诊所在用」——一条被抄错了一个数量级的案例",
    ],
    "shipfast": [
        "被吹爆的 ShipFast，近 30 天收入只剩约 $3,000",
        "累计 $1.27M，现在每月 $3K：一个爆款的完整衰退",
        "同一个作者三年后换个产品，赚得比它还多",
    ],
    "chatbase": [
        "「年入 $20M」和「累计 $20M」，差了一整个量级",
        "同样的产品放到今天做，成本高十倍",
        "一条关于「时机窗口」的教科书",
    ],
    "speel-co": [
        "$65K MRR 是真的，但它已经停止增长了",
        "一个人做到 $65K MRR，然后增长归零",
        "收入曲线是真的，天花板也是真的",
    ],
}

# 宣传性词汇 → 直接替换。公众号「赚钱类」内容容易限流，宁可写保守一点。
SENSITIVE_WORDS = [
    ("轻松月入", "持续营收"), ("躺赚", "低维护"), ("稳赚", "收入稳定"),
    ("暴利", "高毛利"), ("月入", "月营收"),
]

# 分析性词汇 → 只提醒、不替换。
# 「赚钱的生意一点都不性感」这种句子是好句子，机械替换反而写坏了；
# 但出现在标题里就有风险，所以交给写的人自己判断。
WARN_WORDS = ["赚钱", "副业", "风口", "红利", "月赚"]

OUTRO = [
    "这条案例的完整核实记录（含全部原始来源）在案例库里。",
    "海外每天都有新跑通的小项目。我每天筛一批发在社群里：哪些值得看、哪些我直接否掉了、为什么。",
]


# ------------------------------------------------------------------ 小工具
def human_num(n):
    """33000000 -> 33M，方便在正文里读。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            v = n / div
            return ("%.1f" % v).rstrip("0").rstrip(".") + unit
    return str(int(n))


def fmt_metric(key, val):
    if key == "arr" and isinstance(val, (int, float)) and not isinstance(val, bool):
        return "$" + human_num(val)
    return str(val)


def strip_quotes(s):
    """去掉文案里已经带的「」《》，避免嵌套成「「xxx」」。"""
    s = (s or "").strip()
    for a, b in (("「", "」"), ("《", "》"), ("“", "”"), ('"', '"')):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a):-len(b)]
    return s.strip()


def _as_quote(s):
    """把一句别人的说法包成引号句；本来就带引号的**原样保留**。

    为什么改用它（2026-09-22）：钩子第一段原来走 strip_quotes()，把数据里
    本来就有的「」剥掉了，于是那句变成一个没有引号、没有来源的光秃陈述，
    读起来像我们自己的结论；为了给它找落点，第二段才加了
    「这句在中文网上被反复引用」——而这句恰恰是全文唯一没有依据的一句
    （corrections 的 source 是我们去核实的页面，不是这句说法的出处）。
    保住引号，指代问题自然消失，那句假话也就不用了。
    """
    s = (s or "").strip()
    if not s:
        return s
    for a, b in (("「", "」"), ("《", "》"), ("“", "”"), ('"', '"')):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            return s          # 已带同款引号，不动
    return "「%s」" % s      # 没有就补上


def sanitize(text):
    """宣传性词汇替换。返回 (新文本, 命中列表)。"""
    hits = []
    for bad, good in SENSITIVE_WORDS:
        if bad in text:
            hits.append(bad)
            text = text.replace(bad, good)
    return text, hits


def scan_warn(text):
    """分析性词汇只提醒不替换，返回 ['赚钱×2', ...]。"""
    out = []
    for w in WARN_WORDS:
        n = text.count(w)
        if n:
            out.append("%s×%d" % (w, n))
    return out


def money_scale(case):
    """取 metrics 里最大的数字，用作「金额量级」分。"""
    m = case.get("metrics") or {}
    vals = [v for v in m.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return max(vals) if vals else 0


def virality_base(case):
    """可计算的基础分。透明、可复现，但它算不出「哪条更值得讲」。"""
    score = 2.0 * len(case.get("corrections") or [])
    v = money_scale(case)
    if v >= 1e8:
        score += 3.0
    elif v >= 1e7:
        score += 2.0
    elif v >= 1e6:
        score += 1.0
    elif v >= 1e5:
        score += 0.5
    q = (case.get("composite") or {}).get("quadrant")
    if q == "go":
        score += 1.5
    elif q == "export":
        score += 1.0
    return score


def virality_score(case):
    """基础分 + 人工权重。分数只用于排序，不是绝对标准。"""
    return virality_base(case) + EDITORIAL_WEIGHT.get(case.get("id"), 0.0)


def build_titles(case):
    """三条备选标题。没手改过的走模板，并在草稿里标出建议手改。"""
    if case["id"] in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[case["id"]], True
    m = case.get("metrics") or {}
    hook = m.get("headline") or case.get("one_liner") or ""
    return [
        "%s：%s" % (case.get("name", case["id"]), strip_quotes(hook)),
        "%s 这条生意，国内能做吗" % case.get("name", case["id"]),
        "拆解 %s：%s" % (case.get("name", case["id"]), case.get("one_liner", "")),
    ], False


# ------------------------------------------------------------------ 正文分段
ROW_MARK = "@@ROW@@"
# 注：早先键值行靠「······」点线凑对齐，2026-09-21 换成真两端对齐后已删除 DOTS。


def _row(label, value):
    """键值行标记：两个渲染器都会把它渲成两列表格（md 真表格 / html 拼装表格）。"""
    return "%s%s%s%s" % (ROW_MARK, label, ROW_MARK, value)


def _split_row(p):
    parts = p.split(ROW_MARK)
    return parts[1], parts[2]


def _is_row(p):
    return p.startswith(ROW_MARK) and p.count(ROW_MARK) >= 2


def build_sections(case):
    """返回 [(小标题 or None, [段落])]，顺序即发布顺序。

    2026-09-21 改版（用户反馈「正文 AI 味太重」）。诊断结论是问题不在用词、
    在**骨架**，所以这里集中处理三件事：
      · 去掉「一句话：」这类表单式前缀 —— 读起来像填表，不像人说话；
      · 「钱从哪来」只保留正文那一句，structured 字段降级为兜底
        （原来正文 + 「收入模式：」+「定价：」+ 表格「定价」行 = 同一件事四遍）；
      · 开头的引导句加「我」——原本整篇是介绍体，没有作者的在场感。
    编号（一、二、三…）在 render_markdown / render_html 里一并去掉。
    """
    m = case.get("metrics") or {}
    corr = case.get("corrections") or []
    cf = case.get("china_fit") or {}
    sf = case.get("solo_fit") or {}
    comp = case.get("composite") or {}
    secs = []

    # 钩子（不编号）。有纠错就用纠错开场 —— 这是全库转化率最高的钩子。
    # 第一段是「别人说的那句」（保住引号，别剥），第二段是「我核出来的」：
    # 引号就是这句的落点，所以不需要「这句在中文网上被反复引用」这类指代句
    # ——而且那句是全文唯一没有依据的一句（corrections.source 是我们去核实的
    # 页面，不是这句说法的出处），写在一个主打核实的开头最伤可信度。
    if corr:
        secs.append((None, [
            _as_quote(corr[0].get("claim", "")),
            "我去核了一遍：%s" % corr[0].get("truth", ""),
        ]))
    else:
        secs.append((None, [case.get("verdict", "")]))

    # 它是干什么的
    what = []
    if case.get("what_it_does"):
        what.append(case["what_it_does"])
    elif case.get("one_liner"):
        # one_liner 正常情况下已用于标题；只在缺正文描述时兜底，避免同一句话写两遍
        what.append(case["one_liner"])
    if what:
        secs.append(("它是干什么的", what))

    # 钱从哪来：正文那句为主，结构化字段只补它没说的
    money = []
    if case.get("how_it_makes_money"):
        money.append(case["how_it_makes_money"])
    elif case.get("models"):
        money.append("收入模式：%s。" % " · ".join(case["models"]))
    pp = m.get("price_point")
    if pp:
        nums = re.findall(r"\d[\d,.]*", str(pp))
        spoke = "".join(money)
        if not money:
            # 「钱从哪来」整节没有正文描述，价格就是唯一线索
            money.append("定价：%s。" % pp)
        elif nums and not any(n in spoke for n in nums):
            # 正文没提过这个价格数字 —— 那是真信息，补一行；
            # 提过就不补（原来不分情况一律补，25/30 篇都在说第二遍）。
            # 没有数字的 price_point（如「B 端年合同 + 支付/采购流水收入」）
            # 基本是 how_it_makes_money 的摘要，一律不补。
            money.append("定价：%s。" % pp)
    if money:
        secs.append(("钱从哪来", money))

    # 它到底做到多大
    size = []
    for key, label in METRIC_LABELS:
        val = m.get(key)
        if val in (None, "", [], {}):
            continue
        size.append(_row(label, fmt_metric(key, val)))
    if size:
        secs.append(("它到底做到多大", size))

    # 第二条被抄错的（可选）
    if len(corr) > 1:
        secs.append(("还有一条被抄错的", [
            "**流传的说法**：%s" % corr[1].get("claim", ""),
            "**实际情况**：%s" % corr[1].get("truth", ""),
        ]))

    # 它为什么能成
    if case.get("why_it_works"):
        secs.append(("它为什么能成", ["- %s" % x for x in case["why_it_works"]]))

    # 能搬走的部分
    if case.get("playbook"):
        secs.append(("能搬走的部分", ["- %s" % x for x in case["playbook"]]))

    # 能不能搬回国内
    if cf:
        lines = ["**中国移植分：%.1f / 100**" % cf.get("score", 0)]
        dims = cf.get("dims") or {}
        for key, label, _ in CHINA_DIMS:
            if dims.get(key) is not None:
                lines.append(_row(label, "%d/5" % dims[key]))
        if cf.get("note"):
            lines.append(cf["note"])          # 本身就是完整句子，不必套「判断：」
        if cf.get("blocker"):
            lines.append("**卡点**：%s" % cf["blocker"])   # 短标签，好抓，保留
        secs.append(("能不能搬回国内", lines))

    # 一个人能不能做
    if sf:
        lines = ["**个人可做性：%.1f / 100**" % sf.get("score", 0)]
        dims = sf.get("dims") or {}
        for key, label, _ in SOLO_DIMS:
            if dims.get(key) is not None:
                lines.append(_row(label, "%d/5" % dims[key]))
        if sf.get("delivery_note"):
            lines.append(sf["delivery_note"])   # 同上，去「交付说明：」前缀
        secs.append(("一个人能不能做", lines))

    # 结论
    concl = []
    # 无纠错的案例，verdict 已经在开头的钩子里出现过一次，这里不再重复
    if case.get("verdict") and corr:
        concl.append(case["verdict"])
    if comp:
        qlabel = comp.get("quadrant_label") or QUADRANTS.get(
            comp.get("quadrant"), ("", ""))[0]
        concl.append("**结论**：%s（综合 %.1f 分）" % (qlabel, comp.get("score", 0)))
    secs.append(("我的判断", concl))

    return secs


# ------------------------------------------------------------------ 渲染
# 注：这里原有 CN_NUM = "一二三四五六七八九十"，给章节加「一、二、三…」编号。
# 2026-09-21 去掉了 —— 九节等长的编号骨架是正文里最强的「AI 味」来源。


def _flush_md_rows(L, rows):
    """把连续的键值行渲成 markdown 真表格（核对稿用）。

    ⚠ 会**清空 rows**（消费掉）。必须的：调用点的逻辑是「遇到非键值行就 flush」，
    如果 flush 完不清空，同一批 rows 会被后面每个普通段落再 flush 一遍 ——
    实测一个 6 行的评分表在正文里连着打印 3 遍（2026-09-21 修）。
    """
    if not rows:
        return
    L.append("| 维度 | 内容 |")
    L.append("| --- | --- |")
    for lab, val in rows:
        L.append("| %s | %s |" % (lab, val.replace("|", "\\|")))
    L.append("")
    rows.clear()


def render_markdown(case, titles, manual, secs, score):
    L = []
    L.append("# %s" % titles[0])
    L.append("")
    L.append("<!-- 备选标题 2：%s -->" % titles[1])
    L.append("<!-- 备选标题 3：%s -->" % titles[2])
    if not manual:
        L.append("<!-- [!] 标题由模板生成，发布前请手改 -->")
    comp = case.get("composite") or {}
    L.append("<!-- 传播力分 %.1f ｜ 象限 %s ｜ 生成于 %s -->"
             % (score, comp.get("quadrant", "-"), datetime.now().strftime("%Y-%m-%d %H:%M")))
    L.append("")

    for head, paras in secs:
        if head:
            # 不加「一、二、三…」编号：九节等长的编号骨架是最强的「AI 味」来源，
            # 读起来像报告目录，不像有人在这儿跟你说话（2026-09-21 改）。
            L.append("")
            L.append("## %s" % head)
            L.append("")
        rows = []
        for p in paras:
            if not p:
                continue
            if _is_row(p):
                rows.append(_split_row(p))
                continue
            _flush_md_rows(L, rows)
            if head is None:
                L.append("> %s" % p)
                L.append("")
                continue
            L.append(p)
            if not p.startswith("- "):        # 列表项之间不留空行，紧凑一些
                L.append("")
        _flush_md_rows(L, rows)

    L.append("---")
    L.append("")
    for p in OUTRO:
        L.append(p)
        L.append("")
    L.append("<!-- 公众号正文不能放外链：案例库链接放「阅读原文」和自定义菜单 -->")
    L.append("<!--")
    L.append("核对用来源（发布前逐个点开确认，别进正文）：")
    for s in case.get("sources") or []:
        L.append("  · %s <%s>" % (s.get("label", ""), s.get("url", "")))
    for i, c in enumerate(case.get("corrections") or [], 1):
        if c.get("source"):
            L.append("  · 纠正 %d 依据 <%s>" % (i, c["source"]))
    L.append("-->")
    return sanitize("\n".join(L))


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# 正文统一基线：偷自微信「一键排版」的参数，但不引入它的重排器。
# 三级 <section>/<div> 容器在发布链路会被 pm_safe_body 整块 unwrap（div/section
# 会被 ProseMirror 吞掉），所以基线必须落到每个 <p> 上，不能只挂外层——
# 否则正文会退回微信默认字号（之前就是这么丢的：外层 container 设了
# font-size:16px，粘贴后整段回到微信默认 15px/1.6 灰黑，只有标题和评分表有样式）。
# color 用 rgba(0,0,0,0.9) 而非纯黑，是它给出的原值；font-size/line-height 同理。
BODY_BASE = "font-size:17px;line-height:1.8;color:rgba(0,0,0,0.9)"


def _disp_em(s):
    """估算一段文字占多宽（单位 em，按正文 15px 量）。

    全角/半角宽度比约 1 : 0.55。只用来判断「这一行还放不放得下」，
    不做精确排版，所以不需要很准。
    """
    w = 0.0
    for ch in s:
        w += 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55
    return w


# 「标签 + 值」整行超过这个宽度（em）就换版式。手机正文区约 343px、15px 字，
# 去掉左右内边距后一行约 21em；标签再占 2~4em，留给值的余量约 16~18em。
# 取 19 作整行阈值并**宁可提前分流**：不踩「刚好被挤扁」的边界（见下面长值说明）。
LONG_ROW_EM = 19.0


def _flush_html_rows(P, rows):
    """把连续的键值行渲成「一体化评分表」。

    为什么不直接用 <table>：公众号编辑器（ProseMirror）不认 table 标签，
    pm_safe_body 会把它塌成段落。所以表格是「拼」出来的——
    每行一个 p（浅灰底 + 左侧橙条），行间用 border-top 画细线、
    首末行各自加圆角，合起来才像一整张表。末行用 margin-bottom 收尾，
    不再补空段落（空段落会把行距撑开，实测过）。

    2026-09-21 在真实编辑器里实测（tmp/probe_wxstyle*.py，保存草稿后重载读回）：
      · display:flex / justify-content / width / float / border-radius 微信**全保留**
        ——以前 WX_STYLE_DROP 把它们拉黑是猜的，白白让「值」只能靠「······」凑位；
      · 里面没有文字的空 span 会被整块剔除 → 进度条那类空盒子方案不可行，
        要画刻度只能用字符（●●●○○）。

    ⚠ 两个版式按「行宽」分流（2026-09-21 新增）：
      · 短值行 → 标签左 / 值右，display:flex 两端对齐（一体化表的主体）；
      · 长值行 → 「标签：正文」整段左对齐，**不套 flex**。
    为什么要分流：flex 两端对齐排长文本会两败俱伤。微信编辑器会把我们写的
    white-space 覆盖成 break-spaces（实测草稿 100000117），所以「标签
    white-space:nowrap 防挤压」在公众号里**根本无效** —— 一旦值很长，
    flex-shrink 就会把标签压到一列一个字（「备注」竖排），值还右对齐折行，
    非常难看。短值不会触发挤压，所以照旧用两端对齐好看。
    """
    if not rows:
        return
    last = len(rows) - 1
    for i, (lab, val) in enumerate(rows):
        vhtml = _esc(val).replace("&amp;**", "**")
        # 卡片外壳（底色 + 左橙条 + 行间细线 + 首末圆角）：两种版式共用
        card = ["margin:%s" % ("0 0 16px" if i == last else "0"),
                "background:#f6f8fa",
                "border-left:3px solid #e5b567",
                "padding:8px 12px",
                "font-size:15px",
                "line-height:1.7"]
        if i == 0:
            card += ["border-top-left-radius:8px", "border-top-right-radius:8px"]
        else:
            card.append("border-top:1px solid #eaeef2")
        if i == last:
            card += ["border-bottom-left-radius:8px",
                     "border-bottom-right-radius:8px"]
        if _disp_em(lab) + _disp_em(val) > LONG_ROW_EM:
            # 长值行：不套两端对齐，标签与正文同行左对齐自然换行
            P.append('<p style="%s;">'
                     '<span style="color:#57606a;">%s</span>：'
                     '<span style="color:#24292f;">%s</span></p>'
                     % (";".join(card), _esc(lab), vhtml))
        else:
            # 短值行：标签左、值右，两端对齐
            P.append('<p style="%s;display:flex;justify-content:space-between;">'
                     '<span style="color:#57606a;white-space:nowrap;">%s</span>'
                     '<span style="color:#8a5a00;font-weight:bold;text-align:right;'
                     'margin-left:12px;">%s</span></p>'
                     % (";".join(card), _esc(lab), vhtml))
    rows.clear()   # 同 _flush_md_rows：不清空的话同一张表会被反复输出


def render_html(case, titles, manual, secs, score):
    P = []
    P.append('<section style="%s;'
             'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
             '\'Microsoft YaHei\',sans-serif;">' % BODY_BASE)
    if not manual:
        P.append('<!-- [!] 标题由模板生成，发布前请手改 -->')
    P.append('<h1 style="font-size:22px;font-weight:700;line-height:1.4;margin:0 0 18px;">'
             '%s</h1>' % _esc(titles[0]))

    for head, paras in secs:
        if head:
            # 同 render_markdown：去掉「一、二、三…」编号
            P.append('<h2 style="font-size:17px;font-weight:700;margin:26px 0 12px;'
                     'padding-left:10px;border-left:3px solid #e5b567;">%s</h2>'
                     % _esc(head))
        rows = []
        for p in paras:
            if not p:
                continue
            if _is_row(p):
                rows.append(_split_row(p))
                continue
            _flush_html_rows(P, rows)
            if p.startswith("- "):
                P.append('<p style="%s;margin:0 0 8px;padding-left:14px;text-indent:-14px;">'
                         '%s</p>' % (BODY_BASE, _esc(p[2:]).replace("&amp;**", "**")))
            elif head is None:
                # 钩子/导语段：保留灰底灰字的设计，只补字号与行高跟正文对齐
                P.append('<blockquote style="font-size:17px;line-height:1.8;'
                         'margin:0 0 18px;padding:12px 14px;'
                         'background:#f6f8fa;border-left:3px solid #d0d7de;color:#57606a;">%s'
                         '</blockquote>' % _esc(p))
            else:
                P.append('<p style="%s;margin:0 0 14px;">%s</p>'
                         % (BODY_BASE, _esc(p).replace("&amp;**", "**")))
        _flush_html_rows(P, rows)

    P.append('<hr style="border:none;border-top:1px solid #e1e4e8;margin:28px 0 18px;">')
    for p in OUTRO:
        P.append('<p style="margin:0 0 10px;color:#57606a;font-size:14px;">%s</p>' % _esc(p))
    P.append('<!-- 核对用来源见同名 .md 草稿 -->')
    P.append('</section>')
    return sanitize("\n".join(P))


# ------------------------------------------------------------------ 主流程
def load_cases(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
    if not isinstance(cases, list):
        print("[!] cases.json 结构异常：拿不到 cases 列表")
        sys.exit(1)
    # 读者不该看到「（Stripe 验证，2026-09-17 快照）」这类**给作者自己看的**元数据标注。
    # 数据里已经清过一遍，这里是第二道闸门：以后往 cases.json 补字段时漏了也不进正文。
    clean_snapshot_marks(cases)
    return cases


def rank_key(case):
    """排序键：分数降序，并列时按 id 兜底。

    为什么要有第二条：Python 的 sort 是**稳定**的，所以两条同分的案例会保持
    cases.json 里的先后。那等于把「录入顺序」当成了隐藏的排序依据 ——
    同一个库换个顺序输出，公众号排期就跟着变。加 id 兜底后，排期只由内容决定。
    """
    return (-virality_score(case), case.get("id") or "")


def pick(cases, args):
    scored = sorted(((virality_score(c), c) for c in cases),
                    key=lambda t: rank_key(t[1]))
    if args.id:
        hit = [(s, c) for s, c in scored if c.get("id") == args.id]
        if not hit:
            print("[!] 找不到案例 id=%s" % args.id)
            print("    可选：" + "、".join(c.get("id", "?") for _, c in scored))
            sys.exit(1)
        return hit
    if args.min_score is not None:
        scored = [(s, c) for s, c in scored if s >= args.min_score]
    if args.top:
        scored = scored[:args.top]
    return scored


def main():
    ap = argparse.ArgumentParser(
        description="把精写案例转成公众号文章草稿（只读 data/，不改数据）")
    ap.add_argument("--id", help="只生成指定 id 的案例")
    ap.add_argument("--all", action="store_true", help="生成全部精写案例")
    ap.add_argument("--top", type=int, help="按传播力取前 N 条")
    ap.add_argument("--list", action="store_true", help="只打印传播力排名，不写文件")
    ap.add_argument("--min-score", type=float, default=None, help="只要传播力 ≥N 的")
    ap.add_argument("--format", choices=("md", "html", "both"), default="md")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录，默认 out/articles")
    args = ap.parse_args()

    if not (args.id or args.all or args.top or args.list):
        ap.print_help()
        print("\n至少给一个：--list / --id X / --top N / --all")
        return 0

    cases = load_cases(CASES_PATH)
    chosen = pick(cases, args)

    if args.list:
        print("=" * 78)
        print("  传播力排名 = 基础分（纠错/金额/象限，可算） + 人工权重（EDITORIAL_WEIGHT）")
        print("=" * 78)
        print("  %-3s %-20s %6s %6s %6s %-4s %-8s %s"
              % ("#", "id", "基础", "权重", "合计", "纠错", "象限", "headline"))
        for i, (s, c) in enumerate(chosen, 1):
            comp = c.get("composite") or {}
            base = virality_base(c)
            w = EDITORIAL_WEIGHT.get(c.get("id"), 0.0)
            print("  %-3d %-20s %6.1f %+6.1f %6.1f %-4d %-8s %s"
                  % (i, c.get("id", "?"), base, w, s,
                     len(c.get("corrections") or []), comp.get("quadrant", "-"),
                     (c.get("metrics") or {}).get("headline", "")))
        print("-" * 78)
        print("  「权重」这一列就是你的编辑判断，改它 = 改发布顺序。")
        return 0

    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    written, all_hits, all_warns = [], [], []
    for s, c in chosen:
        titles, manual = build_titles(c)
        secs = build_sections(c)
        stem = c.get("id", "case")
        if args.format in ("md", "both"):
            text, hits = render_markdown(c, titles, manual, secs, s)
            p = os.path.join(args.out, stem + ".md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            written.append(p)
            all_hits += hits
            all_warns += ["%s:%s" % (stem, w) for w in scan_warn(text)]
        if args.format in ("html", "both"):
            text, hits = render_html(c, titles, manual, secs, s)
            p = os.path.join(args.out, stem + ".html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            written.append(p)
            all_hits += hits
        print("  [OK] %-20s 传播力 %5.1f  标题%s"
              % (stem, s, "已手改" if manual else "待手改"))

    print("-" * 62)
    print("  生成 %d 个文件 -> %s" % (len(written), os.path.relpath(args.out, ROOT)))
    if all_hits:
        print("  [!] 宣传性词汇已替换：%s" % "、".join(sorted(set(all_hits))))
    if all_warns:
        print("  [!] 以下词语只在正文里保留、没动，但放进标题有风险，自己看一眼：")
        for item in all_warns:
            print("        %s" % item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
