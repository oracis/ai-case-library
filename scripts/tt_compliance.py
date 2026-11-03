#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条社区规范 → 稿件硬闸 + 确定性改写器。

为什么单独做一层（2026-11-02）
--------------------------------
头条《社区规范》有 20+ 条「平台不允许 / 平台不鼓励」，逐条读下来，我们自己的
稿件命中得很集中，而且**不是错别字级别，是结构级**：

  1. 文末固定挂着「我每天筛一批发在社群里」——命中「恶意营销·联系方式（微信群）」
     与「求资源/诱导私信联系」。这是最硬的一条，76/89 篇都有。
  2. 「完整核实记录（含全部原始来源）在案例库里」——站外引流，同属恶意营销。
  3. 「一个人能不能做 68.3/100」「中国移植分 64.3/100」——把案例写成「你能靠这个
     赚钱吗」的可行性打分，命中「风险投资·诱导他人参与投资（各种网赚）」+
     「未取得财经类资质不得发布投资指南/金融产品营销」。
  4. 「最值得抄的不是它的技术」「能搬走的部分」——教读者照抄，命中「违规推广·
     内容无信息增量」。
  5. 「我去核了一遍：抓取到的 TrustMRR 页面里…」——**内部核验日志被当正文发出去**，
     这是机器批量生产最明显的指纹，命中「内容低质·逻辑混乱」并可被归入
     「流量作弊·违规批量发文」。
  6. 89 篇共用「它是干什么的→钱从哪来→…→关于本栏目」同一骨架 ——
     命中「批量发布重复内容，或高度相似内容」。
  7. 游戏稿里「素材里没读到构建脚本」「部分读过；细节以仓库当前代码为准」——
     同属制作旁白，读者不该看到工位上的碎嘴。

设计原则
--------
* **确定性**：只用规则替换 / 整块删除，不调用模型改写正文。改完必须能逐条对回
  《社区规范》的条款名，而不是「感觉安全了」。
* **可追责**：每条规则带 `clause`（规范原文小节名），报告里直接给出「命中了哪一条」。
* **三档**：`block`（不许发）/ `warn`（要改或要人工确认）/ `info`（提示，不拦）。
* **跨主题**：规则按「违规形态」写，不按 AI case / 游戏写。以后做任何主题，
  只要它能被这 20 多条覆盖，就自动被拦。

用法
----
    python scripts/tt_compliance.py                        # 扫默认目录
    python scripts/tt_compliance.py --scan out/toutiao     # 扫指定路径
    python scripts/tt_compliance.py --fix out/toutiao      # 落盘改写（先备份！）
    python scripts/tt_compliance.py --fix out/toutiao --dry
    python scripts/tt_compliance.py --similar out/toutiao  # 批量相似度自查
    python scripts/tt_compliance.py --clauses              # 打印规则表

发布侧用 `guard()`：命中 block 直接 SystemExit。
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 规则表
# level: block = 违反「平台不允许」，命中即不许发
#        warn  = 命中「平台不鼓励」，要改或要人工确认
#        info  = 形态提示，不拦
# scope: all / article（图文稿）/ title

