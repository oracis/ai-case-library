#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用已抓取的一手档案批量起草案例正文（**只写暂存区，不碰台账**）。

为什么要有这个脚本
------------------
门槛放开后候选池有 167 条全部卡在同一个闸门上：正文是空的。
逐条手写的吞吐撑不住这个量级，但**放宽闸门让空正文入库是明确禁止的**
（红线：正文必须真写）。所以走「AI 起草 → 人/规则抽检 → 再入库」。

为什么它不直接写 data/candidates.json
-------------------------------------
1. 起草是**推断**，入库是**承诺**。一步到位会把「模型觉得」和「核过」
   混在一起，而这整个库的卖点就是「每个数字都核过」。
2. 暂存区可以反复重跑、逐条改，改坏了不污染台账。
3. apply 步骤能跑同一套 content_gate，不合格的进不去。

模型的输入边界（硬约束，写进提示词）
------------------------------------
只给两样东西：`tmp/tm_md/summary.json` 的结构化字段，
和 `tmp/tm_md/<slug>.md` 的原文。**明确禁止引入外部知识**：
- 创始人不知道就写「未公开」，不准猜（编一个创始人比留空危害大得多）；
- 团队规模、融资情况同理；
- 收入口径必须与 summary 给的口径一致，MRR 为 0 时只能写「近 30 天收入」；
- 不准写「增长迅猛」「前景广阔」这类无证据形容词。

用法
----
    python scripts/draft_case_text.py --limit 3          # 先试 3 条
    python scripts/draft_case_text.py --ids autoclip,aeo-checker
    python scripts/draft_case_text.py                    # 全量（后台跑）
    python scripts/draft_case_text.py --only-missing     # 只补暂存区里还没有的

产物：tmp/draft_text.json（key = 候选 id）
下一步：人工抽检 → scripts/apply_draft_text.py 写回候选池
"""

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify  # noqa: E402  —— 复用它的 LLM 配置链与 llm()

TM_DIR = os.path.join(ROOT, "tmp", "tm_md")
STAGE = os.path.join(ROOT, "tmp", "draft_text.json")

SYSTEM = """你是一个只写事实、不写形容词的案例库撰稿人。

【语言】全部输出必须是简体中文，包括 uncertain 数组。出现英文句子即为废稿。

你要根据「一手档案素材」给一个海外小型互联网生意写案例正文。

铁律（违反任何一条都是废稿）：

1. 【只用素材】所有事实必须来自下面给的素材。素材里没有的就写「未公开」，
   绝对不许用你的先验知识补充——尤其是创始人姓名、团队人数、融资情况、
   用户数、成立时间。

2. 【口径不许混用】收入只能按素材给的口径写：
   - 若「当前 MRR」> 0 → 写「MRR $X」；
   - 若「当前 MRR」= 0（非订阅制）→ 只能写「近 30 天已验证收入 $Y」，
     不许把近 30 天收入乘 12 当年化，也不许叫它 MRR。

3. 【不许编增长故事】素材没给增速就不写增速。给了「近 30 天增速」也要谨慎：
   上线首月的高增速不是增长故事，写的时候必须同时说明「月度时间线不足」。
   近 30 天增速与 MRR 增速不一致时，只写 MRR 口径。

4. 【禁止复述式空话】下面这类句子一律不许出现——它们只是把素材重说一遍，
   没有任何判断：
   ✗「提供免费套餐来吸引用户」  ✗「面向广泛的用户群体」
   ✗「通过社交媒体和内容营销实现有机增长」  ✗「产品功能强大、易于使用」
   ✗「解决了用户的痛点」  ✗「具有广阔的市场前景」
   ✗「前景广阔」「增长迅猛」「极具潜力」「颠覆」「赋能」「一站式」「显著提升」
   每一条 why_it_works / playbook 必须同时含两样东西：
   (a) 素材里的一个**具体数字或具体事实**；
   (b) 一个**机制解释**——为什么这个事实会导出这个结果。

5. 【verdict 必须是判断，不是复述】
   ✗ 坏例子：「它卖的是一个 AI 视频剪辑工具」（=把产品名再说一遍）
   ✓ 好例子：「它卖的不是剪辑，是『不断更』——客户买的是发布节奏，不是画质」
   写法：指出它真正卖的东西与客户以为自己在买的东西之间的差。

6. 【playbook 必须是可搬走的做法】写「换一个行业也能用的动作」，
   不写「这个产品做得好的地方」。

