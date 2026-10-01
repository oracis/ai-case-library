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

    __slots__ = ("ok", "state", "note", "rc", "out")

    def __init__(self, ok, state="failed", note="", rc=0, out=""):
        self.ok = ok
        self.state = state          # draft_saved / published / failed
        self.note = note
        self.rc = rc
        # 完整 stdout。批量调用要靠它逐条判定成败 —— 只看退出码的话
        # 「37 条里挂了 3 条」会被当成整体失败，已经成功的 34 条会被
        # 误标 failed 而重发。只有 full=True 时才填。
        self.out = out

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
    # 有独立的「补封面」步骤吗（子类覆盖 cover()）
    supports_cover = False
    # 批量支持「只跑指定 id」吗（子类覆盖 batch()）
    supports_only = False

    # ---- 子进程 -----------------------------------------------------------
    def run(self, args, enabled=True, timeout=1800, full=False):
        cmd = [PY, os.path.join(SCRIPTS, self.script)] + list(args)
        if not enabled:
            return Result(True, "pending", "dry-run: " + " ".join(args), 0)
        # ⚠ 必须**显式**给子进程钉死 UTF-8（2026-09-30 修）。
        #   Windows 控制台默认 cp936，子进程会按 GBK 编码 stdout；而本函数
        #   用 encoding="utf-8" 解码 —— 两边对不上，中文全变乱码：
        #     「  ✓ 标题《MORT》已存草稿」→ 「  BÕ¾×¨À¸·¢²¼…」
        #   后果不是难看，是**功能坏掉**：B站适配器靠 `s.startswith("✓ 标题")`
        #   从 stdout 逐条判定成败（"37 条挂 1 条"不能一刀切当整体失败），
        #   勾号变乱码后一条都匹配不上 → 已存好的草稿全被误标 failed，
        #   下次重跑就又建一份重复草稿。实测踩过：mort 重存明明成功、
        #   远端回读也确认了，清单却写 failed。
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUTF8", "1")
        try:
            p = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                               encoding="utf-8", errors="replace", env=env)
        except subprocess.TimeoutExpired:
            return Result(False, "failed", "超时 %ds" % timeout, -1)
        except OSError as e:
            return Result(False, "failed", "启动失败：%s" % e, -1)
        note = _tail(p.stdout)
        ok = p.returncode == 0
        return Result(ok, "draft_saved" if ok else "failed", note,
                      p.returncode, p.stdout if full else "")

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
        # ⚠ 只有 supports_replace 的平台才透传 --replace（2026-09-30 修）。
        #   契约写在 Adapter 上，调用方无条件拼参数就等于绕过契约：
        #   小红书 supports_replace=False，它的脚本没有 --replace 这个参数，
        #   拼上去直接 argparse 报「unrecognized arguments」退出 2，
        #   重试三次全一样的错，最后标 failed。
        #   改文案要重存小红书，得先删掉旧草稿（xhs_publish.py purge）
        #   再普通 publish —— 那边本来就没有「更新草稿」这个动作。
        if replace and self.supports_replace:
            args.append("--replace")
        if yes and self.supports_publish:
            args.append("--yes")
        return self.run(args, not dry)

    def cover(self, art, dry=False, force=False):
        """补封面。不支持的平台返回 pending，runner 会跳过。"""
        return Result(True, "pending", "%s 无独立封面步骤" % self.label)

    def batch(self, cids, dry=False, replace=False, retries=1):
        """一次跑多条。

        为什么要有这条：一条一条 spawn 子进程，每条都要重新起 CDP 连接、
        重新开草稿箱 tab，37 条下来既慢又容易把页面 WebSocket 拖超时
        （B站实测：单条 spawn 版跑到第 2 条就 TimeoutError）。平台脚本
        自带批量时走这里，一个进程内串行搞定。不支持的平台返回 None，
        runner 会退回逐条调用。
        """
        return None

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