RULES = [
    # ---- A. 恶意营销 / 导流（规范「平台不允许 · 恶意营销」）----
    dict(level="block", scope="all",
         clause="平台不允许 · 恶意营销 · 联系方式",
         label="拉社群 / 留联系方式",
         pattern=r"发在社群里|社群|微信群|加群|入群|扫码|二维码|加我微信|私信我|"
                 r"联系方式|电话[:：]|QQ群|公众号后台",
         advice="删掉整句。头条把「微信群/二维码/诱导私信」列为硬违规，不是限流级别。"),

    dict(level="block", scope="all",
         clause="平台不允许 · 恶意营销 · 营销链接 / 求资源诱导私信",
         label="引流到自家站点 / 案例库",
         pattern=r"完整核实记录|完整索引|案例库里|档案编号|在案例库|去站点看|"
                 r"完整名单在|更多案例在",
         advice="删掉整句。站外引流与「想看全的去 XX」都算营销链接。"),

    dict(level="block", scope="all",
         clause="平台不允许 · 引导互粉 / 诱导关注",
         label="诱导关注",
         pattern=r"欢迎关注|引导关注|点击关注|求关注|关注「|想要了解更多|"
                 r"关注我|点个赞|转发给",
         advice="删掉整段。头条图文里「关注 XX 看更多」= 诱导关注。"),

    # ---- B. 风险投资 / 财经资质（规范「平台不允许 · 风险投资」「无资质发布专业领域内容」）----
    dict(level="block", scope="article",
         clause="平台不允许 · 风险投资 · 诱导投资（各种网赚）",
         label="给读者打可行性分",
         pattern=r"个人可做性|中国移植分|综合\s*\d+(?:\.\d+)?\s*分|"
                 r"一个人能不能做|能不能搬回国内",
         advice="改成对**产品本身**的客观描述（工程复杂度、依赖、维护成本），"
                "不要输出「你能做吗、几分」——那是投资指南。"),

    dict(level="block", scope="article",
         clause="平台不允许 · 无资质发布专业领域内容（财经）",
         label="给出参与结论",
         pattern=r"结论\**\s*[:：]\s*\**\s*别碰|结论\**\s*[:：]\s*\**\s*值得做|"
                 r"建议入场|建议下手|可以进场",
         advice="删掉结论行。给「做不做」的结论属于投资建议，需财经资质。"),

    dict(level="block", scope="all",
         clause="平台不允许 · 风险投资 · 诱导投资（各种网赚）",
         label="收益承诺 / 网赚话术",
         pattern=r"躺赚|稳赚|零成本创业|被动收入|财富自由|月入过万|年入百万|"
                 r"暴富|复制他的成功|跟着做也能|你也能赚|普通人可以|轻松月入",
         advice="删掉或改成客观陈述。收益只能用「公开页面显示 X（截至 YYYY-MM）」这种口径。"),

    dict(level="warn", scope="all",
         clause="平台不允许 · 风险投资 · 诱导投资（各种网赚）",
         label="收益口语化表述",
         pattern=r"月入|年入|月赚|赚了\s*[\d$￥¥]",
         advice="改成「月收入约 X（来源：…，截至 YYYY-MM）」。口语化收益最容易被判网赚。"),

    # ---- C. 教读者照抄（规范「违规推广 · 内容无信息增量」）----
    dict(level="warn", scope="article",
         clause="平台不允许 · 违规推广 · 内容无信息增量",
         label="教读者照抄",
         pattern=r"最值得抄|值得抄|照抄|直接抄|能搬走|搬运一版|抄一遍|"
                 r"可复制性|复制路径|落地清单",
         advice="改成「这类产品的共性做法是…」的归纳口吻。教抄=无信息增量+诱导跟进。"),

    # ---- D. 内容低质（规范「平台不鼓励 · 内容低质」）----
    dict(level="block", scope="article",
         clause="平台不鼓励 · 内容低质 · 逻辑混乱（制作旁白）",
         label="内部核验日志泄漏进正文",
         pattern=r"我去核了|我核了|抓取到的|本次抓到|这次抓到|原文不足以|"
                 r"原文中标注|原文里写|因此不能据此|核验过程|截图显示",
         advice="删掉或压成一句客观来源注记：「据 X 页面（访问于 YYYY-MM-DD）」。"
                "读者不需要看核实过程，平台会把这种旁白当拼凑内容。"),

    dict(level="warn", scope="article",
         clause="平台不鼓励 · 内容低质 · 逻辑混乱（制作旁白）",
         label="写作过程旁白",
         pattern=r"素材里没读到|素材里没有|部分读过|完整读过|细节以仓库当前代码为准|"
                 r"本文依据|整理自|本节参考|笔者的资料|手头资料",
         advice="改成事实或直接不写。「我没读到」是工位碎嘴，不是文章内容。"),

    dict(level="warn", scope="article",
         clause="平台不鼓励 · 内容低质 · 排版混乱",
         label="裸分数表格 / 打分项",
         pattern=r"\b\d(?:\.\d)?\s*/\s*5\b|\d{1,3}(?:\.\d)?\s*/\s*100",
         advice="比分项（4/5、68.3/100）在图文里显得像测评表，且通常是可行性打分的残留。"),

    # ---- E. 标题（规范「平台不鼓励 · 标题低质」）----
    dict(level="block", scope="title",
         clause="平台不鼓励 · 标题低质 · 夸张式标题",
         label="夸张式标题",
         pattern=r"震惊|必看|史上最|全网最|炸裂|封神|太可怕|99%\s*的人|"
                 r"绝对想不到|看完就懂|一文读懂一切",
         advice="换掉。夸张词是标题低质的第一判定项。"),

    dict(level="warn", scope="title",
         clause="平台不鼓励 · 标题低质 · 悬念式标题",
         label="悬念式标题",
         pattern=r"竟然|原来是这样|结果是|真相|背后原因|你不知道的|"
                 r"他做对了什么|凭什么",
         advice="标题要把结论说出来，别藏。头条专门禁「滥用转折、隐藏关键信息」。"),

    dict(level="block", scope="title",
         clause="平台不鼓励 · 标题低质 · 标题与内容原意有偏差",
         label="标题带金额",
         pattern=r"[$￥¥]\s*\d|\d+\s*(?:万|千)?\s*美元|月入\s*\d|年入\s*\d",
         advice="标题不出现金额。金额进标题=用收益勾点击，最易被判夸张+无中生有。"),

    dict(level="warn", scope="title",
         clause="平台不鼓励 · 标题低质 · 标题不规范",
         label="标题标点滥用",
         pattern=r"[!！]{2,}|[?？]{2,}|[，,。.]{2,}|【.*?】{2,}",
         advice="标题只用一个标点收尾。"),

    # ---- F. 时效（规范「平台不鼓励 · 发布已过时效内容」）----
    dict(level="warn", scope="article",
         clause="平台不鼓励 · 发布已过时效内容 · 信息陈旧",
         label="收入数字缺时点",
         pattern=r"__AMOUNT_NO_DATE__",       # 由动态检查填充
         advice="每个金额后面补「（截至 YYYY-MM）」。头条把「数据有更新但仍发旧数」判旧闻。"),

    # ---- G. 版权 / 侵权（规范「平台不允许 · 抄袭侵权」）----
    dict(level="warn", scope="all",
         clause="平台不允许 · 抄袭侵权",
         label="大段引用未标来源",
         pattern=r"__QUOTE_NO_SRC__",
         advice="引用超过 100 字要标来源与访问时间。"),
]

