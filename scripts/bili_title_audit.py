#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站标题体检：抓「裸产品名 / 带金额 / 无信息量」的标题。

为什么存在
----------
2026-11-06 用户发现 B站草稿箱里 upvoty 那条标题就是「Upvoty」一个字。
根因在 `bilibili_publish.make_title` 的三级兜底：
  1. `data/bili_title_overrides.json` —— 有就赢（正确路径）
  2. 头条 `out/toutiao/<cid>/meta.json` —— **仅当不含金额**
  3. 文章 h1 截断 —— 自媒体稿的 h1 常常就是产品名
upvoty 恰好：override 缺条目 + 头条标题含 `$40K MRR` 被金额正则拒 +
h1 就是「Upvoty」 ⇒ 三级全落空，静默出裸名（构建日志只打「✓ 标题」）。

判据（任一命中即 FAIL）
  1. 少于 8 字，或没有中文（裸英文产品名）
  2. 没有说明分隔符（`：` `:` `，`）—— 只有名字没有「它是干什么的」
  3. 含金额（`$123` / `12 美元` / `MRR` 后跟数字）—— B站标题禁金额
  4. 超过 30 字（B站建议上限，超过会被截断成半句）

扫描面：产物 `out/bili/*/note.json` + 台账 `data/publish_manifest.json`
        + 覆盖表本身的取值。
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONEY_RE = re.compile(r"[$＄]\s?\d|\d+\s*(美元|美金|刀)|\bMRR\b|\bARR\b", re.I)
SEP_RE = re.compile(r"[：:，,——-]")


def judge(title):
    t = (title or "").strip()
    bad = []
    if len(t) < 8:
        bad.append("过短(%d字)" % len(t))
    if not re.search(r"[\u4e00-\u9fa5]", t):
        bad.append("无中文(裸英文名)")
    # 分隔符只在「短标题」上强制：足够长的中文标题本身就能说明是什么
    # （如「用 Backbone 游戏引擎复刻超级马力欧兄弟」），不必凑冒号。
    if len(t) < 14 and not SEP_RE.search(t):
        bad.append("无说明分隔符")
    if MONEY_RE.search(t):
        bad.append("含金额")
    if len(t) > 30:
        bad.append("超30字(%d)" % len(t))
    return bad


def main():
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "out", "bili", "*", "note.json"))):
        cid = os.path.basename(os.path.dirname(p))
        try:
            rows.append(("产物", cid, (json.load(open(p, encoding="utf-8"))
                                      .get("title") or ""), ""))
        except Exception as e:
            print("读失败 %s: %s" % (p, e))
    mp = os.path.join(ROOT, "data", "publish_manifest.json")
    if os.path.isfile(mp):
        m = json.load(open(mp, encoding="utf-8"))
        for cid, plats in m.items():
            b = (plats or {}).get("bilibili") if isinstance(plats, dict) else None
            if isinstance(b, dict) and (b.get("title") or "").strip():
                rows.append(("台账", cid, b["title"], b.get("state") or ""))
    op = os.path.join(ROOT, "data", "bili_title_overrides.json")
    if os.path.isfile(op):
        for cid, t in json.load(open(op, encoding="utf-8")).items():
            rows.append(("覆盖表", cid, t, ""))

    bad_rows = [(src, cid, t, judge(t), st) for src, cid, t, st in rows if judge(t)]
    # B站**发表后不可编辑** ⇒ published 的金额标题是历史遗留，改不动；
    # 只把它列成 WARN，别当成「今天要修」的活。
    fixable = [r for r in bad_rows if r[4] != "published"]
    legacy = [r for r in bad_rows if r[4] == "published"]
    print("扫描 %d 条标题（产物 %d 条）"
          % (len(rows),
             len(glob.glob(os.path.join(ROOT, "out", "bili", "*", "note.json")))))
    if fixable:
        print("✗ %d 条**可改**且不合格：" % len(fixable))
        for src, cid, t, bad, st in fixable:
            print("  [%s/%s] %-28s %-30s %s"
                  % (src, st or "-", cid, t, "、".join(bad)))
    if legacy:
        print("⚠ %d 条历史已发（不可编辑，仅记录）：" % len(legacy))
        for src, cid, t, bad, _st in legacy:
            print("  [%s] %-28s %-30s %s" % (src, cid, t, "、".join(bad)))
    if not fixable:
        print("✓ 没有待修的标题")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
