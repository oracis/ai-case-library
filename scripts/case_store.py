#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""案例库统一存取层 —— 真值在 SQLite，`data/cases.json` 是自动导出的镜像。

为什么要这层
------------
2026-11-05 起案例库本体从 `data/cases.json` 搬进 `data/ledger.db` 的
`case_library` 表（与发布台账 `platform_posts` 同一个库，一个库一份备份）。
但**报方（脚本）不该关心这件事**：它们要的是一份 `list[dict]`，结构与原来的
`cases.json` 每个条目完全同构。所以读走 `load_cases()`，写走 `save_cases()`。

`cases.json` 为什么还留着
------------------------
它是**镜像**，每次写库后自动重导，继续进 git。这样「数据改动能不能 review」
这件事不丢：`git diff data/cases.json` 照样能一行行看，回滚也只要 checkout
这个文件再 `import_to_db.py`。SQLite 二进制本身没法 diff，这是唯一的解法。
（导出是逐字节还原：键序按 `field_order` 列还原、不补末尾换行、剥掉库内记账列，
 所以没改动的行在 diff 里一个字节都不会动。）

用法
----
    import case_store as CS
    cases = CS.load_cases()            # list[dict]，rowid 序（= 原 JSON 数组顺序）
    by_id = CS.load_by_id()            # {id: dict}
    one   = CS.get_case("1lookup")     # dict | None
    CS.save_cases(cases)               # 写库 + 重导镜像（一个事务）
    CS.save_cases(cases, prune=True)   # 顺便软删「列表里已经没有」的 id

测试隔离
--------
`db.get_conn()` 认环境变量 `LEDGER_DB`，所以：
    LEDGER_DB=tmp/test_ledger.db python scripts/test_xxx.py
整条链（含这个模块）落到测试库，真值一个字节都不碰。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import db  # noqa: E402

#: JSON 镜像路径（进 git，供 diff 评审 / 回滚）
_DEFAULT_MIRROR = os.path.join(ROOT, "data", "cases.json")
MIRROR = _DEFAULT_MIRROR


def mirror_path():
    """镜像该写哪儿。

    🚨 **镜像跟着库走**（2026-11-05 踩到）：测试把 `LEDGER_DB` 指到临时库、
    镜像却还指着 `data/cases.json`，于是「测试写临时库」变成「顺手把真镜像
    写脏了」—— 实测多出两条 `id=a/b` 的空壳案例，连带 test_verify_rules
    误报「存量被拦」。所以只要库不是默认库，镜像就落到库文件旁边。
    测试显式改了 `MIRROR` 变量的，以测试为准。
    """
    if MIRROR != _DEFAULT_MIRROR:
        return MIRROR
    if os.environ.get("CASE_MIRROR"):
        return os.environ["CASE_MIRROR"]
    try:
        dbp = os.path.abspath(db.current_path())
    except Exception:                              # noqa: BLE001
        return MIRROR
    if dbp != os.path.abspath(db.DB_PATH):
        return os.path.join(os.path.dirname(dbp), "cases.json")
    return MIRROR


def _readable_mirror():
    """回落读取用哪个镜像：先看「跟着库走」的路径，没有就用版本库里的默认镜像。

    写镜像和读回落是两件事 —— 写要避开真镜像（测试隔离），读要尽量找到
    那份**进 git 的** `data/cases.json`（新克隆环境根本没有本地库）。
    """
    for p in (mirror_path(), _DEFAULT_MIRROR):
        if os.path.isfile(p):
            return p
    return _DEFAULT_MIRROR


def _guard_real_write(what):
    """测试环境不许写**真**库/真镜像 —— 把静默污染变成一声巨响。"""
    if os.environ.get("LEDGER_DB"):
        return
    if any(m == "unittest" for m in sys.modules):
        raise RuntimeError(
            "%s 正在往真库 data/ledger.db 写数据，而当前进程跑在 unittest 里。\n"
            "  测试请设 LEDGER_DB=<临时库>，否则会污染真值。" % what)

#: 库内记账列：不进镜像
_INTERNAL = {"created_at", "deleted", "field_order", "rowid", "_rid"}


# ------------------------------------------------------------------ 读

def _decode(row):
    """一行 SQLite → 一个与 cases.json 条目同构的 dict（**按原 JSON 键序**）。

    🚨 顺序不是小事（2026-11-05 实测）：`SELECT *` 给的是**列顺序**，如果直接
    照它建 dict，那么任何「读 → 改 → 写」都会把 `field_order` 冲成列序
    （`upsert_case` 从 dict 的键序推导 field_order），导出时 461 条键全部错位
    —— 跑一次 `score_solo_fit` 就制造了 7748 行 permutation diff，数据一个字没变。
    所以这里必须按库里记的 `field_order` 还原，和 `export_cases_to_json` 同一套规则。
    """
    order = []
    raw = row.get("field_order")
    if raw:
        try:
            order = [k for k in json.loads(raw) if k in row]
        except (json.JSONDecodeError, TypeError, ValueError):
            order = []
    keys = order + [k for k in row if k not in order]
    out = {}
    for k in keys:
        v = row[k]
        if k in _INTERNAL:
            continue
        if v is None:
            continue
        out[k] = db.json_field(v) if k in db.CASE_JSON_FIELDS else v
    return out


