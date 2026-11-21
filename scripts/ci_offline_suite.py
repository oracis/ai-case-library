#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 扩展离线测试套件：把平时本地跑、没进 ci.yml 的核心测试纳入自动门禁。

为什么单独一个脚本而不是在 ci.yml 里堆 18 行 unittest：
  · CI checkout 后**没有 data/ledger.db**（它被 gitignore），必须先从 JSON
    播种临时库，再把整个测试进程钉在 LEDGER_DB 上——否则测试会读空库/真库
    导致假绿或假红（PRD #63 真值在 SQLite）；
  · 白名单收在一处，避免 ci.yml 与测试文件清单两处漂移；
  · 跑完校验 data/*.json 指纹未变，任何测试偷写真值当场现形。

白名单原则（别往里塞）：
  ✅ 纯逻辑、有临时库/临时目录隔离、单文件 <5s、无平台依赖、无网络；
  ❌ test_fit_ready / test_make_article / test_score_value_parity —— 真值库
     564→589 后新增案例缺评分表导致的**已知红灯**（数据债，CI 已红，需另修）；
  ❌ test_jargon —— 依赖真库可见字段，seed 前后结果不稳（实测批量假红）；
  ❌ 各 *_publish / *_draft / cover_theme / prerender 等慢测试或需浏览器。
     它们保持本地按需跑（python scripts/run_all_tests.py）。

用法：python scripts/ci_offline_suite.py
退出码：0 全绿 / 1 有失败 / 2 播种失败（环境问题，不算测试失败）
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
DATA = os.path.join(ROOT, "data")

# 顺序固定：先底层数据，再规则/闸，最后 E2E 判据
SUITE = [
    "test_case_store", "test_candidate_store", "test_atomicio",
    "test_ledger", "test_ledger_health", "test_fifo_dedupe",
    "test_publish_one", "test_publish_slot", "test_verify_rules",
    "test_cdp_core", "test_money_scale", "test_title_match",
    "test_score_table_guard", "test_handoff_contract",
    "test_reconcile_verdict", "test_hn_evidence",
    # 本轮新增
    "test_core_guards", "test_e2e_platform_readback",
    # 公网 E2E：CI 无外网时脚本会 skipTest（诚实跳过，不假绿）
    "test_public_site_e2e",
]


def _fingerprint():
    """data/*.json 的 (size, sha256)，用于跑后校验零污染。"""
    out = {}
    for name in sorted(os.listdir(DATA)):
        if not name.endswith(".json"):
            continue
        p = os.path.join(DATA, name)
        with open(p, "rb") as f:
            b = f.read()
        out[name] = (len(b), hashlib.sha256(b).hexdigest())
    return out


def _seed(tmpdir):
    """从 JSON 播种临时库；CI 无 ledger.db 时这是唯一真值来源。"""
    db_path = os.path.join(tmpdir, "ledger.db")
    env = dict(os.environ, LEDGER_DB=db_path)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(SCRIPTS, "import_to_db.py")],
        env=env, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(r.stdout + "\n" + r.stderr)
        return None, env
    return db_path, env


def main():
    before = _fingerprint()
    tmpdir = tempfile.mkdtemp(prefix="ci_suite_")
    db_path, env = _seed(tmpdir)
    if not db_path:
        print("✗ 播种临时库失败 —— 环境问题，不算测试失败", file=sys.stderr)
        return 2

    modules = ["scripts." + n for n in SUITE]
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "unittest"] + modules,
        env=env, cwd=ROOT)
    rc = r.returncode

    after = _fingerprint()
    dirty = [k for k in after if after[k] != before.get(k)]
    if dirty:
        print("✗ data/*.json 被测试改动：%s" % ", ".join(dirty), file=sys.stderr)
        rc = 1

    print("\n[ci_offline_suite] %d 个模块，rc=%d，真值%s" % (
        len(SUITE), rc, "零污染" if not dirty else "被污染"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
