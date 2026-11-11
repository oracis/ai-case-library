#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读验证：真发时 `wechat_fifo` 的形态闸拿到的是**平台草稿正文**。

不发布、不写盘、不改稿。跑的正是 `wechat_fifo.main()` 里那段：
    read_remote → pick_oldest → read_draft_body → FCG.guard(text=...)

判据：
  · `source` 必须是 `platform:appmsg_edit`（不是本地文件路径，也不是
    `platform:draft/view` 那个 B站口径）；
  · 正文长度应与草稿实际长度同量级（≥300 字），不能被标题顶替；
  · 拦/放的结果打出来，人工看是否合理。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LOCALAPPDATA",
                      os.path.join(os.path.expanduser("~"), "AppData", "Local"))
os.environ.setdefault("ProgramFiles", r"C:\Program Files")

import fifo_content_gate as FCG      # noqa: E402
import fifo_dedupe as FD             # noqa: E402
import wechat_fifo as WF             # noqa: E402
import wechat_publish as wp          # noqa: E402


def main():
    drafts, pubs, cdp, tok = WF.read_remote(wp.CDP_PORT)
    print("草稿 %d 条 ｜ 已发表 %d 条" % (len(drafts), len(pubs)))
    (aid, item), rows, st = WF.pick_oldest(
        drafts, pubs, published_cases=FD.published_case_ids("wechat"),
        case_of=lambda _a: WF.local_cid_of(_a)[0])
    if not aid:
        print("✗ 无候选")
        return 2
    title = item.get("title") or ""
    cid = WF.local_cid_of(aid)[0] or ""
    print("候选：appmsgid=%s ｜ case=%r ｜ 标题长度 %d" % (aid, cid, len(title)))

    body = ""
    try:
        body = wp.read_draft_body(cdp, tok, aid, expect_title=title)
    except Exception as e:                               # noqa: BLE001
        print("✗ 读平台正文失败：%s: %s" % (type(e).__name__, str(e)[:120]))
    print("\n平台正文长度: %d 字" % len(body))
    print("前 200 字:\n  %s" % (body[:200].replace("\n", "\n  ") or "(空)"))
    print("后 80 字: %r" % body[-80:])

    v = FCG.guard("wechat", cid, title, text=body or None,
                  text_src="platform:appmsg_edit")
    print("\n--- 闸结果 ---")
    print(json.dumps({k: v[k] for k in ("ok", "checked", "source", "detail")},
                     ensure_ascii=False, indent=2))
    for b in v["blocks"]:
        print("  ✗ block【%s】%s" % (b["rule"], b["detail"][:100]))
    for w in v["warns"]:
        print("  ⚠ warn【%s】%s" % (w["rule"], w["detail"][:80]))

    # 对照：本地源会给什么（证明两者确实不同 —— 否则这次改造没有意义）
    v2 = FCG.guard("wechat", cid, title)
    print("\n--- 对照：不传平台正文（改前的行为）---")
    print(json.dumps({k: v2[k] for k in ("ok", "checked", "source", "detail")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
