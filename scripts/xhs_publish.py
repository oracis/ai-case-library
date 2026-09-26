#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书发布链路（试点版）。

公众号长文搬不动小红书（那边是 图文卡片 + <=1000 字正文），这里把
cases.json 的结构化字段改写成两种产物：

  1) note.md / note.json —— 笔记文案（标题<=20字、正文<=1000字、话题标签）
  2) 5 张 3:4 卡片图（1080x1440）—— 封面 / 是什么·怎么赚钱 / 为什么成立 /
     方法论 / 三张评分

卡片图复用 wechat_publish.py 的 CDP 渲染方案（data URL 打开 HTML ->
Page.captureScreenshot），字号超长自动缩，零第三方依赖。

用法：
  python scripts/xhs_publish.py build --all            # 全量生成（起临时无头 Chrome 渲染）
  python scripts/xhs_publish.py build --case voklit    # 单条
  python scripts/xhs_publish.py preview                # 生成 out/xhs/index.html 本地预览
  python scripts/xhs_publish.py publish --case voklit --dry
      # 走 9222 Chrome（要先人工登录小红书创作平台）预填笔记；--dry 只填不发
      # 不加 --yes 时填完点「存草稿」；--yes 才点「发布」
      # ⚠ 发布选择器按 2026-09 网页版创作平台写，属实验性，变了就人工补

风控注意（2026-09 定稿）：
  - 批量发帖易限流，建议人工节奏每天 <=2 条；
  - 收入表述保留原文口径（MRR 就写 MRR，成交就写成交），不夸大、不写「躺赚」类词。
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402  复用 CDP / 色板 / 案例加载

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "xhs")
CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"

TITLE_MAX = 20          # 小红书标题硬上限
BODY_MAX = 1000         # 小红书正文硬上限
CARD_W, CARD_H = 1080, 1440   # 3:4
BUILD_PORT = 9333       # 临时无头 Chrome（只渲染卡片，不碰 9222 登录态）
PUB_PORT = 9222         # 发布用，与公众号同一调试 Chrome

# ---- 文案：话题标签 --------------------------------------------------------

BASE_TAGS = ["出海创业", "独立开发", "商业拆解", "一人公司"]

TAGS_BY_CATEGORY = {
    "AI 工具": ["AI工具", "AI创业"],
    "开发者工具": ["开发者工具", "API"],
    "营销工具": ["营销自动化", "增长黑客"],
    "垂直行业 SaaS": ["SaaS"],
    "企业服务": ["B端产品", "SaaS"],
    "交易市场": ["交易平台"],
    "创作者经济": ["创作者经济"],
    "电商": ["跨境电商"],
    "客户服务": ["SaaS"],
    "组合策略": ["创业思路"],
    "未分类": [],
}


def load_cases():
    return wp.load_cases()


def find_case(cid):
    for c in load_cases():
        if c.get("id") == cid:
            return c
    raise SystemExit("case not found: %s" % cid)


def find_case_fuzzy(q):
    """认名字/标题片段，不用背 id。「bustem」「网页小组件」「Pieter」都行。"""
    cs = load_cases()
    for c in cs:                                   # 精确 id 优先
        if c.get("id") == q:
            return c
    low = q.lower()
    hit = [c for c in cs
           if low in (c.get("id") or "").lower()
           or low in (c.get("name") or "").lower()
           or low in (c.get("title") or "" if isinstance(c.get("title"), str) else "").lower()]
    if not hit:
        raise SystemExit("没匹配到案例：%s" % q)
    if len(hit) > 1:
        raise SystemExit("「%s」匹配到多个案例，请写全一点：\n  %s"
                         % (q, "\n  ".join("%s —— %s" % (c["id"], c.get("name", ""))
                                           for c in hit)))
    return hit[0]


# ---- 纯函数：文案改写 ------------------------------------------------------

def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace('"', "&quot;")


def compact_amount(m):
    """$128,000 -> $128K；$1.6K/$842 原样。"""
    m = m.replace(" ", "")
    g = re.match(r"^\$([\d,]+(?:\.\d+)?)([KM]?)$", m)
    if not g:
        return m
    num, suf = g.group(1), g.group(2)
    if suf:
        return "$" + num + suf
    try:
        v = int(num.replace(",", ""))
    except ValueError:
        return m
    # 别用 %g：1358 会出「$1.358K」这种三位小数，卡片上很难看（2026-09-25）。
    # K 级：≥10K 取整，否则 1 位小数；M 级：≥100M 取整，否则 2 位。
    if v >= 1000000:
        m = v / 1000000.0
        return ("$%.0fM" % m) if m >= 100 else ("$%.2fM" % m)
    if v >= 1000:
        k = v / 1000.0
        return ("$%.0fK" % k) if k >= 10 else ("$%.1fK" % k)
    return "$%d" % v


def first_amount(headline):
    m = re.search(r"\$\s?[\d,]+(?:\.\d+)?\s?[KM]?(?![\dA-Za-z])",
                  headline or "")
    return compact_amount(m.group(0)) if m else ""


def short_desc(c):
    """给标题/正文用的一句话业务描述：one_liner 可用就用它，否则按品类兜底。"""
    d = (c.get("one_liner") or "").strip()
    if (not d) or d.startswith("（待补充") or "定位未明" in d or "定位未获取" in d:
        cat = (c.get("category") or "").strip()
        d = ("%s小生意" % cat) if cat and cat != "未分类" else "海外小生意"
    d = re.sub(r"（[^）]*）", "", d).strip(" ，,。")
    d = re.sub(r"(服务|工具|产品)$", "", d) or d
    return d


def _hard_clip(s, n):
    """无省略号截断：优先在句读处断，切不出就在词边界硬切。"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    cut = s[:n]
    best = -1
    for p in (cut.rfind(x) for x in "。！？；.!?;，, "):
        if p >= n * 0.6 and p > best:
            best = p
    return cut[:best + 1].rstrip() if best >= 0 else cut.rstrip()


OVERRIDES_PATH = os.path.join(ROOT, "data", "xhs_title_overrides.json")
_OVERRIDES_CACHE = None


def _title_overrides():
    """标题覆盖表（xhs_ai_titles.py 产出）；文件缺失/损坏时当空表。"""
    global _OVERRIDES_CACHE
    if _OVERRIDES_CACHE is None:
        try:
            _OVERRIDES_CACHE = json.load(open(OVERRIDES_PATH, encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            _OVERRIDES_CACHE = {}
    return _OVERRIDES_CACHE


def _title_desc(desc, budget):
    """把一句话业务描述压到 budget 字内：取主干 → 去引号补充 → 硬切。

    之前 one_liner 一超预算就整体退到「海外小生意」，36 条标题全是同一个
    兜底词。实际各案例的 one_liner 都有现成主干（冒号/破折号前那段），
    压一压几乎都能进标题。
    """
    s = re.sub(r"（[^）]*）", "", desc or "").strip(" ，,。")
    if len(s) > budget:
        for sep in ("——", "—", "：", "；", "。", "，", ","):
            parts = s.split(sep)
            head = parts[0].strip(" ，,。")
            if len(parts) > 1 and 4 <= len(head) <= budget:
                s = head
                break
    if len(s) > budget:
        s2 = re.sub(r"「[^」]*」", "", s).strip(" ，,。")
        if len(s2) >= 4:
            s = s2
    return _hard_clip(s, budget)


def make_xhs_title(c, used=None):
    """<=20 字、不带省略号的标题。

    优先级：data/xhs_title_overrides.json（AI/人工覆盖）→ 规则生成
    （desc 先压缩主干再进标题；压缩后仍放不下才依次退到产品名、通用尾缀），
    绝不出现「…」。`used` 是批量生成时已用标题集合，撞车就依次退到下一候选。
    """
    ov = _title_overrides().get(c.get("id") or "")
    if ov and 4 <= len(ov) <= TITLE_MAX:
        return ov
    h = (c.get("metrics") or {}).get("headline") or ""
    amt = first_amount(h)
    desc = short_desc(c)
    name = (c.get("name") or c.get("id") or "").strip()
    generic = ["海外小生意", "一人公司生意", "海外小项目"]
    lead = ("%s成交" % amt) if (amt and "成交" in h) else (
        ("月收%s" % amt) if amt else "")
    cands = []
    if lead:
        budget = TITLE_MAX - len(lead) - 1
        cands.append("%s：%s" % (lead, _title_desc(desc, budget)))
        if name:
            cands.append("%s：%s" % (lead, name))
        cands += ["%s：%s" % (lead, g) for g in generic]
    if name:
        cands.append("%s：%s" % (name, desc))
        cands += ["%s：%s" % (name, g) for g in generic]
    cands.append("拆解一个海外小生意")
    for t in cands:
        t = t.strip()
        if 4 <= len(t) <= TITLE_MAX and (not used or t not in used):
            return t
    # 兜底：截 desc 而不是加省略号
    pre = ("%s：" % lead) if lead else (("%s：" % name) if name else "")
    budget = TITLE_MAX - len(pre)
    if budget >= 6:
        return pre + _hard_clip(desc, budget)
    return "拆解一个海外小生意"


def _clip(s, n):
    """正文用：带省略号的软截断（先在句读处断）。"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    cut = s[:n]
    for p in sorted(cut.rfind(x) for x in "。！？；.!?;，, "):
        if p >= n * 0.6:
            return cut[:p + 1]
    return cut.rstrip() + "…"


