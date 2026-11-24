#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径护栏：拦住「MSYS 风格路径喂给 Windows Python」这类**静默失败**。

## 要解决的具体事故

2026-11-24 清理备份时，有一批删除是用 Git Bash 里调 Windows Python 跑的，
路径写成了 Bash 习惯的 `/d/open-source-games/.cache/xxx.bak.json`：

    os.path.isfile("/d/open-source-games/.cache/game_deepread.bak.json")  → False
    os.path.abspath("/d/open-source-games/...")  → 'D:\\d\\open-source-games\\...'

Windows Python 把 `/d/x` 理解成「**当前盘符根下的 d 目录**」，不是「D 盘的 x」。
于是 `isfile` 如实返回 False —— 包在 `if os.path.isfile(p): os.remove(p)` 里的删除
就被静默跳过（`os.path.exists` 同理），循环照跑完，脚本最后还打印「已删除 N 个」，
文件一个没动却没人看得出。
（注意区分：直接 `os.remove("/d/x")` 反而会抛 FileNotFoundError，因为 `D:\d\`
这个父目录根本不存在。**静默只发生在「先判断再操作」的写法里**，而批量清理
几乎全是这种写法 —— 所以光靠「没抛异常」根本发现不了。）

这不是 Python 的 bug，`/c/Python315/python.exe` 在 Bash 命令行里是**正确**写法
（Bash 自己解析）。陷阱只在「路径跨过 Python 边界」时才出现：
路径字面量、`sys.argv` 参数、配置文件里存的值，都必须写盘符。

## 判据为什么不会误伤 API 路径

仓库里有大量 `/x/dynamic/feed/article/draft/add` 这类 B 站接口路径，
形状同样是「单斜杠 + 单字母 + 斜杠」。所以判据加了一条硬约束：

    只有当 `X:` 这个盘符在本机真实存在时，才判定为笔误。

`/d/...` 会被拦（D 盘存在），`/x/dynamic/...` 不会（X 盘不存在）。
把 API 路径误当文件路径用的情况本来也该拦，所以这条约束没有放水。

## 用法

    import pathguard

    pathguard.check(path, "写入")            # 命中就抛 MSYSPathError
    pathguard.remove_files(paths)            # 预检全量 → 删 → 回读，缺一个就整体中止
    pathguard.move_files([(src, dst), ...])  # 同上，移完回读两端

批量函数的设计前提：**「脚本没报错」不等于「文件真删了」**。
所以它们先 `stat_all()` 全量预检（缺一个即中止，一个都不删），
删完再回读一遍，有残留就抛错而不是返回成功。

环境变量 `PATHGUARD=0` 可临时关闭（应急用，正常不该关）。
"""

import os

__all__ = [
    "MSYSPathError",
    "is_msys_drive_path",
    "check",
    "check_all",
    "stat_all",
    "remove_files",
    "move_files",
]

_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


class MSYSPathError(ValueError):
    """路径写法在当前平台上会被解析到别处，几乎必然是「Bash 习惯带进了 Python」。"""


def enabled():
    """只有 Windows 才需要这层拦截；Linux 上 `/d/foo` 是合法的根目录路径。"""
    if os.name != "nt":
        return False
    return os.environ.get("PATHGUARD", "1") not in ("0", "false", "off")


def is_msys_drive_path(path):
    """判断是否为 `/d/foo` 这种把盘符写成目录的路径，且该盘符真实存在。

    传入 bytes / 非路径对象一律返回 False（不猜）。
    """
    try:
        p = os.fspath(path)
    except TypeError:
        return False
    if not isinstance(p, str):
        return False
    if len(p) < 3 or p[0] != "/" or p[2] != "/" or p[1] not in _LETTERS:
        return False
    return os.path.exists(p[1].upper() + ":/")


def suggest(path):
    """给出纠正后的写法：`/d/a/b` → `D:/a/b`。"""
    p = os.fspath(path)
    return "%s:/%s" % (p[1].upper(), p[3:])


def _message(path, action):
    p = os.fspath(path)
    resolved = os.path.abspath(p)
    cur_drive = os.path.splitdrive(resolved)[0] or "(当前盘符)"
    return (
        "MSYS 风格路径不能直接交给 Windows Python（%s）：%r\n"
        "  Windows 会把它解析成 %r\n"
        "  —— 字母 %r 被当成目录名，落到了「当前盘符 %s 的根目录」下，而不是 %s 盘\n"
        "  ⇒ isfile 静默 False ⇒ 包在 if 里的 remove 被跳过 ⇒ 循环跑完还报成功\n"
        "  改成 %r\n"
        "  注意：Bash 命令行里的 /c/... 是正确写法（Bash 自己解析），\n"
        "        只有跨进 Python 的路径（字面量 / argv / 配置值）才要换盘符。\n"
        "  确有需要可设 PATHGUARD=0 关闭本检查。"
        % (action, p, resolved, p[1].lower(), cur_drive, p[1].upper(), suggest(p))
    )


def check(path, action="访问"):
    """单路径校验。命中抛 `MSYSPathError`；否则原样返回，便于串联调用。"""
    if enabled() and is_msys_drive_path(path):
        raise MSYSPathError(_message(path, action))
    return path


def check_all(paths, action="访问"):
    """批量校验，一次报出全部命中项。返回原序列。"""
    bad = [p for p in paths if enabled() and is_msys_drive_path(p)]
    if bad:
        raise MSYSPathError(
            "%s：%d 个路径是 MSYS 写法（共 %d 个）\n%s"
            % (action, len(bad), len(paths),
               "\n".join("  %r → %r" % (os.fspath(b), suggest(b)) for b in bad[:10]))
        )
    return paths


def stat_all(paths, missing_ok=False):
    """全量预检。返回 `(existing, missing)`。

    `missing_ok=False` 时只要有一个不存在就抛 `FileNotFoundError` ——
    这是批量操作的**第一道闸**：不允许「跳过不存在的、继续删剩下的」，
    因为那正是上一次漏文件和误报成功的成因。
    """
    existing, missing = [], []
    for p in paths:
        fp = os.fspath(p)
        check(fp)
        (existing if os.path.exists(fp) else missing).append(fp)
    if missing and not missing_ok:
        raise FileNotFoundError(
            "预检失败，未改动任何文件：%d/%d 个路径不存在\n%s"
            % (len(missing), len(paths),
               "\n".join("  %s" % m for m in missing[:10]))
        )
    return existing, missing


def remove_files(paths, missing_ok=False, dry_run=False):
    """批量删文件：预检全量 → 逐个删 → 回读确认消失。

    返回 `{"removed": [...], "bytes": n}`；`dry_run=True` 时返回
    `{"would_remove": [...], "bytes": 0}` 不动盘。

    有删除失败或回读仍存在 ⇒ 抛 `OSError`，**不返回成功**。
    """
    existing, _ = stat_all(paths, missing_ok=missing_ok)
    if dry_run:
        return {"would_remove": list(existing), "bytes": 0}

    removed, size, failed = [], 0, []
    for p in existing:
        try:
            sz = os.path.getsize(p)
            os.remove(p)
            size += sz
            removed.append(p)
        except OSError as exc:
            failed.append((p, str(exc)))

    left = [p for p in removed if os.path.exists(p)]
    if failed or left:
        detail = ["%s: %s" % (p, e) for p, e in failed]
        detail += ["%s: 删除后仍存在" % p for p in left]
        raise OSError(
            "删除未完成：%d 个失败 / %d 个残留（已删 %d 个）\n%s"
            % (len(failed), len(left), len(removed),
               "\n".join("  " + d for d in detail[:10]))
        )
    return {"removed": removed, "bytes": size}


def move_files(pairs, missing_ok=False, dry_run=False):
    """批量移动 `[(src, dst), ...]`：预检 src 全在 → 移动 → 回读两端。

    回读判据是「dst 存在且 src 消失」，只信 `shutil.move` 不抛异常不够——
    跨盘移动中途失败会留下半个目标文件。
    """
    import shutil

    srcs = [os.fspath(s) for s, _ in pairs]
    existing, _ = stat_all(srcs, missing_ok=missing_ok)
    keep = set(existing)
    todo = [(os.fspath(s), os.fspath(d)) for s, d in pairs if os.fspath(s) in keep]
    check_all([d for _, d in todo], "写入")

    if dry_run:
        return {"would_move": todo}

    done, failed = [], []
    for s, d in todo:
        try:
            parent = os.path.dirname(d)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.move(s, d)
            done.append((s, d))
        except OSError as exc:
            failed.append((s, d, str(exc)))

    bad = [(s, d) for s, d in done if not os.path.exists(d) or os.path.exists(s)]
    if failed or bad:
        detail = ["%s → %s: %s" % (s, d, e) for s, d, e in failed]
        detail += ["%s → %s: 回读不符（目标缺失或源仍在）" % (s, d) for s, d in bad]
        raise OSError(
            "移动未完成：%d 个失败 / %d 个回读不符（已移 %d 个）\n%s"
            % (len(failed), len(bad), len(done),
               "\n".join("  " + x for x in detail[:10]))
        )
    return {"moved": done}
