#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选池统一存取层 —— `data/candidates.json` 的唯一读写入口。

为什么要这层（2026-11-06）
-------------------------
收口前全仓有约 50 处各自 `open()` / `json.dump()` 直接读写候选池，
每处自己判断缩进、换行、末尾换行、原子性。结果同一个文件被写成了
**三种互不兼容的形态**，而且没人能一次改对：

  * `apply_draft_text.py` 写死 `.replace("\\n", "\\r\\n")` —— 磁盘其实是 LF，
    跑一次就把整本翻成 CRLF；它的「字节级自检」比的是自己刚写的输出，永远通过。
  * `verify_case.py` 用文本模式 `open(..., "w")` + `f.write("\\n")` —— Windows
    上换行被翻译，还凭空补了末尾换行（磁盘本来没有）。
  * `sync_headline_numbers.py` / `audit_delisted.py` 是原地截断写，没有 fsync
    和 `os.replace`，被中断就留半截 JSON；半截 JSON 会被上层当空池继续跑。
  * 其余脚本各自写一份 `_write_like` / `save_like` / `detect_fmt`。

于是「改格式」这件事得改 50 处，改漏一处就又分裂一次；将来候选池真要
迁 SQLite（或加字段校验、审计日志），更是无从下手。现在统一到三个接口，
**报方只管拿 list[dict]、交回 list[dict]**，格式/原子性/并发锁都归这里。

格式口径
--------
不写死。每次写盘前按**文件现有字节**探测 `(indent, newline, trailing_newline)`，
枚举 indent∈{1,2,4} × 末尾换行∈{有,无} 找逐字节匹配的那一种，再按它写回
（算法来自 `contentpack_ready.detect_fmt`，2026-10-10 那次全量 diff 事故的产物）。
2026-11-06 实测磁盘是 **LF + indent=2 + 无末尾换行**；仓库里若干注释写 CRLF
是历史漂移，不要照注释改代码，跑 `--check` 看磁盘。

原子性由 `atomicio.write_text()` 保证（tmp + fsync + os.replace）。
`upsert_candidate()` 额外用 `atomicio.file_lock()` 把「读-改-写」整段包住，
避免两个进程并发改同一条时后写者静默覆盖先写者。

用法
----
    import candidate_store as CSt

    cands = CSt.load_candidates()                 # list[dict]
    by_id = CSt.load_by_id()                      # {id: dict}
    CSt.save_candidates(cands)                    # 整批写回，返回探测到的格式
    CSt.upsert_candidate({"id": "x", ...})        # 单条按 id 插入/合并（并发安全）

测试隔离
--------
所有接口都接受 `path=`，显式传了就**绝不碰默认文件**；也可以替换模块级
`CAND_PATH`（`test_atomicio` / `test_publish_ready` 用的就是这种）。
单测请用临时目录，别拿真台账练手。

⚠️ 边界：本层只管「怎么读怎么写」，**不做事实裁定、不改字段口径**。
`verification` 同名三义（候选池 = 支付侧直读标记）见 `docs/DATA_SCHEMA.md`。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import atomicio  # noqa: E402

_DEFAULT_PATH = os.path.join(ROOT, "data", "candidates.json")
#: 模块级路径，测试可替换；`path=` 参数优先级更高
CAND_PATH = _DEFAULT_PATH

#: 文件不存在时新建用的格式（与 2026-11-06 实测磁盘一致）
DEFAULT_FMT = (2, "\n", False)


def _resolve(path):
    return path or CAND_PATH


