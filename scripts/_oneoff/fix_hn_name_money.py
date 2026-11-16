#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉 2 条 HN 案例展示名里的金额（2026-11-15 全量测试内容债）。

背景：11-13/14 入库的 HN 案例把帖子标题原样写进 name / name_en，
其中两条标题自带金额（~$20K 训练成本 / $300 刺绣机），于是：

  · test_prerender「全库标题不含金额」FAIL
  · test_toutiao_publish「封面不带金额」FAIL

name 是展示字段（案例页 h1、封面、发布稿标题都读它），金额是帖子里的
成本数字，不是产品名的一部分。这里只删金额片段，**不改 id**、不碰证据链
（hn_leads.json / inbox_archive.json 里的原帖标题原样保留），也不动
metrics / one_liner —— 那两处本来就写着「正文不得出现任何金额」。

用法::

    python scripts/_oneoff/fix_hn_name_money.py --check   # 默认，只读预演
    python scripts/_oneoff/fix_hn_name_money.py --apply   # 备份后写盘 + 自检
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS                      # noqa: E402
import prerender                             # noqa: E402
import toutiao_publish as tp                 # noqa: E402  封面金额判据 COVER_MONEY_PAT

CASES = os.path.join(ROOT, "data", "cases.json")
BACKUP = os.path.join(ROOT, "tmp", "clean_contentdebt_20261115_pre_names.json")

OLD = {
    "a-decent-text-to-image-model-trained-for-20k-apache-2-0-weights":
        "A decent text-to-image model trained for ~$20K (Apache 2.0 Weights)",
    "my-300-embroidery-machine-needed-a-subscription-so-i-built-my-own-app":
        "My $300 embroidery machine needed a subscription, so I built my own app",
}
NEW = {
    "a-decent-text-to-image-model-trained-for-20k-apache-2-0-weights":
        "A decent text-to-image model (Apache 2.0 Weights)",
    "my-300-embroidery-machine-needed-a-subscription-so-i-built-my-own-app":
        "My embroidery machine needed a subscription, so I built my own app",
}


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


def _title_money(c, site_name):
    _, full = prerender.case_title(
        c.get("name") or c["id"], c.get("one_liner") or "", site_name,
        fallbacks=(c.get("what_it_does"), c.get("verdict")))
    hit = prerender.MONEY_PAT.search(full) or prerender.MONEY_WORD_PAT.search(full)
    return full, hit.group(0) if hit else ""


def _cover_money(c):
    html = tp.tt_cover_html(c)
    vis = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    vis = re.sub(r"<[^>]+>", " ", vis)
    m = tp.COVER_MONEY_PAT.search(vis)
    return m.group(0) if m else ""


def _report(cases, label):
    site = prerender.load_site(ROOT).get("site_name", "拆解海外")
    print("%s：标题 / 封面金额判据" % label)
    bad = 0
    for c in cases:
        if c["id"] not in OLD:
            continue
        full, tm = _title_money(c, site)
        cm = _cover_money(c)
        bad += bool(tm or cm)
        print("  · %s" % c["id"][:46])
        print("    name : %s" % c.get("name"))
        print("    标题 : %s" % full)
        print("    标题金额=%s ｜ 封面金额=%s" % (tm or "无", cm or "无"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    cases = CS.load_cases()
    by_id = {c.get("id"): c for c in cases}
    miss = [cid for cid in OLD if cid not in by_id]
    if miss:
        print("✗ 案例不存在：%s" % miss)
        return 2
    for cid, old in OLD.items():
        c = by_id[cid]
        if c.get("name") != old or c.get("name_en") != old:
            print("✗ %s 的 name/name_en 不是预期的旧值（可能已改过或被别人动过）" % cid)
            print("   name=%r  name_en=%r" % (c.get("name"), c.get("name_en")))
            return 2

    before = json.dumps(cases, ensure_ascii=False, sort_keys=True)
    for cid, nm in NEW.items():
        c = by_id[cid]
        c["name"] = nm
        c["name_en"] = nm

    site = prerender.load_site(ROOT).get("site_name", "拆解海外")
    for cid in OLD:
        full, tm = _title_money(by_id[cid], site)
        cm = _cover_money(by_id[cid])
        if tm or cm:
            print("✗ 改写后仍命中金额：%s 标题=%s 封面=%s" % (cid, tm, cm))
            return 2
    bad = _report(cases, "预演")

    if not a.apply:
        print("\n（--check，预演命中 %d 处，没有写盘）" % bad)
        return 0 if not bad else 1
    if bad:
        print("\n✗ 预演仍有金额命中，不写盘")
        return 2

    with io.open(CASES, "rb") as f:
        import atomicio
        atomicio.write_bytes(BACKUP, f.read())
    print("\n已备份写盘前镜像 → %s" % os.path.relpath(BACKUP, ROOT))

    CS.save_cases(cases)          # 写 case_library + 重导 cases.json 镜像
    after = CS.load_cases()
    aft = {c.get("id"): c for c in after}
    old_cases = json.loads(before)
    old_by = {c.get("id"): c for c in old_cases}

    diffs, n_bad = [], 0
    for cid, oc in old_by.items():
        fo, fn = _flat(oc), _flat(aft.get(cid, {}))
        for k in sorted(set(fo) | set(fn)):
            if fo.get(k) != fn.get(k):
                diffs.append((cid, k))
    expect = {(cid, ".name") for cid in OLD} | {(cid, ".name_en") for cid in OLD}
    n_bad = len(after) != len(old_cases) or set(diffs) != expect
    print("\n写盘后自检：案例数 %d→%d ｜ 差异字段 %d 处 %s"
          % (len(old_cases), len(after), len(diffs),
             "（只应是 2 条 × name/name_en）" if not n_bad else "✗ 有越界改动"))
    for cid, k in diffs:
        print("   · %s %s" % (cid[:46], k))
    bad2 = _report([aft[cid] for cid in OLD], "盘上")
    ok = not n_bad and not bad2
    print("\n%s" % ("✅ 通过" if ok else "✗ 未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
