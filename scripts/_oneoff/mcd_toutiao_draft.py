#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳参赛作品 → 今日头条草稿（一次性）。

为什么要这个脚本：ai-case-library 的 toutiao_publish.py 只认
`data/cases.json` 里的案例（find_case_fuzzy / load_cases 都从那里取）。
麦当劳这篇不是 AI 案例库的内容，直接塞进 cases.json 会污染案例库数据
（build_static 会把它编进站点）。

所以这里**不碰 cases.json**，改成把合成 case 注入到模块全局：
    tp.find_case_fuzzy / tp.load_cases / tp.pending_cases
三个查找入口都指向同一个 dict，脚本其余逻辑原样复用。

用法：
    python scripts/_oneoff/mcd_toutiao_draft.py build    # 生成 article.html + meta.json
    python scripts/_oneoff/mcd_toutiao_draft.py covers   # 渲染 cover.png
    python scripts/_oneoff/mcd_toutiao_draft.py draft    # 存草稿箱（不发布）

⚠ draft 不带 --send，只进草稿箱，不公开发布。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as tp  # noqa: E402

CID = "mcdpoints"
TITLE = "用麦当劳官方接口查了一下，我的积分正在悄悄过期"

CASE = {
    "id": CID,
    "name": "我的麦当劳积分，正在悄悄过期",
    "one_liner": "2938 分已作废，官方从来不提醒",
    "url": "https://github.com/oracis/mcd-points-liquidator",
    "tags": ["MCP", "麦当劳", "开源"],
    "footer_text": "基于麦当劳中国官方 MCP 的开源小工具，MIT 协议，零第三方依赖。",
}

# 注入：让所有 case 查找入口都返回合成 case
tp.find_case_fuzzy = lambda q: CASE
tp.load_cases = lambda: [CASE]
if hasattr(tp, "pending_cases"):
    tp.pending_cases = lambda **kw: [CASE]

# 标题：兜底模板会拼成「产品名：一句话」，不够抓人，这里直接钉死。
# build / publish 内部都以模块全局方式调用它，patch 对两条路径都生效。
tp.make_toutiao_title = lambda c, hits_out=None: TITLE

import types  # noqa: E402


def ns(**kw):
    return types.SimpleNamespace(**kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("build", "covers", "draft", "publish"))
    a = ap.parse_args()

    if a.step == "build":
        tp.cmd_build(ns(case=CID))
        mj = os.path.join(ROOT, "out", "toutiao", CID, "meta.json")
        print("\n--- meta.json ---")
        if os.path.isfile(mj):
            print(open(mj, encoding="utf-8").read()[:900])

    elif a.step == "covers":
        tp.cmd_covers(ns(case=CID, all=False, port=tp.CDP_PORT))

    elif a.step == "draft":
        # 无 --send / 无 --yes ⇒ 只存草稿箱，不公开发布
        tp.cmd_publish(ns(case=CID, near=1, all=False, skip=None, dry=False,
                          yes=False, force=True, no_cover=False,
                          api=True, send=False))

    elif a.step == "publish":
        # ⚠ 这一步是真的发出去，不可逆。头条侧走 API 一步发表（不经草稿箱）。
        tp.cmd_publish(ns(case=CID, near=1, all=False, skip=None, dry=False,
                          yes=True, force=True, no_cover=False,
                          api=True, send=True))


if __name__ == "__main__":
    main()