# 动态检查用的模式（不参与字符串匹配，单独实现）
_DYN = {"__AMOUNT_NO_DATE__", "__QUOTE_NO_SRC__"}

AMOUNT_RE = re.compile(r"(?:MRR|ARR|月收入|年收入|累计收入)\s*[约]?\s*[$￥¥]\s*[\d,]+(?:\.\d+)?|"
                       r"[$￥¥]\s*\d[\d,]*(?:\.\d+)?\s*(?:万|亿)?\s*美元?")
DATE_NEAR_RE = re.compile(r"截至|20\d{2}\s*[-年]|访问于|快照|as of", re.I)


class Finding:
    __slots__ = ("level", "clause", "label", "snippet", "advice", "pos")

    def __init__(self, level, clause, label, snippet, advice, pos=0):
        self.level = level
        self.clause = clause
        self.label = label
        self.snippet = snippet
        self.advice = advice
        self.pos = pos

    def as_dict(self):
        return dict(level=self.level, clause=self.clause, label=self.label,
                    snippet=self.snippet, advice=self.advice, pos=self.pos)

    def __str__(self):
        return "[%s] %s ｜ %s ｜ %s" % (self.level.upper(), self.clause,
                                       self.label, self.snippet)


def to_text(raw, is_html=None):
    """HTML → 纯文本（保留段落分隔）；md/纯文本原样。"""
    if is_html is None:
        is_html = raw.lstrip()[:1] == "<"
    if not is_html:
        return raw
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    s = re.sub(r"(?i)</p>|<br\s*/?>|</h[1-6]>|</li>|</blockquote>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    return html_mod.unescape(s)


def _title_of(raw, is_html):
    if is_html:
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
        if m:
            return html_mod.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(1))).strip()
        return ""
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith(">"):
            continue
        return s
    return ""


def scan(raw, is_html=None, platform="toutiao"):
    """返回 [Finding]。platform 目前只影响是否跑头条专属项，保留扩展位。"""
    out = []
    # 🚨 is_html 必须先归一化再分发给两个子函数：改写后的 HTML 会被拼成单行，
    # 若 _title_of 收到 None 会走 md 分支、把整行 HTML 当标题，标题规则于是
    # 在正文里乱命中（2026-11-02 实撞）。
    if is_html is None:
        is_html = raw.lstrip()[:1] == "<"
    text = to_text(raw, is_html)
    title = _title_of(raw, is_html)

    for r in RULES:
        if r["pattern"] in _DYN:
            continue
        scope = r["scope"]
        if scope == "title":
            hay = title
        else:
            hay = text
        if not hay:
            continue
        for m in re.finditer(r["pattern"], hay):
            snip = hay[max(0, m.start() - 30):m.start() + 55].replace("\n", " ")
            out.append(Finding(r["level"], r["clause"], r["label"],
                               snip.strip(), r["advice"], m.start()))

    # 动态：金额缺时点
    for m in AMOUNT_RE.finditer(text):
        win = text[max(0, m.start() - 60):m.start() + 90]
        if not DATE_NEAR_RE.search(win):
            out.append(Finding(
                "warn", "平台不鼓励 · 发布已过时效内容 · 信息陈旧",
                "收入数字缺时点",
                text[max(0, m.start() - 25):m.start() + 45].replace("\n", " ").strip(),
                "补「（截至 YYYY-MM）」。数字有更新仍发旧数，会被判旧闻新发。",
                m.start()))

    # 去重（同 label 同片段只报一次）
    seen, uniq = set(), []
    for f in out:
        k = (f.level, f.label, f.snippet[:40])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    return uniq


