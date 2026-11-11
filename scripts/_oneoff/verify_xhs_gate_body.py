#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读验收：小红书 `read_editor_body` 在**真页面**上到底读不读得到正文。

⛔ 不发布、不点任何按钮、不写盘。只：开草稿箱 → 开一条编辑器 → 读正文 → 关页。

为什么要真机验
--------------
单测里那张迷你 DOM 只能证明「选择器写法对」，证明不了「小红书现在还是这个
DOM」。选择器一旦改版，单测照样全绿、闸照样空转 —— 这就是 PRD #124 那个洞
的翻版（本地验过 ≠ 平台验过）。

判据
----
打印：正文长度 / 是否含「我去核了一遍」/ 前 60 字。长度 > 0 且与草稿标题不同
才算读到正文（防止把标题当正文）。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import xhs_publish as x        # noqa: E402
import wechat_publish as wp    # noqa: E402

MARK = "我去核了一遍"


def main():
    cdp = wp.CDP(x.PUB_PORT)
    sub = wp.CDP(x.PUB_PORT)
    tid = cdp.new_target(x.DRAFT_URL)["id"]
    print("已开草稿箱页 target=%s" % tid)
    try:
        time.sleep(5)
        if not sub.connect_target(tid):
            raise SystemExit("✗ 连不上草稿箱页")
        drafts = x.fetch_drafts(sub)
        print("草稿箱读到 %d 条" % len(drafts))
        if not drafts:
            raise SystemExit("✗ 草稿箱是空的（或没登录）")
        ordered = sorted(drafts, key=lambda d: d.get("saved") or "")
        d = ordered[0]
        print("目标草稿：%s ｜ %s" % (d.get("saved"), d.get("title")))

        ok, got = x._open_draft_editor(sub, d.get("title"))
        print("编辑器：ok=%s title=%r" % (ok, got))
        if not ok:
            raise SystemExit("✗ 编辑器没打开")
        ready = x._wait_publish_ready(sub)
        print("发布组件就位等待：%ss" % ready)

        body = x.read_editor_body(sub)
        print()
        print("=" * 70)
        print("read_editor_body 读到 %d 字" % len(body))
        print("含「%s」：%s" % (MARK, MARK in body))
        print("与标题相同：%s" % (body.strip() == (d.get("title") or "").strip()))
        print("-" * 70)
        print(body[:180].replace("\n", " / "))
        print("=" * 70)

        # 闸的实际结论（只读，不发布）
        v = x.gate_draft(sub, d)
        print("\n闸结论：%s" % json.dumps(v, ensure_ascii=False)[:400])
    finally:
        try:
            cdp.close_target(tid)
            print("\n已关草稿箱页")
        except Exception as e:                             # noqa: BLE001
            print("\n[warn] 关页失败：%s" % e)


if __name__ == "__main__":
    main()