def build_tags(c):
    cat = (c.get("category") or "").strip()
    tags = list(BASE_TAGS) + TAGS_BY_CATEGORY.get(cat, [])
    out, seen = [], set()
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append("#" + t)
    return out


def build_body(c):
    h = (c.get("metrics") or {}).get("headline") or ""
    # 小红书种草口径（薛红笙路数）：第一屏就要有钩子——
    # 反差 / 具体数字 / 一句话痛点，别上来就"拆一个案例"这种叙述腔。
    hook_pool = [
        "一个人 + 现成的上游，就能开这门生意，收入是真的👇",
        "明码标价挂牌出售的海外小生意，我把它的账算给你看👇",
        "不写代码也能做：这门生意靠的是转售，不是技术👇",
        "又一个一人公司：收入是真的，成本结构比你想的薄👇",
    ]
    hook = hook_pool[sum(ord(x) for x in c.get("id", "")) % len(hook_pool)]
    why = [x.strip() for x in (c.get("why_it_works") or []) if x.strip()]
    pb = [x.strip() for x in (c.get("playbook") or []) if x.strip()]
    name = c.get("name_en") or c.get("name") or c.get("id")

    def assemble(n_why, how_n, what_n):
        parts = [
            hook,
            "",
            "📌 %s：%s" % (name, short_desc(c)),
            _clip(c.get("what_it_does"), what_n),
            "",
            "💰 它怎么赚钱",
            _clip(c.get("how_it_makes_money"), how_n),
            "",
            "🔍 为什么能成立",
        ]
        parts += ["· " + _clip(x, 52) for x in why[:n_why]]
        if pb:
            parts += ["", "🧠 最值得抄的一点", _clip(pb[0], 60)]
        cal = _clip(h, 56)
        if cal:
            parts += ["", "📊 口径照实说：" + cal + "（来源：TrustMRR 公开挂牌页）"]
        # 结尾要互动（评论区是小红书推荐权重的一部分）；
        # 且**不能带站外导流**——小红书对"引流公众号/站外"判得比微信严，
        # 早期版本那句"完整拆解在公众号…"必须删掉。
        parts += ["", "你觉得这门生意值不值得抄？评论区聊聊👇"]
        return "\n".join(parts)

    tail = "\n" + " ".join(build_tags(c))
    # 逐级收紧字段预算，直到塞进 1000 字
    text = None
    for n_why, how_n, what_n in ((3, 150, 120), (2, 110, 90), (2, 80, 70)):
        text = assemble(n_why, how_n, what_n)
        if len(text) + len(tail) <= BODY_MAX:
            return text + tail
    return (text or "") + tail


def cover_headline(headline, budget=24):
    """封面数字行：按「/」整段拼装，放不下完整段就丢弃（不出半截文案）。"""
    segs = [s.strip() for s in re.split(r"（", headline or "")[0].split("/")]
    out = ""
    for s in segs:
        if not s:
            continue
        cand = (out + " / " + s) if out else s
        if len(cand) <= budget:
            out = cand
    return out


def build_note(c, used=None):
    return {
        "title": make_xhs_title(c, used),
        "body": build_body(c),
        "tags": build_tags(c),
    }


# ---- 卡片 HTML -------------------------------------------------------------
# 自适应原理：.fit 容器在 flex 列里被压缩（overflow:hidden + min-height:0），
# 内容真高 scrollHeight > 视口高 clientHeight 时逐轮缩小容器字号；
# 容器内的文字一律用 1em 相对字号，缩容器=缩全部。固定元素（标题条/评分块）
# 不参与压缩。

PAGE_TITLES = ["封面", "是什么·怎么赚钱", "为什么成立", "方法论", "三张评分"]


def _bullets(items):
    return "".join(
        '<div class="row"><div class="dot"></div>'
        '<div class="rt">%s</div></div>' % esc(x) for x in items)


def _bar(label, v, mx):
    pct = max(0, min(100, 100.0 * (v or 0) / mx))
    return ('<div class="brow"><div class="bl">%s</div>'
            '<div class="btrack"><div class="bfill" style="width:%.0f%%">'
            '</div><div class="bv">%s</div></div></div>'
            % (esc(label), pct,
               esc("%s/%s" % (v if v is not None else "—", mx))))