7. 【数字要带来源】写数字时顺带说明它是「支付网关直读」还是「档案自述」。
   档案自述的用户数、毛利率等未经二次核实，引用必须标明。

8. 【一句话定位里不许写增速】one_liner 只写收入数字（MRR 或近 30 天收入）
   和业务是什么。近 30 天的高增速可能只是上线首月，写进定位就是误导。

9. 【客单价用素材里算好的那个】素材里给了「客单价（已算好）」就直接引用，
   不要自己再做除法。没给就写「无法推算」。

10. 【货币与数字格式】库里所有收入都是 USD，金额一律用「$」符号 +
    千分位（如 $31,677），**绝不许写「元」**（排除欧洲生意的「欧元」）。
    整数不要带 .0 浮点尾（写 $1,408，不要写 1408.0）。小数（如 $7.76）照常保留。

输出**严格**为如下 JSON，不要任何额外文字、不要 markdown 代码块：
{
  "one_liner": "一句话定位（含最硬的那个数字，30-60 字）",
  "what_it_does": "它到底做什么（100-200 字，说清产品形态、创始人/上线时间、订阅数）",
  "how_it_makes_money": "怎么赚钱（80-160 字，说清定价档位，并用「订阅数 ÷ MRR」推算客单价）",
  "verdict": "一句话结论：它真正卖的是什么（40-90 字，判断不是复述）",
  "why_it_works": ["含具体数字+机制的原因1", "原因2", "原因3"],
  "playbook": ["可搬走的做法1", "做法2", "做法3"],
  "replicability": {"tech": 1-5, "distribution": 1-5, "capital": 1-5, "timing": 1-5},
  "uncertain": ["素材里没给的关键事实，用中文写，如：团队规模未披露"]
}

replicability 口径（1 = 最容易，5 = 最难）：
  tech          技术门槛：一个人能不能造出来
  distribution  获客门槛：不靠销售团队能不能触达客户
  capital       资金门槛：要不要垫资/备货/预购算力
  timing        时机门槛：是不是只存在于当前这个窗口
