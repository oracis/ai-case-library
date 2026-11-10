#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 data/cases.json 生成各文本平台适配的草稿 markdown（不调 LLM，离线、数据驱动）。

用法：
    python -X utf8 scripts/gen_platform_md.py --ids chessigma,cod-dex \
        --platforms zhihu,cnblogs,csdn,aliyun,tencentcloud
    python -X utf8 scripts/gen_platform_md.py --tone-report

落盘到 drafts/newbatch/<platform>/<slug>.md，只写缺的（除非 --overwrite）。

🚨 2026-11-04 两轮改写（用户：「AI 味太重 / 套机械模板」→「按每个平台调性编写文稿」）：
  1. 删掉 `> 平台：X ｜ 状态：本地稿` 管线水印与「官网 + TrustMRR 支付验证页」
     罐头来源句；删掉 `t[:100]` 硬切，改按标点裁。
  2. 五个 builder 换成**单一渲染器 + `platform_tone.TONE` 调性表**。此前五个平台
     共用 `_head()`（# 标题 / verdict / 公开口径）与同一批字段换栏目名，实测
     `tencentcloud/cod-dex.md` 与 `aliyun/cod-dex.md` 逐字相同（只差水印平台名）。
     现在每个平台的标题句式、开场、章节顺序、小标题、收尾、禁忌各自不同，
     事实字段一律取自 cases.json，不推断未公开的技术实现。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import platform_tone as TONE        # noqa: E402  九平台调性单一源
import publish_guard as G          # noqa: E402  平台上限单一真理（与发布闸共用）


def load_cases():
    import case_store as CS
    return CS.load_by_id()


def money(v):
    try:
        return "$%s" % format(int(round(float(v))), ",")
    except Exception:
        return str(v)


def metric_head(c):
    return (c.get("metrics") or {}).get("headline") or ""


# ---------------------------------------------------------------- 标题

