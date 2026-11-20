#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补 CSDN `trustmrr` 新稿的 article id（发布回包 article_id=None）。

11-20 12:35 用 `publish_one --platform csdn --case trustmrr --yes` 重发，
`csdn_publish.publish` 的回包没带 `article_id`（打印 `article_id=None`），
于是三处台账都是「有 url 没 id」：

  · `platform_posts`：state=published / remote_id='' ；
  · `publish_manifest.json`：**整条 `trustmrr.csdn` 都没写**；
  · `csdn_drafts.json`：`article_id: null`。

真 id 来自独立回读（两路一致）：

  1. `csdn_publish.py list --query "只读支付密钥怎么核验流水"` →
     `{"id": "171099300", "postTime": "2026-11-20 12:35:15"}`（唯一命中）
  2. 登录态公开页 `verify_page.py --chrome csdn` → 标题 + 3 个正文关键词全中，
     页头「于 2026-11-20 12:35:15 发布 · 公开」

⚠ 不用 `Manifest.save()`（PRD #203 整本重刷事故），直接单条 patch JSON；
  db 也走单条 `UPDATE`（`upsert_post` 有「空字段回填」，写 remote_id 没问题
  但会把 published_at 按 now 重排，不如一条 SQL 干净）。

用法：
    python -X utf8 scripts/_oneoff/backfill_csdn_trustmrr_20261120.py [--apply]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402
import db  # noqa: E402

CASE = "trustmrr"
PLAT = "csdn"
AID = "171099300"
TITLE = "只读支付密钥怎么核验流水：拆 TrustMRR 的验证链路与榜单口径"
URL = "https://blog.csdn.net/sinat_32396037/article/details/171099300"
AT = "2026-11-20 12:35:15"
NOTE = ("｜11-20 12:35 重发（旧稿 169654106 已删、占位放开）：publish 回包 "
        "article_id=None ⇒ 补 id 走双回读：已发列表唯一命中 "
        "`%s`（postTime %s）+ 登录态公开页标题与 3 个正文关键词全中、页头「公开」。"
        % (AID, AT))

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
DRAFTS = os.path.join(ROOT, "data", "csdn_drafts.json")


def _row(conn):
    r = conn.execute(
        "SELECT * FROM platform_posts WHERE case_id=? AND platform=?",
        (CASE, PLAT)).fetchone()
    return dict(r) if r else None


def main(argv):
    apply = "--apply" in argv
    conn = db.get_conn()
    try:
        row = _row(conn)
        print("当前 db：state=%r remote_id=%r url=%r"
              % (row["state"], row["remote_id"], row["url"]) if row else "当前 db：无行")
        if apply:
            if row is None:
                conn.execute(
                    "INSERT INTO platform_posts (case_id, platform, state, remote_id,"
                    " url, title, draft_id, attempt, published_at, updated_at, note)"
                    " VALUES (?,?,?,?,?,?,'',0,?,?,?)",
                    (CASE, PLAT, "published", AID, URL, TITLE, AT, AT, NOTE))
            else:
                conn.execute(
                    "UPDATE platform_posts SET remote_id=?, url=?, title=?,"
                    " note=? WHERE case_id=? AND platform=?",
                    (AID, URL, TITLE, (row.get("note") or "") + NOTE, CASE, PLAT))
            conn.commit()
            print("✓ db remote_id=%s" % AID)

            def _mf(d):
                rec = ((d.get(CASE) or {}).get(PLAT)) or {}
                rec.update({"state": "published", "at": AT, "title": TITLE,
                            "remote_id": AID, "url": URL, "attempt": 0,
                            "error": (rec.get("error") or "") + NOTE})
                d.setdefault(CASE, {})[PLAT] = rec
                return d

            atomicio.update_json(MANIFEST, _mf, indent=1, sort_keys=True, newline="\n")
            print("✓ manifest 补条目")

            def _df(d):
                rec = d.get(CASE) or {}
                rec.update({"article_id": AID, "title": TITLE, "url": URL, "ts": AT})
                d[CASE] = rec
                return d

            atomicio.update_json(DRAFTS, _df, indent=2, sort_keys=False, newline="\n")
            print("✓ csdn_drafts.article_id 补齐")

        # ---- 独立回读 ----------------------------------------------------
        conn2 = db.get_conn()
        try:
            back = _row(conn2)
        finally:
            db.close_conn()
        m = json.load(open(MANIFEST, encoding="utf-8"))
        d = json.load(open(DRAFTS, encoding="utf-8"))
        mrec = ((m.get(CASE) or {}).get(PLAT)) or {}
        ok = (back and back["remote_id"] == AID and back["state"] == "published"
              and mrec.get("remote_id") == AID
              and (d.get(CASE) or {}).get("article_id") == AID)
        print("\n回读：db remote_id=%r manifest=%r drafts.article_id=%r"
              % (back["remote_id"] if back else None, mrec.get("remote_id"),
                 (d.get(CASE) or {}).get("article_id")))
        print("⇒ %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 4
    finally:
        db.close_conn()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
