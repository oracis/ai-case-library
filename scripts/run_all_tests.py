#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量测试跑法（db 隔离版）——一条命令跑完 scripts/test_*.py 全部 59 个文件。

为什么需要这个脚本（2026-11-03）：
  台账真值已迁到 SQLite（data/ledger.db），测试若走到默认路径会把
  假数据灌进真库。解法是环境变量开关：db.get_conn() 优先读
  `LEDGER_DB`，本脚本把整个测试进程钉在临时测试库
  tmp/test_ledger.db 上（每次运行前从 JSON 重新播种，与真库同构）。

  另外沿用 CI「断言数据未被自测污染」的思路：跑前对 data/ledger.db
  和全部 data/*.json 取 sha256 指纹，跑完再取一遍比对——任何测试
  想碰真值都会在这里现形。

用法：
  python scripts/run_all_tests.py            # 全量
  python scripts/run_all_tests.py -k xhs     # 只跑文件名含 xhs 的
  python scripts/run_all_tests.py -v         # 失败文件直接把日志尾打出来

退出码：0 = 全绿；1 = 有失败（失败清单见输出，逐文件日志在
tmp/testout/<test名>.log）。
"""

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUTDIR = os.path.join(ROOT, "tmp", "testout")
TEST_DB = os.path.join(ROOT, "tmp", "test_ledger.db")

# 单文件超时：正常测试几秒内跑完；240s 是给联网/起浏览器的少数文件兜底，
# 卡死不至于拖垮整轮。
PER_FILE_TIMEOUT = 240


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def fingerprint():
    """真值指纹：ledger.db（先 checkpoint WAL 保证字节稳定）+ 全部 data/*.json。"""
    import sqlite3
    conn = sqlite3.connect(os.path.join(DATA, "ledger.db"))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    snap = {"db": sha256(os.path.join(DATA, "ledger.db"))}
    snap["json"] = {
        p: sha256(os.path.join(DATA, p))
        for p in sorted(os.listdir(DATA)) if p.endswith(".json")
    }
    return snap


def seed_test_db():
    """删掉旧测试库，从 JSON 重新播种（与真库同构，读端行为一致）。"""
    for suffix in ("", "-wal", "-shm"):
        p = TEST_DB + suffix
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, LEDGER_DB=TEST_DB)
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "import_to_db.py")],
        capture_output=True, text=True, env=env, cwd=ROOT,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        sys.stderr.write(r.stdout + "\n" + r.stderr)
        raise SystemExit("播种测试库失败，中止（宁可不测也不裸跑碰真库）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="keyword", default="",
                    help="只跑文件名含该关键字的测试")
    ap.add_argument("-v", dest="verbose", action="store_true",
                    help="失败时打印该文件日志最后 30 行")
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    before = fingerprint()
    seed_test_db()

    env = dict(os.environ, LEDGER_DB=TEST_DB)
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if args.keyword:
        files = [f for f in files if args.keyword in os.path.basename(f)]

    results = []            # (name, code, seconds)
    for f in files:
        name = os.path.basename(f)[:-3]
        log = os.path.join(OUTDIR, name + ".log")
        import time
        t0 = time.time()
        with open(log, "w", encoding="utf-8", errors="replace") as lf:
            try:
                r = subprocess.run(
                    [sys.executable, f],
                    stdout=lf, stderr=subprocess.STDOUT,
                    env=env, cwd=ROOT, timeout=PER_FILE_TIMEOUT,
                )
                code = r.returncode
            except subprocess.TimeoutExpired:
                lf.write("\n*** TIMEOUT %ds ***" % PER_FILE_TIMEOUT)
                code = 124
        results.append((name, code, time.time() - t0))
        mark = "ok  " if code == 0 else "FAIL"
        print("%s %-32s %5.1fs  rc=%d" % (mark, name, time.time() - t0, code),
              flush=True)

    # ---- 汇总 ----
    bad = [x for x in results if x[1] != 0]
    print("\n=== 汇总：%d 个文件，%d 失败 ===" % (len(results), len(bad)))
    for name, code, secs in bad:
        print("  FAIL %-32s rc=%d (%.0fs)" % (name, code, secs))

    # ---- 真值未污染断言 ----
    after = fingerprint()
    if after["db"] != before["db"]:
        bad.append(("真值污染: data/ledger.db", 1, 0))
        print("  !! data/ledger.db 指纹变了 —— 有测试绕过了 LEDGER_DB 隔离！")
    jdiff = [k for k in after["json"]
             if after["json"].get(k) != before["json"].get(k)]
    if jdiff:
        bad.append(("真值污染: data/*.json", 1, 0))
        print("  !! data/*.json 变了：%s" % ", ".join(jdiff))
    if not (after["db"] != before["db"] or jdiff):
        print("真值核验：ledger.db + %d 个 data/*.json 指纹全部一致，零污染。"
              % len(before["json"]))

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