def _rel(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:                    # 跨盘符
        return path


def detect_fmt(path=None, default_indent=2):
    """探测现有 JSON 文件的 `(indent, newline, trailing_newline)`。

    不许凭注释写死：仓库里 candidates.json 的换行说明互相矛盾（有写 CRLF
    有写 LF），只有磁盘字节算数。探测不到就回落 `(default_indent, 探测到的
    换行, False)`，并在写盘时保持那个换行。
    """
    path = _resolve(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return default_indent, "\n", False
    nl = "\r\n" if b"\r\n" in raw[:4096] else "\n"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return default_indent, nl, False
    for ind in (1, 2, 4):
        for tail_nl in (True, False):
            body = json.dumps(data, ensure_ascii=False, indent=ind)
            cand = (body + ("\n" if tail_nl else "")).replace("\n", nl)
            if cand.encode("utf-8") == raw:
                return ind, nl, tail_nl
    return default_indent, nl, False


def serialize(cands, fmt=None, path=None):
    """按给定（或探测到的）格式序列化，返回**文本**（未落盘）。"""
    fmt = fmt or detect_fmt(path)
    ind, nl, tail_nl = fmt
    body = json.dumps(cands, ensure_ascii=False, indent=ind)
    return (body + ("\n" if tail_nl else "")).replace("\n", nl)


# ------------------------------------------------------------------ 读

def load_candidates(path=None):
    """读全部候选，`list[dict]`（保序）。

    文件不存在 → `[]`（与 `server.load_json` 既有语义一致）；
    **解析失败必须响亮抛错** —— 半截 JSON 被静默当空池，会让去重、
    triage、发布闸全部误判「这条是新的」，比报错危险得多。
    """
    path = _resolve(path)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("%s 结构异常：候选池必须是列表，实际是 %s"
                         % (_rel(path), type(data).__name__))
    return data


def load_by_id(path=None):
    """`{id: dict}` —— 原来好几个脚本各自拼这个字典。无 id 的条目跳过。"""
    return {c["id"]: c for c in load_candidates(path) if c.get("id")}


def load_ids(path=None):
    return [c["id"] for c in load_candidates(path) if c.get("id")]


def get_candidate(cid, path=None):
    """单条，找不到返回 None。"""
    return next((c for c in load_candidates(path) if c.get("id") == cid), None)


def count(path=None):
    return len(load_candidates(path))


# ------------------------------------------------------------------ 写

def save_candidates(cands, path=None, fmt=None):
    """整批写回，**按文件原生格式**原子落盘。

    返回 `(indent, newline, trailing_newline)` —— `contentpack_ready.cmd_apply`
    会打印它，保持既有返回值不变。

    ⚠️ 语义是「把我手上这份完整列表写进去」，读-改-写整段不在锁内；
    并发场景下请改用 `upsert_candidate()`（单条）或先自行持锁。
    """
    path = _resolve(path)
    fmt = fmt or detect_fmt(path)
    text = serialize(cands, fmt=fmt)
    atomicio.write_text(path, text)
    return fmt


def upsert_candidate(cand, path=None, replace=False, prepend=False):
    """按 `id` 插入/更新一条候选，**读-改-写全程持文件锁**。

    默认字段合并（`dict.update`，不会误删调用方没带的字段）；
    `replace=True` 则整条替换。默认已有条目保持原位置、新条目追加到末尾；
    `prepend=True` 把该条置于列表开头 —— `server.py` 的「新增候选」和
    `verify_case.py` 的插入流程历来是置顶的，前端展示顺序依赖这一点。

    返回 `(cands, added)`，`added=True` 表示是新插入。
    缺 `id` 直接抛 ValueError —— 静默追加一条无 id 的脏数据，
    后面所有按 id 的索引都会错位。
    """
    if not isinstance(cand, dict) or not cand.get("id"):
        raise ValueError("upsert_candidate() 需要带 id 的 dict")
    path = _resolve(path)
    lock_path = path + ".lock"
    with atomicio.file_lock(lock_path):
        cands = load_candidates(path)
        fmt = detect_fmt(path)
        idx = next((i for i, c in enumerate(cands)
                    if c.get("id") == cand["id"]), None)
        added = idx is None
        merged = (dict(cand) if replace or added
                  else {**cands[idx], **cand})
        if idx is not None:
            cands.pop(idx)
        if prepend:
            cands.insert(0, merged)
        elif added:
            cands.append(merged)
        else:
            cands.insert(idx, merged)
        save_candidates(cands, path=path, fmt=fmt)
    return cands, added


def check(path=None):
    """字节级自检：按探测格式重序列化，与磁盘逐字节比。返回 `(ok, 说明)`。"""
    path = _resolve(path)
    if not os.path.isfile(path):
        return False, "文件不存在：%s" % _rel(path)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        cands = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return False, "JSON 解析失败：%s" % exc
    fmt = detect_fmt(path)
    rebuilt = serialize(cands, fmt=fmt).encode("utf-8")
    if rebuilt == raw:
        return True, "格式一致（indent=%d / %s / 末尾换行=%s，%d 条，%d bytes）" % (
            fmt[0], "CRLF" if fmt[1] == "\r\n" else "LF",
            "有" if fmt[2] else "无", len(cands), len(raw))
    return False, "格式不一致：磁盘 %d bytes，按探测格式重建 %d bytes" % (
        len(raw), len(rebuilt))


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="候选池统一存取层（唯一读写入口）")
    ap.add_argument("--path", default="", help="指定候选池文件（默认 data/candidates.json）")
    ap.add_argument("--count", action="store_true", help="条数")
    ap.add_argument("--check", action="store_true", help="格式逐字节自检")
    ap.add_argument("--get", default="", help="按 id 打印一条")
    a = ap.parse_args(argv)
    p = a.path or CAND_PATH

    if a.count:
        print(count(p))
        return 0
    if a.check:
        ok, msg = check(p)
        print("%s：%s" % ("OK" if ok else "FAIL", msg))
        return 0 if ok else 1
    if a.get:
        cand = get_candidate(a.get, p)
        if cand is None:
            print("没找到 %s" % a.get)
            return 1
        print(json.dumps(cand, ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