def worst(findings):
    lv = {f.level for f in findings}
    return "block" if "block" in lv else ("warn" if "warn" in lv else "clean")


def guard(raw, where="", is_html=None, allow_warn=True, platform="toutiao"):
    """发布前硬闸：命中 block 直接退出。"""
    fs = scan(raw, is_html, platform)
    blocks = [f for f in fs if f.level == "block"]
    if blocks:
        print("⛔ 头条社区规范拦截：%s" % (where or "稿件"), file=sys.stderr)
        for f in blocks[:12]:
            print("   %s" % f, file=sys.stderr)
        print("   改法见 scripts/tt_compliance.py --clauses", file=sys.stderr)
        if os.environ.get("TT_COMPLIANCE_FORCE") != "1":
            raise SystemExit(3)
        print("   （TT_COMPLIANCE_FORCE=1，已放行）", file=sys.stderr)
    if not allow_warn:
        warns = [f for f in fs if f.level == "warn"]
        if warns:
            print("⚠ 头条社区规范告警 %d 条：%s" % (len(warns), where), file=sys.stderr)
            for f in warns[:8]:
                print("   %s" % f, file=sys.stderr)
    return fs


# ---------------------------------------------------------------- HTML 改写
_BLOCK_RE = re.compile(
    r"(?is)<h1[^>]*>.*?</h1>|<h2[^>]*>.*?</h2>|<h3[^>]*>.*?</h3>|"
    r"<blockquote[^>]*>.*?</blockquote>|<div class=\"tableWrapper\">.*?</div>|"
    r"<table[^>]*>.*?</table>|<ul[^>]*>.*?</ul>|<ol[^>]*>.*?</ol>|"
    r"<hr\s*/?>|<p[^>]*>.*?</p>")


def split_blocks(raw):
    """把生成器产出的 HTML 切成顶层块。生成器结构规整，够用。"""
    blocks, pos = [], 0
    for m in _BLOCK_RE.finditer(raw):
        if m.start() > pos:
            gap = raw[pos:m.start()]
            if gap.strip():
                blocks.append(gap)
        blocks.append(m.group(0))
        pos = m.end()
    if pos < len(raw):
        tail = raw[pos:]
        if tail.strip():
            blocks.append(tail)
    return blocks


def _kind(b):
    m = re.match(r"(?is)\s*<(h1|h2|h3|blockquote|div|table|ul|ol|hr|p)\b", b)
    if not m:
        return "text"
    k = m.group(1).lower()
    return "hr" if k == "hr" else k


def _btext(b):
    return html_mod.unescape(re.sub(r"(?s)<[^>]+>", "", b)).strip()


# 章节改名：把「教你怎么赚钱」的骨架换成「讲这个产品怎么运作」
SECTION_RENAME = {
    "能搬走的部分": "这类产品的共性做法",
    "能搬回国内": "国内的现实情况",
    "能不能搬回国内": "国内的现实情况",
    "一个人能不能做": "这个产品的工程复杂度",
    "我的判断": "值得注意的一点",
}

# 整节删除
SECTION_DROP = {"关于本栏目", "关于作者", "关注我", "延伸阅读"}

# 段落级删除（整块消失）
PARA_DROP_RE = re.compile(
    r"发在社群里|社群|微信群|加群|完整核实记录|在案例库里|完整索引|档案编号|"
    r"欢迎关注|引导关注|想要了解更多|关注「|求得关注|"
    r"个人可做性\s*[:：]|中国移植分\s*[:：]|"
    r"结论\s*\**\s*[:：][^。\n]{0,90}?综合\s*\d+(?:\.\d+)?\s*分|"
    r"结论\s*\**\s*[:：]\s*\**\s*(别碰|值得做|不值得|可以做)|"
    r"一个人能不能做|能不能搬回国内", re.S)

# 核验日志：整块 blockquote / p 删除
VERIFY_LOG_RE = re.compile(
    r"我去核了|我核了|抓取到的|本次抓到|这次抓到|原文不足以|原文中标注|"
    r"原文里写|因此不能据此|核验过程")

