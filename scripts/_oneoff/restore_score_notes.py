#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复跑分时被 SCORES / DELIVERY 表旧文案覆盖的 note（2026-11-15）。

背景：为补齐 18 条新案例的人工判断，跑了 score_china_fit.py 与
score_solo_fit.py。两者在「表里也有判断」时，会把表里的静态 note 写回
case_library，而库里的 note 是 11-14 数据刷新时按新快照改过的 ⇒ 52 处
文案数字被旧值回退（如「37 个订阅 $506」→「35 个订阅 $496」、
「110 个订阅」→「10 个订阅」）。这不属于本轮授权范围，必须还原。

判据（宁可少改也不越界）：只恢复「跑分前已有非 provisional 判断、
note 非空、且被跑分改掉」的字段。18 条新案例本轮新生成的 note 不动，
rank / scored_at / dims / weights 这些真推进也不动。

用法::

    python scripts/_oneoff/restore_score_notes.py --check   # 默认只读
    python scripts/_oneoff/restore_score_notes.py --apply   # 写盘 + 自检
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS                       # noqa: E402

PRE = os.path.join(ROOT, "tmp", "clean_contentdebt_20261115_pre_scores.json")
FIELDS = (("china_fit", "note"), ("solo_fit", "delivery_note"))
NUM = re.compile(r"[\$¥€£]\s?[\d,.]+|[\d,.]+\s*(?:个订阅|%|倍)")


def _norm(d):
    return d["cases"] if isinstance(d, dict) else d


def _flat(o, p=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_flat(v, p + "." + k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_flat(v, p + "[%d]" % i))
    else:
        out[p] = o
    return out


def _plan(pre, now):
    by_pre = {c["id"]: c for c in pre}
    rows = []
    for c in now:
        o = by_pre.get(c["id"])
        if not o:
            continue
        for sect, key in FIELDS:
            old, new = o.get(sect) or {}, c.get(sect) or {}
            if not old.get(key) or old.get("score") is None or old.get("provisional"):
                continue
            if old[key] == new.get(key):
                continue
            rows.append((c["id"], sect, key, new.get(key), old[key]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    pre = _norm(json.load(open(PRE, encoding="utf-8")))
    now = CS.load_cases()
    rows = _plan(pre, now)
    print("待恢复 %d 处（只含跑分前有非兜底人工判断的 note）" % len(rows))
    for cid, sect, key, cur, want in rows[:8]:
        print("  · %s %s.%s" % (cid[:40], sect, key))
        print("    现: %s" % str(cur)[:120])
        print("    复: %s" % str(want)[:120])
    if len(rows) > 8:
        print("  … 其余 %d 处" % (len(rows) - 8))
    drift = [r for r in rows
             if NUM.findall(str(r[3])) != NUM.findall(str(r[4]))]
    print("其中数字序列不同的：%d 处（恢复方向须以跑分前为准）" % len(drift))

    if not a.apply:
        print("\n（--check，没有写盘）")
        return 0
    if not rows:
        print("无需改动（幂等）")
        return 0

    before = json.dumps(now, ensure_ascii=False, sort_keys=True)
    for cid, sect, key, _cur, want in rows:
        for c in now:
            if c.get("id") == cid:
                c[sect][key] = want
                break
    CS.save_cases(now)

    after = CS.load_cases()
    old_by = {c["id"]: c for c in json.loads(before)}
    after_by = {c["id"]: c for c in after}
    expected = {(cid, ".%s.%s" % (sect, key))
                for cid, sect, key, _c, _w in rows}
    diffs = set()
    for cid, oc in old_by.items():
        fo, fn = _flat(oc), _flat(after_by.get(cid, {}))
        for k in set(fo) | set(fn):
            if fo.get(k) != fn.get(k):
                diffs.add((cid, k))
    extra = diffs - expected
    print("\n写盘后自检：案例数 %d→%d ｜ 实际变化 %d 处 ｜ 越界变化 %d 处"
          % (len(old_by), len(after_by), len(diffs), len(extra)))
    for x in list(extra)[:5]:
        print("   ✗", x)
    again = _plan(pre, after)
    print("复跑 _plan 待恢复：%d 处（应为 0）" % len(again))
    ok = not extra and not again and len(diffs) == len(rows)
    print("✅ 通过" if ok else "✗ 未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
