# -*- coding: utf-8 -*-
"""daily_harvest.py 的测试：只验 git 机制与失败路径，不联网。

为什么单独测这个：每日采集是无人值守的，坏了不会有人立刻发现。
这个脚本把所有「静默失败」的可能都钉住 —— 身份缺失、锁残留、
没有变化时的空提交、伪造提交信息。

用法：python scripts/test_daily_harvest.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "scripts", "daily_harvest.py")

fails = []
passed = 0


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def git(cwd, *args, env=None, timeout=60):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(["git"] + list(args), cwd=cwd, env=e, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def make_repo(tmp, name="repo"):
    """建一个临时 git 仓库，把 daily_harvest.py 拷进去（ROOT 由 __file__ 推导）。"""
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, "data"))
    shutil.copy2(SRC, os.path.join(root, "scripts", "daily_harvest.py"))
    with open(os.path.join(root, "data", "inbox.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "a"}], f)
    git(root, "init")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed")
    return root


def load_module(root):
    """从指定仓库加载 daily_harvest 模块（这样它的 ROOT 指向那个仓库）。"""
    path = os.path.join(root, "scripts", "daily_harvest.py")
    spec = importlib.util.spec_from_file_location("dh_%d" % abs(hash(root)), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    tmp = tempfile.mkdtemp(prefix="dh_test_")
    # 隔离全局 git 配置，避免测试结果受本机 user.name / 代理配置影响
    empty = os.path.join(tmp, "empty.gitconfig")
    open(empty, "w").close()
    os.environ["GIT_CONFIG_GLOBAL"] = empty
    os.environ["GIT_CONFIG_SYSTEM"] = empty
    os.environ.pop("GIT_TERMINAL_PROMPT", None)

    try:
        root = make_repo(tmp)
        mod = load_module(root)
        print("临时仓库: %s" % root)
        print("模块 ROOT: %s" % mod.ROOT)
        chk("模块 ROOT 指向临时仓库", os.path.normcase(mod.ROOT) == os.path.normcase(root))
        print()

        # ---------- 1 变化检测 ----------
        print("[1] 变化检测（决定要不要提交）")
        chk("干净仓库 -> 无变化", mod.changed_paths() == ([], ""))
        with open(os.path.join(root, "data", "inbox.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "a"}, {"id": "b"}], f)
        changed, err = mod.changed_paths()
        chk("改动 data/ 后能检出", len(changed) == 1 and "inbox.json" in changed[0],
            changed)
        # 非 data/ 目录的改动不该被卷进来
        with open(os.path.join(root, "scripts", "note.txt"), "w") as f:
            f.write("x")
        changed2, _ = mod.changed_paths()
        chk("不动非 data/ 文件", len(changed2) == 1, changed2)
        print()

        # ---------- 2 提交信息 ----------
        print("[2] 提交信息生成")
        subj, body = mod.commit_message(
            {"added": 20, "by_source": {"hn": 14, "trustmrr": 6},
             "inbox_total": 147, "archived_this_run": 3})
        chk("标题带条数", "新增 20 条" in subj, subj)
        chk("正文带来源分布（按数量降序）", "hn 14、trustmrr 6" in body, body.splitlines()[0])
        chk("正文带队列总量与归档", "147 条" in body and "归档 3 条" in body,
            [l for l in body.splitlines() if "147" in l])
        chk("正文声明不碰精写案例", "精写案例仍需人工核实" in body)
        subj2, _ = mod.commit_message({"added": 0, "inbox_total": 100})
        chk("0 条时标题不写「新增 0 条」", "新增" not in subj2, subj2)
        subj3, body3 = mod.commit_message({})
        chk("空简报不炸", isinstance(subj3, str) and subj3 != "", subj3)
        print()

        # ---------- 3 index.lock 处理 ----------
        print("[3] .git/index.lock 处理（无人值守最怕卡死）")
        ok, note = mod.handle_index_lock()
        chk("没有锁时放行", ok and note == "")
        lock = os.path.join(root, ".git", "index.lock")
        open(lock, "w").close()
        ok, note = mod.handle_index_lock()
        chk("新锁 -> 拒绝继续（可能真有 git 在跑）", ok is False, note[:50])
        old = time.time() - (mod.STALE_LOCK_SECONDS + 60)
        os.utime(lock, (old, old))
        ok, note = mod.handle_index_lock()
        chk("陈旧锁 -> 清理并放行", ok and not os.path.exists(lock), note[:50])
        print()

        # ---------- 4 提交身份 ----------
        print("[4] 提交身份检测")
        chk("隔离全局配置后 -> 无身份", mod.ensure_identity() is False)
        git(root, "config", "--local", "user.name", "tester")
        git(root, "config", "--local", "user.email", "t@example.com")
        chk("设了局部身份 -> 通过", mod.ensure_identity() is True)
        print()

        # ---------- 5 远程引用兜底 ----------
        print("[5] origin/<branch> 引用兜底（Windows 上引用落不了盘）")
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
        chk("取到当前分支名", bool(branch) and branch != "HEAD", branch)
        chk("无 origin 时先确认缺失",
            git(root, "rev-parse", "--verify", "origin/%s" % branch)[0] != 0)
        chk("分支不存在时安静返回", mod.heal_remote_ref("no-such-branch") == "")
        note = mod.heal_remote_ref(branch)
        ref_file = os.path.join(root, ".git", "refs", "remotes", "origin", branch)
        chk("补齐引用文件", os.path.exists(ref_file), note or "(无提示)")
        if os.path.exists(ref_file):
            head = git(root, "rev-parse", branch)[1]
            with open(ref_file, encoding="ascii") as f:
                chk("引用内容等于本地 HEAD", f.read().strip() == head)
            chk("再次调用不重复写（已存在则跳过）", mod.heal_remote_ref(branch) == "")
            rc, out = git(root, "rev-parse", "--verify", "origin/%s" % branch)
            chk("git 现在能认出 origin/%s" % branch, rc == 0, out[:40])
        print()

        # ---------- 6 CLI 失败路径（子进程，不联网） ----------
        print("[6] CLI 前置检查失败时的行为")
        py = sys.executable
        env = dict(os.environ)

        # 非 git 目录
        plain = os.path.join(tmp, "plain")
        os.makedirs(os.path.join(plain, "scripts"))
        shutil.copy2(SRC, os.path.join(plain, "scripts", "daily_harvest.py"))
        p = subprocess.run([py, os.path.join(plain, "scripts", "daily_harvest.py")],
                           cwd=plain, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        chk("非 git 仓库 -> rc=1 且提示清楚", p.returncode == 1 and "不是 git 仓库" in p.stdout,
            "rc=%d" % p.returncode)

        # git 仓库但无提交身份
        root2 = make_repo(tmp, "repo_noident")
        git(root2, "config", "--local", "--unset", "user.name")
        git(root2, "config", "--local", "--unset", "user.email")
        p = subprocess.run([py, os.path.join(root2, "scripts", "daily_harvest.py")],
                           cwd=root2, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        chk("无提交身份 -> rc=1 且给出配置命令",
            p.returncode == 1 and "user.email" in p.stdout,
            "rc=%d" % p.returncode)

        # 陈旧锁存在时 CLI 能继续（走到采集那步会失败但不该崩）——这里只验提示出现
        lock2 = os.path.join(root2, ".git", "index.lock")
        open(lock2, "w").close()
        p = subprocess.run([py, os.path.join(root2, "scripts", "daily_harvest.py")],
                           cwd=root2, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        chk("新锁 + 无身份 -> 先报身份问题（不崩）",
            p.returncode == 1 and "Traceback" not in p.stdout, "rc=%d" % p.returncode)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 62)
    if fails:
        print("  结果：%d 通过 / %d 失败" % (passed, len(fails)))
        for f in fails:
            print("    ✗ %s" % f)
    else:
        print("  结果：全部通过（%d 项）" % passed)
    print("=" * 62)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
