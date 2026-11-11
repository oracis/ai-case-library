#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIFO 发布路径的**内容形态闸**（2026-11-10，PRD #124）。

背景
----
PRD #90 修掉了「FIFO 绕过 11 闸」里**重复发布**那一半（case 级去重，
`fifo_dedupe.py`），但 `publish_one.run()` 的 FIFO 分支仍是直接 subprocess
调各平台脚本 —— `run_gates()` 里的 `template` / `title` / `cover` 一条都不跑。
11-10 B站一号 FIFO 发出 10-20 积压的 promptmonitor-io 旧稿，正文开头带着
「我去核了一遍：所给原文中不存在任何 TrustMRR 成交记录…」这段**内部核验
注记**（`out/articles/promptmonitor-io.html` 的 `<blockquote>`）⇒ 还剩的
「内容形态闸」这一半没修。

职责
----
判据**不另立一套**：只调 `publish_guard.scan_content()`（九平台 block 规则，
含本次新增的 `内部核验注记`）。本模块只负责「正文从哪儿拿 + 拦下怎么说」。

四个入口在点发布**之前**调 `guard()`：
  · bilibili / bilibili2：`draft/view` 读回的**平台实际正文**（最硬）
  · xhs / wechat：按 case 反查本地正文源（out/articles/*.html、out/xhs/*/note.json）

⛔ 边界（必须如实说，不许写成「全部修好」）：
  · 只补**内容形态闸 + 标题破折号闸**（后者 PRD #135 新增）。dedupe 已由
    `fifo_dedupe` 覆盖；cover / draft / 其余 title 规则仍属「新写选稿」路径，
    FIFO 发的就是箱里建好的草稿，照搬会误杀（#90 实跑抓到 `check_dupe`
    把公众号候选从 49 误杀到 12）。
  · 标题破折号闸**单独前置**：标题来自平台草稿箱、与正文源无关，
    必须在「找不到正文源 ⇒ checked=False 放行」之前判；孤儿草稿也要拦。
    2026-11-11 公众号 FIFO 发出的 Coral 旧稿标题带「——」就是漏在这里。
  · 找不到正文源（孤儿草稿认不出 case、平台接口没给正文）时 `checked=False`
    并 **warn 放行**：一律拦下会让箱里那批孤儿永远发不出去（同 #90
    `filter_rows` 的「认不出的孤儿保留」）。但绝不打印「已验过」。
  · 命中 block ⇒ **不发布、不记账、不自动跳到下一条**。跳等于变相绕过，
    且按 11-08 用户裁定「旧稿不合规格 ⇒ 在旧稿上改稿再发，不许另起新稿」。

用法
----
    python -X utf8 scripts/fifo_content_gate.py --scan-backlog   # 只读：存量脏稿清单
    python -X utf8 scripts/fifo_content_gate.py --scan-backlog --json
"""
import argparse
import glob
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import publish_guard as G  # noqa: E402  —— 形态判据的唯一源

FIFO_PLATFORMS = ("wechat", "xhs", "bilibili", "bilibili2")
COMMENT = re.compile(r"<!--.*?-->", re.S)


def local_sources(platform, case):
    """按 case 反查本地正文源，按「最贴近实际发布正文」排序。

    `ext-ep-*` / `ep-*` 两套 id 都试（`G.norm_id` 归一，见 PRD #90）。
    B站二号产物在游戏仓，不在本仓 out/articles ⇒ 那边靠平台 draft/view 正文，
    不在这里硬编绝对路径（项目零绝对路径依赖）。
    """
    if not case:
        return []
    c = str(case)
    ids = [c]
    try:
        n = G.norm_id(c)
        if n and n != c:
            ids.append(n)
    except Exception:                                    # noqa: BLE001
        pass
    out = []
    for cid in ids:
        out.append(os.path.join(ROOT, "out", "articles", cid + ".html"))
        out.append(os.path.join(ROOT, "out", "articles", cid + ".md"))
        out.append(os.path.join(ROOT, "out", "xhs", cid, "note.json"))
        if platform:
            out.append(os.path.join(ROOT, "drafts", "newbatch",
                                    platform, cid + ".md"))
    return out


def _read_source(path):
    """读正文源；note.json 取 title/body/desc/content 拼成可扫的文本。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    if path.endswith(".json"):
        try:
            d = json.loads(txt)
            if isinstance(d, dict):
                txt = "\n".join(str(d.get(k) or "")
                                for k in ("title", "body", "desc", "content"))
        except (ValueError, TypeError):
            pass
    return txt