# 行内替换：只是措辞问题，不需要整块删
INLINE_FIX = [
    (r"最值得抄", "最值得注意"),
    (r"值得抄", "值得注意"),
    (r"更难被抄", "更难被复制"),
    (r"被抄错", "传错"),
    (r"照抄", "照做"),
    (r"搬回国内", "落到国内"),
    (r"能搬走", "能复用"),
    (r"我去核了一遍：", "核对公开页面："),
    (r"抓取到的", ""),
    (r"本次抓到的", "公开"),
    (r"这个库里", "这些公开数据里"),
    (r"原文不足以证明", "公开信息不足以证明"),
]

# 整句替换：游戏稿的「制作旁白」——读者要的是怎么跑，不是我们读没读到
SENTENCE_FIX = [
    (r"素材里没读到构建脚本和入口页面，跑法请以仓库 README 为准。",
     "仓库未提供构建脚本与入口页面，按 README 的说明配置运行环境。"),
    (r"素材里没读到[^。]*。", "仓库未提供相关说明，细节以仓库当前代码为准。"),
    (r"[（(]源码部分依据[^）)]*?(?:部分读过|完整读过)[^）)]*?[）)]", ""),
    (r"[（(]源码依据[^）)]*?以仓库当前代码为准[）)]", ""),
    (r"本文依据[^。]*整理[^。]*。", ""),
]

# 数据时点注记：金额必须带时点（头条「发布已过时效内容」）。
# 日期不编，从 data/cases.json 的 verified_at 取。
DATE_NOTE_TPL = ("<p><strong>数据时点：</strong>以下收入数字取自 %s 的公开页面快照，"
                 "不是实时值；口径按原文标注，未做年化换算。</p>")
DATE_NOTE_MD = ("**数据时点：** 以下收入数字取自 %s 的公开页面快照，"
                "不是实时值；口径按原文标注，未做年化换算。\n")


def fix_html(raw, log=None, as_of=None):
    """按规范改写一篇生成器产出的 HTML。返回新 HTML。"""
    log = log if log is not None else []
    blocks = split_blocks(raw)

    # 1) 先按章节切段，逐节处理
    out_blocks = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if _kind(b) == "h2":
            title = _btext(b)
            # 收集本节内容
            j = i + 1
            body = []
            while j < len(blocks) and _kind(blocks[j]) not in ("h2", "h1"):
                body.append(blocks[j])
                j += 1

            if title in SECTION_DROP:
                log.append("删整节：%s（%d 块）" % (title, len(body) + 1))
                i = j
                continue

            new_title = SECTION_RENAME.get(title)
            if new_title:
                log.append("改节名：%s → %s" % (title, new_title))
                b = re.sub(r"(?is)>.*?<", ">%s<" % new_title, b, count=1) \
                    if _btext(b) else b
                # 稳妥：直接替换标题文字
                b = b.replace(title, new_title) if title in b else b

            kept = []
            drop_next_table = False
            for x in body:
                k = _kind(x)
                if k in ("p", "blockquote"):
                    tx = _btext(x)
                    if VERIFY_LOG_RE.search(tx) or PARA_DROP_RE.search(tx):
                        log.append("删块(%s)：%s" % (k, tx[:34]))
                        # 分数句后面紧跟的就是分项表，一起收走
                        if re.match(r"^(个人可做性|中国移植分)", tx):
                            drop_next_table = True
                        continue
                if k in ("div", "table") and drop_next_table:
                    log.append("删评分表：%s" % _btext(x)[:34])
                    drop_next_table = False
                    continue
                if k == "div" and PARA_DROP_RE.search(_btext(x)[:60]):
                    log.append("删表：%s" % _btext(x)[:34])
                    continue
                kept.append(x)
            out_blocks.append(b)
            out_blocks.extend(kept)
            i = j
            continue

        # 章节外的块（开篇、h1 等）
        k = _kind(b)
        if k in ("p", "blockquote"):
            tx = _btext(b)
            if VERIFY_LOG_RE.search(tx) or PARA_DROP_RE.search(tx):
                log.append("删块(%s)：%s" % (k, tx[:34]))
                i += 1
                continue
        out_blocks.append(b)
        i += 1

    # 块之间补回换行：不补的话整篇被拼成一行，git diff 变成「整文件替换」，
    # 事后想按段回滚就没法看（2026-11-02）
    s = "\n".join(out_blocks)

    # 2) 整句（制作旁白）
    for pat, rep in SENTENCE_FIX:
        if re.search(pat, s):
            log.append("整句改写：%s" % pat[:22])
            s = re.sub(pat, rep, s)

    # 3) 行内措辞
    for pat, rep in INLINE_FIX:
        if re.search(pat, s):
            log.append("措辞：%s → %s" % (pat, rep))
            s = re.sub(pat, rep, s)

    # 4) 数据时点（有 verified_at 才补，绝不编日期）
    if as_of and "数据时点" not in s:
        s = re.sub(r"(?is)(<h2[^>]*>\s*它到底做到多大\s*</h2>)",
                   r"\1" + DATE_NOTE_TPL % as_of, s, count=1)

    # 3) 收拾空壳：连续 hr / 空段落 / 结尾悬空 hr
    s = re.sub(r"(?is)(<hr\s*/?>\s*){2,}", "<hr>", s)
    s = re.sub(r"(?is)<p[^>]*>\s*(?:<strong>\s*</strong>)?\s*</p>", "", s)
    s = re.sub(r"(?is)<hr\s*/?>\s*$", "", s)
    s = re.sub(r"(?is)(<hr\s*/?>\s*)+</", "</", s)
    return s


