# -*- coding: utf-8 -*-
"""只读探针：为 publish_manifest.json 的 published 条目汇总「远端真值时点」候选。

分层（严格不碰 data/ledger.db 里的值）：
  L1 本次远端实时回读：B站两账号 /x/space/article publish_time、知乎 /articles created
  L2 已入库的明确远端时点字段：published_at / published_ts（不含 at / ts / verified_at）
  L3 无证据：不写，让 importer 退回 at

输出 tmp/_published_truth_report_20261112.json，不修改任何 data/ 文件。
"""
import json
import os
import re
import sys
import time
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import publish_guard as PG  # noqa: E402  (norm_id)


def norm(cid):
    return PG.norm_id(cid)


def ts_to_str(v):
    """unix 秒 -> 本地时间字符串；已是字符串则原样返回。"""
    if v is None or v == "":
        return ""
    s = str(v)
    if s.isdigit() and len(s) >= 10:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(s)))
    return s


def load(name):
    with open(os.path.join("data", name), encoding="utf-8") as f:
        return json.load(f)


def load_tmp(name):
    with open(os.path.join("tmp", name), encoding="utf-8") as f:
        return json.load(f)


manifest = load("publish_manifest.json")
pub = [(cid, p, r) for cid, per in manifest.items()
       for p, r in (per or {}).items()
       if isinstance(r, dict) and r.get("state") == "published"]
print("manifest published 条目:", len(pub))

# ---------------- L1 本次远端实时回读 ----------------
l1 = {}          # (case_id, platform) -> (value, source)
by_remote = {}   # (platform, remote_id) -> (value, source)

bili_remote = load_tmp("_remote_bili_articles_20261112.json")
for plat, arts in (("bilibili", bili_remote["bilibili"]),
                   ("bilibili2", bili_remote["bilibili2"])):
    for a in arts:
        v = ts_to_str(a.get("publish_time"))
        if v:
            by_remote[(plat, str(a.get("id")))] = (v, "L1-%s空间公开列表" % plat, a.get("title") or "")

zhihu_remote = load_tmp("_remote_zhihu_articles_20261112.json")
for a in zhihu_remote:
    v = ts_to_str(a.get("created"))
    if v:
        by_remote[("zhihu", str(a.get("id")))] = (v, "L1-知乎已发表列表created", a.get("title") or "")

def _title_norm(s):
    return re.sub(r"[\s：:，,、·（）()《》「」“”\"']", "", s or "")


for cid, p, r in pub:
    key = (p, str(r.get("remote_id") or ""))
    if key in by_remote:
        v, src, remote_title = by_remote[key]
        local_title = r.get("title") or ""
        a, b = _title_norm(local_title), _title_norm(remote_title)
        # aid 唯一即同一篇；标题后来被改写只作标注，不阻止写入
        mismatch = bool(b and a != b and b[:8] not in a and a[:8] not in b)
        l1[(cid, p)] = (v, src, mismatch)

print("L1 按 remote_id 命中:", len(l1))
print("  按平台:", dict(collections.Counter(p for _, p in l1)))

# ---------------- L2 已入库的明确远端时点字段 ----------------
# 只收明确叫 published_at / published_ts 的字段；at / ts / verified_at
# 是记账或建稿时点，且按 case 跨文件匹配会把别的平台时点混进来，一律不收。
l2 = {}   # (case_id, platform) -> [(value, source)]
l2_by_norm = {}  # (norm(cid), platform) -> [(value, source)]


def add_l2(cid, p, v, src):
    if not v:
        return
    l2.setdefault((cid, p), []).append((v, src))
    l2_by_norm.setdefault((norm(cid), p), []).append((v, src))


# manifest 条目自带的 published_ts：本轮真发/回读时写入的远端真值，最强证据
for cid, per in manifest.items():
    for p, r in (per or {}).items():
        if isinstance(r, dict) and r.get("published_ts"):
            add_l2(cid, p, r["published_ts"], "L2-manifest.published_ts")

# 知乎 drafts：published_ts 是远端列表回读写入的真值
zd = load("zhihu_drafts.json")
for cid, r in zd.items():
    if isinstance(r, dict):
        add_l2(cid, "zhihu", r.get("published_ts"), "L2-zhihu_drafts.published_ts")
        add_l2(cid, "zhihu", r.get("published_at"), "L2-zhihu_drafts.published_at")

# 公众号已发表账：平台已发表列表回读（分钟级）
wp = load("wechat_published.json")
for cid, r in wp.items():
    if isinstance(r, dict):
        add_l2(cid, "wechat", r.get("published_at"), "L2-wechat_published.published_at")

ws = load("wechat_standalone.json")
for cid, r in ws.items():
    if isinstance(r, dict):
        add_l2(cid, "wechat", r.get("published_at"), "L2-wechat_standalone.published_at")

