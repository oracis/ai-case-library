#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对账遗留处置（2026-11-20 用户逐条裁定）。

一次跑完三件事，每件都留证据、都可回读：

1. **博客园删重复稿（删早留新）** —— 用户按 B站先例裁定：
   `23493644`(shipfast 10-30 11:59) / `23533509`(2048 11-03 12:16) /
   `23498379`(游戏厅第1期 10-30 17:52) 三篇都是「首波」，
   在册的是后来重发的 `23559261` / `23558119` / `23546485`。
   ⚠ 博客园删除走 `blogger.deletePost`，**没有回收站、不可恢复**。

2. **补登 clawlink 的博客园漏登** —— 线上两篇《用免配置多应用连接架构撑住
   226个活跃订阅》(`23533748` 11-03 / `23547778` 11-04)，台账一条没记；
   标题与 clawlink 各平台在用的营销体标题完全不同，靠正文认出
   （案例档案 `subscriptions: 226`）。补登**较新一篇** `23547778`，
   较旧一篇写进 note 留档待删。

3. **清串号草稿 id** —— 同一平台里两条 case 记着同一个 `draft_id`，
   后建的那条是抄错的（draft id 号段对不上建稿日期）：
     · aliyun `1699450`：`3ak-track-field`(10-30 批次) 对，
       `ext-ep-005-supertux`(11-16 建) 错；
     · tencentcloud `338084`：`sleek-design`(10-29 批次) 对，`ext-ep-012-openra`(11-16) 错；
     · tencentcloud `338081`：`pdfai`(10-30 批次) 对，`ext-ep-008-mindustry`(11-11) 错。
   只清错的那一侧（留痕），草稿 id 本身不影响发布，只是体检噪音。

CSDN `trustmrr` 169654106 不动：已发列表没有、回收站空、草稿箱空，
但公开页 521 是 WAF 不是 404 ⇒ 没有「它被删了」的证据，只留报告。

用法：
    python scripts/_oneoff/fix_pubrc_leftovers.py            # 只读预览
    python scripts/_oneoff/fix_pubrc_leftovers.py --apply
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

import atomicio          # noqa: E402
import cnblogs_publish as B   # noqa: E402
import db                # noqa: E402
import pub_reconcile as P     # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")

DEL = [("23493644", "shipfast", "23559261",
        "shipfast 首波（10-30 11:59）"),
       ("23533509", "ext-ep-002-2048", "23558119",
        "2048 首波（11-03 12:16）"),
       ("23498379", "ext-ep-001-super-mario-bros", "23546485",
        "游戏厅第1期（10-30 17:52，与 B站 10-30 那篇同批）")]

CLAWLINK = {"case": "clawlink", "keep": "23547778", "dup": "23533748",
            "title": "用免配置多应用连接架构撑住226个活跃订阅"}

DUP_DRAFT = [("aliyun", "ext-ep-005-supertux", "1699450", "3ak-track-field"),
             ("tencentcloud", "ext-ep-012-openra", "338084", "sleek-design"),
             ("tencentcloud", "ext-ep-008-mindustry", "338081", "pdfai")]

TIP = "｜11-20 遗留处置："
TODAY = "11-20"


