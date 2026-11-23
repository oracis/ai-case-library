"""提交前清理本轮 publish_one.py 的两处写盘副作用（11-23）。

1) publish_manifest.json：`Manifest.save()` 整本重刷把 8 个 bilibili 条目的
   at/note 冲成旧值（PRD #203 红线）。以 HEAD 为准恢复这些字段；
   ext-ep-004-quake 的 bilibili 条目是本轮 B站一号真实发布，HEAD 无此键，保留。
2) wechat_published.json：`wechat_fifo.py` 写盘时把 homy 的发布字段洗掉且全文
   顺序重排（174 行 diff）。以 HEAD 顺序/字段为骨架，只替换本轮真实发表的
   wagiff-app 条目。

用法：python -X utf8 scripts/_oneoff/clean_commit_sideeffects_20261123.py
"""
import os
import sys
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402


def head_json(rel):
    txt = subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT)
    return json.loads(txt.decode("utf-8"))


# ---- 1) publish_manifest.json ----
rel = "data/publish_manifest.json"
p = os.path.join(ROOT, rel)
old = head_json(rel)
cur = atomicio.read_json(p, default={})

restore = {
    "ai-prank-call": ["at"],
    "chessigma": ["at"],
    "ext-ep-003-freedoom": ["at", "note"],
    "insect-bite-id": ["at"],
    "kibu": ["at"],
    "le19emetrou": ["at"],
    "nutria-9e3eaa": ["at"],
    "upvoty": ["at", "note"],
}
for case, fields in restore.items():
    ob = old.get(case, {}).get("bilibili", {})
    nb = cur.get(case, {}).get("bilibili", {})
    for f in fields:
        if f in ob:
            nb[f] = ob[f]
        else:
            nb.pop(f, None)

# 保持 HEAD 键序；当前文件新增的键（quake.bilibili）保留在末尾
ordered = {k: cur.get(k, old[k]) for k in old}
for k in cur:
    if k not in ordered:
        ordered[k] = cur[k]

with atomicio.file_lock(p + ".lock", timeout=30):
    atomicio.write_json(p, ordered, indent=1)
print("MANIFEST_CLEANED")

# ---- 2) wechat_published.json ----
rel = "data/wechat_published.json"
p = os.path.join(ROOT, rel)
old = head_json(rel)
cur = atomicio.read_json(p, default={})
wagiff = cur.get("wagiff-app")
assert wagiff and wagiff.get("status") == "published", "wagiff-app 本轮发表记录缺失"
new = dict(old)
new["wagiff-app"] = wagiff
with atomicio.file_lock(p + ".lock", timeout=30):
    atomicio.write_json(p, new, indent=2)
print("WECHAT_CLEANED")