def card_html(c, page, total):
    """第 page(1-based) 张卡片，3:4 全 HTML 文档。"""
    i = sum(ord(x) for x in c.get("id", "")) % len(wp.COVER_THEMES)
    _base, _band, ac = wp.COVER_THEMES[i]
    name = c.get("name") or c.get("id") or ""
    one = re.sub(r"（[^）]*）", "", (c.get("one_liner") or "")).strip() or \
        (c.get("category") or "")
    headline = (c.get("metrics") or {}).get("headline") or ""
    brand = "万物解释者 · 拆解海外"
    page_no = "%02d / %02d" % (page, total)

    head = (
        '<div class="hd"><div class="brand"><span class="sq"></span>%s</div>'
        '<div class="pg">%s</div></div>' % (esc(brand), esc(page_no)))

    if page == 1:      # 封面
        body = (
            '<div class="kicker" style="color:%s">CASE STUDY · 海外小生意</div>'
            '<div class="name fit">%s</div>'
            '<div class="one fit">%s</div>'
            '<div class="numbox"><div class="num fit">%s</div>'
            '<div class="numsub">数据来自公开挂牌页 · 口径见末页</div></div>'
            '<div class="swipe">👉 右滑看完整拆解</div>'
            % (ac, esc(name), esc(one),
               esc(cover_headline(headline) or _clip(headline, 30))))
    elif page == 2:    # 是什么 · 怎么赚钱
        body = (
            '<div class="h2" style="color:%s">它是什么</div>'
            '<div class="blk fit"><div class="para">%s</div></div>'
            '<div class="h2" style="color:%s">它怎么赚钱</div>'
            '<div class="blk fit"><div class="para">%s</div></div>'
            % (ac, esc(c.get("what_it_does") or "—"),
               ac, esc(c.get("how_it_makes_money") or "—")))
    elif page == 3:    # 为什么成立
        body = ('<div class="h2" style="color:%s">它为什么能成立</div>'
                '<div class="blk fit grow"><div class="inner">%s</div></div>'
                % (ac, _bullets(c.get("why_it_works") or [])))
    elif page == 4:    # 方法论
        body = ('<div class="h2" style="color:%s">能抄走的方法论</div>'
                '<div class="blk fit grow"><div class="inner">%s</div></div>'
                % (ac, _bullets(c.get("playbook") or [])))
    else:              # 三张评分
        rep = c.get("replicability") or {}
        sf = c.get("solo_fit") or {}
        cf = c.get("china_fit") or {}
        rows = "".join([
            _bar("技术", rep.get("tech"), 6),
            _bar("获客", rep.get("distribution"), 6),
            _bar("资金", rep.get("capital"), 6),
            _bar("时机", rep.get("timing"), 6)])
        blocker = cf.get("blocker")
        sv = ("%.1f" % sf["score"]) if sf.get("score") is not None else "—"
        cv = ("%.1f" % cf["score"]) if cf.get("score") is not None else "—"
        body = (
            '<div class="h2" style="color:%s">三张评分</div>'
            '<div class="h3">可复制性（1–6）</div>'
            '<div class="blk fit bars">%s</div>'
            '<div class="scards"><div class="scard">'
            '<div class="sv" style="color:%s">%s</div>'
            '<div class="sl">一人上手分 · 第%s名</div></div>'
            '<div class="scard"><div class="sv">%s</div>'
            '<div class="sl">中国适配分 · 第%s名%s</div></div></div>'
            % (ac, rows, ac, esc(sv), esc(str(sf.get("rank") or "—")),
               esc(cv), esc(str(cf.get("rank") or "—")),
               (" · 阻碍：" + esc(str(blocker))) if blocker else ""))
    tail = ""
    if page == total:
        tail = ('<div class="foot">完整拆解 → 公众号「万物解释者」 · '
                '数据核验截至 %s</div>'
                % esc((c.get("verified_at") or c.get("updated_at") or "")[:10]))

    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:0;}"
        "*{box-sizing:border-box;}"
        "body{width:%(W)dpx;height:%(H)dpx;background:#f6f3ee;"
        "font-family:'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif;"
        "color:#26221c;overflow:hidden;}"
        ".wrap{width:100%%;height:100%%;padding:52px 60px 44px;}"
        ".card{position:relative;width:100%%;height:100%%;background:#fffdf9;"
        "border-radius:32px;border:1px solid #e8e1d5;padding:46px 48px 40px;"
        "overflow:hidden;display:flex;flex-direction:column;}"
        ".hd{display:flex;justify-content:space-between;align-items:center;"
        "margin-bottom:30px;flex:0 0 auto;}"
        ".brand{font-size:24px;font-weight:700;color:#8a8072;"
        "display:flex;align-items:center;letter-spacing:2px;}"
        ".sq{display:inline-block;width:18px;height:18px;border-radius:5px;"
        "margin-right:14px;background:%(AC)s;}"
        ".pg{font-size:22px;color:#b3a88f;}"
        ".kicker{font-size:26px;font-weight:800;letter-spacing:6px;flex:0 0 auto;}"
        ".name{font-size:84px;font-weight:900;line-height:1.15;margin-top:26px;}"
        ".one{font-size:40px;font-weight:600;line-height:1.5;color:#5c554a;"
        "margin-top:20px;}"
        ".numbox{margin-top:auto;background:#f6f3ee;border-radius:24px;"
        "padding:34px 34px;flex:0 0 auto;}"
        ".num{font-size:44px;font-weight:800;line-height:1.4;}"
        ".numsub{font-size:22px;color:#8a8072;margin-top:12px;}"
        ".swipe{margin-top:24px;font-size:26px;color:#8a8072;flex:0 0 auto;}"
        ".h2{font-size:44px;font-weight:900;margin-bottom:26px;flex:0 0 auto;}"
        ".h3{font-size:27px;font-weight:700;color:#8a8072;margin:2px 0 16px;"
        "flex:0 0 auto;}"
        ".blk{flex:1 1 0;min-height:0;overflow:hidden;font-size:30px;}"
        ".blk .inner{display:flow-root;}"
        ".blk.bars{flex:0 1 auto;min-height:0;font-size:28px;}"
        ".para{font-size:1em;line-height:1.75;color:#3d382f;}"
        ".row{display:flex;margin-bottom:0.9em;}"
        ".dot{flex:0 0 14px;height:14px;border-radius:50%%;background:%(AC)s;"
        "margin-top:0.6em;margin-right:24px;}"
        ".rt{font-size:1em;line-height:1.65;color:#3d382f;}"
        ".brow{display:flex;align-items:center;margin-bottom:20px;}"
        ".bl{flex:0 0 96px;font-size:1em;font-weight:700;}"
        ".btrack{flex:1;height:20px;border-radius:10px;background:#efe9dd;"
        "margin:0 20px;overflow:hidden;}"
        ".bfill{height:100%%;border-radius:10px;background:%(AC)s;}"
        ".bv{flex:0 0 76px;font-size:0.9em;color:#8a8072;text-align:right;}"
        ".scards{display:flex;gap:22px;margin-top:34px;flex:0 0 auto;}"
        ".scard{flex:1;background:#f6f3ee;border-radius:22px;padding:28px;}"
        ".sv{font-size:60px;font-weight:900;line-height:1.1;}"
        ".sl{font-size:22px;color:#8a8072;margin-top:10px;line-height:1.4;}"
        ".foot{margin-top:auto;font-size:22px;color:#8a8072;line-height:1.5;"
        "border-top:1px solid #e8e1d5;padding-top:22px;flex:0 0 auto;}"
        ".fit{overflow:hidden;min-height:0;}"
        "</style></head><body><div class=\"wrap\"><div class=\"card\">"
        "%(HEAD)s%(BODY)s%(TAIL)s</div></div></body></html>"
        % {"W": CARD_W, "H": CARD_H, "AC": ac,
           "HEAD": head, "BODY": body, "TAIL": tail})


# ---- Chrome / 渲染 ---------------------------------------------------------

def _port_up(port):
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        op.open("http://127.0.0.1:%d/json/version" % port, timeout=2).read()
        return True
    except Exception:
        return False


def ensure_chrome(port=BUILD_PORT):
    """起一个一次性无头 Chrome 专门渲染卡片（临时 profile，不碰 9222 登录态）。"""
    if _port_up(port):
        return None
    profile = os.path.join(OUT, ".chrome-profile")
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--remote-debugging-port=%d" % port,
         "--user-data-dir=" + profile, "--no-first-run",
         "--no-default-browser-check", "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.5)
        if _port_up(port):
            return proc
    raise SystemExit("Chrome 调试端口起不来: %d" % port)


GROW_JS = (
    "(function(){var chg=0;"
    "[].slice.call(document.querySelectorAll('.blk.grow')).forEach(function(e){"
    "var inner=e.querySelector('.inner');if(!inner)return;"
    "var guard=0;"
    "while(inner.offsetHeight*1.12<e.clientHeight&&guard<8){"
    "var fs=parseFloat(getComputedStyle(e).fontSize);"
    "e.style.fontSize=fs*1.1+'px';guard++;chg++;}"
    "while(inner.offsetHeight>e.clientHeight+1&&guard<14){"
    "var fs=parseFloat(getComputedStyle(e).fontSize);"
    "var nf=Math.max(24,fs*0.94);"
    "if(nf>=fs-0.01)break;"
    "e.style.fontSize=nf+'px';guard++;chg++;}});"
    "return JSON.stringify({n:chg});})()")

AUTOFIT_JS = (
    "(function(){var chg=0;"
    "[].slice.call(document.querySelectorAll('.fit')).forEach(function(e){"
    "var guard=0;"
    "while((e.scrollHeight>e.clientHeight+1||e.scrollWidth>e.clientWidth+1)"
    "&&guard<14){var fs=parseFloat(getComputedStyle(e).fontSize);"
    "var nf=Math.max(15,fs*0.94);"
    "if(nf>=fs-0.01)break;"
    "e.style.fontSize=nf+'px';guard++;chg++;}});"
    "return JSON.stringify({n:chg});})()")


def case_page_html(c, cards=5):
    """一条案例的全部卡片拼进一个页面（每张占一个 1080x1440 slot）。

    一次 new_target/连接/收敛，5 次 clip 截图 —— 比每卡开一个 tab 快一个
    数量级（单 tab 路线实测 5 卡要 1m46s，大头在连接与上下文收集）。
    """
    docs = [card_html(c, p, cards) for p in range(1, cards + 1)]
    css = docs[0].split("<style>")[1].split("</style>")[0]
    slots = "".join(
        '<div class="slot">%s</div>'
        % d.split("<body>")[1].split("</body>")[0] for d in docs)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        + css +
        "body{width:%(W)dpx;height:%(H)dpx;}"
        ".slot{width:%(W)dpx;height:%(Hh)dpx;position:relative;overflow:hidden;}"
        "</style></head><body>%(SLOT)s</body></html>"
        % {"W": CARD_W, "H": CARD_H * cards, "Hh": CARD_H, "SLOT": slots})


