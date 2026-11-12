# -*- coding: utf-8 -*-
"""勘察第 2 项的方向：每条冲突案例判定「metrics 新」还是「一手 .md 新」。

只读。判据优先级：
  1) metric_note 里若有「实测口径：MRR $A（... D1 同步」且声明「队列缓存值……当时 $B」，
     则 A 为实测权威；正文若等于 B ⇒ 正文是作废缓存值（方向=改正文）。
  2) 否则比较一手 .md 的 Revenue data last synced 与 metrics 的同步日（从 note 提取，
     提取不到时用 verified_at 近似）。.md 更晚 ⇒ 正文若等于 md 值则正确，metrics 过期（方向=改台账）。
  3) 两边都比不出 ⇒ UNDECIDED。
"""
import sys
import os
import re
import json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

MD = os.path.join(ROOT, "tmp", "tm_md")
FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]
REL_TOL = 0.01  # 相对差 1% 以内视为取整，不算冲突

RE_MD = {
    "mrr": re.compile(r"Current MRR:\s*\$?([\d,]+)"),
    "l30": re.compile(r"Last 30 days revenue snapshot:\s*\$?([\d,]+)"),
    "all": re.compile(r"All-time revenue snapshot:\s*\$?([\d,]+)"),
    "sync": re.compile(r"Revenue data last synced:\s*([\d\-]+)"),
}
RE_NOTE_REAL = re.compile(r"实测口径[：:]\s*(?:MRR|近\s?30\s?天收入)\s*\$?([\d,]+)")
RE_NOTE_SYNC = re.compile(r"(\d{4}-\d{2}-\d{2})\s*同步")
RE_NOTE_CACHE = re.compile(r"当时\s*\$?([\d,]+)")
RE_MRR = re.compile(r"MRR[^0-9$]{0,12}\$\s?([\d][\d,]*)")


def walk(case):
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield "%s[%d]" % (f, i), x
        elif isinstance(v, dict):
            for k, y in v.items():
                if isinstance(y, str):
                    yield "%s.%s" % (f, k), y


def md_parse(cid):
    p = os.path.join(MD, cid + ".md")
    if not os.path.exists(p):
        return None
    t = open(p, encoding="utf-8", errors="replace").read()
    d = {}
    for k, rx in RE_MD.items():
        m = rx.search(t)
        if m:
            d[k] = m.group(1) if k == "sync" else float(m.group(1).replace(",", ""))
    return d


def close(a, b):
    if not b:
        return False
    return abs(a - b) / max(abs(b), 1e-9) <= REL_TOL


cases = CS.load_cases()
res = {"改正文": [], "改台账": [], "UNDECIDED": [], "无需改": []}
detail = []

for c in cases:
    cid = c["id"]
    m = c.get("metrics") or {}
    mrr = m.get("mrr")
    if not isinstance(mrr, (int, float)):
        continue
    note = m.get("metric_note") or ""
    texts = list(walk(c))
    # 正文里出现的 MRR 数字（相对差 >1% 才算偏离 metrics）
    offs = []
    for path, t in texts:
        for mt in RE_MRR.finditer(t):
            got = float(mt.group(1).replace(",", ""))
            if not close(got, mrr):
                offs.append((path, got, t[max(0, mt.start() - 50):mt.start() + 60]))
    if not offs:
        continue

    md = md_parse(cid)
    note_real = RE_NOTE_REAL.search(note)
    note_sync = RE_NOTE_SYNC.search(note)
    note_cache = RE_NOTE_CACHE.search(note)

    # 1) note 有实测声明
    if note_real and "以实测为准" in note:
        real = float(note_real.group(1).replace(",", ""))
        cache = float(note_cache.group(1).replace(",", "")) if note_cache else None
        at_cache = any(close(g, cache) for _, g, _ in offs) if cache else False
        if at_cache and close(real, mrr):
            res["改正文"].append(cid)
            detail.append((cid, "改正文", "note 有实测声明，正文=%s=作废缓存值，实测=%s" % (cache, real), len(offs)))
        else:
            res["UNDECIDED"].append(cid)
            detail.append((cid, "UNDECIDED", "note 有实测声明但正文既非缓存值也非实测值(real=%s cache=%s)" % (real, cache), len(offs)))
        continue

    # 2) 用 .md 判定
    if md and "mrr" in md:
        md_mrr = md["mrr"]
        md_newer = True
        if note_sync:
            md_newer = md.get("sync", "9999") >= note_sync.group(1)
        at_md = any(close(g, md_mrr) for _, g, _ in offs)
        if at_md and md_newer:
            res["改台账"].append(cid)
            detail.append((cid, "改台账", "正文=%s 与一手 .md(%s) 一致且 .md 不早于 metrics" % (md_mrr, md.get("sync")), len(offs)))
        elif at_md and not md_newer:
            res["UNDECIDED"].append(cid)
            detail.append((cid, "UNDECIDED", "正文=md(%s) 但 md 早于 metrics(%s)" % (md_mrr, note_sync.group(1) if note_sync else "?"), len(offs)))
        else:
            res["UNDECIDED"].append(cid)
            detail.append((cid, "UNDECIDED", "正文数字既非 metrics=%s 也非 md=%s" % (mrr, md_mrr), len(offs)))
        continue

    res["UNDECIDED"].append(cid)
    detail.append((cid, "UNDECIDED", "无一手 .md 且 note 无实测声明（metrics=%s）" % mrr, len(offs)))

print("=== 方向判定 ===")
for k in ("改正文", "改台账", "UNDECIDED"):
    print("  %-10s %3d 条 / %3d 处" % (k, len(res[k]), sum(d[3] for d in detail if d[1] == k)))
print()
print("--- 改正文（前 12）---")
for d in [x for x in detail if x[1] == "改正文"][:12]:
    print("  %-28s %d 处 | %s" % (d[0], d[3], d[2]))
print()
print("--- 改台账（前 12）---")
for d in [x for x in detail if x[1] == "改台账"][:12]:
    print("  %-28s %d 处 | %s" % (d[0], d[3], d[2]))
print()
print("--- UNDECIDED（前 12）---")
for d in [x for x in detail if x[1] == "UNDECIDED"][:12]:
    print("  %-28s %d 处 | %s" % (d[0], d[3], d[2]))
