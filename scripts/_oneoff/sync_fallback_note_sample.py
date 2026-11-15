#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 99 条 provisional 兜底文案里虚报的样本数 466 → 465（PRD #185）。

背景
----
`DELIVERY` 里曾有库外幽灵条目 `private-venture-1`（166 条晋升时留下，
git 历史里从未进过 cases.json）。`fallback_delivery()` 用 `len(DELIVERY)`
写文案，于是 99 条缺表案例的 `solo_fit.delivery_note` 被写成
「按全库 466 条判断的中位数兜底」—— 真实只有 465 条人工判断。

#185 删掉幽灵后生成器已输出 465，中位数仍是 4（分值零影响），但库里 99 条
是历史快照，仍写 466。这些是 **provisional 派生文案**，不是人工判断：
`resolve_note` 对 provisional 会用当前初值覆盖，所以下次跑分本就会自动刷新。
本脚本把那次刷新提前做掉，让库与生成器口径一致、减少未来 diff 噪音。

边界
----
  * 只改 `solo_fit.delivery_note` 里的「全库 466 条」→「全库 465 条」；
  * **一个数值字段都不许动**（dims / score / raw / rank / weights / blocker）；
  * 走 `case_store` 全量读写（⛔ 不是子集视图，红线 #156），写库 + 重导镜像；
  * 只处理 provisional 且文案确实写着旧样本数的案例，其余一律不碰；
  * 默认 `--check` 只读预演；`--apply` 才写盘（先备份），可重复执行。

⚠ 一次性脚本按红线进仓库，记得 `git add`。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS         # noqa: E402
import score_solo_fit as SF     # noqa: E402

BAK_JSON = os.path.join(ROOT, "tmp", "parity185_pre_cases.json")
BAK_DB = os.path.join(ROOT, "tmp", "parity185_pre_ledger.db")
SAMPLE = re.compile(r"(全库\s*)(\d+)(\s*条)")
NUM_FIELDS = ("dims", "score", "raw", "rank", "weights", "blocker", "max_raw",
              "provisional", "medal", "quadrant")


def flat(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flat(v, prefix + "." + k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flat(v, "%s[%d]" % (prefix, i)))
    else:
        out[prefix] = obj
    return out


def build(cases):
    want = len(SF.DELIVERY)             # 删幽灵后 = 465
    old = copy.deepcopy(cases)
    new = copy.deepcopy(cases)
    changes = []
    for o, n in zip(old, new):
        sf = n.get("solo_fit") or {}
        if not sf.get("provisional"):
            continue                     # 人工判断：文案是库里唯一真值，绝不碰
        note = sf.get("delivery_note") or ""
        m = SAMPLE.search(note)
        if not m or int(m.group(2)) == want:
            continue
        fixed = SAMPLE.sub(lambda x: x.group(1) + str(want) + x.group(3), note, count=1)
        sf["delivery_note"] = fixed
        changes.append((n["id"], note, fixed))
    return old, new, changes, want


def verify(old, new, changes, label):
    if len(old) != len(new) or [c.get("id") for c in old] != [c.get("id") for c in new]:
        raise SystemExit("%s：条数或 id 序列被改" % label)
    diffs = []
    for o, n in zip(old, new):
        a, b = flat(o), flat(n)
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                diffs.append((o["id"], k, a.get(k), b.get(k)))
    allowed = {(cid, ".solo_fit.delivery_note") for cid, _o, _n in changes}
    bad = [d for d in diffs if (d[0], d[1]) not in allowed]
    if bad:
        raise SystemExit("%s：出现非目标改动 %d 处，例：%r" % (label, len(bad), bad[:3]))
    if any(k in NUM_FIELDS for _cid, k, _x, _y in
           [(d[0], d[1].split(".")[-1], d[2], d[3]) for d in diffs]):
        raise SystemExit("%s：数值字段被改，停止" % label)
    if len(diffs) != len(changes):
        raise SystemExit("%s：差异 %d 处 ≠ 预期 %d 处" % (label, len(diffs), len(changes)))
    return len(diffs)


def main():
    ap = argparse.ArgumentParser(description="同步 provisional 兜底文案的样本数")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只读预演）")
    ap.add_argument("--check", action="store_true", help="只读预演（默认行为）")
    args = ap.parse_args()

    cases = CS.load_cases()             # 真值：SQLite case_library
    old, new, changes, want = build(cases)
    n = verify(old, new, changes, "内存预演")
    print("[口径] 真实人工判断 %d 条 | 命中待同步 %d 条 | 逐键差异 %d 处"
          % (want, len(changes), n))
    for cid, before, after in changes[:3]:
        print("  · %s\n     旧：%s\n     新：%s" % (cid, before, after))
    if len(changes) > 3:
        print("  · … 另有 %d 条同型" % (len(changes) - 3))

    if not args.apply:
        print("\n--check：没有写盘。")
        return 0
    if not changes:
        print("[跳过] 无可改字段（幂等）")
        return 0

    os.makedirs(os.path.dirname(BAK_JSON), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "data", "cases.json"), BAK_JSON)
    src = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    dst = sqlite3.connect(BAK_DB)
    src.backup(dst)
    dst.close()
    src.close()

    CS.save_cases(new)                  # 全量：写库 + 重导镜像（原子写）
    on_disk = CS.load_cases()
    verify(old, on_disk, changes, "落盘复验")
    print("\n[已写盘] 备份 tmp/parity185_pre_cases.json / tmp/parity185_pre_ledger.db；"
          "落盘复验 %d 处改动" % len(changes))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