def render_case_pngs(port, c, out_dir, cards=5):
    """渲染一条案例的全部卡片 PNG，返回成功文件名列表。"""
    html = case_page_html(c, cards)
    data_url = "data:text/html;charset=utf-8;base64," + base64.b64encode(
        html.encode("utf-8")).decode("ascii")
    tid = None
    sub = None
    done = []
    try:
        browser = wp.CDP(port)
        tid = browser.new_target(data_url)["id"]
        sub = wp.CDP(port)
        sub.connect_target(tid)
        # 关键：把视口钉死成画布尺寸。headless 默认视口 ~764x485，
        # Page.captureScreenshot 的 clip 不会按 clip 尺寸重排布局，
        # 不 override 的话卡片会按小视口塌陷（实测 2026-09-25）。
        # dsf 固定 1，放大倍数交给 clip.scale（两个会叠乘）。
        sub.send("Emulation.setDeviceMetricsOverride",
                 {"width": CARD_W, "height": CARD_H, "deviceScaleFactor": 1,
                  "mobile": False})
        time.sleep(1.0)
        # 收敛循环全部放进一次 JS 同步执行（样式改动后读 getComputedStyle
        # 会强制同步重排，纯 JS 内循环即可收敛）。每轮 eval 都带
        # refresh_context 会触发 wp 的上下文收集（实测单卡要 30 秒+），
        # 这里只在第一次拿上下文，后续复用。
        sub.eval(GROW_JS, refresh_context=True)
        sub.eval(AUTOFIT_JS)
        for p in range(1, cards + 1):
            r = sub.send("Page.captureScreenshot", {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {"x": 0, "y": (p - 1) * CARD_H, "width": CARD_W,
                         "height": CARD_H, "scale": 2}})
            data = (r.get("result") or {}).get("data", "")
            if not data:
                print("  [warn] %s 卡片 %d 截图失败" % (c["id"], p))
                continue
            fn = "card-%d.png" % p
            with open(os.path.join(out_dir, fn), "wb") as f:
                f.write(base64.b64decode(data))
            done.append(fn)
        return done
    finally:
        if tid and browser is not None:
            try:
                browser.close_target(tid)
            except Exception:
                pass
        for conn in (sub, browser):
            if conn is not None and getattr(conn, "bws", None) is not None:
                try:
                    conn.bws.close()
                except Exception:
                    pass


def build_case(port, c, cards=5, used=None):
    cid = c["id"]
    d = os.path.join(OUT, cid)
    os.makedirs(d, exist_ok=True)
    note = build_note(c, used)
    if used is not None:
        used.add(note["title"])
    note["images"] = render_case_pngs(port, c, d, cards)
    with open(os.path.join(d, "note.md"), "w", encoding="utf-8") as f:
        f.write(note["title"] + "\n\n" + note["body"] + "\n")
    with open(os.path.join(d, "note.json"), "w", encoding="utf-8") as f:
        json.dump(note, f, ensure_ascii=False, indent=2)
    return note


# ---- preview ---------------------------------------------------------------

