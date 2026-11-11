#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：确认 B站 `draft/view.content` 是**完整 HTML** 还是被截断的预览残文。

为什么必须先问这一句
------------------
`clean_bili1_draft_notes.py` 的验收判据是「回写后 norm(content) == norm(母版重建)」。
如果 content 本身是截断的，这条判据**恒假** —— 真改时会 16/16 报「回读与母版不一致」，
看起来像写坏了，其实只是读到的不是全文。反过来若它是完整 HTML，那判据成立，
`only_note=False` 只能解释成「注记是多行块、note_line 只抠了一行」。

判据（任一成立即判定「截断」）
------------------------
  A. content 末尾不是闭合标签（不以 `>` 结尾 / 标签未闭合）
  B. content 长度远小于母版重建 HTML（< 60%）且母版纯文本 > content 纯文本
  C. content 纯文本在母版纯文本里**是前缀**（截断的典型形态）

⛔ 只读：不调 save_draft、不写盘。
用法：
    python -X utf8 tmp/probe_bili_truncation.py --cid bustem
"""
import argparse
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import bili_draft_api as api            # noqa: E402
import bilibili_publish as bp           # noqa: E402
import toutiao_publish as tt            # noqa: E402
import wechat_publish as wp             # noqa: E402

MARK = "我去核了一遍"


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def build_body(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return tt.blocks_to_html(tt.md_to_blocks(md), footer=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="bustem")
    ap.add_argument("--aid", default="")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()

    md = os.path.join(ROOT, "out", "articles", a.cid + ".md")
    if not os.path.isfile(md):
        print("✗ 母版不存在：%s" % md)
        return 1
    body = build_body(md)
    mplain = norm(body)
    print("母版：HTML %d 字 / 纯文本 %d 字 / 段落块 %d"
          % (len(body), len(mplain), len(re.findall(r"<p[ >]", body))))

    aid = a.aid
    if not aid:
        import sqlite3
        conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
        cur = conn.cursor()
        cur.execute("SELECT remote_id, draft_id FROM platform_posts "
                    "WHERE platform='bilibili' AND state='draft' AND case_id=?",
                    (a.cid,))
        r = cur.fetchone()
        conn.close()
        if not r:
            print("✗ 台账里没有 %s 的 bilibili draft 行" % a.cid)
            return 1
        aid = str(r[0] or r[1])
    print("aid=%s" % aid)

    cdp = wp.CDP(a.port or bp.PUB_PORT)
    api._bili_tab(cdp)
    d = api.view_draft(cdp, aid) or {}
    content = d.get("content") or ""
    cplain = norm(content)

    print("\n--- 远端 draft/view ---")
    print("标题      : %s" % (d.get("title") or "")[:60])
    print("content   : HTML %d 字 / 纯文本 %d 字 / 段落块 %d"
          % (len(content), len(cplain), len(re.findall(r"<p[ >]", content))))
    print("含注记    : %s" % (MARK in content))
    print("字段清单  : %s" % ", ".join(sorted(d.keys())))
    print("末尾 120  : ...%s" % content[-120:].replace("\n", " "))
    print("母版末尾  : ...%s" % body[-120:].replace("\n", " "))

    # 判据 A：末尾标签是否闭合
    tail_ok = content.rstrip().endswith(">")
    # 判据 B：体积比
    ratio = (len(cplain) / len(mplain)) if mplain else 0
    # 判据 C：远端纯文本是否为母版纯文本的前缀
    is_prefix = bool(cplain) and mplain.startswith(cplain)

    print("\n--- 裁定 ---")
    print("A 末尾闭合标签     : %s" % tail_ok)
    print("B 纯文本体积比     : %.3f (%d / %d)" % (ratio, len(cplain), len(mplain)))
    print("C 远端是母版前缀   : %s" % is_prefix)
    if is_prefix and ratio < 0.98:
        print("⇒ 判定：**截断**（C 命中），content 不可当全文真值")
        # 找截断点，便于人工确认断在句中断
        print("   截断处上下文：...%s" % mplain[max(0, len(cplain) - 40):len(cplain) + 40])
    elif not tail_ok:
        print("⇒ 判定：**截断**（A 命中，末尾无闭合标签）")
    else:
        print("⇒ 判定：**完整 HTML**，回读逐字判据可用")
        # 说明 only_note 为什么 False：注记跨了几个块
        note_blocks = [p for p in re.split(r"(?=<(?:p|blockquote|h[1-6]|div|ul|ol|pre)[^>]*>)",
                                           content) if MARK in p]
        print("   含注记的顶层块数：%d（note_line 只取一行 ⇒ 多行块时 only_note 必为 False）"
              % len(note_blocks))
        if note_blocks:
            print("   注记块归一后 %d 字" % len(norm("".join(note_blocks))))
            only = cplain.replace(norm("".join(note_blocks)), "") == mplain
            print("   按整块摘除后与母版相等：%s" % only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
