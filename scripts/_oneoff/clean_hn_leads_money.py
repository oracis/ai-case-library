#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗 `data/hn_leads.json` 里 3 条历史展示名带的金额（PRD #184）。

PRD #183 已在采集 / 事实卡 / 候选池 / 写库四处挡住新金额，但线索池里还留着
11-13 采集的 3 条原始标题（`~$20K` / `$300` / `$7/month`）。这些 `name` 一旦被
晋升或重采就可能绕过展示名闸；本脚本用**同一份判据与清洗函数**把它们洗掉：

  * 只改 `name` / `name_en`，`hn_title` 原样保留原帖标题作证据；
  * 走 `candidate_store` 全量读写（不是子集视图），保持 LF + indent2 + 无尾换行；
  * 写前后逐键比对：除 6 个展示名值外，一个字段都不许动；
  * 洗不干净就抛错打回人工，不让代码补语义。

默认 `--check` 只读预演；`--apply` 才写盘（先备份到 tmp/），可重复执行。
⚠ 一次性脚本按红线进仓库，记得 `git add`。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import candidate_store as CSt   # noqa: E402
import case_name as CN          # noqa: E402

LEADS = os.path.join(ROOT, "data", "hn_leads.json")
BAK = os.path.join(ROOT, "tmp", "unify184_pre_hn_leads.json")


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


def build():
    old = CSt.load_candidates(path=LEADS)
    new = copy.deepcopy(old)
    by_id = {x.get("id"): x for x in new}
    hits = [x for x in old
            if CN.money_hits(x.get("name") or "") or CN.money_hits(x.get("name_en") or "")]
    changes = []
    for src in hits:
        dst = by_id[src["id"]]
        for key in ("name", "name_en"):
            if not src.get(key):
                continue
            cleaned = CN.clean_or_raise(src[key], where="%s.%s" % (src["id"], key))
            if cleaned != src[key]:
                dst[key] = cleaned
                changes.append((src["id"], key, src[key], cleaned))
        if dst.get("hn_title") != src.get("hn_title"):
            raise SystemExit("hn_title 被改了，停止：%s" % src["id"])
    return old, new, hits, changes


def verify(old, new, changes, label):
    if len(old) != len(new) or [x.get("id") for x in old] != [x.get("id") for x in new]:
        raise SystemExit("%s：条数或 id 序列被改" % label)
    a, b = flat(old), flat(new)
    diffs = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    allowed = {"[%d].%s" % (i, key) for i, key in
               [(next(j for j, x in enumerate(old) if x["id"] == cid), key)
                for cid, key, _o, _n in changes]}
    bad = [d for d in diffs if d[0] not in allowed]
    if bad:
        raise SystemExit("%s：出现非展示名改动 %d 处，例：%r" % (label, len(bad), bad[:3]))
    for x in new:
        for key in ("name", "name_en"):
            if CN.money_hits(x.get(key) or ""):
                raise SystemExit("%s：%s.%s 仍带金额" % (label, x.get("id"), key))
    return len(diffs)


def main():
    ap = argparse.ArgumentParser(description="清洗线索池历史展示名金额")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只读预演）")
    ap.add_argument("--check", action="store_true", help="只读预演（默认行为）")
    args = ap.parse_args()

    fmt_ok, fmt_msg = CSt.check(path=LEADS)
    if not fmt_ok:
        raise SystemExit("线索池格式自检没过，先修格式：%s" % fmt_msg)
    print("[格式] %s" % fmt_msg)

    old, new, hits, changes = build()
    n = verify(old, new, changes, "内存预演")
    print("[命中] 带金额线索 %d 条 | 改动字段 %d 处 | 逐键差异 %d 处"
          % (len(hits), len(changes), n))
    for cid, key, before, after in changes:
        print("  · %s.%s\n     旧：%s\n     新：%s" % (cid, key, before, after))
    for x in hits:
        print("  · %s | hn_title 原样保留：%s" % (x["id"], x.get("hn_title")))

    # 只读核对：前两条已晋升进 case_library，清洗结果应与库里展示名一致
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    cs = cases["cases"] if isinstance(cases, dict) else cases
    lib_names = {c.get("name") for c in cs}
    for cid, key, _o, after in changes:
        if key == "name":
            print("  · %s 清洗结果 %s case_library 展示名"
                  % (cid, "已对齐" if after in lib_names else "尚未入库（未晋升，正常）"))

    if not args.apply:
        print("\n--check：没有写盘。")
        return 0
    if not changes:
        print("[跳过] 无可改字段（幂等）")
        return 0

    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    shutil.copyfile(LEADS, BAK)
    CSt.save_candidates(new, path=LEADS)     # 全量列表 + 原子写 + 原生格式
    on_disk = CSt.load_candidates(path=LEADS)
    verify(old, on_disk, changes, "落盘复验")
    fmt_ok, fmt_msg = CSt.check(path=LEADS)
    if not fmt_ok:
        raise SystemExit("写后格式自检失败：%s" % fmt_msg)
    print("\n[已写盘] 备份 tmp/unify184_pre_hn_leads.json；落盘复验与格式自检 %s" % fmt_msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
