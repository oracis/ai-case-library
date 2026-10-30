#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前逐平台检查清单（借鉴字流：封面比例/字数/AI 声明一次过完）。

把散在各发布脚本里的平台规格收拢成一份**离线清单**——发布前跑一遍，
机器能判的判掉，判不了的（AI 声明勾选等）列成人工项，不再靠记忆。

用法：
    python -X utf8 scripts/prepublish_check.py <case_id>          # 四平台全查
    python -X utf8 scripts/prepublish_check.py <case_id> --platform 小红书
    python -X utf8 scripts/prepublish_check.py <case_id> --json   # 机器可读

退出码：0 = 无 FAIL（警告不算）；1 = 有 FAIL（别发，先修）。

判据来源（全部实测，非转述）：
- 公众号  封面 2.35:1（1080×460，发布流程 cover_html 自动生成）；标题 ≤64 且
          禁金额（make_wechat_title 红线）；正文 HTML 禁 <table>（pm_safe_body
          会塌成串行文字）。
- 小红书  封面 3:4（card-N.png）；标题 ≤20 字（平台硬限）；正文 ≤1000 字；
          标题禁金额（存量教训：已发布改不了标题）。
- 今日头条 封面 16:9（cover.png 带标题字）；标题 5~30 字；正文 meta.json。
- B站     复用头条 cover.png（16:9）；标题 ≤80；伪表格补白必须 U+3000。
- 合规    复用 compliance_check.check()，目标平台禁售命中 = FAIL。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import compliance_check  # noqa: E402

OUT = os.path.join(ROOT, "out")
MONEY_RE = re.compile(r"\$[\d.,]+\s*[KkMm]?\b|月收|累计\s*\$|\bMRR\b", re.I)
RATIO_TOL = 0.02          # 封面比例容差 2%
XHS_TITLE_MAX = 20        # 小红书标题硬限
XHS_BODY_MAX = 1000       # 小红书正文硬限
TT_TITLE_RANGE = (5, 30)  # 头条标题
BILI_TITLE_MAX = 80
WECHAT_TITLE_MAX = 64


# ---------------------------------------------------------------- 基础件