"""

# 起草后的自动校验：把明显不合格的挡在暂存区之外。
# 只挡**机器能判定**的两类（非中文、空话模板），判断力不够的不假装能判。
BAN_PHRASES = (
    "广阔的市场前景", "市场前景广阔", "前景广阔", "增长迅猛", "极具潜力",
    "显著提升", "赋能", "一站式", "颠覆",
    "提供免费套餐来吸引用户", "面向广泛的用户群体", "有机增长",
    "功能强大", "易于使用", "解决了用户的痛点", "满足用户需求",
)


def _has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s or "")


# ---------------------------------------------------------------- 数字守卫
# 起草里最危险的失败模式不是写错字，是**编数字**：素材里 mrr_growth 是空的，
# 模型照样写出「MRR 增长 88%」。这种句子一旦进库，整库「每个数字都核过」
# 的承诺就废了 —— 所以数字必须机器可追溯。
#
# 【只认素材原文里出现过的数字】，不做任何派生。
#
# 为什么砍掉「两两四则运算」的放行集合：实测它把成立日期（2026/02/18）、
# 粉丝数、订阅数也拿进运算，组合一爆炸几乎任何数都能凑出来——
# 于是「MRR 增长 88%」这种凭空编的增速照样通过校验。守卫形同虚设。
#
# 客单价不再靠模型算：facts_card() 里已用 MRR ÷ 订阅数算好并写进素材，
# 模型只需引用，所以它天然落在「素材里的数字」范围内。
_NUM_RE = __import__("re").compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(tok):
    """12454.0 / 12,454 / $12,454 → 同一个 float。"""
    try:
        return round(float(tok.replace(",", "")), 2)
    except ValueError:
        return None


def extract_numbers(text):
    out = set()
    for tok in _NUM_RE.findall(text or ""):
        v = _norm(tok)
        if v is not None:
            out.add(v)
    return out


_PCT_RE = __import__("re").compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*%")
# 「MRR $1,155」这类写法：用来抓非订阅制条目却写 MRR 的口径混用
_MRR_RE = __import__("re").compile(r"MRR\s*\$?\s*(\d[\d,]*(?:\.\d+)?)")


def check_caliber(data, s):
    """口径一致性：非订阅制（MRR 恒 0）不许写「MRR $X」。

    为什么单独守：数字守卫只管「数字有没有出处」，管不了「数字贴错了标签」。
    实测 fireply-ai 的素材里 cur_mrr 为空，模型却在 one_liner 写
    「MRR $1,155」，同一条的 how_it_makes_money 又写「近 30 天收入，非订阅制」
    —— 一条之内自己打自己。这正是红线 1（口径不许混用）。
    """
    mrr = s.get("cur_mrr") or 0
    body = " ".join(
        [str(data.get(k) or "") for k in
         ("one_liner", "what_it_does", "how_it_makes_money", "verdict")]
        + [str(x) for x in (data.get("why_it_works") or [])]
        + [str(x) for x in (data.get("playbook") or [])]
    )
    # 情形二（更隐蔽）：MRR 与近 30 天收入都在，但模型把**近 30 天收入**
    # 贴上了 MRR 的标签。数字确实有出处，所以数字守卫拦不住 ——
    # 实测 fireply-ai：素材 MRR=1279、近30天=1155，它写「MRR $1,155」。
    # 这类错误最危险，因为它看起来完全合理。
    mrr_val = round(float(mrr), 2)

    bad = []
    for tok in _MRR_RE.findall(body or ""):
        v = _norm(tok)
        if v is None or v == 0:
            continue                      # 「MRR 恒 0」是正确的说法
        if mrr and abs(v - mrr_val) > 0.011:
            bad.append("$%s" % tok)
        elif not mrr:
            bad.append("$%s" % tok)
    if not bad:
        return []
    if not mrr:
        return [f"口径混用：素材里当前 MRR 为 0（非订阅制），正文却写了"
                f"「MRR {bad[0]}」—— 非订阅制只能写「近 30 天已验证收入」，"
                f"不能挂 MRR 标签"]
    return [f"口径贴错标签：写了「MRR {bad[0]}」，但素材里当前 MRR 是"
            f"${mrr_val:,.0f}—— 这个数多半是近 30 天收入，两者不能混用"]


def check_growth_claims(data, s):
    """任何「X%」的增速断言，必须是素材里明确给的增速值。

    为什么单独守这一条：整段素材（含 .md 原文）里几乎任何数字都能找到，
    通用数字守卫拦不住「MRR 增长 88%」——88 可能只是原文里某个无关数字。
    而增速恰恰是最容易编、编了后果最严重的一类断言
    （红线 2：近 30 天高增速绝不能当增长故事）。
    """
    allowed = set()
    for k in ("growth_30d", "mrr_growth"):
        v = s.get(k)
        if isinstance(v, (int, float)):
            allowed.add(round(abs(float(v)), 2))

    # 毛利率是素材里的真字段，不是增速断言。
    # 光看数字分不清「毛利率 80%」和「增长 80%」，所以看**邻近词**：
    # 挨着增长类词的按增速管，挨着毛利率/毛利的放行。
    other_ok = set()
    pm = s.get("profit_margin")
    if isinstance(pm, (int, float)):
        other_ok.add(round(abs(float(pm)), 2))

    import re as _re
    ctx = _re.compile(
        r"(.{0,12}?)(-?\d[\d,]*(?:\.\d+)?)\s*%")
    bad = []
    bodies = [str(data.get(k) or "") for k in
              ("one_liner", "what_it_does", "how_it_makes_money", "verdict")]
    bodies += [str(x) for x in (data.get("why_it_works") or [])]
    bodies += [str(x) for x in (data.get("playbook") or [])]
    for body in bodies:
        for prefix, tok in ctx.findall(body or ""):
            v = _norm(tok)
            if v is None:
                continue
            av = abs(v)
            if any(abs(av - a) < 0.011 for a in allowed):
                continue                       # 素材给的增速，放行
            if any(abs(av - a) < 0.011 for a in other_ok):
                continue                       # 毛利率，放行
            looks_growth = any(w in prefix for w in
                               ("增长", "增速", "上涨", "环比", "提升", "涨"))
            if looks_growth:
                bad.append("%g%%" % v)
            else:
                # 既不是增速语境也不在白名单 —— 保守起见也算问题
                bad.append("%g%%（语境不明）" % v)
    if not bad:
        return []
    # 文案里含字面量百分号，用 % 格式化会炸
    # TypeError: not enough arguments for format string —— 改用 f-string
    return [f"写了素材没给的增速（{'、'.join(bad[:5])}）—— 素材没给增速"
            f"就一个字都别写；毛利率要写成「毛利率 80%」这种明确说法，"
            f"不要写成增长"]


def check_numbers(data, src_numbers):
    """返回 (问题列表, 凭空出现的数字列表)。"""
    allowed = set(src_numbers)
    # 小数位与百分号写法差异：允许 ±0.01 的浮点误差
    def ok(v):
        return any(abs(v - a) < 0.011 for a in allowed)

    body = " ".join(
        [str(data.get(k) or "") for k in
         ("one_liner", "what_it_does", "how_it_makes_money", "verdict")]
        + [str(x) for x in (data.get("why_it_works") or [])]
        + [str(x) for x in (data.get("playbook") or [])]
    )
    ghosts = sorted(v for v in extract_numbers(body) if not ok(v))
    problems = []
    if ghosts:
        shown = ", ".join(("%g" % g) for g in ghosts[:6])
        problems.append("出现素材里没有的数字（%s）—— 只能引用素材数字，"
                        "或由素材数字算出来的值" % shown)
    return problems, ghosts


def validate(data, src_numbers=None, summary=None):
    """返回问题列表；空列表 = 通过机器校验。"""
    problems = []
    if src_numbers:
        p, _g = check_numbers(data, src_numbers)
        problems += p
    # ⚠ 必须写 `is not None`：空 dict 是 falsy，写成 `if summary:` 会在
    # slug 对不上 summary 时**把口径/增速守卫整个跳过** —— 而那种条目恰恰
    # 最需要守卫（素材为空，模型更容易编）。实测 fireply-ai 就是这么漏过去的。
    if summary is not None:
        problems += check_growth_claims(data, summary)
        problems += check_caliber(data, summary)
    for k in ("one_liner", "what_it_does", "how_it_makes_money", "verdict"):
        if not _has_cjk(data.get(k)):
            problems.append("%s 不是中文" % k)
    for i, item in enumerate(data.get("why_it_works") or [], 1):
        if not _has_cjk(item):
            problems.append("why_it_works[%d] 不是中文" % i)
        for b in BAN_PHRASES:
            if b in item:
                problems.append("why_it_works[%d] 含空话「%s」" % (i, b))
    for i, item in enumerate(data.get("playbook") or [], 1):
        if not _has_cjk(item):
            problems.append("playbook[%d] 不是中文" % i)
        for b in BAN_PHRASES:
            if b in item:
                problems.append("playbook[%d] 含空话「%s」" % (i, b))
    rep = data.get("replicability") or {}
    if set(rep) != {"tech", "distribution", "capital", "timing"}:
        problems.append("replicability 键名不对：%s" % ",".join(sorted(rep)))
    problems += check_currency_format(data)
    return problems


# TrustMRR 所有收入都是 USD，库里惯例用 $ 符号；模型常误写成「元」或裸 .0 浮点。
# 这两类是确定性机械错误，拦下来比事后修更稳。


def check_currency_format(data):
    """返回问题列表。拦：误用人民币「元」（排除「欧元」）、裸 .0 浮点。"""
    problems = []
    fields = ["one_liner", "what_it_does", "how_it_makes_money", "verdict"]
    fields += ["why_it_works", "playbook"]  # 列表字段一并扫
    texts = []
    for k in fields:
        v = data.get(k)
        if isinstance(v, str):
            texts.append((k, v))
        elif isinstance(v, list):
            for i, it in enumerate(v):
                if isinstance(it, str):
                    texts.append(("%s[%d]" % (k, i), it))
    for label, t in texts:
        # 货币「元」= 数字紧邻（或隔空格）在前；排除「欧元」「元素」等含「元」的字。
        if re.search(r"\d\s*元", t):
            problems.append("%s 用了人民币「元」，库里收入全是 USD，应写 $" % label)
            break
        # 裸 .0 浮点（整数被写成浮点），排除正常的非 .0 小数
        for mm in re.findall(r"\d+\.0(?!\d)", t):
            problems.append("%s 含裸 .0 浮点「%s」，整数不要带 .0" % (label, mm))
            break
    return problems


def facts_card(slug, s, md):
    """把素材压成一张事实卡——只保留可核验字段，缺失的明写「未公开」。"""
    def g(k):
        v = s.get(k)
        return v if v not in (None, "", 0) else None

    lines = ["== 一手档案素材（TrustMRR，支付网关 API 直读）=="]
    lines.append("产品名 / slug：%s" % slug)
    if g("description"):
        lines.append("官网一句话：%s" % g("description"))
    lines.append("创始人：%s" % (g("founder") or "未公开"))
    lines.append("成立时间：%s" % (g("founded") or "未公开"))
    lines.append("支付网关：%s" % (s.get("provider") or "未公开"))
    lines.append("收入同步时间：%s" % (s.get("revenue_synced") or "未公开"))

    lines.append("")
    lines.append("-- 收入（不同口径不可混用）--")
    lines.append("当前 MRR：%s" % (s.get("cur_mrr") if s.get("cur_mrr") else 0))
    lines.append("近 24 小时：%s" % s.get("rev_24h"))
    lines.append("近 7 天：%s" % s.get("rev_7d"))
    lines.append("近 30 天已验证收入：%s" % (s.get("rev_30d") or "未公开"))
    lines.append("累计：%s" % (s.get("all_time") or "未公开"))
    lines.append("活跃订阅数：%s" % (s.get("active_subs") if s.get("active_subs") else "未公开/非订阅制"))
    lines.append("毛利率：%s" % (s.get("profit_margin") if s.get("profit_margin") is not None else "未公开"))
    lines.append("近 30 天增速：%s" % (s.get("growth_30d") if s.get("growth_30d") is not None else "未给"))
    lines.append("MRR 增速：%s" % (s.get("mrr_growth") if s.get("mrr_growth") is not None else "未给"))

    # 客单价**由脚本先算好**再喂进去。
    # 为什么不能让模型自己除：实测 glm-4-flash 算 12454÷407 给出 29.04
    #（正确 30.60）、1050÷41 给出 25.85（正确 25.61）—— 它会算错。
    # 而这个库卖的就是「每个数字都核过」，让模型做除法等于把最不该出错的
    # 一步交给最会出错的环节。口径：MRR ÷ 活跃订阅数。
    mrr = s.get("cur_mrr") or 0
    subs = s.get("active_subs") or 0
    if mrr > 0 and subs > 0:
        arpu = round(mrr / subs, 2)
        lines.append("客单价（MRR ÷ 活跃订阅数，已算好，直接用）：$%s" % arpu)
    else:
        lines.append("客单价：非订阅制或订阅数未公开，无法推算——不要硬算")

    lines.append("")
    lines.append("-- 档案自述字段（未经二次核实，引用时要标明「档案自述」）--")
    for k, label in (("value_prop", "价值主张"), ("problem", "解决的问题"),
                     ("pricing_model", "定价"), ("persona", "目标用户"),
                     ("audience", "客群类型"), ("team_size", "团队规模"),
                     ("funding", "融资"), ("user_count", "用户数"),
                     ("x_followers", "X 粉丝")):
        v = s.get(k)
        lines.append("%s：%s" % (label, v if v not in (None, "", 0) else "未公开"))
    if s.get("markets"):
        lines.append("档案分类：%s" % s["markets"])

    if md:
        lines.append("")
        lines.append("-- 档案原文（.md，llms.txt 官方入口抓取）--")
        lines.append(md[:4000])
    return "\n".join(lines)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def lookup_summary(summ, slug, cid):
    """按 slug 找素材，找不到再按候选 id 找。

    为什么需要兜底：候选的 `trustmrr_slug` 与 summary 的键并不总是一致
    （fireply-ai 的 slug 是 `fireply`，而 summary 的键是 `fireply-ai`）。
    查不到就会拿到空素材 —— 那不仅是少一点信息，而是把守卫喂了个空 dict，
    模型对着空气编数字时反而没人管。
    """
    for key in (slug, cid):
        if key and key in summ:
            return summ[key]
    return None


def save_stage(obj):
    with open(STAGE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def draft_one(cid, slug, summ, retries=2):
    s = lookup_summary(summ, slug, cid) or {}
    md_path = os.path.join(TM_DIR, slug + ".md")
    md = ""
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            md = f.read()
    if not s and not md:
        return None, "无素材"

    card = facts_card(slug, s, md)
    src_numbers = extract_numbers(card)
    last = ""
    for attempt in range(retries + 1):
        try:
            out = ai_verify.llm(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": card}],
                temperature=0.3,
                # 智谱 glm-4-flash 怪癖：response_format=json_object + 长生成
                # 会 HTTP 500 或读超时（小 JSON 正常）。所以这里**主动限流输出**，
                # 而不是出问题再加预算。
                max_tokens=2200,
            )
        except Exception as e:  # noqa: BLE001
            last = "LLM 失败：%s" % e
            continue

        try:
            data = json.loads(out)
        except Exception:  # noqa: BLE001
            last = "返回不是合法 JSON"
            continue
        if not isinstance(data, dict) or not data.get("one_liner"):
            last = "返回缺 one_liner"
            continue

        problems = validate(data, src_numbers, s)
        if problems and attempt < retries:
            # 把校验结果塞回提示词让它重来——比直接丢弃有用。
            # 实测有效：第一次被打回后，模型会把编的增速换掉。
            last = "校验不过：%s" % "；".join(problems[:3])
            card = (card
                    + "\n\n== 上一稿被打回，原因是：%s\n"
                      "请重写。额外要求：① 全部中文；② 每条判断必须挂素材里的"
                      "具体数字或事实，素材没给增速就一个字都别写增速；"
                      "③ 禁止复述式空话；④ 金额写成 $12,454 这种带千分位的形式，"
                      "不要写 12454.0。==" % "；".join(problems[:5]))
            continue

        data["_slug"] = slug
        data["_drafted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        # 仍有问题也留着，但打上标记 —— apply 步骤会跳过这些，交人工改
        data["_problems"] = problems
        data["_needs_human"] = bool(problems)
        return data, None
    return None, last or "未知失败"


def main():
    ap = argparse.ArgumentParser(description="批量起草案例正文到暂存区")
    ap.add_argument("--ids", help="逗号分隔的候选 id")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--only-missing", action="store_true",
                    help="跳过暂存区里已经有的")
    ap.add_argument("--sleep", type=float, default=1.0, help="每条之间间隔秒")
    ap.add_argument("--revalidate", action="store_true",
                    help="不调 LLM：把守卫回套到暂存区已有草稿上，"
                         "只更新 _problems / _needs_human")
    args = ap.parse_args()

    cands = load_json(os.path.join(ROOT, "data", "candidates.json"), [])
    summ = load_json(os.path.join(TM_DIR, "summary.json"), {})
    stage = load_json(STAGE, {})

    # 守卫升级后不必重跑 LLM：把新规则回套到已有草稿即可。
    # （口径守卫就是这么加的 —— 重跑要一小时，回套只要一秒。）
    if args.revalidate:
        bad = 0
        for cid, v in stage.items():
            slug = v.get("_slug") or cid
            s = lookup_summary(summ, slug, cid) or {}
            # ⚠ 必须与起草时喂给模型的素材**完全一致**（含 .md 原文），
            # 否则素材里的数字会被误判成「凭空出现」—— 回套第一次就踩了：
            # 漏掉 .md 后把日期（2026）和 .md 里的收入数字报成了编造。
            md = ""
            md_path = os.path.join(TM_DIR, slug + ".md")
            if os.path.exists(md_path):
                with open(md_path, "r", encoding="utf-8") as f:
                    md = f.read()
            card_src = extract_numbers(facts_card(slug, s, md))
            problems = validate(v, card_src, s)
            v["_problems"] = problems
            v["_needs_human"] = bool(problems)
            if problems:
                bad += 1
                print("  ⚠ %-34s %s" % (cid[:34], "；".join(problems)[:80]))
        save_stage(stage)
        print("\n回套完成：%d 条草稿，其中 %d 条被守卫拦下" % (len(stage), bad))
        return 0

    targets = []
    by_id = {c.get("id"): c for c in cands}
    if args.ids:
        wanted = [x.strip() for x in args.ids.split(",") if x.strip()]
    else:
        wanted = [c.get("id") for c in cands]
        if args.limit:
            wanted = wanted[:args.limit]

    for cid in wanted:
        if args.only_missing and cid in stage:
            continue
        c = by_id.get(cid)
        if not c:
            continue
        targets.append((cid, c.get("trustmrr_slug") or cid))

    print("待起草 %d 条（暂存区已有 %d 条）\n" % (len(targets), len(stage)))

    ok = fail = 0
    for i, (cid, slug) in enumerate(targets, 1):
        data, err = draft_one(cid, slug, summ)
        if err:
            fail += 1
            print("  [%d/%d] ✗ %-32s %s" % (i, len(targets), cid[:32], err))
        else:
            ok += 1
            stage[cid] = data
            print("  [%d/%d] ✓ %-32s %s" % (
                i, len(targets), cid[:32], (data.get("one_liner") or "")[:44]))
        # 每 10 条落一次盘：中途挂掉不丢全部
        if i % 10 == 0:
            save_stage(stage)
        if args.sleep and i < len(targets):
            time.sleep(args.sleep)

    save_stage(stage)
    print("\n完成：成功 %d，失败 %d → %s" % (ok, fail, STAGE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
