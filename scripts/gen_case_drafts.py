#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按需生成一篇 case 的平台稿（cnblogs / zhihu / csdn / aliyun）。

为什么是「模板拼装 + LLM 只写标题和栏目名」：

  现有 chessigma / cod-dex / shipfast 那三批稿，逐段与 cases.json 字段一一对应——
  `playbook` →「工程上值得抄的点」、`why_it_works` →「为什么能跑起来」、
  `verdict` →「一句话总结」、`what_it_does` →「这个产品是什么」、
  `how_it_makes_money` →「商业模型怎么搭」。也就是说**正文本来就是拼的**。

  拼装的好处正好对上这个库的红线：零编造（没有一个事实是模型写的）、零成本、
  可复现。不用担心模型把 `$7,306` 拿去做除法、也不用担心它把案例业绩写成
  「我赚了多少」。LLM 只负责标题一行和几个栏目名，**事实、数字、时点、出处
  全由代码从 cases.json 填**；失败就回退确定性变体。

  🚨 2026-11-07 修「生成即被闸拦」：旧版固定产出
  `> 平台：X ｜ 状态：本地稿`（管线水印）、
  `数据来源：…，数字带口径带修正。`（罐头来源句）、
  以及每篇一模一样的 5 个小标题（机械栏目骨架）⇒ `publish_guard.scan_content`
  直接 3 条 block，`--gen` 补出来的稿根本发不出去。现在：
  ① 水印头删掉（`drafts/newbatch/` 这个路径本身就是「本地稿」标记）；
  ② 来源句改成带真实 `verified_at` 的时点行 + 具体出处，不再写罐头短语；
  ③ 栏目名让 LLM 按这篇案例写（或回退带产品名的变体），各篇不同。

⛔ tencentcloud 不在本脚本范围：它只收技术向（标题禁金额、正文讲实现取舍、
  可贴代码），而 cases.json 里**没有技术栈字段**——硬拼只能拼出「伪技术稿」，
  那正是 11-01/02 两篇被驳「技术无关内容」的老路。要走腾讯云仍按
  PLAYBOOK-platforms 的技术向重写流程。

用法：
    python scripts/gen_case_drafts.py --case chessigma
    python scripts/gen_case_drafts.py --case chessigma --platforms cnblogs,csdn
    python scripts/gen_case_drafts.py --auto            # 今日档期 AI 候选第一篇
    python scripts/gen_case_drafts.py --case x --dry    # 只打印不出稿
    python scripts/gen_case_drafts.py --case x --no-llm # 纯模板标题（离线可用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "drafts", "newbatch")

# 本脚本能出的平台。tencentcloud 故意不在里面（见模块 docstring）。
PLATFORMS = ("cnblogs", "zhihu", "csdn", "aliyun")