def _from_json(path, only=None):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
    if not isinstance(cases, list):
        raise ValueError("%s 结构异常：拿不到案例列表" % path)
    if only:
        cases = [c for c in cases if c.get("id") == only]
    return cases


def _rel(path):
    """相对路径（跨盘符时 relpath 会抛 ValueError，退回绝对路径）。"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def load_cases(path=None, only=None):
    """读全部案例（`list[dict]`，rowid 序 = 原 JSON 数组顺序）。

    `path`：只有显式传了一个**非默认**路径时才读文件 —— 测试/离线场景要能
    喂一份临时 JSON。默认走库。
    """
    if path and os.path.abspath(path) != os.path.abspath(MIRROR):
        return _from_json(path, only)

    try:
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT * FROM %s WHERE deleted=0 ORDER BY rowid" % db.CASE_TABLE
        ).fetchall()
    except Exception as exc:                       # noqa: BLE001
        # 库不可用（表没建 / 文件坏）→ 回落读镜像，但要**明说**，
        # 不许让「读了 JSON」看起来像「读了库」。
        p = _readable_mirror()
        sys.stderr.write("[case_store] 读库失败（%s），回落读 %s\n" % (exc, _rel(p)))
        return _from_json(p, only)

    cases = [_decode(dict(r)) for r in rows]
    if not cases:
        p = _readable_mirror()
        sys.stderr.write("[case_store] %s 是空的，回落读镜像 %s\n"
                         "  （跑 scripts/import_to_db.py --cases 重新导入）\n"
                         % (db.CASE_TABLE, _rel(p)))
        return _from_json(p, only)
    if only:
        cases = [c for c in cases if c.get("id") == only]
    return cases


def load_by_id(path=None):
    """`{id: case}` —— 原来 3 个脚本各自拼这个字典，收口到这里。"""
    return {c["id"]: c for c in load_cases(path) if c.get("id")}


def load_ids(path=None):
    """只要 id 列表（有的脚本只为了枚举）。"""
    return [c["id"] for c in load_cases(path) if c.get("id")]


def get_case(case_id):
    """单条，找不到返回 None。"""
    row = db.get_case(db.get_conn(), case_id)
    return _decode(row) if row else None


def count():
    """库里的案例数（不含软删）。"""
    return db.get_conn().execute(
        "SELECT COUNT(*) FROM %s WHERE deleted=0" % db.CASE_TABLE).fetchone()[0]


# ------------------------------------------------------------------ 写

def save_cases(cases, prune=False):
    """把一批案例写进库，**然后重导 JSON 镜像**。

    `prune=True`：列表里已经没有的 id 做软删（`deleted=1`，行不真删）。
    整库重写类脚本（scrub_jargon / backfill_* / fill_*）应该传 True，
    否则被删掉的案例会永远留在库里、继续出现在下一次导出里。

    返回写入条数。镜像导出失败会抛异常 —— 宁可让调用方炸，也不要
    「库改了、镜像没改」这种两边不一致的静默状态。
    """
    _guard_real_write("save_cases()")
    conn = db.get_conn()
    ids = set()
    for c in cases:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        db.upsert_case(conn, c)
        ids.add(c["id"])
    if prune:
        for (cid,) in conn.execute(
                "SELECT id FROM %s WHERE deleted=0" % db.CASE_TABLE).fetchall():
            if cid not in ids:
                db.delete_case(conn, cid)
    conn.commit()
    export_mirror(conn)
    return len(ids)


def save_case(case):
    """单条写入（改一个案例时用它，比整批 save_cases 省事）。"""
    _guard_real_write("save_case()")
    conn = db.get_conn()
    db.upsert_case(conn, case)
    conn.commit()
    export_mirror(conn)
    return case.get("id")


def export_mirror(conn=None):
    """重导 `data/cases.json`。返回导出条数。"""
    conn = conn or db.get_conn()
    n = db.export_cases_to_json(conn, mirror_path())
    if n != conn.execute("SELECT COUNT(*) FROM %s WHERE deleted=0"
                         % db.CASE_TABLE).fetchone()[0]:
        raise RuntimeError("镜像导出条数与库不一致，拒绝留下半截状态")
    return n


# ------------------------------------------------------------------ CLI

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="案例库存取层（真值 SQLite / 镜像 JSON）")
    ap.add_argument("--count", action="store_true", help="库里案例数")
    ap.add_argument("--export", action="store_true", help="重导 data/cases.json 镜像")
    ap.add_argument("--check", action="store_true",
                    help="核对镜像与库是否逐字节一致")
    a = ap.parse_args(argv)

    if a.count:
        print(count())
        return 0
    if a.export:
        print("导出 %d 条 → %s" % (export_mirror(), os.path.relpath(MIRROR, ROOT)))
        return 0
    if a.check:
        conn = db.get_conn()
        tmp = MIRROR + ".check"
        db.export_cases_to_json(conn, tmp)
        a_b = open(MIRROR, "rb").read()
        b_b = open(tmp, "rb").read()
        os.remove(tmp)
        same = a_b == b_b
        print("镜像与库一致：%s（%d bytes）" % ("是" if same else "否", len(a_b)))
        if not same:
            print("  跑 `python scripts/case_store.py --export` 重导")
        return 0 if same else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