# 两朵云：yun_finalize 键为 __community__<draftId>，含公开页回读后的 published_at
yf = load("yun_finalize.json")
yun_by_draft = {}
for k, r in yf.items():
    if isinstance(r, dict) and r.get("published_at"):
        m = re.search(r"(\d{5,})", str(k))
        if m:
            yun_by_draft[m.group(1)] = r["published_at"]

# longtail_sync：platforms 子字典里的 published_at
lt = load("longtail_sync.json")
for k, v in lt.items():
    if not isinstance(v, dict):
        continue
    cid = v.get("case") or k
    for p, r in (v.get("platforms") or {}).items():
        if isinstance(r, dict) and r.get("published_at"):
            add_l2(cid, p, r["published_at"], "L2-longtail_sync.published_at")

print("L2 原始候选条目:", len(l2), "键形态样例:",
      list(l2)[:4])


def l2_lookup(cid, p, r):
    hits = list(l2.get((cid, p), [])) + list(l2_by_norm.get((norm(cid), p), []))
    # 两朵云按 draft_id 补
    for did in (str(r.get("draft_id") or ""), str(r.get("remote_id") or "")):
        if did in yun_by_draft:
            hits.append((yun_by_draft[did], "L2-yun_finalize.published_at"))
    # 去重
    seen, out = set(), []
    for v, s in hits:
        if (v, s) not in seen:
            seen.add((v, s))
            out.append((v, s))
    return out


# ---------------- 汇总 ----------------
report = []
stat = collections.Counter()
conflicts = []
for cid, p, r in pub:
    cur = r.get("published_at")
    l1v = l1.get((cid, p))
    l2v = l2_lookup(cid, p, r)
    at = str(r.get("at") or "")
    chosen, src = None, None
    if l1v:
        chosen, src = l1v[0], l1v[1]
        stat["L1 远端实时"] += 1
        if at[:10] and chosen[:10] != at[:10]:
            stat["  其中与 at 跨日（B站建稿时点坑）"] += 1
        if l1v[2]:
            stat["  其中远端标题被改写（aid 唯一，仍可信）"] += 1
    elif l2v:
        # 多来源值冲突（分钟级不同）⇒ 不固化有争议的值，留待人工裁定
        vals = {v for v, _ in l2v}
        if len({v[:16] for v in vals}) > 1:
            conflicts.append((cid, p, l2v))
            stat["L2 冲突跳过"] += 1
        else:
            sec = [x for x in l2v if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", x[0])]
            chosen, src = (sec or l2v)[0]
            stat["L2 已入库远端快照"] += 1
    else:
        stat["无证据（不写）"] += 1
    if cur:
        stat["已有 published_at"] += 1
    report.append({
        "case_id": cid, "platform": p, "at": at,
        "current_published_at": cur,
        "chosen": chosen, "source": src,
        "l1": l1v[0] if l1v else None,
        "l1_title_mismatch": l1v[2] if l1v else False,
        "cross_day": bool(l1v and at[:10] and l1v[0][:10] != at[:10]),
        "l2": l2v,
        "will_write": bool(chosen and not cur),
    })

print()
print("=== 覆盖统计 ===")
for k, v in stat.items():
    print(" ", k, v)
print()
writable = [x for x in report if x["will_write"]]
print("本次可写入条数:", len(writable))
print("  按平台:", dict(collections.Counter(x["platform"] for x in writable)))
print("  按来源:", dict(collections.Counter(x["source"].split("-")[0] for x in writable)))
print("  秒级精度:", sum(1 for x in writable if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", x["chosen"] or "")))
print("  分钟级精度:", sum(1 for x in writable if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", x["chosen"] or "")))
print()
print("=== L2 多来源冲突（前 10）===")
for x in conflicts[:10]:
    print(" ", x)
print(" 共", len(conflicts), "条")
print()
print("=== 已有字段 2 条是否与本次候选一致 ===")
for x in report:
    if x["current_published_at"]:
        print(" ", x["case_id"], x["platform"], "cur=", x["current_published_at"],
              "chosen=", x["chosen"], "一致=", x["current_published_at"] == x["chosen"])
print()
print("=== 仍无证据的条目（按平台）===")
no_ev = [x for x in report if not x["chosen"] and not x["current_published_at"]]
print(" 共", len(no_ev), dict(collections.Counter(x["platform"] for x in no_ev)))
for x in no_ev[:15]:
    print("   ", x["case_id"], x["platform"], "at=", repr(x["at"][:40]))

out = os.path.join("tmp", "_published_truth_report_20261112.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({"summary": dict(stat), "conflicts": conflicts, "rows": report},
              f, ensure_ascii=False, indent=1)
print()
print("报告已写", out, "（tmp，不入库）")
