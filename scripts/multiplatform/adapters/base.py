#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""适配器基类与注册表。"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable


class Result:
    """一次平台调用的结果。`ok` 只表示流程跑完，不代表远端一定有草稿。"""

    __slots__ = ("ok", "state", "note", "rc")

    def __init__(self, ok, state="failed", note="", rc=0):
        self.ok = ok
        self.state = state          # draft_saved / published / failed
        self.note = note
        self.rc = rc

    def __repr__(self):
        return "<Result %s %s %r>" % (self.state, self.ok, self.note[:40])


class Adapter:
    """平台适配器。子类必须给全 class 属性。"""

    key = ""
    label = ""
    script = ""                    # scripts/ 下的发布脚本文件名
    # 该平台「已经精修过的标题」在 out/<这个值>/<id>/meta.json
    title_from = ""
    # 跑 publish 前必须先存在的产物（相对 ROOT）；空 = 不要求
    requires = ()
    # 该平台有独立的「已发过」判断吗（有则覆盖 done_ids）
    has_state_file = True
    # 需要 build 子命令吗
    can_build = True

    # ---- 子进程 -----------------------------------------------------------
    def run(self, args, enabled=True, timeout=1800):
        cmd = [PY, os.path.join(SCRIPTS, self.script)] + list(args)
        if not enabled:
            return Result(True, "pending", "dry-run: " + " ".join(args), 0)
        try:
            p = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return Result(False, "failed", "超时 %ds" % timeout, -1)
        except OSError as e:
            return Result(False, "failed", "启动失败：%s" % e, -1)
        note = _tail(p.stdout)
        ok = p.returncode == 0
        return Result(ok, "draft_saved" if ok else "failed", note, p.returncode)

    # ---- 流程 -------------------------------------------------------------
    def missing_artifacts(self, art):
        return [r for r in self.requires if not os.path.exists(
            os.path.join(ROOT, r.replace("<id>", art.cid)))]

    def build(self, art, dry=False):
        if not self.can_build:
            return Result(True, "draft_saved", "该平台无 build 步骤")
        return self.run(["build", "--case", art.cid], not dry)

    def publish(self, art, dry=False, replace=False, yes=False):
        args = ["publish", "--case", art.cid]
        if replace:
            args.append("--replace")
        if yes:
            args.append("--yes")
        return self.run(args, not dry)

    def verify(self, art):
        """远端回读校验。默认只看本地状态文件（保守：查不到不算失败）。"""
        return None

    # ---- 幂等 -------------------------------------------------------------
    def done_ids(self):
        """已发过（草稿或已发布）的 case id 集合。"""
        if not self.has_state_file:
            return set()
        try:
            return self._load_done()
        except Exception:                                  # noqa: BLE001
            return set()

    def _load_done(self):
        return set()


def _tail(text, n=3):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return " | ".join(lines[-n:])[:300]


# ---- 注册表 ---------------------------------------------------------------
REGISTRY = {}


def register(cls):
    """注册一个适配器。

    存**实例**而不是类 —— 注册表是全局单例（每个平台只有一个浏览器会话策略），
    存类会让 `reg[key].done_ids()` 变成未绑定方法调用，漏掉 self。
    """
    REGISTRY[cls.key] = cls()
    return cls


def all_adapters():
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def get(key):
    a = REGISTRY.get(key)
    if a is None:
        raise SystemExit("没有这个平台：%s（可选 %s）"
                         % (key, "/".join(sorted(REGISTRY))))
    return a