def cmd_preview(_args):
    cases = load_cases()
    rows = []
    for c in cases:
        d = os.path.join(OUT, c["id"])
        nj = os.path.join(d, "note.json")
        if not os.path.isfile(nj):
            continue
        note = json.load(open(nj, encoding="utf-8"))
        imgs = "".join(
            '<figure><img loading="lazy" src="%s/%s">'
            '<figcaption>%s · %02d/%02d</figcaption></figure>'
            % (c["id"], fn, esc(c["name"]), i + 1, len(note["images"]))
            for i, fn in enumerate(note["images"]))
        rows.append(
            '<section><h2>%s <small>%s</small></h2>'
            '<div class="meta">标题（%d 字）：%s</div>'
            '<pre>%s</pre><div class="row">%s</div></section>'
            % (esc(c["name"]), esc(c["id"]), len(note["title"]),
               esc(note["title"]), esc(note["body"]), imgs))
    html = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        "<title>小红书笔记预览 · 万物解释者</title><style>"
        "body{font-family:'PingFang SC','Microsoft YaHei',sans-serif;"
        "background:#f2efe9;margin:0;padding:32px;color:#26221c;}"
        "h1{font-size:26px;}h2{font-size:20px;margin:0 0 12px;}"
        "section{background:#fffdf9;border-radius:16px;padding:24px;"
        "margin-bottom:28px;border:1px solid #e8e1d5;}"
        "small{color:#8a8072;font-weight:400;}"
        ".meta{font-size:14px;color:#5c554a;margin-bottom:8px;}"
        "pre{white-space:pre-wrap;font:14px/1.7 inherit;background:#f6f3ee;"
        "padding:16px;border-radius:12px;max-width:640px;}"
        ".row{display:flex;gap:16px;flex-wrap:wrap;}"
        "figure{margin:12px 0 0;}"
        "img{width:216px;border-radius:12px;border:1px solid #e8e1d5;"
        "display:block;}"
        "figcaption{font-size:12px;color:#8a8072;margin-top:4px;}"
        "</style></head><body><h1>小红书笔记预览（%d 条）</h1>%s</body></html>"
        % (len(rows), "".join(rows)))
    path = os.path.join(OUT, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("preview ->", path)


# ---- publish（实验性）-------------------------------------------------------

PUB_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
DRAFT_URL = "https://creator.xiaohongshu.com/publish/publish?target=draft"

# 2026-09-25 实测 DOM（小红书 creator 网页版，图文流程）：
#   默认停在「上传视频」tab，必须先切到「上传图文」才会出现编辑表单；
#   表单 = 标题 input.d-text[placeholder*="标题"] + 正文 .tiptap.ProseMirror
#   图文的 file input 在 .img-list 里（别抓到视频区的那个）。
# 注意：这三个字符串会被插进 JS，里面的引号必须用单引号，
# 否则和外层双引号撞车 → querySelector 抛错 → eval 返回 None（2026-09-25 实测）。
SEL_TITLE = "input.d-text[placeholder*='标题']"
SEL_BODY = ".tiptap.ProseMirror[contenteditable='true']"
SEL_FILE = '.img-list input[type=file]'
SEL_FILE_ANY = 'input[type=file]'   # 图文页刚切开时还没有 .img-list
SEL_IMG = '.img-list img'
# 原创声明：内容设置面板里的开关（2026-09-26 实测）
SEL_ORIGINAL = '.original-wrapper .d-switch, .original-wrapper input[type=checkbox]'


def _js_original(state=True):
    """勾/取消「原创声明」。返回当前是否勾选、是否点了一下。"""
    return """(function(){
  var want = %s;
  var box = document.querySelector('.original-wrapper');
  if (!box) return JSON.stringify({ok:false, why:'no_original_block'});
  var sw = box.querySelector('.d-switch');
  var cb = box.querySelector('input[type=checkbox]');
  var on = !!(sw && sw.classList.contains('d-checked')) ||
           (sw && /checked/.test(String(sw.className))) ||
           !!(cb && cb.checked);
  if (on === want) return JSON.stringify({ok:true, on:on, clicked:false});
  (sw || cb).click();
  return JSON.stringify({ok:true, on:on, clicked:true});
})()""" % ("true" if state else "false")


def _js_original_agree():
    """原创声明须知弹窗：勾「我已阅读并同意」，并回报确认按钮是否可点。"""
    return """(function(){
  var wrap = [].slice.call(document.querySelectorAll('.d-checkbox'))
    .filter(function(e){
      return (e.innerText||'').indexOf('我已阅读并同意') >= 0; })[0];
  if (!wrap) return JSON.stringify({ok:false, why:'no_dialog'});
  var box = wrap.querySelector('input[type=checkbox]');
  if (box && !box.checked) { wrap.click(); }
  return JSON.stringify({ok:true, checked: !!(box && box.checked)});
})()"""


def _js_original_confirm():
    """点须知弹窗里的「声明原创」（勾了同意才会 enabled）。"""
    return """(function(){
  var btn = [].slice.call(document.querySelectorAll('button'))
    .filter(function(b){ return (b.innerText||'').trim() === '声明原创'; })[0];
  if (!btn) return JSON.stringify({ok:false, why:'no_btn'});
  if (btn.classList.contains('disabled') || btn.disabled) {
    return JSON.stringify({ok:false, why:'disabled'});
  }
  btn.click();
  return JSON.stringify({ok:true, clicked:true});
})()"""


def _js_trim_tail_jing():
    """删掉正文末尾多余的裸 #（自动加话题失败时会在正文留下尾巴）。"""
    return """(function(){
  var ed = document.querySelector('.tiptap.ProseMirror[contenteditable='true']');
  if (!ed) return JSON.stringify({ok:false, why:'no_editor'});
  var t = ed.innerText || '';
  if (!/[#＃]\\s*$/.test(t)) return JSON.stringify({ok:true, trimmed:0});
  ed.focus();
  var n = 0;
  // 退格把尾巴清干净（ProseMirror 认真实键盘事件）
  while (n < 30 && /[#＃]\\s*$/.test(ed.innerText || '')) {
    document.execCommand('delete');
    n++;
  }
  return JSON.stringify({ok:true, trimmed:n, tail:(ed.innerText||'').slice(-20)});
})()"""


def _js_switch_photo_tab():
    """点「上传图文」tab；已经图文就什么都不做。"""
    # 进了编辑器视图后 .creator-tab 会整个消失，所以先看有没有编辑区。
    return """(function(){
  if ((document.querySelector('.edit-container') ||
       document.querySelector('.tiptap')) &&
      location.href.indexOf('target=image') >= 0) {
    return JSON.stringify({ok:true, active:true, editor:true, url:location.href});
  }
  var act = document.querySelector('.creator-tab.active');
  if (act && (act.innerText||'').trim() === '上传图文') {
    return JSON.stringify({ok:true, active:true, url:location.href});
  }
  var el = [].slice.call(document.querySelectorAll('.creator-tab'))
    .filter(function(e){ return (e.innerText||'').trim() === '上传图文'; })[0];
  if (!el) return JSON.stringify({ok:false, why:'no_tab'});
  el.click();
  return JSON.stringify({ok:true, active:false, url:location.href});
})()"""


def _js_fill_title(title):
    """标题：input 走原生 value setter + input 事件（Vue 能收到）。"""
    return ("""(function(){
  var ti = document.querySelector("%s");
  if (!ti) return JSON.stringify({ok:false, why:'no_title_el'});
  var s = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value');
  s.set.call(ti, %s);
  ti.dispatchEvent(new Event('input', {bubbles:true}));
  return JSON.stringify({ok:true, len:ti.value.length});
})()""" % (SEL_TITLE, json.dumps(title, ensure_ascii=False)))


def _js_fill_body(body):
    """正文：tiptap/ProseMirror 不吃原生 value setter，按段 insertText。"""
    lines = [l for l in body.split("\n")]
    return ("""(function(){
  var ed = document.querySelector("%s");
  if (!ed) return JSON.stringify({ok:false, why:'no_editor'});
  ed.focus();
  var lines = %s, ok = 0;
  for (var i = 0; i < lines.length; i++) {
    try {
      document.execCommand('insertText', false,
        lines[i] + (i < lines.length - 1 ? '\\n' : ''));
      ok++;
    } catch (e) {}
  }
  ed.dispatchEvent(new Event('input', {bubbles:true}));
  return JSON.stringify({ok:true, lines:lines.length, inserted:ok,
                         len:(ed.innerText||'').length});
})()""" % (SEL_BODY, json.dumps(lines, ensure_ascii=False)))


def _js_open_topic():
    """点编辑区「话题」按钮，打开话题选择浮层。"""
    return """(function(){
  var btns = [].slice.call(
    document.querySelectorAll('.contentBtn, .edit-btn'));
  var b = btns.filter(function(e){
    return (e.innerText||'').trim() === '话题'; })[0];
  if (!b) return JSON.stringify({ok:false, why:'no_topic_btn'});
  b.click();
  return JSON.stringify({ok:true});
})()"""


def _js_topic_type(tag):
    """在话题浮层输入框里敲 #标签（焦点已在浮层里时应直接生效）。"""
    return ("""(function(){
  var sel = document.querySelector('.d-select-input-filter input, .d-select-input-filter textarea');
  if (!sel) return JSON.stringify({ok:false, why:'no_topic_input'});
  sel.focus();
  try { document.execCommand('insertText', false, %s); } catch (e) {}
  sel.dispatchEvent(new Event('input', {bubbles:true}));
  return JSON.stringify({ok:true, len:(sel.value||'').length});
})()""" % json.dumps("#" + tag.lstrip("#"), ensure_ascii=False))


def _js_topic_pick():
    """回车后选第一个候选话题，然后关掉浮层。"""
    return """(function(){
  var item = document.querySelector('.d-select-item, .d-select-option, '
    + 'li[class*=d-select]');
  if (item) { item.click(); return JSON.stringify({ok:true, picked:true}); }
  return JSON.stringify({ok:false, why:'no_candidate'});
})()"""


def _wait(sub, js, want, tries=30, gap=1.0, label=""):
    for _ in range(tries):
        try:
            r = sub.eval(js)
            if want(r):
                return r
        except Exception:
            pass
        time.sleep(gap)
    return None


HOME_URL = "https://creator.xiaohongshu.com/new/home"

# 创作者中心首页的暂存提示（2026-09-26 实测版式）：
# 侧边栏改版后顶部「草稿箱(N)」没了，首页会显示「草稿箱中有未发布的作品」。
_JS_DRAFT_SIGNAL = (
    "(function(){var t=document.body.innerText||'';"
    "var m=t.match(/草稿箱\\((\\d+)\\)/);"
    "return {hint:t.indexOf('草稿箱中有未发布的作品')>=0,"
    "n:m?parseInt(m[1],10):-1};})()"
)


def _draft_signal(sub):
    """返回 {'hint':bool,'n':int}；eval 有时给对象有时给 JSON 字符串，都兜住。"""
    try:
        v = sub.eval(_JS_DRAFT_SIGNAL, refresh_context=True)
    except Exception:
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return None


def _save_draft(sub, note):
    """把当前编辑内容存成草稿。

    实测（2026-09-26）：小红书图文发布页**没有「存草稿」按钮**，底部只有
    红色「发布」；唯一办法是**离开编辑页**，小红书会自动把当前内容暂存到
    本地草稿箱（顶部「草稿箱(N)」计数 +1 即暂存成功）。

    所以这里主动 navigate 到创作中心首页，再回读计数验证。
    返回 (是否成功, 计数)。
    """
    before = _draft_signal(sub) or {}
    try:
        sub.send("Page.navigate", {"url": HOME_URL})
    except Exception as e:
        print("[warn] 导航离开失败：%s" % e)
        return False, before
    left = False
    for _ in range(10):          # 等 URL 真的离开发布页
        time.sleep(1.2)
        try:
            if "publish/publish" not in (sub.eval("location.href") or ""):
                left = True
                break
        except Exception:
            pass
    # 导航换了 JS context，这里每次都要带 refresh_context（否则拿的是
    # 已销毁的旧 context，会一直重试）。自动暂存有几秒延迟，轮询几轮。
    for _ in range(8):
        time.sleep(1.5)
        s = _draft_signal(sub)
        if s and (s.get("hint") or (isinstance(s.get("n"), int) and s["n"] > 0)):
            print("  ✅ 已存草稿（首页显示「草稿箱中有未发布的作品」）")
            return True, s
    print("[warn] 离开编辑页后没读到草稿信号%s，请去草稿箱核对：《%s》"
          % ("" if left else "（页面没真的离开）", note["title"]))
    return False, before


def cmd_publish(args):
    # 没写 --case 就走「发最近的没发过的」：默认 1 条（--near 3 = 3 条）
    if not getattr(args, "case", None):
        return _publish_batch(args)
    c = find_case_fuzzy(args.case)
    d = os.path.join(OUT, c["id"])
    nj = os.path.join(d, "note.json")
    if not os.path.isfile(nj):
        raise SystemExit("先 build：out/xhs/%s/note.json 不存在" % args.case)
    if not os.path.isfile(nj):
        build_case(BUILD_PORT, c)          # 没生成过卡片就现造一张
    note = json.load(open(nj, encoding="utf-8"))
    if not _port_up(PUB_PORT):
        raise SystemExit(
            "没找到 %d 调试 Chrome。先起：\n"
            '  "C:/Program Files/Google/Chrome/Application/chrome.exe" '
            "--remote-debugging-port=9222 "
            '--user-data-dir="C:\\Users\\DELL\\chrome-debug-profile" '
            "--no-first-run --no-default-browser-check --remote-allow-origins=* "
            "\n然后人工登录 creator.xiaohongshu.com 一次。" % PUB_PORT)
    cdp = wp.CDP(PUB_PORT)
    tid = None
    for t in cdp.list_targets():
        if "creator.xiaohongshu.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(PUB_URL)["id"]
        time.sleep(4)
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    url = sub.eval("location.href")
    if "publish" not in url:
        sub.send("Page.navigate", {"url": PUB_URL})
        time.sleep(4)
        url = sub.eval("location.href")
    if "login" in url or "passport" in url:
        raise SystemExit("该 Chrome 还没登录小红书，请先人工登录后再跑 publish。")

    # 默认重新导航到发布页：编辑区的图/字都只是本地状态，留着只会越叠越多。
    # 想接着上一次的编辑继续，加 --keep。
    if not getattr(args, "keep", False):
        sub.send("Page.navigate", {"url": PUB_URL})
        time.sleep(6)
    _T0 = time.time()
    print("  [%.0fs] 页面已就绪" % (time.time() - _T0))

    # ---- 1) 切到「上传图文」tab（默认停在视频 tab，没有编辑表单）----
    # 页面是异步挂载的：navigate 完直接找 tab 会偶发失败，
    # 这里先等 .creator-tab 出现再切，并允许点完再复查几轮。
    if _wait(sub, "document.querySelectorAll('.creator-tab').length",
             lambda v: int(v or 0) > 0, tries=25, gap=1.0) is None:
        raise SystemExit(
            "[err] 发布页 25 秒都没渲染出 tab，检查网络或登录态。")
    r = {}
    for _ in range(6):
        r = json.loads(sub.eval(_js_switch_photo_tab(), refresh_context=True))
        if r.get("ok"):
            break
        time.sleep(2)
    if not r.get("ok"):
        raise SystemExit("[err] 找不到「上传图文」tab：%s" % r)
    # 切 tab 后 DOM 是异步替换的；注意 .img-list 是「传了图之后」才出现的，
    # 所以这里只能等裸的 input[type=file]（2026-09-25 实测）。
    if _wait(sub, "document.querySelectorAll('%s').length" % SEL_FILE_ANY,
             lambda v: int(v or 0) > 0, tries=20, gap=1.0) is None:
        raise SystemExit(
            "[err] 切到图文 tab 后 20 秒仍没有 file input，页面结构可能又变了。")

    # ---- 2) 逐张传图（图文的 file input 在 .img-list 下）----
    for fn in note["images"]:
        p = os.path.abspath(os.path.join(d, fn))
        q = sub.send("DOM.getDocument", {"depth": 0})
        root = (q.get("result") or {}).get("root", {}).get("nodeId")
        n = sub.send("DOM.querySelector",
                     {"nodeId": root, "selector": SEL_FILE})
        nid = (n.get("result") or {}).get("nodeId")
        if not nid:      # 还没传过图 → 用裸 input
            n = sub.send("DOM.querySelector",
                         {"nodeId": root, "selector": SEL_FILE_ANY})
            nid = (n.get("result") or {}).get("nodeId")
        if not nid:
            print("[warn] 没找到图文 file input，跳过 %s（请手动传）" % fn)
            continue
        before = int(sub.eval("document.querySelectorAll('%s').length" % SEL_IMG)
                     or 0)
        sub.send("DOM.setFileInputFiles", {"nodeId": nid, "files": [p]})
        got = _wait(
            sub, "document.querySelectorAll('%s').length" % SEL_IMG,
            lambda v, b=before: (int(v or 0) > b), tries=45, gap=1.0)
        if got is None:
            print("[warn] %s 上传没被识别（列表没增加），可能要手动传" % fn)
        else:
            print("  图片已加：%s（第 %s/%s 张）"
                  % (fn, int(got or 0), len(note["images"])))

    # ---- 3) 等编辑表单出现 ----
    if _wait(sub, "!!document.querySelector('.edit-container')",
             lambda v: v and v != "false") is None:
        raise SystemExit(
            "[err] 等了 30 秒编辑表单还没出来。图传完没？去浏览器里看一眼。")

    # ---- 4) 标题 / 正文 ----
    r = json.loads(sub.eval(_js_fill_title(note["title"])))
    if not r.get("ok"):
        print("[warn] 标题没填上：%s。请到浏览器里手动填。" % r)
    r = json.loads(sub.eval(_js_fill_body(note["body"])))
    if not r.get("ok"):
        print("[warn] 正文没填上：%s。请到浏览器里手动填。" % r)
    else:
        print("  正文已填：%s 行，编辑器内 %s 字" % (r.get("inserted"),
                                                    r.get("len")))

    # ---- 5) 话题（默认尝试自动加，失败只提示，不阻断）----
    # 话题保持默认关闭：话题浮层的候选选择器目前对不上，硬试会在正文末尾
    # 留下孤立 #（实测出现过「#SaaS#」）。正文末尾本来就带同名 #标签，够了。
    tags = note.get("tags") or []
    if tags and getattr(args, "tags", False):
        sub.eval(_js_open_topic())
        time.sleep(1.5)
        for tag in tags:
            if "#" not in tag:
                tag = "#" + tag.lstrip("#")
            r = json.loads(sub.eval(_js_topic_type(tag.lstrip("#"))))
            if not r.get("ok"):
                print("[warn] 话题框没打开，%s 请手加（正文里已带同名 #）" % tag)
                break
            time.sleep(1.2)
            # 发真实回车（ProseMirror 只认 keydown，合成 KeyboardEvent 不稳）
            for typ, extra in (("keyDown", {"text": "\r", "nativeVirtualKeyCode": 13}),
                               ("keyUp", {})):
                sub.send("Input.dispatchKeyEvent",
                         dict({"type": typ, "key": "Enter", "code": "Enter",
                               "windowsVirtualKeyCode": 13}, **extra))
            time.sleep(1.2)
            p = json.loads(sub.eval(_js_topic_pick()))
            if not p.get("ok"):
                print("[warn] %s 没有候选，跳过" % tag)
            time.sleep(0.8)
        try:
            sub.send("Input.dispatchKeyEvent",
                     {"type": "keyDown", "key": "Escape",
                      "code": "Escape", "windowsVirtualKeyCode": 27})
            sub.send("Input.dispatchKeyEvent", {"type": "keyUp",
                                                "key": "Escape",
                                                "code": "Escape",
                                                "windowsVirtualKeyCode": 27})
        except Exception:
            pass

    # ---- 5.5) 清尾巴 + 勾选「原创声明」（默认开，--no-original 才不勾）----
    sub.eval(_js_trim_tail_jing(), refresh_context=True)
    _wait(sub, "document.querySelectorAll('.original-wrapper').length",
          lambda v: int(v or 0) > 0, tries=15, gap=1.0)
    want = not getattr(args, "no_original", False)
    o = {}
    for _ in range(5):
        o = json.loads(sub.eval(_js_original(want)))
        if o.get("on") is want:
            break
        # 勾开会弹出《原创声明须知》：要勾同意再点「声明原创」才算数
        a = json.loads(sub.eval(_js_original_agree(), refresh_context=True))
        if a.get("ok"):
            time.sleep(1.0)
            c2 = json.loads(sub.eval(_js_original_confirm()))
            if c2.get("ok"):
                print("  原创声明：已签署须知")
            else:
                print("[warn] 原创须知没确认：%s" % c2)
        time.sleep(1.5)
    if o.get("on") is want:
        print("  原创声明：%s%s" % ("已勾选" if want else "未勾选（按参数要求）",
                                    "" if not o.get("clicked") else "（本次点开的）"))
    else:
        print("[warn] 「原创声明」没勾上：%s —— 请在浏览器里手动处理。" % o)

    # ---- 6) 提交 ----
    if args.dry:
        print("DRY：标题/正文/图片/话题都已就位，未点任何按钮。"
              "请到浏览器里检查（标签页已给你留在最前）。")
        return
    if not args.yes:
        # 页面没有「存草稿」按钮：离开编辑页小红书会自动暂存（2026-09-26 实测）。
        ok, _ = _save_draft(sub, note)
        mark_drafted(c["id"])
        print(("已存为草稿，去创作中心「草稿箱」里审核后再发"
               "（草稿是浏览器本地的，换设备/清缓存就没了）。") if ok else
              ("[warn] 没确认到草稿。可手动关掉发布页暂存，或加 --yes 直接发。"))
        return
    # 真发布：底部红色「发布」按钮查不到常规 DOM（探针 2026-09-26：
    # 全元素搜文本「发布」只有左上角导航按钮），按 viewport 比例坐标
    # 真实点击（截图实测 556/1080, 644/676），点击后跳 /publish/success。
    sub.eval("""(function(){
      [].slice.call(document.querySelectorAll('*')).forEach(function(e){
        if (e.scrollHeight > e.clientHeight + 50 &&
            /auto|scroll/.test(getComputedStyle(e).overflowY)) {
          e.scrollTop = e.scrollHeight;
        }
      });
      return 'ok';
    })()""")
    time.sleep(1)
    iw = int(sub.eval("window.innerWidth") or 1080)
    ih = int(sub.eval("window.innerHeight") or 676)
    x = round(iw * 556.0 / 1080.0)
    y = round(ih * 644.0 / 676.0)
    print("点击底部「发布」：(%d, %d)" % (x, y))
    sub.send("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(0.2)
    sub.send("Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": x, "y": y, "button": "left",
              "clickCount": 1})
    time.sleep(0.12)
    sub.send("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": x, "y": y, "button": "left",
              "clickCount": 1})
    for _ in range(20):
        time.sleep(1.5)
        if "success" in (sub.eval("location.href") or ""):
            print("✅ 发布成功（页面已跳 /publish/success）")
            mark_published(c["id"])
            return
    print("[warn] 点击后 30 秒没跳 success 页，请回浏览器确认是否成稿。")


