#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 统一台账层（第二步）。

设计目标：把 `data/` 下散乱的 JSON 台账并入单文件 SQLite，现有 JSON
保持为导出/备份格式，不立即删除。新代码优先走 `db.py`，老脚本可
逐步迁移。

核心表（三张，覆盖现有全部 JSON 台账）：

1. cases            —— 核心案例库（cases.json / anti_cases.json / graveyard.json
   合并，用 `type` 区分 case/game）。一条 case/game 一条记录。
2. platform_posts   —— 统一发布台账（publish_manifest.json / longtail_sync.json /
   * _published.json / *_drafts.json 合并）。一条 case 在一个平台的一条记录。
3. verifications    —— 核实记录（verifications.json）。一条 case 可有多条核实来源。

所有字段用 TEXT 存，结构不固定的（metrics / sources / corrections …）存 JSON 字符串，
读取时再 `json.loads`。这样既保留 JSON 的灵活性，又获得 SQL 查询、事务、索引。

依赖：仅 Python 标准库 `sqlite3`。
"""

import json
import os
import sqlite3
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "ledger.db")

# 线程本地连接：同一线程内复用连接，跨线程各自独立，避免 check_same_thread 报错。
_local = threading.local()

# 发布状态的「前进序」：数字越大表示越靠后（越不该被回退）。
# `upsert_post` 靠它防止 draft 覆盖 published（2026-11-03 实测 58 条被写反）。
# 未知状态按 2（草稿档）处理，宁可保留也不静默降级。
_STATE_RANK = {
    "pending": 0,
    "building": 1,
    "failed": 1,
    "draft": 2,
    "draft_saved": 2,
    "auditing": 2,
    "审核中": 2,
    "blocked": 3,
    "published": 4,
    "deleted": 5,          # 平台侧已删，不可恢复；不许被 draft/published 冲掉
}


def _db_path():
    """解析 db 文件路径：环境变量 `LEDGER_DB` 可覆盖默认 `data/ledger.db`。

    测试隔离开关：`LEDGER_DB=tmp/test_ledger.db python scripts/test_xxx.py`
    让整条测试链（含 state/ledger 的 db 写出口）全部落到测试库，
    真值 `data/ledger.db` 一个字节都不碰。
    """
    return os.environ.get("LEDGER_DB") or DB_PATH


def current_path():
    """当前生效的 db 路径（供脚本打印/备份用，尊重 LEDGER_DB 覆盖）。"""
    return _db_path()


def get_conn(db_path=None):
    """获取当前线程的 SQLite 连接，首次调用自动建表。

    线程本地连接记住了自己的文件路径：`LEDGER_DB` 在进程中途被改
    （比如某测试 setUp 里切换库）时，旧连接自动作废重开，不会出现
    「半截会话还连着真库」的错位。
    """
    if db_path is None:
        db_path = _db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "conn_path", None) != db_path:
        conn.close()
        conn = None
    if conn is None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")      # 并发读更友好
        conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(conn)
        _local.conn = conn
        _local.conn_path = db_path
    return conn


def close_conn():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.conn_path = None


def _init_schema(conn):
    cur = conn.cursor()

    # ---- 表 1：cases 核心案例库 ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cases (
        id             TEXT PRIMARY KEY,
        type           TEXT NOT NULL DEFAULT 'case',   -- case | game
        name           TEXT,
        name_en        TEXT,
        origin         TEXT,
        one_liner      TEXT,
        category       TEXT,
        industry       TEXT,
        status         TEXT,
        verification   TEXT,
        caliber        TEXT,
        metrics        TEXT,        -- JSON
        models         TEXT,        -- JSON array
        replicability  TEXT,        -- JSON
        verdict        TEXT,
        what_it_does   TEXT,
        how_it_makes_money TEXT,
        why_it_works   TEXT,        -- JSON array
        playbook       TEXT,        -- JSON array
        signals        TEXT,        -- JSON array
        founder        TEXT,
        traffic_sources TEXT,       -- JSON array
        corrections    TEXT,        -- JSON array
        sources        TEXT,        -- JSON array
        published_from TEXT,
        verified_at    TEXT,
        updated_at     TEXT,
        tags           TEXT,        -- JSON array
        tier           TEXT,
        tier_reason    TEXT,
        quality_score  INTEGER,
        created_at     TEXT,
        deleted        INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_type ON cases(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_verification ON cases(verification)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_tier ON cases(tier)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_deleted ON cases(deleted)")

    # ---- 表 2：platform_posts 统一发布台账 ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS platform_posts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id       TEXT NOT NULL,
        platform      TEXT NOT NULL,   -- wechat/toutiao/bilibili/xhs/zhihu/csdn/cnblogs/aliyun/tencentcloud/...
        state         TEXT,            -- draft / published / archived / deleted
        remote_id     TEXT,
        url           TEXT,
        title         TEXT,
        draft_id      TEXT,
        attempt       INTEGER DEFAULT 0,
        published_at  TEXT,
        updated_at    TEXT,
        note          TEXT,
        FOREIGN KEY (case_id) REFERENCES cases(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_case ON platform_posts(case_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_platform ON platform_posts(platform)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_state ON platform_posts(state)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_case_platform ON platform_posts(case_id, platform)")
    # 🚨 (case_id, platform) 必须数据库级唯一（2026-11-04 补）：此前只有普通
    #   索引，去重完全靠 `upsert_post` 的 SELECT→DELETE→INSERT 应用层约定；
    #   多进程/并发建稿时这层约定会漏，而 `longtail_sync.json` 的键是**批次**
    #   （<case>-<日期>-<序号>）不是幂等键 ⇒ 同一 case 同平台重复建稿无痕
    #   （实测 43 条里 12 组重复标题）。与 `verifications` 同款：先去重再建
    #   唯一索引，老库平滑升级；每组保留 id 最大（最新）那条。
    ux = cur.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                     "AND name='ux_posts_case_platform'").fetchone()
    if not ux:
        # 只在首次升级时去重；别让「读台账/出发布计划」这种只读路径
        # 每次连接都跑一条 DELETE（会开写事务、动 WAL）。
        cur.execute("""
            DELETE FROM platform_posts WHERE id NOT IN (
                SELECT MAX(id) FROM platform_posts GROUP BY case_id, platform)
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_case_platform "
                    "ON platform_posts(case_id, platform)")

    # ---- 表 3：verifications 核实记录 ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS verifications (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id       TEXT NOT NULL,
        verification  TEXT,
        caliber       TEXT,
        caliber_reason TEXT,
        source_kinds  TEXT,          -- JSON array
        checked_at    TEXT,
        note          TEXT,
        gates         TEXT,          -- JSON array
        gates_denied  TEXT,          -- JSON array
        musts         TEXT,          -- JSON array
        bonus         TEXT,          -- JSON array
        FOREIGN KEY (case_id) REFERENCES cases(id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ver_case ON verifications(case_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ver_verification ON verifications(verification)")
    # ⚠ case_id 唯一：每案例一条核实记录（2026-11-03 补）。此前无约束 +
    #   upsert_verification 裸 INSERT，重复运行导入脚本会整表翻倍。
    #   先去重（每组 case_id 保留最新一条）再建唯一索引，老库也能平滑升级。
    cur.execute("""
        DELETE FROM verifications WHERE id NOT IN (
            SELECT MAX(id) FROM verifications GROUP BY case_id)
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_ver_case ON verifications(case_id)")

    conn.commit()


# ---------------------------------------------------------------- cases

def _table_columns(conn, table):
    """返回指定表的所有列名集合。"""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


_CASE_COLUMNS = None


def upsert_case(conn, row):
    """插入或更新一条 case。`row` 为 dict，键与 cases 表字段一致，值为标量或需 JSON 序列化的对象。

    只写入表中已存在的列，未知字段被忽略（避免老数据里有 `needs_review`
    这类后来加的字段导致建表失败）。
    """
    global _CASE_COLUMNS
    if _CASE_COLUMNS is None:
        _CASE_COLUMNS = _table_columns(conn, "cases")

    json_fields = ("metrics", "models", "replicability", "why_it_works", "playbook",
                   "signals", "traffic_sources", "corrections", "sources", "tags")
    now = row.get("updated_at") or _now()
    params = {}
    for k, v in row.items():
        if k not in _CASE_COLUMNS:
            continue
        if k in json_fields and v is not None:
            params[k] = json.dumps(v, ensure_ascii=False)
        else:
            params[k] = v
    params["updated_at"] = now
    params.setdefault("created_at", now)
    params.setdefault("type", "case")
    params.setdefault("deleted", 0)

    cols = ", ".join(params.keys())
    placeholders = ", ".join("?" for _ in params)
    updates = ", ".join(f"{k}=excluded.{k}" for k in params.keys() if k != "id")
    sql = f"""
    INSERT INTO cases ({cols}) VALUES ({placeholders})
    ON CONFLICT(id) DO UPDATE SET {updates}
    """
    conn.execute(sql, tuple(params.values()))
    return params["id"]


def get_case(conn, case_id):
    cur = conn.execute("SELECT * FROM cases WHERE id=? AND deleted=0", (case_id,))
    return _row_to_dict(cur.fetchone())


def list_cases(conn, where="", params=(), limit=None, offset=None):
    sql = "SELECT * FROM cases WHERE deleted=0"
    if where:
        sql += " AND " + where
    sql += " ORDER BY updated_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params = params + (limit,)
    if offset is not None:
        sql += " OFFSET ?"
        params = params + (offset,)
    cur = conn.execute(sql, params)
    return [_row_to_dict(r) for r in cur.fetchall()]


def delete_case(conn, case_id):
    """软删除。"""
    conn.execute("UPDATE cases SET deleted=1 WHERE id=?", (case_id,))


# ---------------------------------------------------------------- platform_posts

def upsert_post(conn, case_id, platform, row):
    """插入或更新一条平台发布记录。若 (case_id, platform) 已存在则更新，否则新增。

    若 case_id 不在 cases 表中，自动补一个 stub（仅 id、type），以满足
    外键约束，同时不丢失发布记录。

    🚨 **状态只许前进，不许回退**（2026-11-03 修）：`platform_posts` 是
    (case_id, platform) 唯一，但同一个 platform 有多个台账来源（manifest /
    longtail / *_published / *_drafts）。盲目「后写覆盖」会让 **draft 盖掉
    published** —— 实测 58 条状态被写反，其中 bilibili/toutiao 多篇真发过的
    稿子变成 draft，`publish_slot` 会当成「没发过」重新选进候选池 ⇒ 重磅。
    现在按 `_STATE_RANK` 比较：新状态更靠后就覆盖，更靠前就保留旧状态与
    旧 `published_at`，只补空字段。真要强制改状态，传 `force_state=True`。
    """
    # 确保 case 存在（外键约束）
    existing_case = conn.execute("SELECT id FROM cases WHERE id=?", (case_id,)).fetchone()
    if existing_case is None:
        conn.execute(
            "INSERT INTO cases (id, type, status, updated_at, created_at) VALUES (?, 'case', 'imported_stub', ?, ?)",
            (case_id, _now(), _now()))

    now = row.get("updated_at") or _now()
    row = dict(row)
    force = bool(row.pop("force_state", False))
    row["updated_at"] = now
    row.setdefault("published_at", now)

    # 先查是否存在
    cur = conn.execute(
        "SELECT * FROM platform_posts WHERE case_id=? AND platform=?",
        (case_id, platform))
    existing = cur.fetchone()

    if existing is not None and not force:
        old = dict(existing)
        old_rank = _STATE_RANK.get((old.get("state") or "").strip(), 2)
        new_rank = _STATE_RANK.get((row.get("state") or "").strip(), 2)
        if old_rank > new_rank:
            # 旧状态更靠后（比如已 published、新来的是 draft）⇒ 保留旧状态
            row["state"] = old.get("state")
            row["published_at"] = old.get("published_at") or row.get("published_at")
            row["updated_at"] = old.get("updated_at") or row.get("updated_at")
        # 空字段回填：新数据没带的东西不要冲掉已有的
        for k in ("remote_id", "url", "title", "draft_id", "note"):
            if not row.get(k) and old.get(k):
                row[k] = old[k]
        if not int(row.get("attempt") or 0) and int(old.get("attempt") or 0):
            row["attempt"] = old["attempt"]

    fields = [k for k in row.keys() if k not in ("case_id", "platform")]
    if existing:
        # 🚨 UPDATE 而非 DELETE+INSERT（2026-11-04）：后者会重置 AUTOINCREMENT
        #   id、把没带的字段冲成 NULL，并在唯一索引下留出「删了还没插」的
        #   竞态窗口 —— 两个进程同时建同一篇稿就会各插一条。只更新本次
        #   带来的字段，其余保留旧值，与「空字段回填」那段语义一致。
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE platform_posts SET {sets} WHERE id=?",
            tuple(row.get(k) for k in fields) + (existing["id"],))
    else:
        cols = ["case_id", "platform"] + fields
        vals = [case_id, platform] + [row.get(k) for k in fields]
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO platform_posts ({', '.join(cols)}) VALUES ({placeholders})",
            vals)
    return True


def get_posts_by_case(conn, case_id):
    cur = conn.execute(
        "SELECT * FROM platform_posts WHERE case_id=? ORDER BY updated_at DESC",
        (case_id,))
    return [_row_to_dict(r) for r in cur.fetchall()]


def get_post(conn, case_id, platform):
    """按 case_id + platform 查单条发布记录。找不到返回 None。"""
    cur = conn.execute(
        "SELECT * FROM platform_posts WHERE case_id=? AND platform=? ORDER BY updated_at DESC LIMIT 1",
        (case_id, platform))
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_posts_by_platform(conn, platform, state=None, limit=None):
    sql = "SELECT * FROM platform_posts WHERE platform=?"
    params = [platform]
    if state:
        sql += " AND state=?"
        params.append(state)
    sql += " ORDER BY updated_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    cur = conn.execute(sql, params)
    return [_row_to_dict(r) for r in cur.fetchall()]


def get_drafts_fifo(conn, platform, limit=1):
    """按 updated_at 升序取该平台最旧的 draft（FIFO 草稿箱语义）。"""
    cur = conn.execute(
        "SELECT * FROM platform_posts WHERE platform=? AND state='draft' "
        "ORDER BY updated_at ASC LIMIT ?",
        (platform, limit))
    return [_row_to_dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- verifications

def upsert_verification(conn, case_id, row):
    # 确保 case 存在（外键约束）
    existing_case = conn.execute("SELECT id FROM cases WHERE id=?", (case_id,)).fetchone()
    if existing_case is None:
        conn.execute(
            "INSERT INTO cases (id, type, status, updated_at, created_at) VALUES (?, 'case', 'imported_stub', ?, ?)",
            (case_id, _now(), _now()))

    json_fields = ("source_kinds", "gates", "gates_denied", "musts", "bonus")
    _VER_COLUMNS = _table_columns(conn, "verifications")
    params = {}
    for k, v in row.items():
        if k not in _VER_COLUMNS:
            continue
        if k in json_fields and v is not None:
            params[k] = json.dumps(v, ensure_ascii=False)
        else:
            params[k] = v
    params["case_id"] = case_id
    cols = ", ".join(params.keys())
    placeholders = ", ".join("?" for _ in params)
    updates = ", ".join(f"{k}=excluded.{k}" for k in params if k != "case_id")
    # ⚠ 必须是 upsert 而非裸 INSERT：import_to_db.py 可重复运行（幂等），
    #   纯 INSERT 会让 verifications 每跑一次翻倍（2026-11-03 实撞：
    #   重跑一次 108 → 279）。按 case_id 冲突时整行更新。
    conn.execute(
        f"""INSERT INTO verifications ({cols}) VALUES ({placeholders})
        ON CONFLICT(case_id) DO UPDATE SET {updates}""",
        tuple(params.values()))


def get_verifications_by_case(conn, case_id):
    cur = conn.execute(
        "SELECT * FROM verifications WHERE case_id=? ORDER BY id DESC",
        (case_id,))
    return [_row_to_dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- utils

def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def json_field(value):
    """把读出来的 JSON 字段反序列化；不是字符串或解析失败则原样返回。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def export_cases_to_json(conn, out_path):
    """把 cases 表导出为 JSON 数组（与原 cases.json 结构兼容）。"""
    rows = list_cases(conn)
    out = []
    for r in rows:
        for jf in ("metrics", "models", "replicability", "why_it_works", "playbook",
                   "signals", "traffic_sources", "corrections", "sources", "tags"):
            r[jf] = json_field(r.get(jf))
        out.append(r)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return len(out)


def export_posts_to_json(conn, out_path):
    """把 platform_posts 导出为 publish_manifest 风格的嵌套 dict：{case_id: {platform: {...}}}"""
    cur = conn.execute("SELECT * FROM platform_posts ORDER BY updated_at DESC")
    result = {}
    for r in cur.fetchall():
        d = _row_to_dict(r)
        cid = d.pop("case_id")
        plat = d.pop("platform")
        result.setdefault(cid, {})[plat] = d
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return len(result)


def export_verifications_to_json(conn, out_path):
    cur = conn.execute("SELECT * FROM verifications ORDER BY id DESC")
    result = {}
    for r in cur.fetchall():
        d = _row_to_dict(r)
        cid = d.pop("case_id")
        for jf in ("source_kinds", "gates", "gates_denied", "musts", "bonus"):
            d[jf] = json_field(d.get(jf))
        result[cid] = d
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return len(result)
