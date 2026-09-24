# -*- coding: utf-8 -*-
"""每日采集 + 自动提交推送：让 GitHub 上的库跟着长。

流程：
  1. 跑 harvest.py，把新素材捞进 data/inbox.json（只写采集队列）
  2. 看 data/ 有没有真的变化 —— 没变化就直接退出，不制造空提交
  3. 有变化就 commit + push

刻意不做的事：**绝不自动改 data/cases.json**（精写案例）。核实必须由人工完成，
这是这个库跟二手转述聚合站的根本区别；自动提交只负责搬运机器采回来的素材。

用法：
    python scripts/daily_harvest.py              # 采集 + 提交 + 推送
    python scripts/daily_harvest.py --dry-run    # 采集但不提交（data/ 的改动留在工作区）
    python scripts/daily_harvest.py --no-push    # 提交到本地，不推送
    python scripts/daily_harvest.py --limit 30   # 参数透传给 harvest.py

退出码：0 = 正常（含「本次无新增」）；1 = 出错（采集失败 / git 失败）
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")
REPORT = os.path.join(ROOT, "data", "last_harvest.json")

# 超过这个秒数的 .git/index.lock 视为上次异常中断留下的，可以清理
STALE_LOCK_SECONDS = 600

# 无人值守的环境里，git 绝不能停下来等人输入密码 —— 否则定时任务会一直挂着
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")


def git(*args, **kw):
    """跑一条 git 命令，返回 (rc, 合并后的输出)。超时返回 124，找不到 git 返回 127。"""
    timeout = kw.pop("timeout", 300)
    try:
        p = subprocess.run(["git"] + list(args), cwd=ROOT, env=ENV,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, "找不到 git 命令，请先安装 git 并加进 PATH"
    except subprocess.TimeoutExpired:
        return 124, "git %s 超时（%d 秒）" % (args[0] if args else "", timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def read_report():
    try:
        with open(REPORT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_identity():
    """确认 git 有可用的提交身份（全局或局部都算）。"""
    name = git("config", "user.name")[1]
    mail = git("config", "user.email")[1]
    return bool(name and mail)


def handle_index_lock():
    """处理 .git/index.lock，返回 (能否继续, 说明)。

    无人值守的定时任务里，一个卡死的锁会让之后每一次提交都静默失败，
    所以这里做一次保守处理：只清理超过 10 分钟的锁，太新的说明真有一次 git 在跑。
    """
    lock = os.path.join(ROOT, ".git", "index.lock")
    if not os.path.exists(lock):
        return True, ""
    age = time.time() - os.path.getmtime(lock)
    if age < STALE_LOCK_SECONDS:
        return False, (".git/index.lock 是 %.0f 秒前创建的，可能另有一次 git 正在运行，"
                       "本次跳过。" % age)
    try:
        os.remove(lock)
    except OSError as e:
        return False, "清理 .git/index.lock 失败：%s" % e
    return True, "已清理陈旧的 .git/index.lock（%.0f 分钟前留下）" % (age / 60)


def run_harvest(source, limit, max_inbox):
    cmd = [sys.executable, HARVEST, "--source", source, "--limit", str(limit)]
    if max_inbox is not None:
        cmd += ["--max-inbox", str(max_inbox)]
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "采集超时（900 秒）"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def changed_paths():
    """返回 (变化列表, 错误)。只关心 data/，别的文件一律不动。"""
    rc, out = git("status", "--porcelain", "--", "data/")
    if rc != 0:
        return None, out
    return [l for l in out.splitlines() if l.strip()], ""


def commit_message(report):
    """按采集简报生成提交信息：(标题, 正文)。"""
    added = report.get("added") or 0
    date = datetime.now().strftime("%Y-%m-%d")
    subject = ("采集：新增 %d 条素材（%s）" % (added, date)) if added \
        else "采集：更新数据（%s）" % date

    lines = []
    by = report.get("by_source") or {}
    if by:
        lines.append("来源分布：" + "、".join(
            "%s %d" % (k, v) for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    if report.get("inbox_total") is not None:
        tail = ""
        if report.get("archived_this_run"):
            tail = "（本次归档 %s 条，留底不删）" % report["archived_this_run"]
        lines.append("采集队列 %s 条%s" % (report["inbox_total"], tail))
    lines.append("只写采集队列；精写案例仍需人工核实。")
    return subject, "\n".join(lines)


def heal_remote_ref(branch):
    """推送成功后补齐本地的 origin/<branch> 引用。

    Windows 上 git 子进程偶尔创建不了 .git/refs/remotes/<remote>/ 这种嵌套目录，
    于是 fetch/push 报告成功、引用却没落盘，`git status` 一直显示 [gone]。
    这里在推送成功、且引用确实缺失时直接写文件兜底。
    """
    if git("rev-parse", "--verify", "origin/%s" % branch)[0] == 0:
        return ""
    rc, sha = git("rev-parse", branch)
    if rc != 0 or not sha:
        return ""
    d = os.path.join(ROOT, ".git", "refs", "remotes", "origin")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, branch), "w", encoding="ascii") as f:
            f.write(sha + "\n")
    except OSError:
        return ""
    return "已补齐本地 origin/%s 引用（[gone] 显示问题的兜底）" % branch


def main():
    ap = argparse.ArgumentParser(description="每日采集 + 自动提交推送")
    ap.add_argument("--source", default="all", choices=["hn", "ph", "trustmrr", "all"])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--max-inbox", type=int, default=None,
                    help="采集队列上限，透传给 harvest.py（默认用 harvest 自己的值）")
    ap.add_argument("--dry-run", action="store_true", help="只采集，不提交也不推送")
    ap.add_argument("--no-push", action="store_true", help="提交到本地但不推送")
    ap.add_argument("--message", default="", help="自定义提交标题（默认按简报生成）")
    args = ap.parse_args()

    print("=" * 64)
    print("  每日采集 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 64)

    # ---------- 0 前置检查 ----------
    if git("rev-parse", "--is-inside-work-tree")[0] != 0:
        print("[!] %s 不是 git 仓库，无法自动提交。" % ROOT)
        return 1
    if not ensure_identity():
        print("[!] git 没有配置提交身份，无法自动提交。先执行：")
        print('      git config --global user.name "你的名字"')
        print('      git config --global user.email "你的邮箱"')
        return 1
    ok, note = handle_index_lock()
    if note:
        print("[i] %s" % note)
    if not ok:
        return 1
    rc, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = (branch or "main").strip()
    if branch in ("HEAD", ""):
        print("[!] 当前处于游离 HEAD 状态，不敢自动提交。")
        return 1

    # ---------- 1 采集 ----------
    print("\n[1/4] 采集素材")
    rc, out = run_harvest(args.source, args.limit, args.max_inbox)
    for line in (out or "").splitlines():
        print("      " + line)
    if rc != 0:
        print("\n[!] 采集失败（rc=%d），本次不提交。" % rc)
        return 1

    # ---------- 2 有没有真变化 ----------
    print("\n[2/4] 检查 data/ 有没有变化")
    changed, err = changed_paths()
    if changed is None:
        print("[!] git status 失败：%s" % err)
        return 1
    if not changed:
        print("      data/ 无变化 —— 本次没捞到新素材，不做空提交。")
        return 0
    print("      有 %d 个文件发生变化：" % len(changed))
    for l in changed:
        print("        %s" % l)

    if args.dry_run:
        print("\n--dry-run：已采集，但不提交。data/ 的改动留在工作区。")
        return 0

    # ---------- 3 提交 ----------
    print("\n[3/4] 提交")
    rc, out = git("add", "--", "data/")
    if rc != 0:
        print("[!] git add 失败：%s" % out)
        return 1

    # 只有被追踪且未忽略的文件才会进暂存区。采集队列（inbox / archive /
    # last_harvest）已移出 main、改由 harvest.yml 在 data 分支用 `git add -f`
    # 强制提交，所以在本机或 main 上跑这个脚本时 data/ 通常没有可提交内容——
    # 这不算错误，直接跳过（不要去碰 main）。
    rc, out = git("diff", "--cached", "--name-only")
    if not out.strip():
        print("      没有可提交的改动（采集队列由 CI 的 data 分支承载，本机/主线不提交）。")
        return 0

    report = read_report()
    if args.message:
        subject, body = args.message, ""
    else:
        subject, body = commit_message(report)
    cmd = ["commit", "-m", subject]
    if body:
        cmd += ["-m", body]
    rc, out = git(*cmd)
    if rc != 0:
        print("[!] git commit 失败：\n%s" % out)
        return 1
    rc, sha = git("rev-parse", "--short", "HEAD")
    print("      %s  %s" % (sha, subject))

    if args.no_push:
        print("\n--no-push：已提交到本地，未推送。")
        return 0

    # ---------- 4 推送 ----------
    print("\n[4/4] 推送")
    rc, out = git("push", "origin", branch, timeout=600)
    for line in (out or "").splitlines():
        print("      " + line)
    if rc != 0:
        print("\n[!] 推送失败。本地提交是好的（%s），网络恢复后手动 `git push` 即可。" % sha)
        return 1
    note = heal_remote_ref(branch)
    if note:
        print("      %s" % note)
    print("      已推送 %s -> origin/%s" % (sha, branch))

    # ---------- 收尾汇报 ----------
    print("\n" + "=" * 64)
    print("  完成：新增 %s 条，采集队列 %s 条，已推送到 origin/%s"
          % (report.get("added", "?"), report.get("inbox_total", "?"), branch))
    top = report.get("top") or []
    if top:
        print("\n  本次最值得先看的 %d 条：" % len(top))
        for t in top[:5]:
            print("    · %-28s [%s] %s" % ((t.get("name") or "")[:28],
                                           t.get("source") or "",
                                           t.get("headline") or ""))
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
