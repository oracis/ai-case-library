#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初筛 —— 在花钱核之前，先零成本判断「哪条值得核」。

为什么要有这个文件
------------------
候选池 33 条 + 采集队列 441 条。深核一条要联网检索、抓多页原文、再调一次 LLM，
是这条流水线上唯一真正花钱的环节。而 `ai_verify.py --limit 5` 以前是
**按录入顺序**取前 5 条 —— 采到什么就先核什么，于是队列里最值得核的那条
可能永远排在第 400 位，排在前面的则可能是早就核完、只差人点发布的那种。

所以这里只做一件事：**不看网页、不调 LLM、纯确定性**地给每条打一个
「值得核」分，并给出下一步干什么。分工因此变成——

    triage（零成本）  →  挑出值得核的少数  →  ai_verify（贵）深核  →  人背书

三条判断，全部只用记录里已有的字段
--------------------------------
1. **证据起点** —— 这条手上已有几成材料？有一手来源（支付网关 / 官方披露）
   的可以直接核；只有自报的得先找；什么都没有的，核了也核不出东西。
2. **数字可得性** —— 拿不拿得到数字？headline 是「未获取」的先别送去深核，
   那是在花 AI 的钱去补一件零成本脚本一次能干完的事（见下面的 backfill）。
3. **量级** —— 数字够不够大？本库已发布案例里最小的月收入是 $1,619，
   一个 $84/月 的产品写得再细也导不出可复刻的东西，样本本身不成立。

grade 不是形容词，每一级对应一个动作
----------------------------------
    ready     材料已齐（草稿判定可发布）→ 去后台发布，**别再花 AI**
    deep      值得深核 → 送 ai_verify
    backfill  有现成的零成本脚本能把材料补上 → 先跑脚本
    later     缺关键材料 → 等下一轮采集，或人工扫一眼
    drop      建议归档

`ready` 这一级是被真实数据教出来的：写这个文件时，6 份草稿**全部**已被规则引擎
判成「可发布」，其中 5 条还挂在候选池里。它们不缺 AI，缺的是人点一下发布 ——
不拦掉，`--limit 5` 就会把预算正好花在这 5 条上。

两条刻意写死的判断
------------------
- **候选池不由分数判死**。候选池的条目是人工从队列里一条条挑出来的，
  已经过一次人的筛选。机器可以用分数排前后，但不该用分数推翻人的决定 ——
  所以候选池里唯一能判 `drop` 的是硬标记（占位条目、已发布重复）。
- **高分不等于深核**。深核的条件是「有数字 **且** 有能追的来源」——
  没有数字就没有可核的东西，不是材料厚就值得花钱。

刻意不做的事
------------
不联网、不调 LLM、不写任何数据。它是纯函数集合 + 一个只读 CLI，
同样的输入永远同样的输出，因此可以被单测钉死。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules as VR                                          # noqa: E402


# ---------------------------------------------------------------------------
# 权重与阈值。集中放在这里，是为了「为什么这条排前面」永远答得上来 ——
# 分数本身就是解释，不是黑箱。调参只动这里，别散到函数里。
# ---------------------------------------------------------------------------
WEIGHTS = {
    # 证据起点：决定「核下去有多大可能核出结果」
    "first_hand": 40,          # 已有一手来源（stripe 支付网关 / 官方披露）
    "first_hand_claimed": 20,  # 只自称一手，却没落下来源链接
    "third_party": 16,         # 有独立第三方来源（媒体 / 评测），口径待核
    "verified_source": 18,     # 采集源本身就是支付网关验证源（TrustMRR）
    # 数字可得性
    "has_numbers": 16,         # 记录里真的带着数字
    "has_caliber": 8,          # 口径明确（MRR / ARR），不是「某个金额」
    "has_trade": 10,           # 有挂牌 / 成交数据（卖多少钱、几倍）
    # 关注度：自报项目没有网关数据时，这是唯一能替代的公开信号
    "attention_high": 16,
    "attention_mid": 9,
    "attention_low": 4,
    # 可核性
    "has_site": 6,             # 有自家官网（不是只指向代码仓库或聚合站）
    "has_models": 5,           # 商业模式与分类有值（「未分类」不算）
    "scored": 4,               # 已做过国内适配 / 单人可做评分
}

PENALTIES = {
    "no_numbers": 20,          # 拿不到任何数字，深核也核不出东西
    "raw_title": 12,           # 名字还是论坛原始标题，没洗过
    "code_only": 14,           # 只指向代码仓库 —— 开源项目通常没有生意
    "small": 15,               # 人已标注「体量太小，暂不精写」
    "tiny_revenue": 12,        # 月收入低于可成案的量级
    "bare": 10,                # 连来源性质都没标（候选池里才有意义，见 scope）
}

# 判级线。数字是从真实数据倒推的：队列 points 的中位数只有 2 分，
# 所以「有 10 分」就已经是前 10%，够得上一条关注度加分。
DEEP_LINE = 56
LATER_LINE = 28