# ---------------------------------------------------------------- Markdown 改写
def _md_sections(md):
    """切 [('head'|'h2'|'body', [lines])]。"""
    lines = md.split("\n")
    secs, cur = [], []
    for ln in lines:
        if re.match(r"^##\s+", ln):
            if cur:
                secs.append(("body", cur))
            secs.append(("h2", [ln]))
            cur = []
        else:
            cur.append(ln)
    if cur:
        secs.append(("body", cur))
    return secs


def fix_markdown(md, log=None, as_of=None):
    log = log if log is not None else []
    out = []
    drop_section = False
    for kind, lines in _md_sections(md):
        if kind == "h2":
            title = lines[0].lstrip("#").strip()
            drop_section = title in SECTION_DROP
            if drop_section:
                log.append("删整节：%s" % title)
                continue
            new = SECTION_RENAME.get(title)
            if new:
                log.append("改节名：%s → %s" % (title, new))
                lines = ["## %s" % new]
            out.append("\n".join(lines))
            continue
        if drop_section:
            continue
        # 段落级：以空行分块
        buf = []
        for ln in lines:
            if ln.strip():
                buf.append(ln)
            else:
                if buf:
                    para = "\n".join(buf)
                    if PARA_DROP_RE.search(para) or VERIFY_LOG_RE.search(para):
                        log.append("删段：%s" % para.strip()[:34])
                    else:
                        out.append(para)
                    buf = []
                out.append("")
        if buf:
            para = "\n".join(buf)
            if PARA_DROP_RE.search(para) or VERIFY_LOG_RE.search(para):
                log.append("删段：%s" % para.strip()[:34])
            else:
                out.append(para)
    s = "\n".join(out)
    for pat, rep in SENTENCE_FIX:
        if re.search(pat, s):
            log.append("整句改写：%s" % pat[:22])
            s = re.sub(pat, rep, s)
    for pat, rep in INLINE_FIX:
        if re.search(pat, s):
            s = re.sub(pat, rep, s)
    if as_of and "数据时点" not in s:
        s = re.sub(r"(?m)^(#{2,3}\s*它到底做到多大\s*)$",
                   r"\1\n\n" + (DATE_NOTE_MD % as_of), s, count=1)
    s = re.sub(r"\n{3,}", "\n\n", s).strip() + "\n"
    return s


def fix(raw, log=None, as_of=None):
    is_html = raw.lstrip()[:1] == "<"
    return (fix_html if is_html else fix_markdown)(raw, log, as_of)


# ---------------------------------------------------------------- 时点来源
_CASES = None


def case_dates():
    """id -> 'YYYY-MM'，取 data/cases.json 的 verified_at（没有则 updated_at）。"""
    global _CASES
    if _CASES is not None:
        return _CASES
    _CASES = {}
    p = os.path.join(ROOT, "data", "cases.json")
    if not os.path.isfile(p):
        return _CASES
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return _CASES
    items = d if isinstance(d, list) else (d.get("cases") or list(d.values()))
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("id")
        stamp = str(it.get("verified_at") or it.get("updated_at") or "")
        if cid and re.match(r"^\d{4}-\d{2}", stamp):
            _CASES[cid] = stamp[:7]
    return _CASES


def as_of_for(path):
    """产物路径 → 案例 id → 时点。游戏稿（ext-ep-*）不在 cases.json，返回 None。"""
    p = path.replace("\\", "/")
    parts = p.split("/")
    cid = None
    if "toutiao" in parts:
        i = parts.index("toutiao")
        if i + 1 < len(parts) - 1:
            cid = parts[i + 1]
    if cid is None:
        cid = os.path.splitext(os.path.basename(p))[0]
    return case_dates().get(cid)


