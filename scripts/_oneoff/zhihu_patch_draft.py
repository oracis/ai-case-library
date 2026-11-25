# -*- coding: utf-8 -*-
"""在原知乎草稿上改稿（不新建草稿）。

背景：2026-11-08 用户裁定「草稿箱有旧稿不合规则 ⇒ 在旧稿上改稿再发，不许另起新稿」。
`smt zhihu_publish.py draft` 只会 POST 新建；改已有草稿走
`PATCH zhuanlan.zhihu.com/api/articles/{id}/draft`（与 create_draft 第二步同端点）。

用法：
  python scripts/_oneoff/zhihu_patch_draft.py --id 2102032057182446679 \
      --file D:/open-source-games/content/ep-003-freedoom/platform/zhihu.md
  加 --apply 才真写（默认干跑，只打印将要写入的标题/字数）。

验证：改完必须回读 `zhihu_creator.py list`，比对 content_words 与标题。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import zhihu_publish as Z  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="已有草稿 article id")
    ap.add_argument("--file", required=True, help="新正文 markdown")
    ap.add_argument("--apply", action="store_true", help="真写（默认干跑）")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as f:
        md = f.read()
    md = "\n".join(ln for ln in md.split("\n") if not ln.startswith("> 平台："))
    title, body_md = Z.split_md(md)
    body_zh = len(re.findall(r"[\u4e00-\u9fff]", body_md))
    html = Z.md_to_html(body_md)

    print("目标草稿 : %s" % args.id)
    print("新标题   : %s（%d 字）" % (title, len(title)))
    print("正文     : 中文 %d 字 / HTML %d 字节" % (body_zh, len(html)))
    if not args.apply:
        print("\n（干跑）加 --apply 才写入平台草稿。")
        return 0

    payload = {"content": html, "table_of_contents": False,
               "delta_time": 0, "can_reward": False}
    try:
        d = Z._call_zhuanlan("PATCH", "/api/articles/%s/draft" % args.id, payload)
        print("PATCH 正文 ok: %s" % json.dumps(d, ensure_ascii=False)[:200])
    except Exception as e:                                    # noqa: BLE001
        print("PATCH 正文失败: %s: %s" % (type(e).__name__, str(e)[:300]))
        return 1

    # 标题单独再试一次（有的版本 /draft 不吃 title 字段）
    try:
        d2 = Z._call_zhuanlan("PATCH", "/api/articles/%s/draft" % args.id,
                              {"title": title, "delta_time": 0,
                               "can_reward": False})
        print("PATCH 标题 ok: %s" % json.dumps(d2, ensure_ascii=False)[:160])
    except Exception as e:                                    # noqa: BLE001
        print("PATCH 标题失败（正文已写入，标题可能仍为旧值）: %s" % str(e)[:200])

    print("\n下一步必须回读验证：")
    print("  python scripts/zhihu_creator.py list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