# 关注度分档（Hacker News 的 points / 评论数）
ATT_HIGH, ATT_MID, ATT_LOW = 100, 30, 10
CMT_HIGH, CMT_MID, CMT_LOW = 30, 15, 5

# 收入量级门槛（美元/月）。定在 1000 是有依据的：已发布案例里最小的月收入
# 是 startclaw 的 $1,619，再低就撑不起一篇拆解 —— 数字小到没有可复刻的判断空间。
# 注意它只挡「深核」，不判归档：量级是会变的，今天 $80 不等于明年还是 $80。
TINY_REVENUE = 1000.0

GRADE_ORDER = ["ready", "deep", "backfill", "later", "drop"]

GRADE_LABEL = {
    "ready": "材料已齐",
    "deep": "值得深核",
    "backfill": "先补数字",
    "later": "先放着",
    "drop": "建议归档",
}

# 每一级对应的一句话动作。写成表，CLI 与后台都能直接用，不必各写一套。
GRADE_ACTION = {
    "ready": "草稿已判定可发布 —— 去后台点发布，别再花 AI",
    "deep": "送 ai_verify 深核",
    "backfill": "先跑零成本补数，补完再核",
    "later": "缺关键材料，等下一轮采集或人工扫一眼",
    "drop": "建议归档（后台归档，不删除）",
}

# 能不能深核：有数字之外，还得有「能追下去的来源」。只满足「材料厚」不算。
DEEP_EVIDENCE_KEYS = ("first_hand", "first_hand_claimed", "third_party",
                      "verified_source")

# 采集源可信度。与 harvest.KIND_RANK 同序，但不共用常量：
# 那边回答「数字能不能信」，这边回答「值不值得花力气核」——
# 相关但不是一回事（一个自报数字的项目，若官网清楚、在售、有人讨论，仍然值得核）。
SOURCE_KINDS = ("verified", "self_reported", "secondary", "discovery")

# 一手来源的采集源 id。口径同 verify_rules.FIRST_HAND_KINDS，只是换成采集侧的名字。
VERIFIED_HARVESTS = ("trustmrr",)

# 第三方来源的 kind（媒体 / 评测站）。参考案例数据里实际用过的值。
THIRD_PARTY_KINDS = ("press", "review", "secondary")

# 只指向代码仓库的域名 —— 绝大多数是开源库，不是生意。
# 例外要人工判，机器先按「没有收入可核」处理。
CODE_HOSTS = (
    "github.com", "gitlab.com", "bitbucket.org", "sourceforge.net",
    "pypi.org", "npmjs.com", "crates.io", "hub.docker.com", "codeberg.org",
)

# 采集站自己的页面。它们不是「产品官网」，所以既不算有官网、也不算只指向代码。
AGG_HOSTS = (
    "news.ycombinator.com", "trustmrr.com", "indiehackers.com",
    "producthunt.com", "arr.club", "hn.algolia.com",
)

# 数字占位文案。带这些词说明「字段在，但数字没拿到」，不能算有数字。
#
# 「疑似」必须在列：采集器给 Hacker News 条目的 headline 写的是
# 「疑似金额 $99」—— 那是从标题里正则抽出来的，note 里自己写着
# 「自动抽到的金额只作线索，可能是售价、举例或行价，不是收入」。
# 把它当成已有数字，就会让一批「标题里带了个数字」的帖子冒充「有数据的项目」，
# 直接挤进深核队列 —— 这正是初筛最该拦下的那类误判。
NUM_PLACEHOLDERS = ("未获取", "待补", "暂无", "未知", "尚未", "疑似")

# 只从标题抽到的疑似金额。它是个线索，不是数据，也不足以支撑深核 ——
# 但比什么都没有强：至少知道官网在哪、值不值得点开看。
SUSPECT_AMOUNT = "疑似金额"

# 采集器给「成组占位」条目写的名字前缀（在开头的全角括号里）。
# 这类条目在候选池里是**故意的**，不是脏数据：作者拿它们占位，
# 等采到真项目再填。所以不能当噪音归档，只能挡在核实预算之外。
#
#   待发现 / 待补   —— 还没采到真项目，占位
#   其余（基准线 / 方法论 / 制度观察 / 市场均价观察）—— 参考条目，本来就不是案例
NAME_PREFIX = re.compile(r"^（([^）]{0,16})）")
PLACEHOLDER_PREFIXES = ("待发现", "待补", "占位")

# 收入抽取。只认两种写法，宁可少认也不认错 —— 抽错一个数就会把量级判反。
# 1) 明确写「收入 $X」：headline 里还有售价和倍数，认前缀才不会抓错那一个
# 2) 「$X MRR / $X ARR」：单位后缀定死了是收入口径
REV_PREFIX = re.compile(r"收入\s*[¥$€£]?\s*([\d.,]+)\s*([KkMmBb]?)")
REV_SUFFIX = re.compile(r"[¥$€£]\s*([\d.,]+)\s*([KkMmBb]?)\s*(MRR|ARR)\b", re.I)

