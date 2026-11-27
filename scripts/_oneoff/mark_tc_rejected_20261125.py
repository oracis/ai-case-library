#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云三条挂 auditing 的行 → 按登录态真值改判（2026-11-25）。

证据来源（只读，不点任何按钮）：
    python scripts/tc_manage.py status --pages 3
    → 2792910 easyMix    你的文章已驳回,原因: 内容包含广告/引流信息
    → 2791861 OpenRCT2   你的文章已驳回,原因: 灌水内容/技术无关内容
    → 2791816 Mindustry  你的文章已驳回,原因: 灌水内容/技术无关内容
    顶部统计：已发布 34 ｜ 未通过 3 ｜ 审核中 0

判两条：
- easymix / ext-ep-015-openrct2：驳回 → 走 `yun_state.record_publish(verdict="rejected")`
  （#109 口径：落 draft、清 remote_id/url/published_at、reason 进 note；不自创 rejected 态）
- pdfai：平台侧**没有这篇**，且台账 remote_id 2791816 实为 Mindustry 的文章（串号）
  ⇒ 同样清 id 落 draft，note 记串号来源，避免明天 publish_guard 当「已发过」挡掉

红线：单行 UPDATE（走 record_publish），不碰 Manifest，不动其他行。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db                        # noqa: E402
import yun_state as Y            # noqa: E402

TARGETS = [
    dict(case="easymix", draft_id="351973", article_id="2792910",
         title="把混音和母带压成一次上传：easyMix 的音频流水线与本地化定价工程",
         reason="内容包含广告/引流信息", route="rejected"),
    dict(case="ext-ep-015-openrct2", draft_id="351351", article_id="2791861",
         title="老游戏怎么被重写：读 OpenRCT2",
         reason="灌水内容/技术无关内容", route="rejected"),
    dict(case="pdfai", draft_id="338081", article_id="2791816",
         title="把 PDF 变成可问答的知识库：切分、溯源与一次退款操作的风险",
         reason="台账 remote_id 2791816 实为 Mindustry 文章（串号），平台侧无本 case 稿件",
         route="orphan"),
]


def current(case):
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("select case_id,state,remote_id,draft_id,url,published_at,title "
                "from platform_posts where platform='tencentcloud' and case_id=?",
                (case,))
    r = cur.fetchone()
    return dict(r) if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真写库（缺省只打印计划）")
    args = ap.parse_args()

    for t in TARGETS:
        old = current(t["case"])
        print("=" * 70)
        print("%-22s 现在：%s" % (t["case"], old))
        if not old:
            print("  ✗ 台账无此行，跳过")
            continue
        if t["route"] == "rejected":
            print("  → draft（驳回：%s）" % t["reason"])
            if args.yes:
                Y.record_publish("tencentcloud", t["case"], t["draft_id"],
                                 t["article_id"], t["title"],
                                 verdict="rejected", reason=t["reason"])
                # 🚨 record_publish 会保留 remote_id 供追溯，但这里必须清掉：
                #   `publish_slot._rows_yun` 判 `state=='published' or bool(remote_id)`
                #   ⇒ remote_id 非空会被当成「已发」；`publish_guard.check_dupe` 同理。
                #   articleId 已经在 note 里（record_publish 写的「文章 279xxxx」），不丢。
                conn = db.get_conn()
                conn.execute(
                    "update platform_posts set remote_id='', "
                    "note=coalesce(note,'') || ?, "
                    "updated_at=datetime('now','localtime') "
                    "where platform='tencentcloud' and case_id=? "
                    "and state='draft' and remote_id=?",
                    ("\n[2026-11-25 对账] 驳回后清 remote_id："
                     "article %s 留在 note 与本脚本，重发不受 check_dupe 拦截"
                     % t["article_id"], t["case"], t["article_id"]))
                conn.commit()
        else:
            print("  → draft（清串号 id：%s）" % t["reason"])
            if args.yes:
                conn = db.get_conn()
                conn.execute(
                    "update platform_posts set state='draft', remote_id='', "
                    "url='', published_at=NULL, "
                    "note=coalesce(note,'') || ?, "
                    "updated_at=datetime('now','localtime') "
                    "where platform='tencentcloud' and case_id=?",
                    ("\n[2026-11-25 对账] " + t["reason"]
                     + "；已清 remote_id/draft_id，可重发", t["case"]))
                conn.commit()
        if args.yes:
            print("  写入后：%s" % current(t["case"]))

    if not args.yes:
        print("\n（预演，未写盘。真写：--yes）")


if __name__ == "__main__":
    main()
