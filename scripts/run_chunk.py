# -*- coding: utf-8 -*-
"""前台分块跑 test_*.py（后台任务会被回收，见 2026-11-06 实测）。

每跑完一个文件就落一行到 tmp/testdone.txt，下次调用跳过已完成的。
--budget 控制单次预算秒数，超了就先退，避免一条命令撞 600s 上限。

用法：
  python -X utf8 scripts/run_chunk.py --budget 420
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "scripts")
TEST_DB = os.path.join(ROOT, "tmp", "test_ledger.db")
DONE = os.path.join(ROOT, "tmp", "testdone.txt")
OUTDIR = os.path.join(ROOT, "tmp", "testout")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=420)
    a = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    done = {}
    if os.path.isfile(DONE):
        for line in open(DONE, encoding="utf-8"):
            p = line.strip().split("\t")
            if len(p) == 2:
                done[p[0]] = p[1]
    files = sorted(os.path.basename(f)[:-3] for f in
                   glob.glob(os.path.join(HERE, "test_*.py")))
    todo = [n for n in files if n not in done]
    print("总 %d 个文件，已完成 %d，待跑 %d" % (len(files), len(done), len(todo)),
          flush=True)

    env = dict(os.environ, LEDGER_DB=TEST_DB)
    t_start = time.time()
    for name in todo:
        if time.time() - t_start > a.budget:
            print("[budget] 到时，剩余 %d 个下次继续" % (len(todo) - todo.index(name)),
                  flush=True)
            break
        t0 = time.time()
        log = os.path.join(OUTDIR, name + ".log")
        with open(log, "w", encoding="utf-8", errors="replace") as lf:
            try:
                r = subprocess.run([sys.executable, os.path.join(HERE, name + ".py")],
                                   stdout=lf, stderr=subprocess.STDOUT,
                                   env=env, cwd=ROOT, timeout=240)
                code = r.returncode
            except subprocess.TimeoutExpired:
                lf.write("\n*** TIMEOUT 240s ***")
                code = 124
        secs = time.time() - t0
        print("%s %-34s %5.1fs rc=%d" % ("ok  " if code == 0 else "FAIL",
                                         name, secs, code), flush=True)
        with open(DONE, "a", encoding="utf-8") as f:
            f.write("%s\t%d\n" % (name, code))

    left = [n for n in files if n not in
            dict(l.strip().split("\t") for l in open(DONE, encoding="utf-8")
                 if len(l.strip().split("\t")) == 2)]
    bad = [k for k, v in
           dict(l.strip().split("\t") for l in open(DONE, encoding="utf-8")
                if len(l.strip().split("\t")) == 2).items() if v != "0"]
    print("\n剩余未跑 %d ｜ 已失败 %d %s" % (len(left), len(bad), bad or ""), flush=True)
    return 0 if not left and not bad else (3 if left else 1)


if __name__ == "__main__":
    sys.exit(main())