def _plain_money(s):
    """`$18,380`→`18,380 美元`（正文里用；技术平台标题会再被 _no_money 清掉）。"""
    s = re.sub(r"\$([0-9][0-9.]*)\s*[Mm]\b",
               lambda m: "%g 万美元" % (float(m.group(1)) * 100), s)
    s = re.sub(r"\$([0-9][0-9.]*)\s*[Kk]\b",
               lambda m: "%g 万美元" % (float(m.group(1)) / 10.0), s)
    s = re.sub(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", r"\1 美元", s)
    return s


def _no_money(s):
    """技术平台标题里不许留金额（CSDN 一.10 / 腾讯云驳回单 / 新 title 闸）。"""
    s = re.sub(r"[¥$€£]\s*\d[\d,.]*\s*(?:[KkMm万亿])?", "", s)
    s = re.sub(r"\d[\d,.]*\s*(?:万美元|万美金|美金|美元|万元|欧元|英镑)", "", s)
    s = re.sub(r"(?<!\w)MRR\s*\$?\d*|(?<!\w)ARR\s*\$?\d*", "", s)
    s = re.sub(r"近\s*30\s*天(?:已验证)?收入\s*", "", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ，,、：:；;")


def _strip_meta(s):
    """抹掉数据层旁注：`，TrustMRR 实时核对`、`公开口径：`、`（非订阅制…）`。"""
    s = re.sub(r"[，,]?\s*TrustMRR\s*(实时)?核对[^，,。]*$", "", s)
    s = re.sub(r"^\s*公开口径[:：]\s*", "", s)
    s = re.sub(r"[（(]\s*非订阅制[^）)]*[）)]", "", s)
    return s.strip(" ，,、：:；;")


def _cut(s, limit):
    """按标点截断，不留半个词组；不产生 `t[:N]` 那种悬空尾巴。"""
    s = s.strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    pos = max(head.rfind(x) for x in "，,、；;：:")
    if pos >= limit * 0.5:
        return head[:pos].rstrip("，,、；;：: 的")
    return head.rstrip("，,、；;：: 的")


def _hook(c, limit=26):
    """一句话的第一分句：丢书名号/加粗，保留「具体做什么」。

    ⚠ `「」` 要换成空格而不是删掉：`「免费版 Chess.com」拿下` 直接删会粘成
    `Chess.com拿下`。另外 `_cut` 可能停在枚举中间（`把登录、支付`），
    这时丢掉冒号后半截，只留完整的名词短语。
    """
    s = (c.get("one_liner") or c.get("what_it_does") or "").strip()
    s = re.split(r"[。！？]", s)[0]
    s = s.replace("「", "").replace("」", " ").replace("**", "")
    if not s:
        s = (c.get("industry") or c.get("category") or "").strip()
    h = _cut(_strip_meta(_plain_money(s)), limit).rstrip("，,、 的")
    m = re.search(r"[：:]", h)
    if m and len(h) - m.end() < 8 and m.start() >= 6:
        h = h[:m.start()]
    return re.sub(r"\s{2,}", " ", h).strip()


def _subs(c):
    m = c.get("metrics") or {}
    n = m.get("subscriptions")
    if isinstance(n, (int, float)) and n > 0:
        return "%d" % int(n)
    return ""


def _title_tpl(platform, c, hook):
    """每个平台的**标题句式**不同，不再是同一串模板换名字。

    ⛔ 产品名后缀不许再用「—— 名字」（2026-11-10 用户裁定标题禁破折号）。
    旧模板三个技术平台都是 `主标题 —— 产品名`，是 `publish_guard.TITLE_DASH`
    的直接来源：生成即被闸拦，`test_platform_tone` 里
    `test_generated_titles_pass_guard_and_tone` 因此红。现在统一改成
    句末括号署名 `（产品名）`——读得通、不带破折号、产品名也不丢。
    """
    name = c.get("name") or c.get("id")
    n = _subs(c)
    if platform == "zhihu":
        if n:
            return "%s：%s，为什么只有 %s 人付费？" % (name, hook, n)
        return "%s：%s，这件事能成立吗？" % (name, hook)
    if platform == "cnblogs":
        return "读 %s：%s 的形态与边界" % (name, hook)
    if platform == "csdn":
        return "%s：工程上要过哪几道关（%s）" % (hook, name)
    if platform == "tencentcloud":
        return "%s 的架构取舍：状态、幂等与成本（%s）" % (hook, name)
    if platform == "aliyun":
        return "%s：负载形状怎么反过来约束架构（%s）" % (hook, name)
    return "%s：%s" % (name, hook)


def make_title(c, money=True, platform=None):
    """按平台调性生成标题；`money` 保留兼容参数（技术平台一律不带金额）。

    🚨 旧实现 `return t[:100]` 是硬切（注释却写 ≤62），把标题拦腰切断。
    现在按标点裁，并套 `publish_guard.TITLE_MAX` 的平台上限。
    """
    hook = _no_money(_hook(c)) if platform in G.TECH_PLATFORMS else _hook(c)
    if not hook:
        hook = _no_money(_strip_meta(metric_head(c))) or (c.get("name") or c.get("id"))
    t = _title_tpl(platform, c, hook)
    if money and platform not in G.TECH_PLATFORMS:
        pf = _proof(c)
        if pf and pf not in t:
            cand = "%s，%s" % (t, pf)
            if len(cand) <= G.TITLE_MAX.get(platform, 62):
                t = cand
    return _cut(t, G.TITLE_MAX.get(platform, 62))


def _proof(c, limit=30):
    """非技术平台可带一个数字事实；优先增长曲线，其次当期收入、用户量。"""
    m = c.get("metrics") or {}
    for k in ("growth", "headline", "customers"):
        v = (m.get(k) or "").strip()
        if not re.search(r"[0-9]", v):
            continue
        v = re.split(r"[；;。]", v)[0].strip()
        if v:
            return _cut(_strip_meta(_plain_money(v)), limit).rstrip("，,、")
    return ""


def webfix(s):
    # CSDN 会把段落内两个 $ 当行内公式（博客园/知乎/两朵云不用转）
    return s.replace("$", "\\$")


# ---------------------------------------------------------------- 渲染

def _text(v, fallback=""):
    """字段可能是 str / list / None：统一成正文文本，不编内容。"""
    if isinstance(v, list):
        return "\n".join("- %s" % x for x in v if x)
    return (v or fallback or "").strip()


def build_ctx(c, platform):
    """把 cases.json 的事实字段映射成调性表里的占位符。"""
    name = c.get("name") or c.get("id")
    what = _text(c.get("what_it_does"), c.get("one_liner"))
    verdict = (c.get("verdict") or c.get("one_liner") or "").strip()
    m = c.get("metrics") or {}
    metric = _text(m.get("headline") or "未公开可核验的收入数字")
    return {
        "name": name,
        "verdict": _strip_meta(verdict),
        "what": what,
        "what_short": _cut(re.split(r"[。！？]", what)[0], 60),
        "money": _text(c.get("how_it_makes_money")),
        "metric": metric,
        "playbook": _text(c.get("playbook")),
        "why": _text(c.get("why_it_works"), verdict),
    }


def render(platform, c):
    """单一渲染器；平台差异全部来自 `TONE.profile(platform)`。"""
    p = TONE.profile(platform)
    ctx = build_ctx(c, platform)
    lines = ["# %s" % make_title(c, money=True, platform=platform), ""]
    lines += [p["opening"].format(**ctx), ""]
    for heading, key in p["sections"]:
        body = ctx.get(key) or ""
        lines += ["## %s" % heading, "", body, ""]
    lines += [p["closing"].format(**ctx), ""]
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
    if platform == "csdn":
        txt = webfix(txt)
    return txt


def _make_builder(platform):
    def _b(c):
        return render(platform, c)
    _b.__name__ = "build_%s" % platform
    _b.__doc__ = "%s：%s" % (TONE.profile(platform)["label"],
                             TONE.profile(platform)["voice"])
    return _b


BUILDERS = {p: _make_builder(p) for p in TONE.TEXT_PLATFORMS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids")
    ap.add_argument("--platforms")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--tone-report", action="store_true", help="打印九平台调性表")
    a = ap.parse_args()
    if a.tone_report:
        for k, v in TONE.TONE.items():
            print("%-14s %-12s %-4d %s" % (k, v["title_style"], v["limit"], v["voice"][:40]))
            print("    %s" % " / ".join(h for h, _ in v["sections"]))
        return 0
    if not (a.ids and a.platforms):
        ap.error("要 --ids + --platforms，或 --tone-report")
    cases = load_cases()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    plats = [x.strip() for x in a.platforms.split(",") if x.strip()]
    made = 0
    for cid in ids:
        c = cases.get(cid)
        if not c:
            print("跳过未知 case:", cid)
            continue
        for plat in plats:
            if plat not in BUILDERS:
                print("未知平台:", plat)
                continue
            d = os.path.join(ROOT, "drafts", "newbatch", plat)
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, cid + ".md")
            if os.path.exists(fp) and not a.overwrite:
                print("已有跳过:", plat, cid)
                continue
            txt = render(plat, c)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(txt)
            made += 1
            print("生成:", plat, cid, len(txt), "字")
    print("完成，新增", made, "篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