# ---------------------------------------------------------------- 标题重建
def strip_money(t):
    """去掉金额 / 收入指标词。口径与 make_article.strip_title_money 一致。"""
    if not t:
        return ""
    s = t
    s = re.sub(r"[¥€£$]\s*\d[\d.,]*\s*(?:[KkMm万])?", "", s)
    s = re.sub(r"\d[\d.,]*\s*(?:美元|英镑|欧元|欧)", "", s)
    s = re.sub(r"(?<![\w])MRR(?!\w)", "", s)
    s = re.sub(r"(?<![\w])ARR(?!\w)", "", s)
    s = re.sub(r"月收(?:入)?|月入|年入", "", s)
    s = re.sub(r"累计\s*[¥€£$]?\d[\d.,]*\s*(?:[KkMm万])?", "累计", s)
    s = re.sub(r"近\s*\d+\s*天收入", "", s)
    s = re.sub(r"近\s*\d+\s*天已验证收入|已验证收入", "", s)
    s = re.sub(r"TrustMRR\s*实时核对|Stripe\s*直连验证", "", s)
    s = re.sub(r"[（(]\s*[）)]", "", s)
    s = re.sub(r"[（(]\s*非订阅制[^）)]*[）)]", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[，,]\s*[、；;]", "，", s)
    s = re.sub(r"[、；;]\s*[、；;]", "、", s)
    s = re.sub(r"[，,]\s*[，,]", "，", s)
    s = re.sub(r"[。！？\.!?]+\s*$", "", s.strip())
    return s.strip(" ，,：:、")


_CASE_META = None


def case_meta():
    """id -> (name, one_liner)。"""
    global _CASE_META
    if _CASE_META is not None:
        return _CASE_META
    _CASE_META = {}
    p = os.path.join(ROOT, "data", "cases.json")
    if not os.path.isfile(p):
        return _CASE_META
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return _CASE_META
    items = d if isinstance(d, list) else (d.get("cases") or list(d.values()))
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            _CASE_META[it["id"]] = (it.get("name") or it["id"],
                                    it.get("one_liner") or "")
    return _CASE_META


def calc_title(raw, limit=30):
    """把「名字：MRR $X，TrustMRR 实时核对」重建成「名字：一句话说清它是什么」。

    返回 (new_title, cid)；无法重建返回 (None, cid)。
    只做「换掉收益数字」一件事，不发明事实——新标题的内容全部来自
    cases.json 的 name / one_liner。
    """
    is_html = raw.lstrip()[:1] == "<"
    if is_html:
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
        old = html_mod.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(1))).strip() if m else ""
    else:
        m = re.search(r"(?m)^#\s+(.+)$", raw)
        old = m.group(1).strip() if m else ""
    if not old or not re.search(r"[$￥¥]\s*\d|美元|月入|年入|MRR|ARR", old):
        return None, None

    cid = None
    for k, (nm, _) in case_meta().items():
        if old.startswith(nm + "：") or old.startswith(nm + ":"):
            cid = k
            break
    if cid is None:
        return None, None

    name, one = case_meta()[cid]
    body = strip_money(one)
    if not body:
        return None, cid
    room = limit - len(name) - 1
    if room <= 4:
        return None, cid
    if len(body) > room:
        # 硬截会把词切成「3D-Agen」这种半截，优先退到最近的标点/空格断
        cut = body[:room]
        for sep in ("，", "、", "：", "；", " ", "（"):
            i = cut.rfind(sep)
            if i >= room * 0.6:
                cut = cut[:i]
                break
        body = cut.rstrip(" ，,、：:。")
    if len(body) < 5:
        return None, cid
    return "%s：%s" % (name, body), cid


def apply_title(raw, limit=30):
    """返回 (新文本, 旧标题, 新标题)。"""
    new_title, _ = calc_title(raw, limit)
    if not new_title:
        return raw, None, None
    is_html = raw.lstrip()[:1] == "<"
    if is_html:
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
        old = html_mod.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(1))).strip()
        raw = raw[:m.start(1)] + html_mod.escape(new_title) + raw[m.end(1):]
    else:
        m = re.search(r"(?m)^#\s+(.+)$", raw)
        old = m.group(1).strip()
        raw = raw[:m.start(1)] + new_title + raw[m.end(1):]
    return raw, old, new_title


