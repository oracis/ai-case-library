#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 收敛到「只留 Codédex 一篇」。

2026-11-04 用户拍板「先只留 Codédex 那一篇」（放弃重发 Chessigma / TrustMRR / 游戏稿）。

    python -X utf8 scripts/csdn_keep_only.py --dry      # 只打印计划
    python -X utf8 scripts/csdn_keep_only.py --trash    # 移入回收站（可恢复）
    python -X utf8 scripts/csdn_keep_only.py --deep     # 彻底删除（不可恢复）

🚨 双白名单，两个方向都上锁：
   KEEP     —— 要留的文章，任何情况下不碰；
   TARGETS  —— 要删的文章，写死 ID，**不做「非 KEEP 即删」的差集**。
   差集会误伤以后新建的文章，而这套操作不可逆。
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import csdn_publish as C               # noqa: E402

KEEP = {
    "169850647": "Codédex 技术拆解（st=1，198 阅读，有封面）",
}

# id -> (说明, 当前所在)
TARGETS = {
    "170045008": ("Chessigma 草稿（改写后未发）", "draft"),
    "170041126": ("2048 草稿（改写后未发）", "draft"),
    "169938945": ("马力欧 草稿（改写后未发）", "draft"),
    "169742331": ("Chessigma 旧稿，审核未通过 st=32", "deleted"),
    "169654106": ("TrustMRR 旧稿，审核未通过 st=32", "deleted"),
}

LEDGER = os.path.join(ROOT, "data", "csdn_drafts.json")


def _assert_safe():
    bad = set(TARGETS) & set(KEEP)
    if bad:
        raise SystemExit("✗ 同一个 ID 同时在 KEEP 和 TARGETS：%s —— 改脚本再跑" % bad)
    for aid in list(TARGETS) + list(KEEP):
        if not (isinstance(aid, str) and aid.isdigit() and len(aid) >= 9):
            raise SystemExit("✗ ID 形状不对：%r" % (aid,))


def main():
    ap = argparse.ArgumentParser(description="CSDN 只留 Codédex")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="只打印计划")
    g.add_argument("--trash", action="store_true", help="移入回收站（可恢复）")
    g.add_argument("--deep", action="store_true", help="彻底删除（不可逆）")
    a = ap.parse_args()
    _assert_safe()

    print("保留：")
    for aid, why in KEEP.items():
        print("  ✔ %s  %s" % (aid, why))
    print("将删除 %d 条：" % len(TARGETS))
    for aid, (why, where) in TARGETS.items():
        print("  ✂ %s  [%s]  %s" % (aid, where, why))

    if a.dry:
        print("\n干跑：未做任何改动")
        return 0

    # 台账先备份（删完要回填，别丢了原始记录）
    bak = os.path.join(ROOT, "tmp", "csdn_drafts.bak-%s.json"
                       % ("deep" if a.deep else "trash"))
    if os.path.isfile(LEDGER):
        shutil.copy2(LEDGER, bak)
        print("\n台账已备份 → %s" % os.path.relpath(bak, ROOT))

    # 删除走浏览器内请求（直连恒 401），得先把 9224 的 Chrome 拉起来
    import csdn_set_cover as SC
    proc, _cdp = SC.open_cdp()
    ok, fail = [], []
    try:
        for aid, (why, where) in TARGETS.items():
            if a.trash and where == "deleted":
                print("  跳过 %s（已在回收站，移入无意义）" % aid)
                continue
            try:
                C.delete_article(aid, deep=a.deep)
                ok.append(aid)
                print("  ✓ %s %s" % ("彻底删除" if a.deep else "移入回收站", aid), flush=True)
            except Exception as e:                  # noqa: BLE001
                fail.append((aid, repr(e)[:160]))
                print("  ✗ %s 失败：%s" % (aid, repr(e)[:160]), flush=True)
    finally:
        proc.terminate()

    print("\n成功 %d / 失败 %d" % (len(ok), len(fail)))
    if fail:
        print("⚠ 有失败项，先重跑同一命令（幂等），别急着往下走")
        return 1

    # 台账同步：删掉的条目改成 deleted / removed，保留 Codédex 那条
    if os.path.isfile(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            led = json.load(f)
        n = 0
        for k, v in led.items():
            aid = str((v or {}).get("article_id") or "")
            if aid in TARGETS:
                v["state"] = "deep_deleted" if a.deep else "trashed"
                v["ts"] = "2026-11-04 17:1x"
                n += 1
        with open(LEDGER, "w", encoding="utf-8") as f:
            json.dump(led, f, ensure_ascii=False, indent=1)
        print("台账更新 %d 条" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