def _publish_batch(args):
    """「发最近的 n 条没发过的」：默认 1 条；--all 全发；--case 已在 cmd_publish 分流。

    默认只存草稿，--yes 才真发。缺卡片的案例会在发之前现造（build_case）。
    """
    if args.all:
        todo = [c for c in pending_cases(need_cards=True)]
    else:
        todo = pending_cases(need_cards=False)[: max(1, args.near)]
    if not todo:
        print("没有待发的案例了（库里 %d 条都已进过小红书）。" % len(load_cases()))
        return
    print("待发 %d 条，按库里最新在最前：" % len(todo))
    for c in todo:
        has = os.path.isfile(os.path.join(OUT, c["id"], "note.json"))
        print("  · %-24s %s" % (c["id"], "（现造卡片）" if not has else ""))
    fail = []
    for i, c in enumerate(todo, 1):
        print("\n[%d/%d] %s —— %s" % (i, len(todo), c["id"], c.get("name", "")))
        # keep 必须 False：沿用当前编辑页会往已有列表里**再叠 5 张图**。
        # 每条都重新导航，图/字才是干净的（2026-09-26 实测）。
        a = argparse.Namespace(
            case=c["id"], dry=args.dry, yes=args.yes, tags=args.tags,
            no_original=args.no_original, keep=False,
        )
        try:
            cmd_publish(a)
        except SystemExit as e:
            print("[err] 跳过：%s" % e)
            fail.append(c["id"])
        except Exception as e:               # 单条炸了不连坐，继续下一条
            print("[err] 异常：%s：%s" % (type(e).__name__, e))
            fail.append(c["id"])
    print("\n完成：%d 条，失败 %d 条" % (len(todo) - len(fail), len(fail)))
    if fail:
        print("失败清单（重跑即可，会跳过已成的）：%s" % ", ".join(fail))


