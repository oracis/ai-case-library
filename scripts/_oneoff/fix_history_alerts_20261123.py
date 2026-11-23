#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性：修复 2026-11-23 全平台发布后的历史告警。

范围（均已有线上唯一证据，见 docs/PRD.md 下一条）：
  1. 头条 3 条 STALE：线上 45 篇已发列表确无此稿 ⇒ 从已发账移出，db 落 deleted 终态防重发
  2. 小红书 checkvibe DUP + NO_ID、ext-ep-002-2048 NO_ID：按线上唯一命中补 note_id，
     并合并草稿账冗余行（已发优先，草稿侧时间写进 note）
  3. CSDN publbee NO_ID：已发列表唯一命中补 article_id
  4. 腾讯云 ext-ep-013-0-a-d TITLE_DRIFT：公开页回读同步台账 title/url

⛔ 不调 ledger.save() / Manifest.save() 整本落盘；⛔ 不调 db.upsert_post（空字段回填）。
博客园删旧稿是平台写操作，另走 cnblogs_publish.py delete，不在此脚本内。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402
import db  # noqa: E402

NOW = time.strftime("%Y-%m-%d %H:%M:%S")


def edit_json(name, fn, indent):
    p = os.path.join(ROOT, "data", name)
    with atomicio.file_lock(p + ".lock", timeout=30):
        d = atomicio.read_json(p, default=None)
        out = fn(d)
        atomicio.write_json(p, out, indent=indent)
    return out


# ---- 1) 头条：线上查无 ⇒ 从已发账移出（list 布局的 state 靠文件位置表达）
TT = {
    "ext-ep-002-2048": "7702797168431612479",
    "ext-ep-004-quake": "7702799110571475475",
    "ext-ep-001-super-mario-bros": "7702394082697527848",
}


def tt_fn(d):
    return [x for x in (d or []) if x.get("id") not in TT]


left = edit_json("toutiao_published.json", tt_fn, 2)
print("TOUTIAO_JSON_LEFT", len(left), "REMOVED", len(TT))

# ---- 2) 小红书：线上标题唯一命中 → 补 note_id / title
XHS = {
    "checkvibe": ("6b00f44a000000002b000d69", "CheckVibe：给 AI 应用查漏洞"),
    "ext-ep-002-2048": ("6b0286e0000000001803ad39", "2048：一个网页就能玩的数字合并"),
}


def xhs_pub_fn(d):
    for x in (d or []):
        cid = x.get("id")
        if cid not in XHS:
            continue
        nid, title = XHS[cid]
        x["note_id"] = nid
        x["title"] = title
        x["verified_at"] = NOW
        x["evidence"] = "online_readback_20261123"
        note = (x.get("note") or "") + "｜11-23 对账：线上唯一命中补 note_id=%s（%s）" % (nid, title)
        if cid == "checkvibe":
            note += "｜草稿账同条 2026-10-05 21:02:23 已合并（已发优先）"
        x["note"] = note
    return d


edit_json("xhs_published.json", xhs_pub_fn, 2)


def xhs_draft_fn(d):
    return [x for x in (d or [])
            if not (isinstance(x, dict) and x.get("id") == "checkvibe")]


left = edit_json("xhs_drafts.json", xhs_draft_fn, 2)
print("XHS_DRAFT_LEFT", len(left), "DUP_MERGED", 1)


# ---- 3) CSDN publbee：补真实 article_id
def csdn_fn(d):
    r = d["publbee"]
    r["article_id"] = "171183916"
    r["note"] = (r.get("note") or "") + "｜11-23 对账：已发列表唯一命中补 article_id=171183916"
    return d


edit_json("csdn_drafts.json", csdn_fn, 2)
print("CSDN_JSON_OK")

# ---- 4) SQLite 单行 UPDATE（不动 published_at / remote_id 之外的历史字段）
conn = db.get_conn()
try:
    for cid, gid in TT.items():
        cur = conn.execute(
            "update platform_posts set state='deleted', published_at=NULL, updated_at=?, note=? "
            "where platform='toutiao' and case_id=?",
            (NOW, "[11-23 对账] 线上 45 篇已发列表无此稿（原 gid=%s），线上查无 ⇒ deleted 防重发；"
                  "冻结期未碰平台写端点" % gid, cid))
        print("DB_TOUTIAO", cid, cur.rowcount)

    for cid, (nid, title) in XHS.items():
        cur = conn.execute(
            "update platform_posts set state='published', remote_id=?, title=?, updated_at=?, note=? "
            "where platform='xhs' and case_id=?",
            (nid, title, NOW,
             "[11-23 对账] 线上唯一命中补 note_id=%s（%s）" % (nid, title), cid))
        print("DB_XHS", cid, cur.rowcount)

    cur = conn.execute(
        "update platform_posts set remote_id=?, updated_at=?, note=? "
        "where platform='csdn' and case_id='publbee'",
        ("171183916", NOW,
         "[11-23 对账] 已发列表唯一命中补 article_id=171183916"))
    print("DB_CSDN", cur.rowcount)

    cur = conn.execute(
        "update platform_posts set title=?, url=?, updated_at=?, note=? "
        "where platform='tencentcloud' and case_id='ext-ep-013-0-a-d'",
        ("读 0 A.D. 源码：C++ 引擎内核、JS 脚本层与只传指令的回合同步",
         "https://cloud.tencent.com/developer/article/2787249", NOW,
         "[11-23 对账] 11-18 重写稿后线上改标题，按公开页回读同步台账 title/url，state 保持 published"))
    print("DB_TENCENTCLOUD", cur.rowcount)
    conn.commit()
finally:
    db.close_conn()

print("DONE", NOW)