def png_size(path):
    """stdlib 读 PNG 尺寸；非 PNG 返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def ratio_ok(size, target, tol=RATIO_TOL):
    if not size:
        return None
    r = size[0] / size[1]
    return abs(r - target) <= target * tol


def has_money(text):
    return bool(MONEY_RE.search(text or ""))


def load_case(cid):
    """从 cases.json 取案例记录（公众号标题要用）。"""
    p = os.path.join(ROOT, "data", "cases.json")
    try:
        for c in json.load(open(p, encoding="utf-8")):
            if c.get("id") == cid:
                return c
    except (OSError, ValueError):
        pass
    return None


def compliance_item(text, platform, items):
    """跑合规检查并把结果写成清单项。"""
    if not (text or "").strip():
        items.append(("WARN", "合规", "没有可用文本，跳过合规扫描"))
        return
    fatal, warns = compliance_check.check(text, target_platform=platform)
    if fatal:
        for f in fatal:
            items.append(("FAIL", "合规·禁售类目",
                          "%s（%s → 目标平台禁售）" % (f.get("cat"), f.get("note", ""))[:60]))
    n_warn = len([w for w in warns if w.get("level") == "warn"])
    n_info = len([w for w in warns if w.get("level") == "info"])
    if n_warn:
        items.append(("WARN", "合规", "%d 条警告项，发布前人工过一遍（compliance_check.py 看详情）" % n_warn))
    if n_info:
        items.append(("PASS", "合规", "无禁售命中（%d 条语义级疑似，仅提示）" % n_info))
    if not warns:
        items.append(("PASS", "合规", "无禁售命中，无警告"))


# ---------------------------------------------------------------- 各平台

def check_xhs(cid, items, manual):
    d = os.path.join(OUT, "xhs", cid)
    note_path = os.path.join(d, "note.json")
    if not os.path.isfile(note_path):
        items.append(("FAIL", "产物", "out/xhs/%s/note.json 不存在（先跑 xhs 产物构建）" % cid))
        return
    n = json.load(open(note_path, encoding="utf-8"))
    title, body = n.get("title") or "", n.get("body") or ""

    if len(title) > XHS_TITLE_MAX:
        items.append(("FAIL", "标题字数", "%d 字 > 上限 %d（平台硬限，超了会被截断）" % (len(title), XHS_TITLE_MAX)))
    else:
        items.append(("PASS", "标题字数", "%d 字 ≤ %d" % (len(title), XHS_TITLE_MAX)))
    if has_money(title):
        items.append(("FAIL", "标题禁金额", "标题含收入指标 —— 已发布的笔记改不了标题，这是存量教训"))
    else:
        items.append(("PASS", "标题禁金额", "干净"))

    if len(body) > XHS_BODY_MAX:
        items.append(("FAIL", "正文字数", "%d 字 > 上限 %d" % (len(body), XHS_BODY_MAX)))
    else:
        items.append(("PASS", "正文字数", "%d 字 ≤ %d" % (len(body), XHS_BODY_MAX)))

    cards = sorted(glob.glob(os.path.join(d, "card-*.png")))
    if not cards:
        items.append(("FAIL", "封面", "没有 card-N.png（小红书是图文平台，无图=发不了）"))
    else:
        bad = [os.path.basename(c) for c in cards if not ratio_ok(png_size(c), 3 / 4)]
        if bad:
            items.append(("FAIL", "封面比例", "非 3:4：%s（要求 3:4，如 2160×2880）" % "、".join(bad)))
        else:
            items.append(("PASS", "封面比例", "%d 张卡片全部 3:4" % len(cards)))

    md = os.path.join(d, "note.md")
    text = open(md, encoding="utf-8").read() if os.path.isfile(md) else body
    compliance_item(text, "小红书", items)
    manual.append("小红书：发布动作最后仍需人工点击（登录态在本机，CDP 只备稿）")


def check_wechat(cid, items, manual):
    art = os.path.join(OUT, "articles", "%s.html" % cid)
    if not os.path.isfile(art):
        items.append(("FAIL", "产物", "out/articles/%s.html 不存在" % cid))
        return
    html = open(art, encoding="utf-8").read()

    c = load_case(cid)
    title = ""
    if c:
        try:
            import wechat_publish as wp
            title = wp.make_wechat_title(c) or ""
        except Exception:
            title = ""
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    if len(title) > WECHAT_TITLE_MAX:
        items.append(("FAIL", "标题字数", "%d 字 > 上限 %d" % (len(title), WECHAT_TITLE_MAX)))
    elif not title:
        items.append(("WARN", "标题", "取不到标题（cases.json 无此案例且正文无 h1）"))
    else:
        items.append(("PASS", "标题字数", "%d 字 ≤ %d" % (len(title), WECHAT_TITLE_MAX)))
    if title and has_money(title):
        items.append(("FAIL", "标题禁金额", "make_wechat_title 红线：金额是正文的事，进标题判营销感过重"))
    else:
        items.append(("PASS", "标题禁金额", "干净"))

    if re.search(r"<table[\s>]", html, re.I):
        items.append(("FAIL", "正文表格", "含 <table> —— pm_safe_body 会把它塌成串行文字，多列信息改「层｜量级｜条数」分行段落"))
    else:
        items.append(("PASS", "正文表格", "无 <table>"))

    items.append(("PASS", "封面", "2.35:1（1080×460）由发布流程 cover_html 现截，无需备图"))

    md = os.path.join(OUT, "articles", "%s.md" % cid)
    text = open(md, encoding="utf-8").read() if os.path.isfile(md) else ""
    compliance_item(text, "公众号", items)
    manual.append("公众号：群发前人工确认「声明原创」与封面摘要；草稿箱「查无」≠被删，判发布状态要双侧读")


def check_tt(cid, items, manual):
    d = os.path.join(OUT, "toutiao", cid)
    meta_path = os.path.join(d, "meta.json")
    if not os.path.isfile(meta_path):
        items.append(("FAIL", "产物", "out/toutiao/%s/meta.json 不存在" % cid))
        return
    m = json.load(open(meta_path, encoding="utf-8"))
    title = (m.get("title") or "").strip()

    lo, hi = TT_TITLE_RANGE
    if not (lo <= len(title) <= hi):
        items.append(("FAIL", "标题字数", "%d 字不在 %d~%d 区间（超 30 字会被截断）" % (len(title), lo, hi)))
    else:
        items.append(("PASS", "标题字数", "%d 字在 %d~%d" % (len(title), lo, hi)))
    if has_money(title):
        items.append(("FAIL", "标题禁金额", "标题含收入指标（红线）"))
    else:
        items.append(("PASS", "标题禁金额", "干净"))

    cover = os.path.join(d, "cover.png")
    size = png_size(cover) if os.path.isfile(cover) else None
    if size is None:
        items.append(("FAIL", "封面", "cover.png 缺失或非 PNG（B站也复用这张图，缺了两个平台一起卡）"))
    elif not ratio_ok(size, 16 / 9):
        items.append(("FAIL", "封面比例", "%dx%d 不是 16:9" % size))
    else:
        items.append(("PASS", "封面", "%dx%d，16:9 ✓（带标题字版）" % size))

    chars = m.get("chars") or len(m.get("plain") or "")
    items.append(("PASS" if chars >= 300 else "WARN", "正文字数",
                  "%d 字%s" % (chars, "" if chars >= 300 else "（<300 字难过原创门槛，考虑扩写）")))

    compliance_item(m.get("plain") or "", "今日头条", items)
    manual.append("今日头条：发布页「内容由 AI 生成」声明 —— 本库正文为 AI 辅助生成，按平台规则如实勾选")


def check_bili(cid, items, manual):
    d = os.path.join(OUT, "bili", cid)
    note_path = os.path.join(d, "note.json")
    if not os.path.isfile(note_path):
        items.append(("FAIL", "产物", "out/bili/%s/note.json 不存在" % cid))
        return
    n = json.load(open(note_path, encoding="utf-8"))
    title = n.get("title") or ""

    if len(title) > BILI_TITLE_MAX:
        items.append(("FAIL", "标题字数", "%d 字 > 上限 %d" % (len(title), BILI_TITLE_MAX)))
    else:
        items.append(("PASS", "标题字数", "%d 字 ≤ %d" % (len(title), BILI_TITLE_MAX)))
    if has_money(title):
        items.append(("WARN", "标题含金额", "B站未硬禁，但与「标题禁金额」红线不一致，确认是否有意保留"))

    cover = os.path.join(OUT, "toutiao", cid, "cover.png")
    size = png_size(cover) if os.path.isfile(cover) else None
    if size is None:
        items.append(
            ("FAIL", "封面",
             "复用头条 cover.png，但它不存在（python scripts/bilibili_publish.py cover --case %s 补）"
             % cid))
    elif not ratio_ok(size, 16 / 9):
        items.append(("FAIL", "封面比例", "%dx%d 不是 16:9" % size))
    else:
        items.append(("PASS", "封面", "%dx%d，16:9 ✓（复用头条）" % size))

    risk = n.get("risk") or []
    if risk:
        items.append(("WARN", "构建期风险", "note.json 记录了 %d 条 risk：%s" % (len(risk), str(risk)[:80])))
    else:
        items.append(("PASS", "构建期风险", "无"))

    html = os.path.join(d, "article.html")
    body_text = ""
    if os.path.isfile(html):
        h = open(html, encoding="utf-8").read()
        if "\u3000" in h:
            items.append(("PASS", "伪表格补白", "含 U+3000（表意空格，恒 1em）—— 正解"))
        elif re.search(r"[｜|]", h):
            items.append(("WARN", "伪表格补白", "有「｜」分隔但无 U+3000 —— 半角空格补齐已实测会翻车"))
        # 合规扫描用纯文本（strip 标签+脚本样式），note.json 不存正文
        body_text = compliance_check.strip_code_and_style(
            re.sub(r"<[^>]+>", " ", h))
    compliance_item(body_text, "B站", items)
    manual.append("B站：AI 声明按平台规则如实勾选；分区/标签发布时人工确认")


CHECKERS = {"小红书": check_xhs, "公众号": check_wechat,
            "今日头条": check_tt, "B站": check_bili}


# ---------------------------------------------------------------- 主流程

def run(cid, only=None):
    lines = ["## 发布前检查：%s" % cid]
    total_fail = total_warn = 0
    all_manual = []
    for platform, fn in CHECKERS.items():
        if only and platform != only:
            continue
        items, manual = [], []
        fn(cid, items, manual)
        all_manual += manual
        n_fail = sum(1 for s, *_ in items if s == "FAIL")
        n_warn = sum(1 for s, *_ in items if s == "WARN")
        total_fail += n_fail
        total_warn += n_warn
        verdict = "✅ 可发" if n_fail == 0 else "⛔ %d 项 FAIL" % n_fail
        lines.append("")
        lines.append("### %s —— %s%s" % (platform, verdict,
                                         ("（%d 警告）" % n_warn) if n_warn else ""))
        for s, k, msg in items:
            mark = {"PASS": "✓", "WARN": "△", "FAIL": "✗"}[s]
            lines.append("  %s %s：%s" % (mark, k, msg))
    if not only:
        lines.append("")
        lines.append("### 人工项（机器查不了，发布时过一遍）")
        for mtext in all_manual:
            lines.append("  □ %s" % mtext)
    lines.append("")
    lines.append("汇总：%d 项 FAIL / %d 项 WARN —— %s"
                 % (total_fail, total_warn,
                    "可以进发布流程" if total_fail == 0 else "先修 FAIL 再发"))
    return "\n".join(lines), total_fail


def main():
    ap = argparse.ArgumentParser(description="发布前逐平台检查清单")
    ap.add_argument("case_id")
    ap.add_argument("--platform", choices=list(CHECKERS), help="只查一个平台")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args()

    text, n_fail = run(args.case_id, only=args.platform)
    if args.json:
        # JSON 模式复用文本结果，机器侧自行解析汇总行
        print(json.dumps({"case": args.case_id, "fail": n_fail, "report": text},
                         ensure_ascii=False, indent=1))
    else:
        print(text)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