def guard(platform, case=None, title="", text=None, pack=None, text_src=None):
    """发布前形态闸。返回结构化结果，不抛异常、不写盘、不起浏览器。

    text   —— 平台草稿的**实际正文**，优先级最高
              （B站 `draft/view` 的 content；公众号编辑页 DOM，见
              `wechat_publish.read_draft_body`）
    pack   —— 本地产物路径
    case   —— 反查本地正文源用
    text_src —— 报告里 `source` 显示的名字，缺省 `platform:draft/view`
              （B站口径）。公众号必须显式传，否则日志会把 appmsg_edit 读来的
              正文标成 draft/view，排查时误以为验的是 B站。
    结果：{ok, checked, source, blocks, warns, detail}
      ok=False      ⇒ 调用方必须停住，不发布、不记账
      checked=False ⇒ 正文源没拿到，闸**未生效**，只 warn 放行
    """
    # 🚨 PRD #135（2026-11-11）：FIFO 草稿标题来自平台草稿箱，`run_gates()`
    #   的 title 闸整条被绕过 ⇒ 11-11 公众号发出带「——」的 Coral 旧稿。
    #   判据复用 `publish_guard.TITLE_DASH`，不另立一套。
    #   ⚠ 必须放在正文源判定之前：孤儿草稿 `checked=False` 那条提前 return
    #   会把标题闸一起跳过（标题与有没有正文源完全无关）。
    title_blocks = []
    if title and G.TITLE_DASH.search(title):
        title_blocks.append({
            "level": "block", "rule": "标题含破折号",
            "detail": "命中「%s」⇒ 11-10 用户裁定九平台标题禁破折号；"
                      "在平台草稿上改成冒号/逗号后再发，不许另起新稿"
                      % G.TITLE_DASH.search(title).group(0)})

    body = text
    src = (text_src or "platform:draft/view") if text else None
    if not body and pack:
        body = G.body_of(pack)
        src = pack
    if not body and case:
        for p in local_sources(platform, case):
            if os.path.isfile(p):
                body = _read_source(p)
                try:
                    src = os.path.relpath(p, ROOT)
                except ValueError:      # Windows 跨盘符（C: 临时目录 vs D: 仓库）
                    src = p
                break
    if not body:
        return {"ok": not title_blocks, "checked": False, "source": None,
                "blocks": title_blocks, "warns": [],
                "detail": "找不到正文源（case=%r，本地产物与平台接口都没给正文）"
                          "⇒ 本次内容形态闸未生效，不假装验过%s"
                          % (case or "",
                             "；标题破折号闸已独立生效" if title else "")}

    fs = G.scan_content(platform, body)
    blocks = title_blocks + [f for f in fs if f["level"] == "block"]
    warns = [f for f in fs if f["level"] == "warn"]
    return {"ok": not blocks, "checked": True, "source": src,
            "blocks": blocks, "warns": warns,
            "detail": ("已验正文 %d 字｜block %d 条｜warn %d 条"
                       % (len(body), len(blocks), len(warns)))}


def report(v, platform, title=""):
    """把闸结果打给操作者。`checked=False` 必须显式说「未生效」。"""
    head = "[FIFO 形态闸/%s] %s" % (platform, (title or "")[:36])
    # ⚠ block 优先于 checked：孤儿草稿 checked=False 但标题带破折号时，
    #   必须先说清「被什么拦下」，不能只打印「闸未生效」让人以为放行了。
    if v["blocks"]:
        for b in v["blocks"]:
            print("  ✗ %s 拦下【%s】：%s" % (head, b["rule"], b["detail"]))
        print("  ⇒ 不发布、不记账。按 11-08 裁定「在旧稿上改稿再发，不许另起新稿」，"
              "也不许自动跳到草稿箱下一条。")
        return
    if not v["checked"]:
        print("  ⚠ %s 未生效：%s" % (head, v["detail"]))
        return
    print("  ✓ %s：%s（源 %s）" % (head, v["detail"], v["source"]))
    for w in v["warns"][:2]:
        print("      warn %s：%s" % (w["rule"], w["detail"][:80]))


def block_reason(v, platform, title=""):
    """给 `raise SystemExit` / `return False` 用的单行原因。"""
    return ("FIFO 内容形态闸拦下《%s》：%s ⇒ 不发布、不记账；按 11-08 裁定"
            "在旧稿上改稿再发（不许另起新稿、不许跳过）"
            % ((title or "")[:40],
               "；".join(b["rule"] for b in v["blocks"]) or "见上"))


# ---------------------------------------------------------------- 只读盘点

BACKLOG_GLOBS = (
    os.path.join(ROOT, "out", "articles", "*.html"),
    os.path.join(ROOT, "out", "articles", "*.md"),
    os.path.join(ROOT, "out", "xhs", "*", "note.json"),
    os.path.join(ROOT, "drafts", "newbatch", "*", "*.md"),
)


def scan_backlog():
    """只读：列出**会渲染进正文**的内部核验注记残留，供改稿授权用。

    ⚠ 必须剥 HTML 注释 —— `<!-- 核对用来源（…别进正文） -->` 是合法注释。
    """
    hit = []
    for pat in BACKLOG_GLOBS:
        for p in sorted(glob.glob(pat)):
            try:
                txt = _read_source(p)
            except OSError:
                continue
            if G.VERIFY_NOTE.search(COMMENT.sub("", txt)):
                hit.append(os.path.relpath(p, ROOT).replace("\\", "/"))
    return hit


def main(argv=None):
    ap = argparse.ArgumentParser(description="FIFO 内容形态闸 / 存量脏稿只读盘点")
    ap.add_argument("--scan-backlog", action="store_true",
                    help="只读：列出带内部核验注记的存量产物")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    a = ap.parse_args(argv)
    if not a.scan_backlog:
        ap.print_help()
        return 0
    hit = scan_backlog()
    if a.json:
        print(json.dumps({"count": len(hit), "files": hit},
                         ensure_ascii=False, indent=2))
    else:
        print("存量带内部核验注记的产物：%d 个" % len(hit))
        for p in hit:
            print("  " + p)
        print("\n⛔ 批量改文案须先授权；按 11-08 裁定应在旧稿上改稿再发，"
              "不许另起新稿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
