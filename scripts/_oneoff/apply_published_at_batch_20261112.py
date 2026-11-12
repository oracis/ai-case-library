# -*- coding: utf-8 -*-
"""把 tmp/_published_truth_report_20261112.json 里 82 条有远端真值证据的
published_at 定点写入 data/publish_manifest.json。

铁律：
  - 只给 state=published 且 will_write=True 的条目写；不碰 db 里的任何值
  - 文本级按字母序插入（文件是 LF + indent=1 + sort_keys=True），不整本 json.dump
  - 写前备份、写后逐条断言其余记录与格式完全不变
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

MANIFEST = "data/publish_manifest.json"
REPORT = "tmp/_published_truth_report_20261112.json"
TS_FILE = "tmp/_published_at_batch_ts.txt"
TS = open(TS_FILE, encoding="utf-8").read().strip()
BACKUP = "tmp/backup_publish_manifest.json." + TS

report = json.load(open(REPORT, encoding="utf-8"))
targets = {}
for r in report["rows"]:
    if r["will_write"]:
        assert r["source"], (r["case_id"], r["platform"], "缺来源")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", r["chosen"]), \
            (r["case_id"], r["chosen"])
        targets[(r["case_id"], r["platform"])] = r["chosen"]
print("待写入条目:", len(targets))

orig_text = open(BACKUP, encoding="utf-8", newline="").read()
disk_text = open(MANIFEST, encoding="utf-8", newline="").read()
assert orig_text == disk_text, "备份后 manifest 被改动，停手"
orig = json.loads(orig_text)

# 0) 原文件必须已是规范格式（LF + indent=1 + sort_keys），否则整本 dump 会有噪音
canon_orig = json.dumps(orig, ensure_ascii=False, indent=1, sort_keys=True)
assert orig_text.rstrip("\n") == canon_orig.rstrip("\n"), "原文件非 sort_keys/indent=1 规范格式"

# 1) 非 published 条目一律不许出现在目标里
for cid, per in orig.items():
    for p, r in (per or {}).items():
        if isinstance(r, dict) and (cid, p) in targets:
            assert r.get("state") == "published", ("非 published 条目", cid, p)
            assert "published_at" not in r, ("已有字段却重复写", cid, p)

# 2) 按行扫描，在目标平台块内第一个字母序 > published_at 的字段前插入
def decode_key(raw):
    return json.loads('"' + raw + '"')


out_lines = []
cur_case = None
cur_plat = None
inserted = set()
for line in orig_text.splitlines(keepends=True):
    body = line.rstrip("\n")
    m_case = re.match(r'^ "(.+)": \{', body)
    if m_case:
        cur_case = decode_key(m_case.group(1))
        cur_plat = None
    else:
        m_plat = re.match(r'^  "(.+)": \{', body)
        if m_plat:
            cur_plat = decode_key(m_plat.group(1))

    key = (cur_case, cur_plat)
    m_field = re.match(r'^   "([A-Za-z_][A-Za-z0-9_]*)":', body)
    if (key in targets and key not in inserted and m_field
            and m_field.group(1) > "published_at"):
        val = json.dumps(targets[key], ensure_ascii=False)
        out_lines.append("   \"published_at\": %s,\n" % val)
        inserted.add(key)
    out_lines.append(line)

missing = set(targets) - inserted
assert not missing, ("有块内找不到插入位置: %s" % sorted(missing)[:5])
assert len(inserted) == len(targets)
new_text = "".join(out_lines)

# 3) 写回前：新文本必须仍等于「原数据 + 82 个字段」的规范 dump
new = json.loads(new_text)
canon_new = json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True)
assert new_text.rstrip("\n") == canon_new.rstrip("\n"), "插入后格式不符合 sort_keys/indent=1"
assert orig_text.count("\n") + len(targets) == new_text.count("\n"), "行数增量不符"

# 4) 逐条断言：除新增的 published_at 外所有记录完全不变
assert set(new) == set(orig), "顶层 case 集合变了"
n_changed = 0
for cid, per in orig.items():
    assert set(new[cid]) == set(per), ("平台集合变了", cid)
    for p, r in per.items():
        nr = new[cid][p]
        if not isinstance(r, dict):
            assert nr == r
            continue
        if "published_at" in r:
            # 上一轮已补的 2 条：整条必须原样不动
            assert nr == r, ("已有字段条目被改动", cid, p)
            continue
        stripped = {k: v for k, v in nr.items() if k != "published_at"}
        assert stripped == r, ("记录漂移", cid, p)
        if (cid, p) in targets:
            assert nr.get("published_at") == targets[(cid, p)], ("写入值不符", cid, p)
            n_changed += 1
        else:
            assert "published_at" not in nr, ("多写了字段", cid, p)
assert n_changed == len(targets), (n_changed, len(targets))

# 5) 落盘（保持 LF）
with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
    f.write(new_text)

check = open(MANIFEST, encoding="utf-8", newline="").read()
assert check == new_text
print("已写入 %d 条；其余 %d 条记录逐条不变" %
      (n_changed, sum(len(v) for v in orig.values()) - n_changed))
print("格式校验：LF + indent=1 + sort_keys 一致；文件 %d -> %d 字节" %
      (len(orig_text), len(new_text)))

# 6) 分层统计
import collections
src_stat = collections.Counter()
for r in report["rows"]:
    if r["will_write"]:
        src_stat[r["source"]] += 1
print()
print("=== 证据来源分布 ===")
for k, v in src_stat.most_common():
    print("  %-46s %d" % (k, v))
print()
print("=== 精度 ===")
sec = sum(1 for v in targets.values() if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", v))
print("  秒级", sec, "分钟级", len(targets) - sec)
