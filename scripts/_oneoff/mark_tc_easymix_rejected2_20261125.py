#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""easymix 腾讯云三次提交（2793275）被驳回 → 台账改判 draft（2026-11-25）。

证据（登录态只读，`tc_manage.py status`）：
    2793275  从分轨到母带：一次上传出成品的混音链路怎么搭
             └ 已驳回,原因: 内容包含广告/引流信息
             「立即修改 easyMix 是一个面向法语区说唱歌手与独立音乐人的在线混音工具…」
    ⇒ 驳回摘要引用的正是正文第一段的产品自我介绍。

判两条：
1. `yun_state.record_publish(verdict="rejected")` → 落 draft（#109 口径，不自创 rejected 态）
2. 补一条 UPDATE 清 `remote_id`：`publish_slot._rows_yun` 判 `bool(remote_id)` 即「已发」，
   `publish_guard.check_dupe` 也是 `or r["remote_id"]` ⇒ 不清就永远发不出去。
   articleId 2793275 留在 note 里可追溯。

用法::

    python scripts/_oneoff/mark_tc_easymix_rejected2_20261125.py          # 干跑
    python scripts/_oneoff/mark_tc_easymix_rejected2_20261125.py --yes    # 落库
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db                # noqa: E402
import yun_state as Y    # noqa: E402

CASE = "easymix"
ARTICLE = "2793275"
DRAFT = "352180"
TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"
REASON = "内容包含广告/引流信息（驳回摘要引用正文第一段产品自我介绍）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    conn = db.get_conn()
    print("改前:", Y._row(conn, CASE, "tencentcloud"))
    if a.yes:
        Y.record_publish("tencentcloud", CASE, DRAFT, ARTICLE, TITLE,
                         verdict="rejected", reason=REASON)
        cur = conn.cursor()
        cur.execute("update platform_posts set remote_id='' "
                    "where platform='tencentcloud' and case_id=?", (CASE,))
        print("清 remote_id 影响行数:", cur.rowcount)
        conn.commit()
    else:
        print("（干跑，加 --yes 落库）")
        return 0
    back = Y._row(conn, CASE, "tencentcloud")
    print("改后:", back)
    ok = back and back["state"] == "draft" and not back["remote_id"]
    print("✓ 已改判 draft 且清空 remote_id" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
