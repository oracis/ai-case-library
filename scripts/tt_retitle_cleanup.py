#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删掉头条草稿箱里标题含金额的旧稿（为换标题腾位）。

为什么必须删而不是改：头条**没有 draft/update 接口**（`cmd_dedup` 的注释与
`bili` 的对比都记着），改标题只能删旧稿 + 用新标题重存。

⚠ 判据必须是**标题本身含金额**，不是「本地有 override」：
   - 远端有4 条不含金额的稿（harperai / easymix / MORT / Quran Unlock），
     它们不需要动；
   - 也不能按 case id 删，因为远端标题是旧文案，对不上新标题。

删完必须回读确认残留数= 0，且剩下的条数 = 原条数 - 删掉数。

用法：
    python -X utf8 scripts/tt_retitle_cleanup.py --dry     # 只列清单
    python -X utf8 scripts/tt_retitle_cleanup.py --go      # 真删
    python -X utf8 scripts/tt_retitle_cleanup.py --verify   # 回读残留
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

import toutiao_publish as T  # noqa: E402

# 金额判据：$85K / $1.6K / 月收$1.6K / 年收$10M
MONEY = re.compile(r"[0-9][0-9.,]*\s*[KkMm]\b|[$¥￥]\s*[0-9]")


def _open_box():
    cdp, t = T._open(T.TT_DRAFT, wait=10)
    T._check_login(cdp)
    time.sleep(4)
    return cdp, t


def read_titles(cdp):
    return T._draft_all_titles(cdp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--max-del", type=int, default=40)
    args = ap.parse_args()

    if not (args.dry or args.go or args.verify):
        ap.print_help()
        return 1

    cdp, t = _open_box()
    try:
        before = read_titles(cdp)
        money = [x for x in before if MONEY.search(x)]
        print("草稿箱共 %d 条，其中标题含金额 %d 条" % (len(before), len(money)))
        print("不含金额（保留）：%d 条" % (len(before) - len(money)))

        if args.dry:
            for x in money:
                print("  [dry删] %s" % x)
            return 0

        if args.verify:
            print("\n=== 残留检查 ===")
            print("含金额残留：%d 条 %s" % (len(money), money or "（无）"))
            print("\n全部 %d 条：" % len(before))
            for x in before:
                print(("  $ " if MONEY.search(x) else "    ") + x)
            return 0 if not money else 2

        # —— 真删 ——
        done, failed = [], []
        for i, title in enumerate(money, 1):
            if len(done) >= args.max_del:
                print("达到 --max-del=%d，停止" % args.max_del)
                break
            n, pos = T._count_dup_and_delbtn(cdp, title, keep_newest=False,
                                             expand=True)
            if n <= 0 or not pos:
                print("[%d/%d] 找不到可删的：%s" % (i, len(money), title))
                failed.append((title, "定位不到删除按钮"))
                continue
            ok = T._confirm_delete(cdp, pos)
            print("[%d/%d] %s %s" % (i, len(money),
                                     "OK" if ok else "FAIL", title), flush=True)
            (done if ok else failed).append(title)
            time.sleep(1.0)

        print("\n删除 %d 条，失败 %d 条" % (len(done), len(failed)))
        for t2, why in failed:
            print("  [fail] %s —— %s" % (t2, why))

        # 回读
        cdp2, t2 = _open_box()
        try:
            after = read_titles(cdp2)
        finally:
            cdp2.close_target(t2["id"])
        left = [x for x in after if MONEY.search(x)]
        print("\n回读：%d 条（原 %d，删 %d），含金额残留 %d 条"
              % (len(after), len(before), len(done), len(left)))
        for x in left:
            print("  [残留] %s" % x)
        return 0 if not left else 2
    finally:
        cdp.close_target(t["id"])


if __name__ == "__main__":
    sys.exit(main())
