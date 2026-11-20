#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CSDN `trustmrr` 169654106 已由用户手动删除 ⇒ 台账落 `deleted`。

背景（PRD #204 ⑥ 遗留）：11-20 首轮五平台对账把这条记在「仍留报告不动」——
线上已发列表 / 回收站 / 草稿箱三处皆空，但公开页返 521 是 WAF 挑战页、
不是 404，所以「没有已删证据」，只能保持 published。

用户 11-20 明确「这个被我删了」⇒ 证据链补齐（已发列表 6 页 24 条、
最旧 11-02 23:32，覆盖 11-03 这篇的发表时点，仍无该 id）。

落盘三处（都是单条 patch，⛔ 不走 `Manifest.save()` 整本重刷，见 PRD #203）：
  1. SQLite `platform_posts`：state→deleted、published_at→NULL、note 追加证据；
     保留 remote_id / url（溯源用，`plan()` 只审 state==published 的行，
     不会把这条再报成 STALE）。
  2. `data/publish_manifest.json` 镜像：同一条 csdn 记录对齐（镜像不是真值）。
  3. `data/csdn_drafts.json` 的 `_deleted` 记录追加今天的确认。

⚠ `deleted` 在 `_STATE_RANK` 里是最高档（5）且 `fifo_dedupe.OCCUPIED_STATES`
  含它 ⇒ 记成 deleted 以后，FIFO 不会再把这篇排到 CSDN。要重发必须显式
  放开占位（`force_state=True` 或删行），别指望自动覆盖。

用法：
    python -X utf8 scripts/_oneoff/fix_csdn_trustmrr_deleted.py            # 干跑
    python -X utf8 scripts/_oneoff/fix_csdn_trustmrr_deleted.py --apply
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402
import db  # noqa: E402

CASE = "trustmrr"
PLAT = "csdn"
RID = "169654106"
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
DRAFTS = os.path.join(ROOT, "data", "csdn_drafts.json")

EVIDENCE = (
    "｜11-20 用户确认「CSDN trustmrr 169654106 这个被我删了」：CSDN 已发列表"
    "（6 页 24 条，最旧 2026-11-02 23:32，覆盖本篇 11-03 发表日）无此 id；"
    "同日首轮对账已实测「已发 / 回收站 / 草稿箱」三处皆空（公开页 521 是 WAF "
    "挑战页，不构成 404 证据，故当时只报告）。现按用户裁定落 deleted："
    "原 published_at=2026-11-03 21:04:52 迁入本 note，remote_id 保留溯源；"
    "⛔ deleted 占位 ⇒ FIFO 不会自动重发，要重发须人工放开。"
)


def _row(conn):
    cur = conn.execute(
        "SELECT * FROM platform_posts WHERE case_id=? AND platform=?",
        (CASE, PLAT))
    r = cur.fetchone()
    return dict(r) if r else None


def main(argv):
    apply = "--apply" in argv
    conn = db.get_conn()
    try:
        row = _row(conn)
        if not row:
            print("✗ 台账里没有 %s/%s" % (CASE, PLAT))
            return 2
        print("当前 db：state=%r remote_id=%r published_at=%r url=%r"
              % (row["state"], row["remote_id"], row["published_at"], row["url"]))
        if str(row["remote_id"]) != RID:
            print("✗ remote_id 不是 %s（%r）⇒ 停手，人工确认"
                  % (RID, row["remote_id"]))
            return 3

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        note = (row.get("note") or "") + EVIDENCE

        if not apply:
            print("\n[dry] 将执行：")
            print("  UPDATE platform_posts SET state='deleted', published_at=NULL,")
            print("         updated_at=%r, note=<旧 note + 证据>" % now)
            print("  manifest[%s][%s] → state=deleted / at=%s / 删 published_at"
                  % (CASE, PLAT, now))
            print("  csdn_drafts.json[_deleted] 追加确认")
            return 0

        conn.execute(
            "UPDATE platform_posts SET state='deleted', published_at=NULL,"
            " updated_at=?, note=? WHERE case_id=? AND platform=?",
            (now, note, CASE, PLAT))
        conn.commit()
        print("✓ db 已更新")

        def _patch_manifest(d):
            rec = ((d.get(CASE) or {}).get(PLAT)) or {}
            rec["state"] = "deleted"
            rec["at"] = now
            rec.pop("published_at", None)
            rec["error"] = note
            d.setdefault(CASE, {})[PLAT] = rec
            return d

        atomicio.update_json(MANIFEST, _patch_manifest, indent=1,
                             sort_keys=True, newline="\n")
        print("✓ manifest 镜像已对齐")

        def _patch_drafts(d):
            key = "%s_%s" % (CASE, RID)
            old = (d.get("_deleted") or {}).get(key, "")
            d.setdefault("_deleted", {})[key] = (
                old + "；2026-11-20 用户再次确认已删（已发列表 24 条 / 11-02~11-20 "
                      "无此 id），台账落 deleted、FIFO 占位不自动重发")
            return d

        atomicio.update_json(DRAFTS, _patch_drafts, indent=2,
                             sort_keys=False, newline="\n")
        print("✓ csdn_drafts.json 已追加确认")

        # ---- 独立回读 ----------------------------------------------------
        conn2 = db.get_conn()
        try:
            back = _row(conn2)
        finally:
            db.close_conn()
        mtxt = open(os.path.join(ROOT, "data", "publish_manifest.json"),
                    encoding="utf-8").read()
        mrec = __import__("json").loads(mtxt)[CASE][PLAT]
        ok = (back and back["state"] == "deleted"
              and back["published_at"] is None
              and EVIDENCE[:20] in (back.get("note") or "")
              and mrec.get("state") == "deleted"
              and "published_at" not in mrec
              and RID in mtxt)
        print("\n回读：db state=%r published_at=%r remote_id=%r"
              % (back["state"], back["published_at"], back["remote_id"]))
        print("      manifest state=%r at=%r published_at键存在=%s"
              % (mrec.get("state"), mrec.get("at"), "published_at" in mrec))
        print("⇒ %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 4
    finally:
        db.close_conn()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