JS_CLICK_CHAIN = """
function jclick(el){
  var chain=[el];
  for(var p=el.parentElement; p && chain.length<5; p=p.parentElement) chain.push(p);
  chain.forEach(function(x){
    x.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
    x.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    x.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
    if(x.click) x.click();
  });
}
"""

JS_CARDS = "(function(){%s\n" % JS_CLICK_CHAIN + """
  var cards=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    var t=(x.innerText||'');
    return t.indexOf('保存于')>=0 && t.indexOf('编辑')>=0 && t.indexOf('删除')>=0;
  });
  cards = cards.filter(function(c){
    return !cards.some(function(o){ return o!==c && c.contains(o); });
  });
  return JSON.stringify(cards.map(function(c){
    var lines=(c.innerText||'').trim().split('\\n').map(function(s){return s.trim();})
                 .filter(Boolean);
    var saved='';
    for(var i=0;i<lines.length;i++){
      var m=lines[i].match(/保存于\\s*([0-9:\\-\\s]+)/);
      if(m){ saved=lines[i].replace(/保存于/,'').trim(); break; }
    }
    return {title:(lines[0]||'').replace(/^保存于\\s*/,''), saved:saved,
            raw:lines.slice(0,3).join(' | ')};
  }));
})()"""


def _draft_cdp():
    """连上 9222 里已登录的小红书创作中心，停在草稿箱页。"""
    cdp = wp.CDP(PUB_PORT)
    tid = None
    for t in cdp.list_targets():
        if t.get("type") == "page" and "creator.xiaohongshu.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(PUB_URL)["id"]
        time.sleep(4)
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    sub.send("Page.navigate", {"url": DRAFT_URL})
    time.sleep(8)
    sub.eval("1", refresh_context=True)
    return sub


def _js(sub, expr):
    r = sub.eval(expr, refresh_context=True)
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except (ValueError, TypeError):
            pass
    return r


def fetch_drafts(sub=None):
    """拉草稿箱里的图文草稿，最近在前：[{title, saved, raw}]。

    传 sub 就用现成的连接（比如你已经开好新标签页，避开了卡死的旧 tab）。
    """
    if sub is None:
        sub = _draft_cdp()

    def js(e):
        return _js(sub, e)

    if js("""(function(){
      return [].slice.call(document.querySelectorAll('*')).filter(function(x){
        return (x.innerText||'').indexOf('保存于')>=0;}).length>0 ? 'open' : 'closed';
    })()""") != "open":                       # 抽屉可能开着，省一次点击
        if js("(function(){%s\n" % JS_CLICK_CHAIN + """
          var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
            return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()) &&
                   x.getBoundingClientRect().width>0; });
          if(!es.length) return 'notfound';
          jclick(es[es.length-1]);
          return 'clicked';
        })()""") != "clicked":
            raise SystemExit("打不开草稿箱抽屉")
        time.sleep(3)
    if js("(function(){%s\n" % JS_CLICK_CHAIN + """
      var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
        return /图文笔记\\(\\d+\\)/.test((x.innerText||'').trim()) &&
               (x.innerText||'').trim().length<12 &&
               x.getBoundingClientRect().width>0; });
      if(!es.length) return 'notfound';
      jclick(es[es.length-1]);
      return 'clicked';
    })()""") != "clicked":
        raise SystemExit("没找到「图文笔记」tab")
    for _ in range(6):
        time.sleep(2.5)
        if js("[].slice.call(document.querySelectorAll('*')).filter(function(x){\n"
              "return (x.innerText||'').indexOf('保存于')>=0;}).length"):
            break
    return js(JS_CARDS) or []


def norm_title(t):
    """标题归一：去空白/标点/话题井号，越大越好对。"""
    t = re.sub(r"[\s\u3000]+", "", t or "")
    t = re.sub(r"[#＃]", "", t)
    t = re.sub(r"(话题|收藏|点赞)\s*$", "", t)   # 结尾残留的话题标签词
    t = re.sub(r"[，,。.；;：:！!？?（）()「」“”\"'’‘·、\-—_/\\|]", "", t)
    return t.lower()


