import sys
import json
import difflib
import sqlite3

sys.path.insert(0, "scripts")
import ledger as L

PLAT = "bilibili"
recs = L.load(PLAT)
cid, old, aid, atitle = "wagiff-app", "343836", "54107760", "Wagiff：中东北非男性的戒断社区"
r = recs[cid]
r["state"] = L.PUBLISHED
r["remote_id"] = aid
r["note"] = (r.get("note") or "") + "｜11-28 对账：台账原记 draft（草稿 id %s），线上已发表 %s ⇒ 改 published" % (old, aid)
if str(r.get("draft_remote_id") or "") == old:
    r.pop("draft_remote_id", None)
recs[cid] = r

real_write = L._write_file
real_sync = L._db_sync
captured = {}
L._write_file = lambda p, v, nl, ind: captured.setdefault(p, (v, nl, ind))
L._db_sync = lambda platform, rec: captured.setdefault("db:" + rec["id"], rec)
L.save(PLAT, recs)
L._write_file = real_write
L._db_sync = real_sync

import os
for p, val in captured.items():
    if p.startswith("db:"):
        continue
    v, nl, ind = val
    before = open(p, encoding="utf-8").read().splitlines()
    after = json.dumps(v, ensure_ascii=False, indent=ind).replace("\n", nl).splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm="", n=1))
    print("FILE", p, "before_lines", len(before), "after_lines", len(after), "diff_lines", len(diff))
    print("\n".join(diff[:60]))
print("DB_SYNC rows:", len([k for k in captured if k.startswith("db:")]))

conn = sqlite3.connect("data/ledger.db")
conn.row_factory = sqlite3.Row
changed = []
for k, rec in captured.items():
    if not k.startswith("db:"):
        continue
    row = conn.execute("select * from platform_posts where case_id=? and platform=?",
                       (rec["id"], "bilibili")).fetchone()
    if not row:
        changed.append((rec["id"], "NO_ROW"))
        continue
    d = dict(row)
    for kk, vv in {
        "state": rec.get("state") or "draft",
        "remote_id": rec.get("remote_id") or "",
        "draft_id": rec.get("draft_remote_id") or "",
        "published_at": (None if (rec.get("state") or "draft") in ("draft", "deleted")
                         else (rec.get("at") or "")),
        "updated_at": rec.get("at") or "",
        "note": rec.get("note") or "",
    }.items():
        if (d.get(kk) or "") != (vv or ""):
            changed.append((rec["id"], kk, str(d.get(kk))[:60], str(vv)[:60]))
print("DB_FIELD_DIFF count:", len(changed))
print("\n".join(str(x) for x in changed[:40]))
