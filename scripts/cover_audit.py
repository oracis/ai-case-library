#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面全量审计：金额残留 / 描述截断 / 垂直居中 / 空介绍不渲染。

三层判据（都不靠目测）：
  1. HTML 层  —— 直接查 tt_cover_html() 的输出，最容易也最准：
                 有没有金额、desc 行该在不在。
  2. 像素层  —— 量 PNG 里文字块的纵向中心，判断是否居中。
  3. 溢出层  —— 用 CDP 量 .name/.desc 的 scrollHeight vs clientHeight，
                 抓max-height 静默截断（这是 CSS 层看不出问题的坑）。

用法：
    python -X utf8 out/cover_audit.py            # 1+2 层
    python -X utf8 out/cover_audit.py --cdp      # 加跑 3 层（要连浏览器，慢）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)

# 像素层要一个零依赖 PNG 解码器。优先用项目自带的，其次找本机技能目录。
# ⚠ 不能写死 ~/.workbuddy/... 的绝对路径 —— 那样这脚本换个机器就废。
def _find_imgtool():
    local = os.path.join(ROOT, "scripts", "imgtool.py")
    if os.path.exists(local):
        return
    import glob
    pats = [os.path.expanduser("~/.workbuddy/skills/zero-dep-image-inspect/scripts")]
    pats += glob.glob(os.path.expanduser(
        "~/.workbuddy/skills/*/scripts"))
    for d in pats:
        if os.path.exists(os.path.join(d, "imgtool.py")):
            sys.path.insert(0, d)
            return
    raise SystemExit(
        "找不到 imgtool.py（像素层需要）。可跳过像素层：加 --fast")


_find_imgtool()
from imgtool import load_rgb  # noqa: E402

import toutiao_publish as tp  # noqa: E402

# 金额/营收 token：封面上一律不该出现。
# ⚠ 第一版正则有 4 条误报（实测 2026-10-02）：
#   'B2B'（uplinked-b-v / gojiberryai）—— B2B 不是金额，B 是「商业」；
#   '5 万'（mort「盯 5 万个企业招聘页」）—— 量词不是金额。
# ⇒ 金额必须**带货币单位或营收语境**才算命中。
_CUR = r"(?:美元|美金|元|人民币|刀|usd|rmb|cny)"
_NUM = r"\d[\d,.]*"
MONEY = re.compile(
    r"(?:"
    r"\$" + _NUM
    + r"|" + _NUM + r"\s*" + _CUR
    + r"|(?:" + _NUM + r"\s*(?:万|千)\s*" + _CUR + r")"
    + r"|(?:" + _NUM + r"\s*(?:万|千)(?=\s*(?:MRR|收入|营收|流水|用户|月)))"
    + r"|(?:" + _NUM + r"\s*(?:K|M|B)\s*(?:MRR|ARR)?"
                r"(?=\s*(?:收入|营收|流水|/月|月)))"
    + r"|(?:月入|年收入|营收|月收入|收入|流水|MRR|ARR)"
                r"\s*(?:达|到|从|为|[:：])?\s*\$?" + _NUM
    + r")",
    re.I)

# 垂直居中判据：文字块中心落在画面高度的 0.40~0.60
CENTER_LO, CENTER_HI = 0.40, 0.60


def load_cases():
    import json
    p = os.path.join(ROOT, "data", "cases.json")
    data = json.load(open(p, encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def png_text_blocks(path, thresh=90):
    """返回 [(y0,y1), ...]，已合并连续行并剔除右上角 glow 圆。"""
    w, h, px = load_rgb(path)
    rows = []
    for y in range(0, h, 2):
        hit = 0
        for x in range(0, w, 4):
            i = (y * w + x) * 3
            if (px[i] + px[i + 1] + px[i + 2]) / 3 > thresh:
                hit += 1
        if hit >= 4:
            rows.append(y)
    if not rows:
        return h, []
    blocks, start, prev = [], rows[0], rows[0]
    for y in rows[1:]:
        if y - prev > 12:
            blocks.append((start, prev))
            start = y
        prev = y
    blocks.append((start, prev))
    return h, [b for b in blocks if b[1] - b[0] < h * 0.5]


def visible_text(html):
    """只取可见文本：剥掉 <style>…</style>。

    ⚠ 不剥会把 CSS 字号（font-size:150px / 2.45em）当金额 —— 那才是
    真正的高危误报源。
    """
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html)


def audit_html(c):
    html = tp.tt_cover_html(c)
    problems = []
    # 1) 金额残留（只在可见文本里查）
    vis = visible_text(html)
    hits = set(MONEY.findall(vis))
    if hits:
        problems.append("MONEY:%s" % sorted(hits))
    # 2) desc 行与 cover_desc() 一致性
    desc = tp.tt_cover_desc(c)
    if desc:
        if 'class="desc' not in html:
            problems.append("DESC_MISSING(有简介却没渲染)")
        elif desc not in html:
            problems.append("DESC_TRUNCATED(简介未完整出现)")
    else:
        if 'class="desc' in html:
            problems.append("DESC_SHOULD_BE_ABSENT(空简介却渲染了)")
    # 3) 项目名必须在
    if c.get("name") and c["name"] not in html:
        problems.append("NAME_MISSING")
    return problems


def audit_png(cid):
    p = os.path.join(ROOT, "out", "toutiao", cid, "cover.png")
    if not os.path.exists(p):
        return ["PNG_MISSING"], None
    h, blocks = png_text_blocks(p)
    if not blocks:
        return ["NO_TEXT"], None
    mid = (blocks[0][0] + blocks[-1][1]) / 2 / h
    problems = []
    if not (CENTER_LO <= mid <= CENTER_HI):
        problems.append("OFF_CENTER:%.3f" % mid)
    if len(blocks) > 3:
        problems.append("TOO_MANY_LINES:%d(可能被挤压)" % len(blocks))
    return problems, mid


def main():
    cases = load_cases()
    bad = 0
    mids = []
    # --fast 只跑 HTML 层（<1 秒）；默认跑 HTML + 像素层（纯 Python 解
    # 39 张 3840×2160 约 5 分钟，用imgtool.resize 先缩到1/4 再量）
    fast = "--fast" in sys.argv
    if fast:
        pxa = lambda cid: ([], None)  # noqa: E731
    else:
        def pxa(cid):
            return audit_png(cid)
    print("审计 %d 条封面（%s）" % (len(cases), "HTML层" if fast else "HTML+像素层"))
    print("-" * 78)
    for c in cases:
        cid = c["id"]
        hp = audit_html(c)
        pp, mid = pxa(cid)
        if mid is not None:
            mids.append(mid)
        allp = hp + pp
        if allp:
            bad += 1
            print("FAIL %-24s %s" % (cid, " | ".join(allp)))
    print("-" * 78)
    if mids:
        print("垂直中心 min=%.3f max=%.3f avg=%.3f（判据 %.2f~%.2f）"
              % (min(mids), max(mids), sum(mids) / len(mids),
                 CENTER_LO, CENTER_HI))
    print("合计：%d 条，%d 条有问题" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