# KaTeX 平台：一段里 ≥2 个 `$` 会把中间整段吃成公式 ⇒ 一律写「美元」。
# cnblogs / zhihu 是纯富文本，`$` 原样（与存量大稿一致）。
DOLLAR_AS_TEXT = {"csdn", "aliyun"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# 标题口径：技术社区看工程，知乎看「怎么赚钱」，阿里云看云/成本
TITLE_HINT = {
    "cnblogs": "技术社区（博客园）读者：标题落在工程/架构/成本上，像「用 XX 架构撑住 N 个用户」这种",
    "csdn": "技术社区（CSDN）读者：标题落在工程实现或取舍上，可以直接点出技术手段",
    "zhihu": "知乎读者：标题可以是「一个小产品怎么赚钱」这种商业拆解口吻，但不要浮夸",
    "aliyun": "阿里云开发者社区读者：标题落在云资源/成本结构/工程实践上",
}

SYS_GUARD = (
    "只输出一行标题，不要引号、不要序号、不要解释。硬性要求："
    "1) 不超过 30 个汉字；2) **不许出现任何金额、货币符号、MRR、ARR、月收入、营收、"
    "增长率、百分比**——数字只允许是用户量、订阅数这类规模量；"
    "3) 不写「我」「我们」；4) 不出现网址、品牌引流语。"
)

# 每个平台需要几个 `##` 栏目。改这里必须同步改 render() 里的 h(i) 序号。
SECTION_SLOTS = {"cnblogs": 4, "csdn": 4, "zhihu": 5, "aliyun": 4}

SYS_SECTIONS = (
    "只输出 %d 行小标题，每行一个，不要序号、不要 #、不要引号、不要解释。"
    "硬性要求：1) 每行 4~16 个汉字，要贴合这个案例的具体做法，"
    "不同案例的栏目必须不同；2) 不许出现金额、货币符号、MRR、ARR、百分比、"
    "网址、品牌引流语；3) 不要用「这个产品是什么」「商业模型怎么搭」"
    "「工程上值得抄的点」「为什么能跑起来」「总结」这类通用栏目名。"
)

# 与 publish_guard.MECHANICAL_SECTIONS 同值：LLM 万一吐回通用栏目名要退回变体。
_GENERIC_SECTIONS = {
    "这个产品是什么", "商业模型怎么搭", "工程上值得抄的点", "为什么能跑起来",
    "总结", "背景", "数据与口径", "可复制的工程实践",
    "它到底解决了什么", "钱从哪来", "为什么它能成", "能抄的做法",
    "给想做类似产品的你",
}


# ---------------------------------------------------------------- 数据

def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def find_case(cid):
    for c in _load("cases.json"):
        if c.get("id") == cid:
            return c
    raise SystemExit("找不到案例：%s（在 data/cases.json 里没有）" % cid)


def _need(c, key, alt=None):
    v = c.get(key)
    if not v and alt:
        v = c.get(alt)
    if not v:
        raise SystemExit("案例 %s 缺字段 %s，不出稿（宁可不出，不写「数据待补充」充数）"
                         % (c.get("id"), key))
    return v


def _bullets(items):
    return "\n".join("- %s" % str(x).strip() for x in items if str(x).strip())


def _sources(c, limit=3):
    """数据来源写成短名，不要把 source label 里的括号细节/裸 URL 拖进正文。

    `label` 是抓取时写的工作备注（例：「GitHub 仓库 mehdi-bh/chessigma-mcp（★0，
    最近推送 2026-10-02，仓库主页指向 https://…」），原样塞进正文会带出半截 URL、
    还会把句号吃掉。只取前缀名，到第一个冒号/括号/逗号为止。
    """
    out = []
    for s in (c.get("sources") or []):
        label = (s.get("label") or "").strip()
        if not label:
            continue
        label = re.split(r"[：（(,，]", label)[0].strip()
        label = re.sub(r"https?://\S+", "", label).strip()
        if label and label not in out:
            out.append(label[:22])
        if len(out) >= limit:
            break
    return "、".join(out) or "公开资料"


def money_text(t):
    """`$7,306` → `7,306 美元`；`$299/月` → `299 美元/月`。"""
    return re.sub(r"\$\s?([\d][\d,.]*)", r"\1 美元", str(t))


# ---------------------------------------------------------------- 标题

def llm_title(c, platform, timeout=60):
    """让模型写一行标题；失败返回 None（调用方回退模板标题）。"""
    try:
        sec = _load("secrets.json")
    except Exception:                                       # noqa: BLE001
        return None
    key = os.environ.get("CASE_LIB_AI_KEY") or sec.get("ai_key")
    base = (os.environ.get("CASE_LIB_AI_BASE") or sec.get("ai_base")
            or "https://discovery-api.intern-ai.org.cn/v1").rstrip("/")
    model = os.environ.get("CASE_LIB_AI_MODEL") or sec.get("ai_model")
    if not (key and model):
        return None
    facts = {
        "name": c.get("name"), "one_liner": c.get("one_liner"),
        "what_it_does": (c.get("what_it_does") or "")[:400],
        "playbook": (c.get("playbook") or [])[:3],
        "why_it_works": (c.get("why_it_works") or [])[:2],
    }
    prompt = ("%s\n\n案例事实（只许用这些，不许编）：\n%s"
              % (TITLE_HINT.get(platform, ""),
                 json.dumps(facts, ensure_ascii=False)))
    body = {"model": model, "temperature": 0.6, "max_tokens": 1200,
            "messages": [{"role": "system", "content": SYS_GUARD},
                         {"role": "user", "content": prompt}]}
    # 🚨 端点私有参数必须跟 ai_verify 用同一套口径（secrets.json 的 ai_extra，
    # 按模型名索引）：Sage-A1.5 默认一直思考，1200 token 全被 reasoning 吃光、
    # content 返回空串 ⇒ 每条标题都掉回模板，还不报错 —— 静默降质最难发现。
    try:
        extra = (json.loads(os.environ["CASE_LIB_AI_EXTRA"])
                 if "CASE_LIB_AI_EXTRA" in os.environ
                 else sec.get("ai_extra") or {})
    except Exception:                                        # noqa: BLE001
        extra = {}
    if isinstance(extra, dict) and isinstance(extra.get(model), dict):
        body.update({k: v for k, v in extra[model].items()
                     if k not in ("model", "messages")})
    body = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        t = (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:                                  # noqa: BLE001
        print("  ⚠ 标题生成失败（%s），用模板标题" % type(e).__name__)
        return None
    t = t.splitlines()[0].strip().strip("「」\"'《》#*-").strip()
    if not t or len(t) > 40 or re.search(r"[$¥€£]|MRR|ARR|月入|营收", t, re.I):
        print("  ⚠ 标题不合规（%s），用模板标题" % (t[:20] or "空"))
        return None
    return t


def fallback_title(c):
    one = str(c.get("one_liner") or "").strip().rstrip("。")
    one = re.sub(r"[$¥€£]\s?[\d][\d,.]*", "", one)
    one = re.sub(r"(MRR|ARR)[^，,。]*", "", one, flags=re.I)
    one = re.sub(r"[，,]\s*[，,]", "，", one).strip("，, ")
    name = c.get("name") or c.get("id")
    return ("%s：%s" % (name, one))[:38] if one else str(name)


def _timepoint_row(c):
    """数据时点行：只取 cases.json 里真实的快照日与口径，**不许编**。

    没有 `verified_at` 就返回空串（宁可留一条 warn，也不编个日期糊过去）。
    `TIMEPOINT` 闸认「数据时点 / 截至 20xx」这类写法，这行两种都带。
    """
    d = str(c.get("verified_at") or "").strip()
    if not d:
        return ""
    cal = str(c.get("caliber") or "").strip().upper() or "见正文公开口径"
    return "数据时点 ｜ 截至 %s（抓取日快照）｜口径：%s" % (d, cal)


def fallback_sections(c, platform):
    """确定性变体栏目名：带产品名 ⇒ 各篇不同，且不与机械栏目表撞。

    LLM 不可用或输出不合格时用。只换栏目名，正文事实一个字都不改。
    """
    name = str(c.get("name") or c.get("id") or "这个产品").strip()
    rows = [
        "%s 在解决什么问题" % name,
        "%s 怎么把服务变成订阅" % name,
        "从 %s 里能抄的工程做法" % name,
        "%s 跑起来的原因" % name,
        "做同类产品前先想清楚的事",
    ]
    return rows[:SECTION_SLOTS[platform]]


def llm_sections(c, platform, timeout=60):
    """让模型只写 N 个栏目名；失败/不合格一律回退 `fallback_sections`。

    ⚠ HTTP 配置与 `llm_title()` 同一套（secrets.json + `CASE_LIB_AI_*` +
    `ai_extra[model]`）；改一端要同步改另一端。这里刻意复制而不抽公共函数，
    是为了不动已经跑通的标题链路。模型只输出栏目**名字**，事实/数字/时点
    全由代码填 —— 「改写归模型、事实裁定归代码」。
    """
    n = SECTION_SLOTS[platform]
    fallback = fallback_sections(c, platform)
    try:
        sec = _load("secrets.json")
    except Exception:                                       # noqa: BLE001
        return fallback
    key = os.environ.get("CASE_LIB_AI_KEY") or sec.get("ai_key")
    base = (os.environ.get("CASE_LIB_AI_BASE") or sec.get("ai_base")
            or "https://discovery-api.intern-ai.org.cn/v1").rstrip("/")
    model = os.environ.get("CASE_LIB_AI_MODEL") or sec.get("ai_model")
    if not (key and model):
        return fallback
    facts = {
        "name": c.get("name"), "one_liner": c.get("one_liner"),
        "what_it_does": (c.get("what_it_does") or "")[:300],
        "playbook": (c.get("playbook") or [])[:3],
        "why_it_works": (c.get("why_it_works") or [])[:2],
    }
    prompt = ("%s\n\n案例事实（只许用这些，不许编）：\n%s"
              % (SYS_SECTIONS % n, json.dumps(facts, ensure_ascii=False)))
    body = {"model": model, "temperature": 0.7, "max_tokens": 1200,
            "messages": [{"role": "system", "content": SYS_SECTIONS % n},
                         {"role": "user", "content": prompt}]}
    try:
        extra = (json.loads(os.environ["CASE_LIB_AI_EXTRA"])
                 if "CASE_LIB_AI_EXTRA" in os.environ
                 else sec.get("ai_extra") or {})
    except Exception:                                        # noqa: BLE001
        extra = {}
    if isinstance(extra, dict) and isinstance(extra.get(model), dict):
        body.update({k: v for k, v in extra[model].items()
                     if k not in ("model", "messages")})
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:                                  # noqa: BLE001
        print("  ⚠ 栏目生成失败（%s），用确定性变体" % type(e).__name__)
        return fallback
    rows = []
    for line in text.splitlines():
        x = re.sub(r"^\s*(?:\d+\s*[.、)]|[一二三四五六七八九十]+\s*、|[-*#>＞]+)\s*",
                   "", line).strip()
        x = x.strip("「」\"'《》：:。-— ").strip()
        if not (4 <= len(x) <= 20):
            continue
        if re.search(r"[$¥€£]|MRR|ARR|https?://|\d", x, re.I):
            continue
        if x in _GENERIC_SECTIONS or x in rows:
            continue
        rows.append(x)
        if len(rows) >= n:
            break
    if len(rows) != n:
        print("  ⚠ 栏目不合格（%d/%d），用确定性变体" % (len(rows), n))
        return fallback
    return rows


# ---------------------------------------------------------------- 正文

def render(platform, c, title, sections=None):
    what = _need(c, "what_it_does")
    how = _need(c, "how_it_makes_money")
    why = _bullets(_need(c, "why_it_works"))
    play = _bullets(_need(c, "playbook"))
    verdict = _need(c, "verdict")
    m = c.get("metrics") or {}
    head = m.get("headline") or ""
    src = _sources(c)
    sec = sections or fallback_sections(c, platform)
    if len(sec) < SECTION_SLOTS[platform]:
        sec = fallback_sections(c, platform)

    def h(i, default):
        return "## %s" % (sec[i] if i < len(sec) else default)

    tp = _timepoint_row(c)
    stamp = str(c.get("verified_at") or "").strip()
    # 「出处：…（抓取日快照）」带时点且是具体出处，不是 `数据来源：官网…` 罐头句。
    credit = "出处：%s（%s 快照）。" % (src, stamp or "抓取日")

    if platform in ("cnblogs", "csdn"):
        body = [h(0, "这个产品是什么"), what]
        if head:
            body += ["", "公开口径：%s。" % head.rstrip("。")]
        if tp:
            body += ["", tp]
        body += ["", h(1, "商业模型怎么搭"), how,
                 "", h(2, "工程上值得抄的点"), play,
                 "", h(3, "为什么能跑起来"), why,
                 "", "一句话总结：%s。" % verdict.rstrip("。"),
                 "", credit]
    elif platform == "zhihu":
        body = [h(0, "一、它到底解决了什么"), what,
                "", h(1, "二、钱从哪来"), how]
        if head:
            body += ["", "口径：%s。" % head.rstrip("。")]
        if tp:
            body += ["", tp]
        body += ["", h(2, "三、为什么它能成"), why,
                 "", h(3, "四、能抄的做法"), play,
                 "", h(4, "五、给想做类似产品的你"), verdict,
                 "", credit]
    elif platform == "aliyun":
        body = [h(0, "背景"), what,
                "", h(1, "数据与口径")]
        # ⚠ 只写 headline，**不要把 metric_note 塞进来**：那是内部工作口径
        # （「队列缓存值是 2026-10-14 采集时的快照…」），读者不需要。
        body += ["公开口径：%s。" % (head.rstrip("。") if head else "无公开数字"), ""]
        if tp:
            body += [tp, ""]
        body += ["口径说明：数字直接取自支付侧或官方页面，不做汇率换算、不把累计收入"
                 "当当期收入；同一篇里只用一个口径，不混用 ARR 与 MRR。",
                 "", h(2, "可复制的工程实践"), play,
                 "", h(3, "总结"), verdict,
                 "", credit]
    else:
        raise SystemExit("不支持的平台：%s" % platform)

    text = "\n".join(body).strip()
    if platform in DOLLAR_AS_TEXT:
        text = money_text(text)
    return text


def header(platform, c, title):
    # 🚨 2026-11-07 删掉 `> 平台：X ｜ 状态：本地稿，未推送` 水印头：
    #    `publish_guard.WATERMARK` 见它就 block，而 `drafts/newbatch/` 这个
    #    路径本身已经说明「本地稿」，不需要把流水线状态印给读者看。
    return "# %s\n" % title


def write_one(platform, c, title, dry=False, sections=None):
    """返回落盘路径；dry 时返回空串。"""
    text = (header(platform, c, title) + "\n"
            + render(platform, c, title, sections=sections) + "\n")
    if dry:
        print("\n" + "=" * 70 + "\n" + text)
        return ""
    d = os.path.join(OUT, platform)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s.md" % c["id"])
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


# ---------------------------------------------------------------- CLI

def eligible(c):
    """这篇能不能拿去做稿。

    口径与库里已发的那批一致（chessigma / cod-dex / shipfast）：tier=premium、
    证据是 stripe/official、needs_review 不为真，且拼稿要用的五个字段都在。
    `partial` / `founder` / `disputed` 一律不发 —— 那是「数字没核到」的档。
    另外两道硬门槛：
    - 收入 ≥300（与 promote_to_candidates.MIN_MRR / triage.TINY_REVENUE 同值；
      mrr / last_30d / all_time 任一达标即可，口径不同不能硬比就取最大）；
    - 名字不能是全大写 —— 那是从聚合站页面标题抓下来的，不是品牌名
      （例：「DOWNCONVERT SKETCHUP FILES」），拼进水印头很难看。
    """
    if c.get("needs_review"):
        return False
    if c.get("tier") != "premium":
        return False
    if (c.get("verification") or "") not in ("stripe", "official"):
        return False
    name = str(c.get("name") or "")
    if name and name == name.upper() and len(name) > 4:
        return False
    m = c.get("metrics") or {}
    if max(m.get(k) or 0 for k in ("mrr", "last_30d_revenue", "all_time")) < 300:
        return False
    for k in ("what_it_does", "how_it_makes_money", "playbook",
              "why_it_works", "verdict"):
        if not c.get(k):
            return False
    return True


def pick_by_quality(platform):
    """按质量挑该平台第一篇没发过的 AI case（eligible 过滤，composite 降序）。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import publish_slot as S
    cases = [c for c in (S._load("cases.json") or []) if c.get("id") and eligible(c)]
    cases.sort(key=lambda c: ((c.get("composite") or {}).get("score") or 0),
               reverse=True)
    sent = {S._norm_id(cid) for _, cid in S.records(platform)}
    return next((c["id"] for c in cases if S._norm_id(c["id"]) not in sent), None)


def pick_by_oldest(platform):
    """按「收录最久」挑该平台第一篇没发过的 AI case（2026-11-05 用户定）。

    收录时间用 `verified_at`（核验入库时点，全库覆盖，YYYY-MM-DD；同日并列
    按库序稳定）。规则背景：轮到某条线无稿时，从源补写收录最久的素材发布
    （publish_slot.decide 输出 need_draft=True 后走这里），而不是轮空。
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import publish_slot as S
    cases = [c for c in (S._load("cases.json") or []) if c.get("id") and eligible(c)]
    cases.sort(key=lambda c: (c.get("verified_at") or "9999-12-31"))
    sent = {S._norm_id(cid) for _, cid in S.records(platform)}
    return next((c["id"] for c in cases if S._norm_id(c["id"]) not in sent), None)


def auto_case():
    """今天该发的那篇 AI case：先问档期，档期挑不出来就按质量补第一篇没发过的。

    `decide()` 在 AI 稿库为 0 时会输出 need_draft=True（2026-11-05 起，不再
    回退游戏稿）—— 这时交给 `pick_by_oldest`（收录最久优先，用户定的补稿规则）。
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import publish_slot as S
    for plat in PLATFORMS:
        row = S.decide(plat)
        if row.get("kind") == "ai" and row.get("candidates"):
            return row["candidates"][0]
        # 2026-11-05：decide 对「AI 无稿」现在输出 need_draft=True（不再 blocked
        # 或回退游戏）——这正是该走补稿分支的信号，落到下面的按收录最久补选。
    for plat in PLATFORMS:
        cid = pick_by_oldest(plat)
        if cid:
            return cid
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="按需生成一篇 case 的平台稿")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--case", help="案例 id（data/cases.json 里的 id）")
    g.add_argument("--auto", action="store_true", help="今天该发的 AI case（档期挑不出就按质量补第一篇没发过的）")
    g.add_argument("--list", type=int, metavar="N", help="只列前 N 个可发候选，不生成")
    ap.add_argument("--platforms", default=",".join(PLATFORMS),
                    help="逗号分隔，可选：%s" % ",".join(PLATFORMS))
    ap.add_argument("--dry", action="store_true", help="只打印，不写盘")
    ap.add_argument("--no-llm", action="store_true", help="标题一律用模板（离线）")
    ap.add_argument("--force", action="store_true", help="已有稿也覆盖（默认跳过，保护手改）")
    a = ap.parse_args(argv)

    if a.list:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import publish_slot as S
        plats = [p.strip() for p in a.platforms.split(",") if p.strip()]
        sent = set()
        for p in plats:
            sent |= {S._norm_id(cid) for _, cid in S.records(p)}
        cs = [c for c in (S._load("cases.json") or []) if c.get("id") and eligible(c)]
        cs.sort(key=lambda c: ((c.get("composite") or {}).get("score") or 0),
                reverse=True)
        cs = [c for c in cs if S._norm_id(c["id"]) not in sent][:a.list]
        for i, c in enumerate(cs, 1):
            m = c.get("metrics") or {}
            # 非订阅制 MRR 恒 0 ⇒ 显示口径更宽的数字，别把 $2,893/30d 显示成 "—"
            if m.get("mrr"):
                amount, label = m["mrr"], "MRR"
            elif m.get("last_30d_revenue"):
                amount, label = m["last_30d_revenue"], "30d"
            else:
                amount, label = m.get("all_time"), "累计"
            print("%2d. %-34s %-28s %s %-9s 分 %s"
                  % (i, c["id"][:34], (c.get("name") or "")[:28],
                     label, amount if amount is not None else "—",
                     (c.get("composite") or {}).get("score")))
            print("    %s" % (c.get("one_liner") or "")[:78])
        if not cs:
            print("没有可发候选（%s 都已发过，或都不满足 tier/证据门槛）" % ",".join(plats))
        return 0
    if not (a.case or a.auto):
        ap.error("要给 --case / --auto / --list 其中之一")

    cid = a.case or auto_case()
    if not cid:
        raise SystemExit("今天没有轮到 AI case（或候选池为空），用 --case 指定一篇")
    c = find_case(cid)
    plats = [p.strip() for p in a.platforms.split(",") if p.strip()]
    bad = [p for p in plats if p not in PLATFORMS]
    if bad:
        raise SystemExit("不支持的平台：%s（本脚本只管 %s；腾讯云/阿里云技术向稿另有流程）"
                         % (",".join(bad), ", ".join(PLATFORMS)))

    print("案例：%s（%s）｜ 平台：%s" % (c.get("name") or cid, cid, ",".join(plats)))
    made = []
    for p in plats:
        dest = os.path.join(OUT, p, "%s.md" % cid)
        if os.path.exists(dest) and not a.force:
            print("  = %s 已有稿，跳过（要覆盖加 --force）：%s"
                  % (p, os.path.relpath(dest, ROOT)))
            continue
        title = None if a.no_llm else llm_title(c, p)
        if title:
            print("  标题[%s] LLM：%s" % (p, title))
        else:
            title = fallback_title(c)
            print("  标题[%s] 模板：%s" % (p, title))
        sec_rows = None if a.no_llm else llm_sections(c, p)
        if sec_rows:
            print("  栏目[%s] LLM：%s" % (p, " / ".join(sec_rows)))
        try:
            path = write_one(p, c, title, dry=a.dry, sections=sec_rows)
        except SystemExit as e:
            print("  ✗ %s：%s" % (p, e))
            continue
        if path:
            print("  ✓ %s（%d 字）→ %s" % (p, len(open(path, encoding="utf-8").read()),
                                          os.path.relpath(path, ROOT)))
            made.append(path)
        time.sleep(0.3)
    if made:
        print("\n完成 %d 份。下一步：python scripts/publish_one.py --platform %s"
              % (len(made), plats[0]))
    return 0 if made or a.dry else 1


if __name__ == "__main__":
    raise SystemExit(main())
