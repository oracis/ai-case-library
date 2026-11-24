#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 `publish_manifest.json` 里被覆盖的 `note` 与被截断的 `at`（默认干跑）。

为什么单独一个脚本（2026-11-24）
--------------------------------
`Manifest.save()` 会整本重刷（PRD #203 已在 `verification.md` 记过），而
`bilibili_publish._mark_manifest_published()` 在补账时会**累加** note。实测
2026-11-24 二号线补账后出现两类损坏，且 `repair_manifest_at_regression.py`
抓不到（它判的是「at 时间回退」，这两类是时间没变、**后缀文字丢了**）：

  ① `at` 丢尾巴：`"2026-11-22 11:28 草稿箱→发布（send-draft）"` → `"…11:28"`
     （时间部分相同 ⇒ 不是回退，旧脚本判 0 条）
  ② `note` 被累加串盖掉：`"草稿箱→发布（send-draft）"` →
     `"｜更新草稿 aid=N（draft/add 回读校验通过）｜更新草稿 aid=N…"`（同一句重复）

判据（都比「猜」更硬）：
  · at 修复条件：`HEAD 值` 以 `当前值` 为前缀且两者不等 ⇒ 只是被截断；
  · note 修复条件：当前值以 `｜更新草稿` 开头、且「更新草稿」出现 ≥2 次，
    而 HEAD 值不含该串 ⇒ 是被累加串整段替换掉了。

用法：
    python -X utf8 scripts/_oneoff/repair_manifest_note_clobber.py          # 干跑
    python -X utf8 scripts/_oneoff/repair_manifest_note_clobber.py --apply  # 写盘
"""
import argparse
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")


def leaves(d, pre=()):
    """遍历 `{case: {platform: {...}}}`，产出 (路径, 记录)。"""
    for k, v in (d or {}).items():
        if isinstance(v, dict) and ("state" in v or "at" in v):
            yield pre + (k,), v
        elif isinstance(v, dict):
            yield from leaves(v, pre + (k,))


def head_version():
    raw = subprocess.check_output(
        ["git", "show", "HEAD:data/publish_manifest.json"], cwd=ROOT)
    return json.loads(raw.decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认干跑）")
    a = ap.parse_args()

    import atomicio

    cur = json.load(io.open(MANIFEST, encoding="utf-8"))
    head = head_version()
    hmap = {p: v for p, v in leaves(head)}

    fix_at, fix_note = [], []
    for p, v in leaves(cur):
        hv = hmap.get(p)
        if not hv:
            continue                                  # 新条目，HEAD 没有
        ha, ca = str(hv.get("at") or ""), str(v.get("at") or "")
        if ha and ca and ha != ca and ha.startswith(ca):
            fix_at.append((p, ca, ha))
        hn, cn = str(hv.get("note") or ""), str(v.get("note") or "")
        if (hn and cn != hn and cn.startswith("｜更新草稿")
                and cn.count("更新草稿") >= 2 and "更新草稿" not in hn):
            fix_note.append((p, cn, hn))

    print("at 被截断 %d 条：" % len(fix_at))
    for p, c, h in fix_at:
        print("  %s ｜%r → %r" % ("/".join(p), c, h))
    print("note 被覆盖 %d 条：" % len(fix_note))
    for p, c, h in fix_note:
        print("  %s ｜%r → %r" % ("/".join(p), c[:60], h))

    if not fix_at and not fix_note:
        print("\n无需修复")
        return 0

    if not a.apply:
        print("\n[干跑] 加 --apply 才写盘")
        return 0

    bypath = {}
    for p, c, h in fix_at:
        bypath.setdefault(p, {})["at"] = h
    for p, c, h in fix_note:
        bypath.setdefault(p, {})["note"] = h

    # 就地把值写回去（不重建整本，避免又一次「整本重刷」）
    for p, kv in bypath.items():
        node = cur
        for seg in p[:-1]:
            node = node[seg]
        node[p[-1]].update(kv)

    with atomicio.file_lock(MANIFEST + ".lock", timeout=30.0):
        atomicio.write_json(MANIFEST, cur, indent=2)
    print("\n[已写盘] %d 条恢复" % len(bypath))

    # 独立回读：重读磁盘，逐条核
    back = json.load(io.open(MANIFEST, encoding="utf-8"))
    bmap = {p: v for p, v in leaves(back)}
    bad = 0
    for p, c, h in fix_at:
        if bmap[p].get("at") != h:
            bad += 1
            print("  ✗ at 未生效 %s" % "/".join(p))
    for p, c, h in fix_note:
        if bmap[p].get("note") != h:
            bad += 1
            print("  ✗ note 未生效 %s" % "/".join(p))
    print("回读核对：%d 条不一致" % bad if bad else "回读核对：全部一致")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
