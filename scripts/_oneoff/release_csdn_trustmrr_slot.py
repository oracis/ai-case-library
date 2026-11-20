#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""放开 CSDN `trustmrr` 的 `deleted` 占位，让这篇能重新走发布链路。

背景：11-20 12:25 按用户裁定把 CSDN `trustmrr` 169654106 落成 `deleted`
（文章已被用户删除）。但 `deleted` 在 `_STATE_RANK` 里是最档、且
`fifo_dedupe.OCCUPIED_STATES` 含它 ⇒ FIFO 永不再排这篇；同时
`publish_guard.check_dupe()` 的判据是 **`state in PUBLISHED_STATES` 或
`remote_id` 非空**（`deleted` + 非空 remote_id 同样拦死）⇒ 连显式
`publish_one --case trustmrr --platform csdn` 也过不了 dedupe 闸。

用户 11-20 12:29 裁定「先放开占位（force_state 或删行）再走发布闸」。
这里选**删行**（比 `force_state` 干净：留着 deleted 行仍会被 FIFO 当占位、
被 check_dupe 按 remote_id 拦）：
  1. 旧行整条导出到 `tmp/release-csdn-trustmrr-<ts>.json`（审计用）；
  2. `DELETE FROM platform_posts WHERE case_id=? AND platform=?`；
  3. `data/publish_manifest.json` 删掉 `trustmrr.csdn` 这一条（镜像不该留幽灵）；
  4. `data/csdn_drafts.json._deleted` 追加「已放开占位」；
  5. 回读：`check_dupe('csdn','trustmrr')` 必须 `ok=True`，manifest 无该条目。

⚠ 删行后「这篇曾经发过 169654106、被删、又放开」这段历史只在
  `csdn_drafts.json._deleted` + `docs/PRD.md #204⑦` + git 历史里，db 里不再有。

用法：
    python -X utf8 scripts/_oneoff/release_csdn_trustmrr_slot.py           # 干跑
    python -X utf8 scripts/_oneoff/release_csdn_trustmrr_slot.py --apply
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402
import db  # noqa: E402
import publish_guard as G  # noqa: E402

CASE = "trustmrr"
PLAT = "csdn"
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
DRAFTS = os.path.join(ROOT, "data", "csdn_drafts.json")
TMP = os.path.join(ROOT, "tmp")


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
        dupe = G.check_dupe(PLAT, CASE)
        print("当前 db：%s" % (json.dumps(row, ensure_ascii=False) if row else "(无行)"))
        print("check_dupe：ok=%s level=%s detail=%s"
              % (dupe["ok"], dupe["level"], dupe["detail"]))

        if not apply:
            print("\n[dry] 将执行：① 旧行导出 tmp；② DELETE 该行；③ 删 manifest 条目；"
                  "④ csdn_drafts 追加「已放开占位」；⑤ 回读 check_dupe")
            return 0

        if row is not None:
            os.makedirs(TMP, exist_ok=True)
            bak = os.path.join(
                TMP, "release-csdn-trustmrr-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
            atomicio.write_json(bak, {"row": row, "why": "放开 deleted 占位，走发布闸"},
                                indent=1, sort_keys=True)
            print("✓ 旧行已导出：%s" % os.path.relpath(bak, ROOT))
            conn.execute("DELETE FROM platform_posts WHERE case_id=? AND platform=?",
                         (CASE, PLAT))
            conn.commit()
            print("✓ db 行已删除")

        def _drop_manifest(d):
            ent = d.get(CASE) or {}
            if PLAT in ent:
                ent.pop(PLAT)
                print("✓ manifest 删掉 %s.%s" % (CASE, PLAT))
            else:
                print("· manifest 本来就没有 %s.%s" % (CASE, PLAT))
            if not ent:
                d.pop(CASE, None)
            else:
                d[CASE] = ent
            return d

        atomicio.update_json(MANIFEST, _drop_manifest, indent=1,
                             sort_keys=True, newline="\n")

        def _note(d):
            key = "%s_169654106" % CASE
            old = (d.get("_deleted") or {}).get(key, "")
            add = ("；2026-11-20 12:3x 用户裁定「放开占位再走发布闸」：删 db 行 + "
                   "manifest 条目（旧 article 169654106 仍为已删，不复活）")
            d.setdefault("_deleted", {})[key] = old + add
            return d

        atomicio.update_json(DRAFTS, _note, indent=2, sort_keys=False, newline="\n")
        print("✓ csdn_drafts.json 已记「放开占位」")

        # ---- 独立回读 ----------------------------------------------------
        conn2 = db.get_conn()
        try:
            gone = _row(conn2) is None
        finally:
            db.close_conn()
        m = json.load(open(MANIFEST, encoding="utf-8"))
        no_entry = PLAT not in ((m.get(CASE) or {}))
        dupe2 = G.check_dupe(PLAT, CASE)
        print("\n回读：db 行已无=%s  manifest 无条目=%s  check_dupe ok=%s (%s)"
              % (gone, no_entry, dupe2["ok"], dupe2["detail"]))
        ok = gone and no_entry and dupe2["ok"]
        print("⇒ %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 4
    finally:
        db.close_conn()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