# 论坛原始标题的痕迹。产品名不会长这样。
RAW_TITLE_PREFIX = re.compile(r"^\s*(show|ask|tell)\s+hn\b", re.I)
RAW_TITLE_MARKERS = (
    "i built", "i made", "i pointed", "i created", "we built",
    "my side project", "my own product", "what do you think",
)
RAW_TITLE_MAX = 56          # 产品名不会是一句超过这个长度的话

# HN 采集时写进 note 的分 / 评论数
NOTE_ATTENTION = re.compile(r"(\d+)\s*分\s*/\s*(\d+)\s*条讨论")


# ---------------------------------------------------------------------------
# 小工具：都做成纯函数，方便单测直接钉
# ---------------------------------------------------------------------------
def host_of(url):
    """取 URL 主机名（去 www.、去端口）。取不到返回空串。"""
    m = re.match(r"https?://([^/\s]+)", (url or "").strip())
    if not m:
        return ""
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def norm_name(s):
    """归一化名称，用于跨条目比同一条目。

    去掉论坛前缀、标点与大小写 —— 「Show HN: Foo」和「Foo」是同一条。
    中文保留，否则纯中文名会被归一化成空串而互相撞上。
    """
    s = (s or "").lower()
    s = re.sub(r"^(show|ask|tell)\s+hn\s*[:\-–—]?\s*", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)
    return s


def name_prefix(name):
    """取名字开头全角括号里的前缀（「（待发现）X」→「待发现」）。没有就返回空串。"""
    m = NAME_PREFIX.match((name or "").strip())
    return m.group(1).strip() if m else ""


def norm_url(url):
    """归一化 URL 用于「是不是同一个页面」的比对。去 scheme/www、统一小写、去尾斜杠。"""
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/").split("#")[0]


def product_hosts(rec):
    """一条记录里「属于产品自己」的域名集合。

    两个来源都要看：案例的官网写在 sources[].url 里（未必有 source_url 字段），
    队列条目则只有 source_url。采集站自己的域名不算 —— 那是我们的来源页，
    不是这个产品的门牌号。
    """
    urls = [rec.get("source_url")]
    urls += [s.get("url") for s in (rec.get("sources") or []) if isinstance(s, dict)]
    out = set()
    for u in urls:
        h = host_of(u)
        if h and h not in AGG_HOSTS:
            out.add(h)
    return out


def looks_like_raw_title(name):
    """名字是不是还没洗过的论坛标题。"""
    n = (name or "").strip()
    if not n:
        return False
    if RAW_TITLE_PREFIX.match(n):
        return True
    low = n.lower()
    if any(k in low for k in RAW_TITLE_MARKERS):
        return True
    return len(n) > RAW_TITLE_MAX


def has_numbers(rec):
    """记录里有没有真数字。

    headline 与各数值字段都看；带「待补 / 未获取」的字段不算 ——
    字段在但数字没拿到，正是 backfill 要处理的，不能当成已有数字。
    """
    for v in (rec.get("metrics") or {}).values():
        if not isinstance(v, (str, int, float)):
            continue
        s = str(v)
        if any(p in s for p in NUM_PLACEHOLDERS):
            continue
        if re.search(r"\d", s):
            return True
    return bool(rec.get("trade"))


def _amount(num, unit):
    try:
        v = float(str(num).replace(",", ""))
    except ValueError:
        return None
    u = (unit or "").lower()
    for suffix, mul in (("k", 1e3), ("m", 1e6), ("b", 1e9)):
        if u == suffix:
            v *= mul
    return v


def revenue_of(rec):
    """这条的月收入（美元）。抽不到返回 None —— 抽不到就绝不当「量级太小」。

    优先用已结构化的 mrr / arr 字段（arr 折成月），没有才从 headline 抽。
    """
    m = rec.get("metrics") or {}
    for k in ("mrr", "last_30d_revenue"):
        v = m.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    v = m.get("arr")
    if isinstance(v, (int, float)) and v > 0:
        return float(v) / 12.0

    text = str(m.get("headline") or "")
    mm = REV_PREFIX.search(text)
    if mm:
        return _amount(mm.group(1), mm.group(2))
    mm = REV_SUFFIX.search(text)
    if mm:
        got = _amount(mm.group(1), mm.group(2))
        if got is not None and mm.group(3).upper() == "ARR":
            got /= 12.0
        return got
    return None