def _title_of(remote, pid):
    v = remote.get(pid) or {}
    return v.get("title") or "", v.get("at") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    cfg = B.load_config()
    items = B.recent_posts(cfg, count=200)
    remote = {str(it["post_id"]): {"title": it.get("title") or "",
                                   "at": P._cnblogs_ts(it.get("dateCreated"))}
              for it in items}
    print("博客园线上 %d 篇" % len(remote))
    todo_del = []
    for pid, _case, keep, why in DEL:
        t, at = _title_of(remote, pid)
        if not t:
            print("  跳过 %-9s（已不在线上，上一轮已删）" % pid)
            continue
        todo_del.append((pid, _case, keep, why))
        print("  删 %-9s %-19s %-34s （留 %s）" % (pid, at, t[:34], keep))
    if CLAWLINK["keep"] not in remote:
        raise SystemExit("⛔ clawlink 补登目标 %s 不在线上" % CLAWLINK["keep"])

    if not a.apply:
        print("\n[干跑] 加 --apply 才删稿 / 写台账")
        return 0

    # ---- 1. 删早留新
    for pid, _case, _keep, _why in todo_del:
        B.delete_post(cfg, pid)
        print("  ✓ 已删 %s" % pid)
    after = {str(it["post_id"]) for it in B.recent_posts(cfg, count=200)}
    if todo_del:
        print("  回读：%d → %d 篇" % (len(remote), len(after)))
        left = [p for p, _c, _k, _w in todo_del if p in after]
        if left:
            raise SystemExit("⛔ 回读仍在线上：%s" % left)
    for _pid, _c, keep, _w in DEL:
        if keep not in after:
            raise SystemExit("⛔ 在册那篇 %s 不见了" % keep)

    # ---- 2. 补登 clawlink + 3. 清串号草稿 id，一次写库
    conn = db.get_conn()
    man = {}
    try:
        at = (remote.get(CLAWLINK["keep"]) or {}).get("at") or ""
        dup_at = (remote.get(CLAWLINK["dup"]) or {}).get("at") or ""
        note = (TIP + "博客园线上有两篇同题稿（%s / %s），台账此前一条未记；"
                "补登较新一篇 %s，较旧一篇 %s 为重复稿待删"
                % (CLAWLINK["dup"], CLAWLINK["keep"], CLAWLINK["keep"],
                   CLAWLINK["dup"]) if dup_at else TIP + "补登")
        db.upsert_post(conn, CLAWLINK["case"], "cnblogs", {
            "state": "published", "remote_id": CLAWLINK["keep"],
            "url": P._url("cnblogs", CLAWLINK["keep"]),
            "title": CLAWLINK["title"], "published_at": at, "note": note})
        man[(CLAWLINK["case"], "cnblogs")] = {
            "state": "published", "remote_id": CLAWLINK["keep"],
            "url": P._url("cnblogs", CLAWLINK["keep"]), "title": CLAWLINK["title"],
            "at": at}
        print("  ✓ 补登 clawlink ← %s（%s）" % (CLAWLINK["keep"], at))

        for plat, cid, bad_id, right in DUP_DRAFT:
            row = db.get_post(conn, cid, plat) or {}
            if str(row.get("draft_id") or "") != bad_id:
                print("  ⚠ %s/%s 的 draft_id 已是 %r，跳过"
                      % (plat, cid, row.get("draft_id")))
                continue
            note = row.get("note") or ""
            if "清掉串号草稿 id" not in note:
                note += (TIP + "清掉串号草稿 id %s（该 id 实属 %s，号段与建稿日期对不上）"
                         % (bad_id, right))
            # 🚨 这里不能用 `upsert_post`：它有「空字段回填」——传 draft_id="" 会被
            # 旧值救回来（2026-11-20 首次跑就是这么失败的，和 clawlink 那次
            # `deleted` 盖不掉 `remote_id` 是同一个坑）。清空只能走一条 SQL。
            conn.execute(
                "UPDATE platform_posts SET draft_id='', note=?, updated_at=? "
                "WHERE case_id=? AND platform=?",
                (note, db._now(), cid, plat))
            conn.commit()
            man[(cid, plat)] = {"draft_id": ""}
            print("  ✓ 清 %s/%s 的 draft_id %s（应是 %s 的）" % (plat, cid, bad_id, right))
    finally:
        db.close_conn()

    n = P.patch_manifest(man)
    print("  manifest 单条 patch %d 处" % n)

    # ---- 回读核验
    bad = []
    conn = db.get_conn()
    try:
        cl = db.get_post(conn, CLAWLINK["case"], "cnblogs") or {}
        if str(cl.get("remote_id")) != CLAWLINK["keep"] or cl.get("state") != "published":
            bad.append("clawlink cnblogs")
        for plat, cid, bad_id, _right in DUP_DRAFT:
            if str((db.get_post(conn, cid, plat) or {}).get("draft_id") or "") == bad_id:
                bad.append("%s/%s" % (plat, cid))
            other = {"aliyun": ("3ak-track-field", "1699450"),
                     "tencentcloud": None}
        # 对侧仍在
        for plat, _cid, _bad, right in DUP_DRAFT:
            r = db.get_post(conn, right, plat) or {}
            if str(r.get("draft_id") or "") not in ("", _bad):
                bad.append("对侧 %s/%s" % (plat, right))
    finally:
        db.close_conn()
    print("\n回读核验 %s" % ("全部对上" if not bad else "失败：%s" % bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
