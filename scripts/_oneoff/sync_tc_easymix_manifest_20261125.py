#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""manifest 里 easymix.tencentcloud 镜像改成 db 真值（draft / 无 remote_id）。

db 已是真值（2793275 被驳回 → draft、remote_id 清空）。manifest 还指着旧稿
2792910 + auditing + 公开页 url —— 陈旧镜像会让 `_rows_dict` 与档期统计继续误判。

⛔ 不调 `Manifest.save()`（整本落盘会带出全表回退，PRD #203）；只改一个子键，
保留所有既有键（note 追加，不删），indent=1 + 无尾换行。

用法::

    python scripts/_oneoff/sync_tc_easymix_manifest_20261125.py          # 干跑
    python scripts/_oneoff/sync_tc_easymix_manifest_20261125.py --yes    # 落库
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
CASE, PLAT = "easymix", "tencentcloud"
TITLE = "从分轨到母带：一次上传出成品的混音链路怎么搭"
ADD = ("[2026-11-25 13:00 对账] 改稿重发的 2793275 也被驳回（内容包含广告/引流信息，"
       "驳回摘要引用正文第一段产品自我介绍）⇒ state=draft、清 remote_id 以便重发；"
       "旧稿 2792910 同因驳回，两条 articleId 都留档")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    with open(MANIFEST, encoding="utf-8") as f:
        before = json.load(f)
    after = json.loads(json.dumps(before))
    node = after[CASE]
    entry = dict(node[PLAT])
    old_note = entry.get("note") or ""
    entry.update({
        "state": "draft",
        "remote_id": "",
        "url": "",
        "title": TITLE,
        "published_at": "",
        "note": (old_note + " ‖ " + ADD) if ADD not in old_note else old_note,
    })
    node[PLAT] = entry

    changed = [("%s.%s" % (c, p))
               for c in set(before) | set(after)
               for p in set(before.get(c) or {}) | set(after.get(c) or {})
               if (before.get(c) or {}).get(p) != (after.get(c) or {}).get(p)]
    lost = [k for k in before if k not in after]
    print("变更项:", sorted(changed), "| 丢键:", lost)
    if lost or changed != ["%s.%s" % (CASE, PLAT)]:
        raise SystemExit("✗ 变更超出预期，停手")
    if not a.yes:
        print("（干跑）加 --yes 落库")
        return 0
    with atomicio.file_lock(MANIFEST + ".lock"):
        atomicio.write_text(MANIFEST, json.dumps(after, ensure_ascii=False, indent=1))
    with open(MANIFEST, encoding="utf-8") as f:
        back = json.load(f)
    got = back[CASE][PLAT]
    raw = open(MANIFEST, "rb").read()
    print("回读:", json.dumps({k: got[k] for k in ("state", "remote_id", "url")},
                             ensure_ascii=False))
    print("条目总数 before/after:", len(before), len(back), "| 尾换行:", raw.endswith(b"\n"))
    ok = got["state"] == "draft" and not got["remote_id"] and not got["url"]
    print("✓ 已同步" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