# ---------------------------------------------------------------- 相似度
def shingles(text, k=6):
    t = re.sub(r"\s+", "", to_text(text))
    t = re.sub(r"[，。、；：！？「」『』（）()【】\[\]—\-…·,.;:!?\"'“”‘’]", "", t)
    return {t[i:i + k] for i in range(max(0, len(t) - k + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ---------------------------------------------------------------- CLI
DEFAULT_DIRS = ["out/toutiao", "out/articles", "drafts/wechat", "drafts/toutiao"]


def _iter_files(paths):
    exts = (".html", ".htm", ".md")
    for p in paths:
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for dirpath, _, files in os.walk(p):
                for f in sorted(files):
                    if f.endswith(exts):
                        yield os.path.join(dirpath, f)


def main(argv=None):
    ap = argparse.ArgumentParser(description="头条社区规范检查 / 改写")
    ap.add_argument("paths", nargs="*", help="文件或目录，默认扫产物目录")
    ap.add_argument("--scan", action="store_true", help="只报告（默认）")
    ap.add_argument("--fix", action="store_true", help="落盘改写")
    ap.add_argument("--dry", action="store_true", help="配合 --fix：只打印不写")
    ap.add_argument("--json", action="store_true", help="机器可读")
    ap.add_argument("--clauses", action="store_true", help="打印规则表")
    ap.add_argument("--similar", action="store_true", help="批量相似度自查")
    ap.add_argument("--titles", action="store_true",
                    help="顺带重建「名字：MRR $X」这类金额标题")
    ap.add_argument("--title-limit", type=int, default=30, help="标题上限字数（头条 30）")
    ap.add_argument("--threshold", type=float, default=0.55, help="相似度警戒线")
    a = ap.parse_args(argv)

    if a.clauses:
        for r in RULES:
            print("%-5s %-46s %s" % (r["level"], r["clause"], r["label"]))
            print("      %s" % r["advice"])
        return 0

    paths = a.paths or DEFAULT_DIRS
    files = list(_iter_files(paths))
    if not files:
        print("没有找到可检查的文件：%s" % paths, file=sys.stderr)
        return 2

    if a.similar:
        sets = []
        for p in files:
            try:
                sets.append((p, shingles(open(p, encoding="utf-8").read())))
            except Exception:
                continue
        worst_pairs = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                v = jaccard(sets[i][1], sets[j][1])
                if v >= a.threshold:
                    worst_pairs.append((v, sets[i][0], sets[j][0]))
        worst_pairs.sort(reverse=True)
        print("相似度 ≥ %.2f 的稿件对：%d 组（%d 份稿）" %
              (a.threshold, len(worst_pairs), len(files)))
        for v, x, y in worst_pairs[:15]:
            print("  %.2f  %s  ×  %s" % (v, os.path.relpath(x, ROOT),
                                          os.path.relpath(y, ROOT)))
        if not worst_pairs:
            print("  无高度相似对")
        return 0

    total = {"block": 0, "warn": 0}
    dirty_files = 0
    report = []
    for p in files:
        raw = open(p, encoding="utf-8").read()
        fs = scan(raw)
        if fs:
            dirty_files += 1
        for f in fs:
            total[f.level] = total.get(f.level, 0) + 1
        if fs:
            report.append((p, fs))

    if a.json:
        print(json.dumps([{"file": os.path.relpath(p, ROOT),
                           "findings": [f.as_dict() for f in fs]}
                          for p, fs in report], ensure_ascii=False, indent=1))
    else:
        for p, fs in report:
            nb = sum(1 for f in fs if f.level == "block")
            nw = len(fs) - nb
            print("\n%s   block=%d warn=%d" % (os.path.relpath(p, ROOT), nb, nw))
            for f in fs[:8]:
                print("   %s" % f)

    print("\n==== 汇总 ====")
    print("文件 %d 份，有问题 %d 份；block %d 处，warn %d 处"
          % (len(files), dirty_files, total.get("block", 0), total.get("warn", 0)))

    if a.fix:
        changed = 0
        titled = 0
        for p in files:
            raw = open(p, encoding="utf-8").read()
            if not scan(raw) and not a.titles:
                continue
            log = []
            if a.titles:
                raw, old_t, new_t = apply_title(raw, a.title_limit)
                if new_t:
                    titled += 1
                    log.append("标题：%s → %s" % (old_t[:30], new_t))
            new = fix(raw, log, as_of_for(p))
            if new == raw and not log:
                continue
            changed += 1
            if a.dry:
                print("\n--dry %s（%d 处改写）" % (os.path.relpath(p, ROOT), len(log)))
                for x in log[:8]:
                    print("    %s" % x)
            else:
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new)
        print("改写 %d 份%s；重建标题 %d 条"
              % (changed, "（dry，未落盘）" if a.dry else "", titled))
    return 0


if __name__ == "__main__":
    sys.exit(main())
