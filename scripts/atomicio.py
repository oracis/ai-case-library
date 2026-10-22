#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子写盘：先写同目录临时文件，`os.replace` 一步换上去。

**为什么要有这一层。** 写 `data/*.json` 时被 Ctrl-C、断连、休眠打断，原地
截断写会留下**半截 JSON** —— 整本台账/候选池当场失效；更坏的是被上层
`except: pass` 吞掉当成空库（2026-10-21 实测：写坏的 cases.json 被当空集合，
重复检测全失效，等于把整个库重新灌一遍）。本模块只管一件事：
**要么是旧的完整文件，要么是新的完整文件，不存在中间态。**

换行/缩进/末尾换行这些**格式**问题不归这里管 —— 各脚本自己按原文件探测
（见 docs/CODE_STYLE.md §3.3，范式 `ledger._read_file` 与
`contentpack_ready.detect_fmt`）。写错格式是 diff 噪音，写坏内容是数据事故，
两件事分开治。

仓库里此前有 20 处各写各的 `tmp + os.replace`（`harvest.save_json`、
`ledger._write_file`、`multiplatform.state.Manifest.save`、
`bilibili_publish` 的清单、`fill_case_text.save_like` …），新代码统一走这里，
别再抄第五遍。

用法::

    import atomicio

    atomicio.write_text(path, text)                  # 文本原样落盘（\\\\n 不翻译）
    atomicio.write_json(path, obj, indent=2)         # 顺手序列化
"""

import json
import os


def detect_newline(path, default="\n"):
    """读文件头几百字节判断换行风格；文件不存在/没线索时返回 default。

    🚨 为什么写盘前必须先问这个：`data/` 下两种换行**并存**（实测
    cases/inbox_archive/candidates 是 LF，inbox 是 CRLF）。用文本模式
    `open(path, "w")` 在 Windows 上会把 `\\n` 译成 `\\r\\n` —— 一次写入就把
    整个文件的行尾换掉，diff 全红但**数据一个字没变**，看着像被搞乱了。
    （2026-10-10 实测：跑一次 harvest 就把 623 条的 inbox_archive.json 从
    LF 改成 CRLF，13286 行全变。）

    只看开头几百字节就够：JSON 前几十行必然有换行，不会碰上「整个文件挤在
    一行」这种极端情况；而读全文在大文件上纯属浪费。
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


def write_text(path, text, newline=""):
    """原子写文本。

    `newline` 语义同 `open()`：默认 `""` = 不做换行翻译（JSON 里的 `\\n`
    原样落盘，Windows 上也不会被翻成 `\\r\\n`）。
    """
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())     # 断电也不留残片；进程被杀由 replace 兜底
        os.replace(tmp, path)
    except BaseException:
        # 失败要把半截临时文件收掉，否则下次 detect_fmt 之类会读到它
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_bytes(path, data):
    """原子写字节（不做任何编码/换行处理）。

    给「原样还原」用：备份文件拷回、抓下来的原始响应落盘这类场景，
    文本层任何加工都是污染。
    """
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


def write_json(path, obj, indent=2, sort_keys=False, newline=""):
    """原子写 JSON（`ensure_ascii=False`，中文可读）。"""
    return write_text(path,
                      json.dumps(obj, ensure_ascii=False, indent=indent,
                                 sort_keys=sort_keys),
                      newline=newline)
