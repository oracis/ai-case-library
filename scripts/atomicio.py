#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子读写盘 + 进程级文件锁。

本模块只管两件事：

1. **原子写**：先写同目录临时文件，`os.replace` 一步换上去。
   要么是旧的完整文件，要么是新的完整文件，不存在中间态。
   避免 Ctrl-C / 断电 / 进程被杀留下半截 JSON 把整本台账搞挂。

2. **进程级文件锁**：多个脚本并发改同一个 `data/*.json` 时，
   读 - 改 - 写整个过程持有锁，防止后写者静默覆盖先写者的改动
   （这是 `cases.json` / `publish_manifest.json` 等核心台账历史上反复出事故的原因）。

**为什么不直接上数据库。** 仓库原则「零第三方依赖，只用 Python 标准库」。
SQLite 虽然好，但要把全部读写逻辑重写、现有脚本逐个迁移，是第二步的事。
本模块是第一步：在 JSON 现状下，先把「读安全、写原子、并发不串扰」钉死。

换行/缩进/末尾换行这些**格式**问题不归这里管（各脚本自己按原文件探测）。
写错格式是 diff 噪音，写坏内容是数据事故，两件事分开治。

仓库里此前有 20 处各写各的 `tmp + os.replace`（`harvest.save_json`、
`ledger._write_file`、`multiplatform.state.Manifest.save`、
`bilibili_publish` 的清单、`fill_case_text.save_like` …），新代码统一走这里，
别再抄第五遍。

用法::

    import atomicio

    # 原子读
    obj = atomicio.read_json(path)                 # 文件不存在抛 FileNotFoundError
    obj = atomicio.read_json(path, default={})     # 文件不存在返回 {}

    # 原子写
    atomicio.write_text(path, text)                # 文本原样落盘
    atomicio.write_json(path, obj, indent=2)       # 顺手序列化

    # 读 - 改 - 写原子事务（持有文件锁，并发安全）
    atomicio.update_json(path, lambda cases: [c for c in cases if c["id"] != "x"])

    # 显式加锁（自己管理粒度更细的读改写）
    with atomicio.file_lock(path + ".lock"):
        obj = atomicio.read_json(path)
        obj["foo"] = "bar"
        atomicio.write_json(path, obj)
"""

import contextlib
import json
import os
import sys
import time


# ---------------------------------------------------------------------------
# 文件级进程锁（跨平台：Windows 用 msvcrt，其它用 fcntl）
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def file_lock(lock_path, timeout=30.0):
    """进程级互斥锁。锁粒度由调用方决定，通常锁 `data/xxx.json.lock`。

    Windows 下用 `msvcrt.locking` 锁定 1 字节；Linux/macOS 下用 `fcntl.flock`。
    超时未抢到锁抛 `TimeoutError`。退出 `with` 块自动释放并删除锁文件。
    """
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    deadline = time.time() + float(timeout)
    fh = None
    while True:
        try:
            fh = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (OSError, IOError):
            if fh is not None:
                try:
                    os.close(fh)
                except OSError:
                    pass
                fh = None
            if time.time() >= deadline:
                raise TimeoutError("获取文件锁超时: %s" % lock_path)
            time.sleep(0.05)

    try:
        yield
    finally:
        if fh is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fh, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                try:
                    os.close(fh)
                except OSError:
                    pass
                try:
                    os.remove(lock_path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# 原子读
# ---------------------------------------------------------------------------

def detect_newline(path, default="\n"):
    """读文件头几百字节判断换行风格；文件不存在/没线索时返回 default。

    `data/` 下两种换行**并存**（cases/inbox_archive/candidates 是 LF，inbox 是 CRLF）。
    用文本模式 `open(path, "w")` 在 Windows 上会把 `\n` 译成 `\r\n`，
    一次写入就把整个文件的行尾换掉，diff 全红但数据一个字没变。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return default
    if b"\r\n" in head:
        return "\r\n"
    if b"\n" in head:
        return "\n"
    return default


def read_text(path, default=None, encoding="utf-8"):
    """安全读文本。文件不存在时返回 default（不传则抛 FileNotFoundError）。"""
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def read_json(path, default=None, encoding="utf-8"):
    """安全读 JSON。文件不存在/解析失败时返回 default（不传则抛原异常）。

    注意：只负责读，不加锁。如需并发安全，外部套 `file_lock()` 或用
    `update_json()` / `update_text()`。
    """
    try:
        text = read_text(path, default=None, encoding=encoding)
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        if default is not None:
            return default
        raise


# ---------------------------------------------------------------------------
# 原子写（原有功能保留）
# ---------------------------------------------------------------------------

def write_text(path, text, newline=""):
    """原子写文本。

    `newline` 语义同 `open()`：默认 `""` = 不做换行翻译（JSON 里的 `\n`
    原样落盘，Windows 上也不会被翻成 `\r\n`）。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_bytes(path, data):
    """原子写字节（不做任何编码/换行处理）。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_json(path, obj, indent=2, sort_keys=False, newline="",
               trailing_newline=False):
    """原子写 JSON（`ensure_ascii=False`，中文可读）。

    `trailing_newline=True` 时在 JSON 文本末尾补一个 `\n`，仍在
    同一次 `os.replace` 里原子落盘，不破坏原子性。
    """
    text = json.dumps(obj, ensure_ascii=False, indent=indent,
                      sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    return write_text(path, text, newline=newline)


# ---------------------------------------------------------------------------
# 读 - 改 - 写原子事务（并发安全，推荐优先使用）
# ---------------------------------------------------------------------------

def update_json(path, transformer, indent=2, sort_keys=False, newline="",
                lock_timeout=30.0, default=None):
    """对 `path` 执行「读 - 转换 - 原子写回」，全程持有文件锁。

    参数：
        transformer: 接收当前解析后的对象，返回新对象。抛出异常则不写盘。
        default: 文件不存在时传给 `read_json` 作为初始值。
        lock_timeout: 获取文件锁的最长等待秒数。

    返回：写回后的新对象。
    """
    lock_path = path + ".lock"
    with file_lock(lock_path, timeout=lock_timeout):
        obj = read_json(path, default=default)
        new_obj = transformer(obj)
        write_json(path, new_obj, indent=indent, sort_keys=sort_keys,
                   newline=newline)
        return new_obj


def update_text(path, transformer, newline="", lock_timeout=30.0,
                default=None):
    """对 `path` 执行「读 - 转换 - 原子写回」的文本版本，全程持有文件锁。"""
    lock_path = path + ".lock"
    with file_lock(lock_path, timeout=lock_timeout):
        text = read_text(path, default=default)
        new_text = transformer(text)
        write_text(path, new_text, newline=newline)
        return new_text
