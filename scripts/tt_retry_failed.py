#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重试 `tt_body_fill --all` 里失败的案例（单进程内串行，避免反复起浏览器）。

⚠ 为什么需要它：`tt_body_fill.main()` 的 `--case` 只接**一个** id，重试 N 条
   就得起 N 个进程；而批量跑完/中断后草稿箱列表会回缩，重试又必须重新展开
   一次。本脚本在一个进程里连续跑完所有 cid，共用一次浏览器与登录态。

⚠ 幂等：`fill_one` 会先读远端表格数，已经是目标内容就返回 skip，不重复写。

用法：
    python -u -X utf8 scripts/tt_retry_failed.py voklit mortlancer-app
    python -u -X utf8 scripts/tt_retry_failed.py --from-log _tt_fill_all.log
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tt_body_fill as bf                                         # noqa: E402
import toutiao_publish as tp                                      # noqa: E402


def ids_from_log(path):
    """从批量日志里捞出失败的 cid。

    判据＝行首 `[n/m] <cid>` 之后的结果既不是 `ok` 也不是 `skip`
    （批量脚本自己就把异常归到失败这两类之外）。
    """
    failed = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"\[\d+/\d+\]\s+(\S+)\s+(.*)$", line.strip())
            if not m:
                continue
            cid, res = m.group(1), m.group(2)
            if res.startswith("ok") or res.startswith("skip"):
                continue
            if cid not in failed:
                failed.append(cid)
    return failed


def remote_titles(cids):
    """只读草稿箱，给这批 cid 建「远端标题」映射（标题漂移的按远端点）。"""
    mapping = bf._match_remote_titles(
        [c for c in tp.load_cases() if c["id"] in set(cids)])
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cids", nargs="*", help="要重试的案例 id")
    ap.add_argument("--from-log", default=None, help="从批量日志里提取失败项")
    args = ap.parse_args()

    cids = list(args.cids)
    if args.from_log:
        cids += ids_from_log(args.from_log)
    # 去重保序
    seen, todo = set(), []
    for c in cids:
        if c not in seen:
            seen.add(c)
            todo.append(c)
    if not todo:
        print("没有要重试的案例。")
        return 0

    print("== 重试 %d 条：%s\n" % (len(todo), " ".join(todo)), flush=True)
    print("== 只读草稿箱，建远端标题映射 ==", flush=True)
    remote = remote_titles(todo)
    print("   映射上 %d / %d\n" % (len(remote), len(todo)), flush=True)

    by_id = {c["id"]: c for c in tp.load_cases()}
    ok = bad = skip = 0
    for i, cid in enumerate(todo, 1):
        c = by_id.get(cid)
        if not c:
            print("[%d/%d] %-20s 本地找不到这个案例" % (i, len(todo), cid),
                  flush=True)
            bad += 1
            continue
        if cid not in remote:
            # ⚠ 不在草稿箱就别去点「编辑」——会掉进作品管理兜底空等 90s
            print("[%d/%d] %-20s 草稿箱里没有，跳过" % (i, len(todo), cid),
                  flush=True)
            skip += 1
            continue
        try:
            r = bf.fill_one(c, dry=False, remote_title=remote[cid])
        except Exception as e:                              # noqa: BLE001
            r = "%s: %s" % (type(e).__name__, e)
        print("[%d/%d] %-20s %s" % (i, len(todo), cid, r), flush=True)
        ok += r.startswith("ok")
        skip += r.startswith("skip")
        bad += not (r.startswith("ok") or r.startswith("skip"))

    print("\n成功 %d / 跳过 %d / 失败 %d" % (ok, skip, bad))
    print("失败清单：%s" % (json.dumps(
        [c for c in todo if c not in remote], ensure_ascii=False)))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())