def match_drafts(drafts, cases=None):
    """草稿箱条目 ←→ 案例。返回 (已对上 case_id 的, 没对上的孤儿草稿)。

    小红书标题是改写过的，所以拿 out/xhs/<id>/note.json 里的 title 去比。
    """
    need = {}
    if cases is None:
        cases = load_cases()
    for c in cases:
        nj = os.path.join(OUT, c["id"], "note.json")
        if not os.path.isfile(nj):
            continue
        with open(nj, encoding="utf-8") as f:
            need[norm_title(json.load(f)["title"])] = c["id"]
    matched, orphans = [], []
    for d in drafts:
        key = norm_title(d.get("title"))
        cid = need.get(key)
        if cid is None:                       # 退一步：包含匹配
            for k, v in need.items():
                if k and len(k) >= 6 and (k in key or key in k):
                    cid = v
                    break
        (matched if cid else orphans).append(
            {"id": cid or "", "title": d.get("title", ""),
             "saved": d.get("saved", ""), "raw": d.get("raw", "")})
    return matched, orphans


def cmd_drafts(args):
    """把草稿箱里真实的稿子拉回来对账，重建「已发记录」。"""
    drafts = fetch_drafts()
    if not drafts:
        print("草稿箱里没读到任何草稿。")
        return
    for i, d in enumerate(drafts, 1):
        d["seq"] = i
    print("草稿箱里 %d 条（最近在前）：" % len(drafts))
    for d in drafts:
        print("  %2d. %-26s %s" % (d["seq"], d["saved"], d["title"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(drafts, f, ensure_ascii=False, indent=2)
        print("\n原始清单已写：%s" % args.json)
    matched, orphans = match_drafts(drafts)
    print("\n对上案例 %d 条，孤儿草稿（认不出案例）%d 条"
          % (len(matched), len(orphans)))
    if orphans:
        for d in orphans:
            print("  ? %s %s" % (d["saved"], d["title"]))
    # 同一个案例可能有多份草稿，drafts 已是最近在前，这里只留最新那份
    latest, dup = {}, []
    for d in matched:
        if not d["id"]:
            continue
        if d["id"] in latest:
            dup.append(d)
        else:
            latest[d["id"]] = d
    if dup:
        print("\n重复草稿（同一案例多份，保留时间最新的那份）：")
        for d in dup:
            print("  旧 · %s  %s  → %s" % (d["saved"], d["title"], d["id"]))
        print("  想清掉就把上面几条的时间填进 out/xhs/del_dup_drafts.py 的 TARGETS，"
              "跑 python out/xhs/del_dup_drafts.py --go")
    if args.write:
        for cid in latest:
            _mark(_draft_file(), cid)
        print("\n已把 %d 个案例写进 data/xhs_drafts.json（待发队列会跳过它们）"
              % len(latest))
    else:
        print("（dry-run。加 --write 把对上的 %d 条写进已发记录）" % len(matched))


def cmd_queue(args):
    """看看接下来会发哪几条（不连浏览器、不花钱）。"""
    todo = pending_cases(need_cards=not args.fresh)
    n = max(1, args.near)
    if not todo:
        print("待发队列是空的。")
        return
    print("待发 %d 条（最新的在最上），当前取前 %d 条：\n" % (len(todo), n))
    for c in todo[:n]:
        has = os.path.isfile(os.path.join(OUT, c["id"], "note.json"))
        print("  %-24s %-28s %s" % (c["id"], c.get("name", ""),
                                    "" if has else "（没卡片，会现造）"))
    print("\n要发就跑：python scripts/xhs_publish.py publish --near %d" % n)


def _pub_file():
    return os.path.join(ROOT, "data", "xhs_published.json")


def _published_ids():
    p = _pub_file()
    if not os.path.isfile(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        return list(d.keys())
    return [x.get("id") for x in d if isinstance(x, dict) and x.get("id")]


def mark_published(cid):
    _mark(_pub_file(), cid)


def _draft_file():
    return os.path.join(ROOT, "data", "xhs_drafts.json")


def mark_drafted(cid):
    """进过草稿箱也算「发过了」，避免重复生成草稿（2026-09-26 清重复踩过）。"""
    _mark(_draft_file(), cid)


def _mark(p, cid):
    try:
        d = json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else []
    except Exception:
        d = []
    ids = list(d.keys()) if isinstance(d, dict) else \
        [x.get("id") for x in d if isinstance(x, dict) and x.get("id")]
    if cid in ids:
        return
    ids.append(cid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump([{"id": i, "at": time.strftime("%Y-%m-%d %H:%M")} for i in ids],
                  f, ensure_ascii=False, indent=2)


def _drafted_ids():
    p = _draft_file()
    if not os.path.isfile(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        return list(d.keys())
    return [x.get("id") for x in d if isinstance(x, dict) and x.get("id")]


def pending_cases(need_cards=True):
    """待发队列：还没进过小红书的案例，库里最新的排最前。

    need_cards=True 时只算已 build 过（有 note.json）的；
    False 则连没生成卡片的也算，交给出发前自动 build。
    """
    skip = set(_published_ids()) | set(_drafted_ids()) | {"voklit"}
    built = set()
    if os.path.isdir(OUT):
        for d in os.listdir(OUT):
            if os.path.isfile(os.path.join(OUT, d, "note.json")):
                built.add(d)
    todo = [c for c in load_cases() if c["id"] not in skip]
    if need_cards:
        todo = [c for c in todo if c["id"] in built]
    return todo[::-1]          # 倒序：最近录入的案例排最前


# ---- build -----------------------------------------------------------------

def cmd_build(args):
    cases = load_cases()
    if args.case:
        todo = [find_case(args.case)]
    else:
        todo = list(cases)
    proc = ensure_chrome(BUILD_PORT)
    used = set()
    try:
        for i, c in enumerate(todo, 1):
            note = build_case(BUILD_PORT, c, used=used)
            print("[%d/%d] %-22s 标题《%s》 正文 %d 字 · %d 张卡片"
                  % (i, len(todo), c["id"], note["title"],
                     len(note["body"]), len(note["images"])))
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
    cmd_preview(args)


def main():
    ap = argparse.ArgumentParser(description="小红书笔记生成/发布")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--case")
    b.add_argument("--all", action="store_true")
    b.set_defaults(fn=cmd_build)
    p = sub.add_parser("preview")
    p.set_defaults(fn=cmd_preview)
    dd = sub.add_parser("drafts",
                        help="把草稿箱里真实的稿子拉回来对账（重建已发记录）")
    dd.add_argument("--json", help="额外把原始清单写到这个路径")
    dd.add_argument("--write", action="store_true",
                    help="把对上案例的写进 data/xhs_drafts.json")
    dd.set_defaults(fn=cmd_drafts)
    qq = sub.add_parser("queue", help="看看接下来会发哪几条（不动浏览器）")
    qq.add_argument("--near", type=int, default=5, help="预览条数，默认 5")
    qq.add_argument("--fresh", action="store_true",
                    help="把还没生成卡片的案例也算进来")
    qq.set_defaults(fn=cmd_queue)
    q = sub.add_parser("publish",
                       help="不带参数 = 发最近 1 条没发过的（--near 3 = 3 条）")
    q.add_argument("--case", help="案例 id，也认名字/标题片段（不用背 id）")
    q.add_argument("--near", type=int, default=1,
                   help="发最近几条没发过的，默认 1")
    q.add_argument("--all", action="store_true",
                   help="publish --all：把其余全部依次存成草稿")
    q.add_argument("--skip", help="额外跳过的 id，逗号分隔")
    q.add_argument("--dry", action="store_true")
    q.add_argument("--yes", action="store_true",
                   help="真发布（默认只存草稿）")
    q.add_argument("--tags", action="store_true",
                   help="尝试走话题浮层加标签（默认关闭：候选选择器对不上，"
                        "硬试会在正文末尾留下孤立 #）")
    q.add_argument("--no-original", action="store_true",
                   help="不勾选「原创声明」（默认会勾上）")
    q.add_argument("--keep", action="store_true",
                   help="沿用当前编辑页状态（默认重新导航，避免图/字叠加）")
    q.set_defaults(fn=cmd_publish)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