def attention_of(rec):
    """从采集笔记里读 HN 的分与评论数。读不到返回 (None, None)。"""
    m = NOTE_ATTENTION.search(rec.get("note") or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def source_of(rec):
    """这条是从哪个采集源来的。harvest_source 优先，其次按 source_url 的主机推。"""
    src = (rec.get("harvest_source") or "").strip().lower()
    if src:
        return src
    host = host_of(rec.get("source_url"))
    for dom, sid in (
        ("trustmrr.com", "trustmrr"),
        ("indiehackers.com", "indiehackers"),
        ("news.ycombinator.com", "hn"),
        ("ycombinator.com", "hn"),
        ("producthunt.com", "producthunt"),
        ("arr.club", "arrclub"),
    ):
        if host == dom or host.endswith("." + dom):
            return sid
    return ""


def source_kind_of(rec):
    """这条的来源性质。认不出来的按最低档处理，宁可保守。"""
    kind = (rec.get("source_kind") or "").strip().lower()
    if kind in SOURCE_KINDS:
        return kind
    src = source_of(rec)
    if src in VERIFIED_HARVESTS:
        return "verified"
    if src in ("indiehackers", "hn", "hackernews"):
        return "self_reported"
    if src == "arrclub":
        return "secondary"
    return "discovery"


# ---------------------------------------------------------------------------
# 去重索引：已发布 / 已在候选池 / 已有草稿的，都不该再占核实预算
# ---------------------------------------------------------------------------
class Index:
    """一份「这些已经处理过了」的名单。空索引也合法（单测与离线场景要用）。"""

    def __init__(self, cases=None, candidates=None, inbox=None, drafts=None):
        cases = cases or []
        candidates = candidates or []
        inbox = inbox or []
        self.case_ids = {c.get("id") for c in cases if c.get("id")}
        self.case_names = {norm_name(c.get("name")) for c in cases} - {""}
        # 官网域名：案例的官网在 sources[].url 里，source_url 字段是空的 ——
        # 只读 source_url 的话这个集合会是空的，去重就等于没建。
        self.case_hosts = set()
        for c in cases:
            self.case_hosts |= product_hosts(c)
        # 采集站页面的完整地址。同一域名下的不同页不是同一条目 ——
        # TrustMRR 的 /marketplace 和某个案例的 /startup/<slug> 都算 trustmrr.com，
        # 只按域名比会把 50 条队列条目全判成「已发布过」。
        self.case_urls = set()
        for c in cases:
            for u in [c.get("source_url")] + [
                    s.get("url") for s in (c.get("sources") or []) if isinstance(s, dict)]:
                if u:
                    self.case_urls.add(norm_url(u))
        self.cand_ids = {c.get("id") for c in candidates if c.get("id")}
        self.cand_names = {norm_name(c.get("name")) for c in candidates} - {""}
        self.inbox_ids = {i.get("id") for i in inbox if i.get("id")}
        self.drafts = drafts or {}

    def duplicate_of(self, rec, scope):
        """这条是不是已经处理过了。返回一句人话，或 None。"""
        rid = rec.get("id")
        name = norm_name(rec.get("name"))
        if scope == "inbox":
            if rid and rid in self.cand_ids:
                return "已在候选池，不必再进一次队列"
            if name and name in self.cand_names:
                return "与候选池条目同名"
        if rid and rid in self.case_ids:
            return "已发布成案例"
        if name and name in self.case_names:
            return "与已发布案例同名"
        if rec.get("source_url") and norm_url(rec["source_url"]) in self.case_urls:
            return "来源页与已发布案例是同一个"
        if product_hosts(rec) & self.case_hosts:
            return "官网与已发布案例相同"
        return None

    def draft_of(self, rec):
        """取这条的草稿，并顺手用规则引擎判一次。判不动就当没有。"""
        d = self.drafts.get(rec.get("id"))
        if not isinstance(d, dict):
            return None, None
        try:
            r = VR.evaluate(d)
        except Exception:
            return d, None
        return d, r


def name_collisions(records):
    """同一批里重名的条目（按归一化名）。返回 [(名字, [id...])]，供 CLI 报出来。

    刻意不放进打分函数 —— 那会引入「谁先谁后」的顺序依赖，
    同一个条目在不同排序下拿到不同分数，纯函数就白写了。
    """
    seen = {}
    for r in records:
        n = norm_name(r.get("name"))
        if not n:
            continue
        seen.setdefault(n, []).append(r.get("id"))
    return [(n, ids) for n, ids in sorted(seen.items()) if len(ids) > 1]


# ---------------------------------------------------------------------------
# 核心：打分
# ---------------------------------------------------------------------------
def _score_evidence(rec, scope, hits, misses, flags):
    """证据起点与来源缺口。返回命中的加分键集合。"""
    fh = VR.first_hand_kinds(rec)
    v = str(rec.get("verification") or "")
    kinds = {s.get("kind") for s in (rec.get("sources") or []) if isinstance(s, dict)}

    if fh:
        hits.append(("first_hand", WEIGHTS["first_hand"],
                     "已有一手来源（%s）" % "/".join(sorted(fh))))
    elif v in VR.FIRST_HAND_KINDS:
        # 案例数据里确实有这种：标了 stripe，sources 却是空的。
        # audit_evidence.py 会因此把它降级 —— 初筛要先把这类挑出来，
        # 免得拿去深核时才发现「来源链接根本没存」。
        hits.append(("first_hand_claimed", WEIGHTS["first_hand_claimed"],
                     "标注为「%s」但没落下来源链接" % v))
        flags.append(("missing_source_url",
                      "缺来源链接，规则引擎会据此降级（见 audit_evidence.py）"))
    elif kinds & set(THIRD_PARTY_KINDS) or v == "partial":
        hits.append(("third_party", WEIGHTS["third_party"],
                     "有独立第三方来源，口径待核"))
    elif v == "founder":
        misses.append(("founder_only", 0, "只有创始人自报，无第三方来源"))
    elif v == "unverified":
        misses.append(("unverified", 0, "尚未核实"))

    if source_kind_of(rec) == "verified":
        hits.append(("verified_source", WEIGHTS["verified_source"],
                     "采集源是支付网关验证源（%s）" % (source_of(rec) or "verified")))

    # 「什么材料都没有」才扣分。候选池 31/33 条的 sources[] 是空的，
    # 但它们的来源性质（verification）是标了的 —— 那只是「链接没落库」，
    # 属数据缺口而非质量差，全体扣一遍等于把一个不具区分度的常数加进分数里。
    if scope == "candidate" and not kinds and not fh and v in ("", "unverified"):
        misses.append(("bare", PENALTIES["bare"], "连来源性质都没标"))
    return {k for k, _, _ in hits}


def _score_data(rec, hits, misses, flags):
    """数字可得性与量级。返回 (是否有数字, 月收入或 None)。"""
    headline = str((rec.get("metrics") or {}).get("headline") or "")
    suspect = SUSPECT_AMOUNT in headline

    got = has_numbers(rec)
    if got:
        hits.append(("has_numbers", WEIGHTS["has_numbers"], "记录里带着数字"))
    else:
        misses.append(("no_numbers", PENALTIES["no_numbers"],
                       "只有疑似金额，没有收入数据" if suspect
                       else "数字未获取 —— 深核也核不出东西"))
    if suspect:
        flags.append(("suspect_amount",
                      "headline 里的金额是从标题抽的疑似值，不是收入"))

    if str(rec.get("caliber") or "") in ("mrr", "arr"):
        hits.append(("has_caliber", WEIGHTS["has_caliber"],
                     "口径明确（%s）" % rec.get("caliber")))
    if rec.get("trade"):
        hits.append(("has_trade", WEIGHTS["has_trade"], "带挂牌 / 成交数据"))

    rev = revenue_of(rec)
    if rev is not None and rev < TINY_REVENUE:
        misses.append(("tiny_revenue", PENALTIES["tiny_revenue"],
                       "月收入 $%s，低于可成案的量级（$%d）"
                       % (("%.0f" % rev), int(TINY_REVENUE))))
        flags.append(("tiny_revenue", "量级太小，撑不起一篇拆解"))
    return got, rev


def _score_attention(rec, hits):
    """公开关注度。返回档位（high/mid/low/None），高关注度用于免于被判噪音。"""
    p, c = attention_of(rec)
    if p is None:
        return None
    c = c or 0
    if p >= ATT_HIGH or c >= CMT_HIGH:
        hits.append(("attention_high", WEIGHTS["attention_high"],
                     "公开关注度高（%d 分 / %d 条讨论）" % (p, c)))
        return "high"
    if p >= ATT_MID or c >= CMT_MID:
        hits.append(("attention_mid", WEIGHTS["attention_mid"],
                     "公开关注度中（%d 分 / %d 条讨论）" % (p, c)))
        return "mid"
    if p >= ATT_LOW or c >= CMT_LOW:
        hits.append(("attention_low", WEIGHTS["attention_low"],
                     "公开关注度低但非零（%d 分 / %d 条讨论）" % (p, c)))
        return "low"
    return None


def _score_reachability(rec, hits, misses, flags):
    """可核性：有没有自家官网、商业模式说清了没有。"""
    host = host_of(rec.get("source_url"))
    if host in CODE_HOSTS:
        misses.append(("code_only", PENALTIES["code_only"],
                       "只指向代码仓库（%s），开源项目通常没有收入可核" % host))
    elif host and host not in AGG_HOSTS:
        hits.append(("has_site", WEIGHTS["has_site"], "有自家官网（%s）" % host))

    models = rec.get("models") or []
    cat = str(rec.get("category") or "")
    if models or (cat and cat != "未分类"):
        hits.append(("has_models", WEIGHTS["has_models"], "商业模式与分类已有值"))

    if rec.get("china_fit") or rec.get("solo_fit") or rec.get("composite"):
        hits.append(("scored", WEIGHTS["scored"], "已做过适配度评分"))

    if "体量太小" in (rec.get("blocking") or ""):
        misses.append(("small", PENALTIES["small"], "人已标注「体量太小，暂不精写」"))
        flags.append(("small", "人已判定暂不精写"))

    if looks_like_raw_title(rec.get("name")):
        misses.append(("raw_title", PENALTIES["raw_title"],
                       "名字还是论坛原始标题，没洗过"))
    return host


def backfill_reason(rec, scope, got_numbers):
    """这条的材料能不能用**现成的零成本脚本**补上。返回 (key, 说明) 或 None。

    关键是「现成」两个字：这里是整份文件里唯一会给出可执行命令的地方，
    所以只认真的跑得起来的路径。写作时只有一条 ——

    49 条 TrustMRR 条目的 headline 写着「具体数字待补」：它们的收入早就被
    支付网关验证过，只是采集时没抓数字字段。送去深核，是拿 AI 的钱补一件
    `backfill_trustmrr.py` 一次能干完的活。

    「有官网但数字未获取」不在这里 —— 打开官网看定价是人去点的活，
    没有脚本可跑，所以那类走 later + hint，不冒充零成本补数。
    """
    if got_numbers:
        return None
    if source_of(rec) in VERIFIED_HARVESTS:
        return ("trustmrr_numbers",
                "TrustMRR 收入由支付网关直读，但采集时没抓数字字段 —— "
                "跑 python scripts/backfill_trustmrr.py --only %s 可零成本补齐"
                % ("inbox" if scope == "inbox" else "candidates"))
    return None


def _hint(rec, scope, rev, bf):
    """给 later 的条目一句「下次可以怎么补」—— 不是动作，是线索。"""
    if bf:
        return None
    prefix = name_prefix(rec.get("name"))
    if prefix:
        return "占位 / 参考条目：本来就不进核实流程"
    if rev is not None and rev < TINY_REVENUE:
        return "量级偏小，除非后续口径或收入有明显变化"
    headline = str((rec.get("metrics") or {}).get("headline") or "")
    host = host_of(rec.get("source_url"))
    if SUSPECT_AMOUNT in headline:
        if host and host not in AGG_HOSTS and host not in CODE_HOSTS:
            return "标题里的金额是疑似值 —— 先看官网定价确认口径"
        return "标题里的金额是疑似值，且没有官网 —— 无从确认"
    if not has_numbers(rec) and host and host not in AGG_HOSTS and host not in CODE_HOSTS:
        return "有官网但数字未获取 —— 先看官网定价与在售状态，再决定核不核"
    if not has_numbers(rec):
        return "数字未获取 —— 等下一轮采集补上数字"
    return None


def _can_deep(scope, got_numbers, rev, flag_keys, hit_keys, host):
    """深核的条件：有数字、量级够、且有能追下去的来源线索。

    单看分数不行 —— 分数厚可能是因为采集时字段填得全，不代表有东西可核。
    反过来，一条只有自报数字但在售、有官网的队列条目，恰恰是 ai_verify
    最能发挥的对象（它的活就是去找一手来源）。

    候选池放宽一档：那些条目是人工一条条挑进来的，手上还有数字 ——
    数字在手就说明有东西可核，缺的只是来源等级，而那正是深核要做的事。
    队列不放宽：队列里数字稀少（441 条里只有 4 条带真数字），
    放宽等于让 AI 去核一堆连数字都没有的帖子。
    """
    if not got_numbers or "small" in flag_keys or "tiny_revenue" in flag_keys:
        return False
    if rev is not None and rev < TINY_REVENUE:
        return False
    if scope == "candidate":
        return True
    if set(hit_keys) & set(DEEP_EVIDENCE_KEYS):
        return True
    return bool(host) and host not in AGG_HOSTS and host not in CODE_HOSTS


def _grade(rec, scope, score, flags, hit_keys, got, rev, att, ready, bf, host):
    """判级。顺序即优先级：硬标记 > 已就绪 > 可补 > 深核 > 分数 > 关注度兜底。"""
    keys = {k for k, _ in flags}
    if "duplicate" in keys:
        return "drop"
    if ready:
        return "ready"
    if bf:
        return "backfill"
    if "placeholder" in keys or "reference" in keys:
        # 占位与参考条目不进核实流程，也不该被归档 —— 它们是作者故意留在池子里的，
        # 归档等于替作者撤销一次决定。机器在这里只做一件事：别让它们占预算。
        return "later"
    if _can_deep(scope, got, rev, keys, hit_keys, host):
        return "deep"
    if score >= DEEP_LINE:
        return "deep"
    if scope == "candidate":
        # 候选池是人工从队列里一条条挑出来的。机器能排前后，不能推翻人的选择：
        # 要归档只有一条路 —— 硬标记（已发布重复），其余一律留在池子里等材料。
        return "later"
    if score >= LATER_LINE:
        return "later"
    # 两道兜底，都只拦「归档」，不抬成深核：
    #   有真实数字 —— 数字是这个数据集里最稀缺的东西（441 条队列里只有 4 条有），
    #     哪怕 $25/月 也不该和「零分的开源仓库」一起被扫进归档。量级会变，
    #     数字本身是可核的。
    #   高关注度 —— 300 分的 Show HN 至少值得人扫一眼标题。
    if rev is not None or att == "high":
        return "later"
    return "drop"


def score_record(rec, scope="candidate", index=None):
    """给一条记录打「值得核」分。纯函数：同样的输入永远同样的输出。

    scope 决定「哪些字段本来该有」。候选池的 sources[] 本该有，
    采集队列本来就没有（还没核到那一步），拿队列缺 sources 去扣分是错怪它。
    """
    index = index or Index()
    hits, misses, flags = [], [], []

    name = rec.get("name") or ""
    prefix = name_prefix(name)
    if not name.strip():
        flags.append(("placeholder", "空名条目"))
    elif prefix:
        if any(p in prefix for p in PLACEHOLDER_PREFIXES):
            flags.append(("placeholder", "占位条目「%s」—— 等采到真项目再核" % prefix))
        else:
            flags.append(("reference", "参考条目「%s」—— 不是案例，不进精写流程" % prefix))
    dup = index.duplicate_of(rec, scope)
    if dup:
        flags.append(("duplicate", dup))

    draft, verdict = index.draft_of(rec)
    ready = bool(verdict and verdict.get("publishable"))

    hit_keys = _score_evidence(rec, scope, hits, misses, flags)
    got, rev = _score_data(rec, hits, misses, flags)
    att = _score_attention(rec, hits)
    host = _score_reachability(rec, hits, misses, flags)

    score = sum(w for _, w, _ in hits) - sum(w for _, w, _ in misses)
    score = max(0, min(100, score))

    bf = backfill_reason(rec, scope, got)
    grade = _grade(rec, scope, score, flags, hit_keys, got, rev, att, ready, bf, host)
    tier, tier_why = suggest_tier(rec, scope, got)

    out = {
        "id": rec.get("id"),
        "name": name,
        "scope": scope,
        "grade": grade,
        "grade_label": GRADE_LABEL[grade],
        "score": score,
        "action": (bf[1] if grade == "backfill" and bf else GRADE_ACTION[grade]),
        "hint": _hint(rec, scope, rev, bf),
        "tier": tier,
        "tier_reason": tier_why,
        "revenue": rev,
        "hits": [{"key": k, "points": w, "why": d} for k, w, d in hits],
        "misses": [{"key": k, "points": w, "why": d} for k, w, d in misses],
        "flags": [{"key": k, "why": d} for k, d in flags],
        "attention": att,
        "ready": ready,
    }
    if bf:
        out["backfill_kind"] = bf[0]
    if verdict:
        out["draft_verdict"] = verdict.get("verdict")
        out["draft_score"] = verdict.get("bonus_score")
    return out


def suggest_tier(rec, scope, got_numbers):
    """建议这条补齐后会落在哪一档。返回 (tier, 理由)，定不下来就 (None, 说明)。

    候选池直接问规则引擎 —— 档位政策只有一处实现（verify_rules.default_case_tier），
    初筛不另立一套，否则「初筛说进精品、发布后进了备选」就成了新的口径分叉。

    队列条目没有 sources[]，规则引擎判不出档位。这里给的是**预判**：
    TrustMRR 来源的收入由支付网关直读，补齐数字后就是一手来源。
    预判不是档位，所以理由里明说「补完才谈得上档位」。
    """
    if scope == "candidate":
        return VR.default_case_tier(rec)
    if source_kind_of(rec) == "verified":
        if got_numbers:
            return (VR.TIER_PREMIUM,
                    "支付网关验证源且数字已在手，补齐来源链接后可进精品池")
        return (None, "支付网关验证源，但数字还没补 —— 补完才谈得上档位")
    return (None, "队列条目还没有来源，档位要等深核之后由规则引擎定")


def triage_all(candidates=None, inbox=None, index=None):
    """把两个池子一起初筛。返回 {"items", "collisions", "summary"}。"""
    index = index or Index()
    items = [score_record(c, "candidate", index) for c in (candidates or [])]
    items += [score_record(i, "inbox", index) for i in (inbox or [])]
    return {
        "items": items,
        "collisions": name_collisions((inbox or []) + (candidates or [])),
        "summary": summarize(items),
    }


def summarize(items):
    """分级汇总。总数、每级条数、每级 scope 拆分，给 CLI 与后台共用。"""
    by_grade = Counter(i["grade"] for i in items)
    out = {
        "total": len(items),
        "by_grade": {g: by_grade.get(g, 0) for g in GRADE_ORDER},
        "by_scope": {},
    }
    for scope in ("candidate", "inbox"):
        sub = [i for i in items if i["scope"] == scope]
        out["by_scope"][scope] = {
            "total": len(sub),
            "by_grade": {g: sum(1 for i in sub if i["grade"] == g) for g in GRADE_ORDER},
        }
    return out


# ---------------------------------------------------------------------------
# 读数据 / CLI
# ---------------------------------------------------------------------------
def load(name):
    with open(os.path.join(ROOT, "data", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def load_all():
    """读齐四份数据。缺哪个都不致命（草稿文件可能还不存在），缺了当空。"""
    out = {}
    for name in ("cases", "candidates", "inbox", "verifications"):
        try:
            out[name] = load(name)
        except Exception:
            out[name] = {} if name == "verifications" else []
    return out


def build_index(data):
    return Index(cases=data.get("cases"), candidates=data.get("candidates"),
                 inbox=data.get("inbox"), drafts=data.get("verifications"))


def print_report(result, top=5, only_grade=None):
    """人读的分级清单。表格对齐，一屏看得完。"""
    s = result["summary"]
    # 表头只列实际在看的池子 —— --scope inbox 时不写「候选池 0 条」，
    # 那会让人以为候选池是空的。
    parts = ["%s %d 条" % (label, s["by_scope"][key]["total"])
             for key, label in (("candidate", "候选池"), ("inbox", "采集队列"))
             if s["by_scope"][key]["total"]]
    print("初筛：%s（零成本、未联网）\n" % (" · ".join(parts) or "没有数据"))
    for g in GRADE_ORDER:
        n = s["by_grade"].get(g, 0)
        if not n:
            continue
        c = s["by_scope"]["candidate"]["by_grade"].get(g, 0)
        i = s["by_scope"]["inbox"]["by_grade"].get(g, 0)
        print("  %-8s %-5s %4d 条（候选 %d · 队列 %d）—— %s"
              % (g, GRADE_LABEL[g], n, c, i, GRADE_ACTION[g]))

    coll = result["collisions"]
    if coll:
        print("\n  队列内有 %d 组同名条目（大概率是重复采集）：" % len(coll))
        for name, ids in coll[:6]:
            print("    %-30s %s"
                  % (name[:30], " / ".join(x for x in ids if x)[:70]))

    for g in GRADE_ORDER:
        if only_grade and g != only_grade:
            continue
        rows = sorted([i for i in result["items"] if i["grade"] == g],
                      key=lambda i: -i["score"])
        if not rows:
            continue
        print("\n== %s · %d 条（%s）" % (GRADE_LABEL[g], len(rows), GRADE_ACTION[g]))
        for i in rows[:top]:
            # hint 只给 later：那两列问的是「缺什么、下次怎么补」。
            # 其余级的动作是确定的，塞 hint 进去反而看不清该干什么。
            tail = (i["hint"] or i["action"]) if g == "later" else i["action"]
            print("  %3d  %-1s %-26s %s"
                  % (i["score"], "候" if i["scope"] == "candidate" else "队",
                     (i["id"] or "")[:26], tail[:60]))
        if len(rows) > top:
            print("  …… 另有 %d 条（--top 调条数，--grade 只看一级）" % (len(rows) - top))
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="零成本初筛：给候选池与采集队列打「值得核」分"
                    "（不联网、不调 LLM、不写数据）")
    ap.add_argument("--scope", choices=["all", "candidate", "inbox"], default="all",
                    help="只看某一边（默认两边都看）")
    ap.add_argument("--grade", choices=GRADE_ORDER, default=None,
                    help="只列某一级")
    ap.add_argument("--top", type=int, default=5, help="每级列几条（默认 5）")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON（给工具消费）")
    ap.add_argument("--write", action="store_true",
                    help="顺便把结果写到 data/triage.json（只写这一份，不动任何源数据）")
    ap.add_argument("--schema", action="store_true",
                    help="打印权重与阈值表，不读数据")
    args = ap.parse_args()

    if args.schema:
        print(json.dumps({
            "weights": WEIGHTS,
            "penalties": PENALTIES,
            "deep_line": DEEP_LINE,
            "later_line": LATER_LINE,
            "tiny_revenue": TINY_REVENUE,
            "attention": {"high": [ATT_HIGH, CMT_HIGH],
                          "mid": [ATT_MID, CMT_MID],
                          "low": [ATT_LOW, CMT_LOW]},
            "grade_order": GRADE_ORDER,
            "grade_label": GRADE_LABEL,
            "grade_action": GRADE_ACTION,
            "source_kinds": list(SOURCE_KINDS),
        }, ensure_ascii=False, indent=2))
        return 0

    data = load_all()
    result = triage_all(candidates=data["candidates"], inbox=data["inbox"],
                        index=build_index(data))
    if args.scope != "all":
        result["items"] = [i for i in result["items"] if i["scope"] == args.scope]
        result["summary"] = summarize(result["items"])

    if args.write:
        out = os.path.join(ROOT, "data", "triage.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("已写 %s\n" % out)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return print_report(result, top=args.top, only_grade=args.grade)


if __name__ == "__main__":
    sys.exit(main())
