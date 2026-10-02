#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站草稿箱去重：删掉标题完全相同的多余副本。

## 背景：2026-10-03 凌晨事故

改标题时 `draft/add` 的 `article_id` 没带上（早期版本从 `draft/view` 取
`draft["arg"]`，而 view 返回的是**扁平**结构，`arg` 为空 ⇒ 变成新建而非更新）。
结果远端草稿从 38 条涨到 **101 条**，新建副本还丢了封面和正文。

## ⚠⚠ 保留哪一条：**不能按 mtime**

第一版按「mtime 最新」保留，看着合理，实测**正好留反了**：

| | mtime | 正文 | 封面 |
|---|---|---|---|
| 事故空壳（新建副本） | 1790957809 **更新** | **0 段** | ❌ 无 |
| 有效草稿（原草稿） | 1790948376 较早 | 42 段 | ✅ 有 |

空壳是**最后一次**保存动作的产物，mtime 必然更新 ⇒ 按 mtime 保留
= **删掉有效草稿、留下空壳**，正好把唯一的好数据删了。

⇒ **判据必须是「内容完整性」**：`opus.content.paragraphs` 非空
**且** `image_urls` 非空者为保留方。mtime 只作同完整度时的tiebreak。

## 为什么必须逐条 `view`

`draft/list` 只给标题摘要，**没有** `image_urls` / 段落数 ——
用它判断完整性会得出「全部一样完整」的错误结论。
所以先 list 找出同标题组，再对**组内每条** view 验完整性。

⚠ 但 101 次 view 太慢且单进程会被回收 ⇒ 复用 `out/bili_cover_state.json`
（`bili_cover_check.py --fresh` 的产物，已含每条的段落数与封面）。
没有该台账时才现场 view。

## 用法

    python -u -X utf8 scripts/bili_dedupe.py            # 只打印计划
    python -u -X utf8 scripts/bili_dedupe.py --apply    # 真删
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili_title_push as tp        # noqa: E402
import bili_draft_api as api         # noqa: E402

SEP = "─" * 78
STATE = os.path.join(tp.bp.ROOT, "out", "bili_cover_state.json")


def _quality(cdp, aid, st):
    """返回 (段落数, 有封面)。优先读台账，miss 才现场 view。"""
    v = st.get(str(aid))
    if v and not v.get("err"):
        return v.get("paras") or 0, bool(v.get("cover"))
    d = api.view_draft(cdp, aid)
    paras = len(((d.get("opus") or {}).get("content") or {})
                .get("paragraphs") or [])
    return paras, bool(d.get("image_urls"))


def _better(a, b):
    """a 是否比 b 更该保留：先比完整性，再比 mtime。"""
    ap, ac = a["paras"], a["cover"]
    bp, bc = b["paras"], b["cover"]
    if (bool(ap), bool(ac)) != (bool(bp), bool(bc)):
        return (bool(ap), bool(ac)) > (bool(bp), bool(bc))
    return (a["mtime"] or 0) > (b["mtime"] or 0)


def groups(cdp, st):
    ds = api.list_drafts(cdp, pn=1, ps=200)
    g = defaultdict(list)
    for d in ds:
        t = (d.get("title") or "").strip()
        if t:
            g[t].append(d)
    dup = {t: v for t, v in g.items() if len(v) > 1}
    rows = []
    for t, vs in sorted(dup.items()):
        info = []
        for d in vs:
            p, c = _quality(cdp, d.get("article_id"), st)
            info.append({"aid": d.get("article_id"), "mtime": d.get("mtime"),
                         "paras": p, "cover": c, "title": t})
        info.sort(key=lambda x: x["mtime"] or 0, reverse=True)
        keep = info[0]
        for cand in info[1:]:
            if _better(cand, keep):
                keep, cand_keep = cand, keep
                # 保持 drop 列表完整：被换下的那条也要进待删
                info.remove(cand_keep)
                info.append(cand_keep)
                keep = cand
        # 重新按「keep 最优」排一次
        rest = [x for x in info if x["aid"] != keep["aid"]]
        rows.append((t, keep, rest))
    return ds, rows


def plan(cdp):
    st = {}
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (ValueError, OSError):
        pass
    ds, rows = groups(cdp, st)
    todo = []
    print("远端草稿 %d 条｜同标题组 %d｜待删 %d"
          % (len(ds), len(rows), sum(len(r) for _, _, r in rows)))
    print(SEP)
    for t, keep, rest in rows:
        print("  %s" % t)
        print("    保留 aid=%-8s %2d段 %s mtime=%s"
              % (keep["aid"], keep["paras"],
                 "有封面" if keep["cover"] else "⚠无封面", keep["mtime"]))
        for d in rest:
            print("    删除 aid=%-8s %2d段 %s mtime=%s"
                  % (d["aid"], d["paras"],
                     "有封面" if d["cover"] else "⚠无封面", d["mtime"]))
            todo.append(d["aid"])
    print(SEP)
    print("待删 %d 条 → 删完应剩 %d 条" % (len(todo), len(ds) - len(todo)))
    return todo


def apply(cdp):
    st = {}
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (ValueError, OSError):
        pass
    ds, rows = groups(cdp, st)
    todo = []
    for _t, _keep, rest in rows:
        todo += [d["aid"] for d in rest]
    print("开始删 %d 条（远端共 %d 条）" % (len(todo), len(ds)))
    ok = fail = 0
    for i, aid in enumerate(todo, 1):
        try:
            r = api.delete_draft(cdp, aid)
            if (r or {}).get("code") == 0:
                print("[%d/%d] 删除 aid=%s OK" % (i, len(todo), aid), flush=True)
                ok += 1
            else:
                print("[%d/%d] 删除 aid=%s FAIL %s" % (i, len(todo), aid, r),
                      flush=True)
                fail += 1
        except Exception as exc:                      # noqa: BLE001
            print("[%d/%d] 删除 aid=%s ERROR %s"
                  % (i, len(todo), aid, str(exc)[:100]), flush=True)
            fail += 1
    print(SEP)
    print("删除成功 %d / 失败 %d" % (ok, fail))
    return fail


def main():
    ap = argparse.ArgumentParser(description="B站草稿箱去重（按内容完整性保留）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cdp = tp.connect()
    rc = apply(cdp) if args.apply else (plan(cdp) and 0)
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
