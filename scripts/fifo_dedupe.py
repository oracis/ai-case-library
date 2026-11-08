#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIFO 共用：按 `(case_id, platform)` 剔「已发表案例的旧稿」。

🚨 架构级根因（2026-11-07 两起重复稿 + 历史两起，见 PRD #90）
------------------------------------------------------------------
项目里幂等键是 `(case_id, platform)`，唯一实现是
`publish_guard.check_dupe()`。但 `publish_one.run()` 的 FIFO 分支是
**直接 subprocess 调各平台脚本**，整条绕过 11 闸 ⇒ 四个 FIFO 入口只能
各自认亲，而且用的都是**可变的键**：

  * `wechat_fifo.pick_oldest`      只按标题剔同名（注释自己写了「更早的
                                   同名旧稿剔不掉」）
  * `bilibili_publish.send_draft`  只按 manifest 的 remote_id / title，
                                   而 manifest 的 title 是建稿时的镜像
                                   （#83 口径），二号箱内 8 条 11-06 全换过
  * `xhs_publish.pick_draft`       11-07 已改成 case 级（本模块是它的共用版）
  * `bilibili2`                    同 bilibili，账簿还在游戏仓

标题会改（`divine-widgets` 11-02 改过）、id 有两套（`ep-*` / `ext-ep-*`）、
台账会滞后 ⇒ 只要认不出，FIFO 就会把「旧标题的已发稿」再发一遍。
历史：10-18 prosp 12:09/22:14 两篇、11-04 重复建稿、11-07 Freedoom
（公众号）、11-07 Divine（小红书，发后删除）。

本模块只做三件事，**不碰平台侧、不改标题、不写盘**：

  1. `published_case_ids()`：已发案例集合 = SQLite 真值 ∪ 各平台 JSON 快照。
     db 在迁移期常不全（实测 xhs 8 条 vs JSON 32 条、wechat 40 vs JSON 47），
     只认 db 就会漏；只认 JSON 又会漏 db-only 的新记录 ⇒ 必须取并集。
     归一走 `publish_guard.norm_id`（`ext-ep-001-x → ep-001-x`），堵两套 id。
  2. `filter_rows()`：认到 case_id 且已发 ⇒ 跳过；**认不出的孤儿保留**
     （否则箱里那批孤儿永远发不出去，见 #88）。
  3. 只拦 `published`，不拦 `draft` —— FIFO 发的本来就是箱里的草稿。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import publish_guard as PG  # noqa: E402  复用 norm_id / _ro_conn，不另造口径

# 与 publish_guard.PUBLISHED_STATES 同口径（含审核通过即公开的写法）
PUBLISHED_STATES = frozenset({
    "published", "publish", "sent", "success", "audited", "auditing_passed",
})

DATA = os.path.join(ROOT, "data")


def norm(cid):
    """`ext-ep-001-x → ep-001-x`；空值返回空串。"""
    return PG.norm_id(cid)


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _ids_from_rows(data):
    """快照既可能是 list[{id}]，也可能是 dict[cid]=记录。"""
    out = set()
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and r.get("id"):
                out.add(norm(r["id"]))
    elif isinstance(data, dict):
        for k, v in data.items():
            if str(k).startswith("_"):
                continue
            # dict 值带状态字段时只收已发表的；纯 dict 快照视为全已发
            if isinstance(v, dict) and v.get("status"):
                if str(v["status"]).strip().lower() in PUBLISHED_STATES:
                    out.add(norm(k))
            else:
                out.add(norm(k))
    return {x for x in out if x}


def _db_ids(platform, db_path=None):
    """SQLite 真值。**只读**连接：不许为了「读」去建表（#82 红线）。"""
    conn = PG._ro_conn(db_path)
    if conn is None:
        return set()
    try:
        rows = conn.execute(
            "SELECT case_id, state, remote_id FROM platform_posts "
            "WHERE platform=?", (platform,)).fetchall()
    except Exception:                                       # noqa: BLE001
        return set()
    finally:
        conn.close()
    out = set()
    for r in rows:
        state = str(r["state"] or "").strip().lower()
        # 🚨 不许用 `or remote_id` 判已发！`publish_guard.check_dupe()` 那套
        #    是给**新写**路径防重复建稿用的；FIFO 发的就是箱里的草稿，
        #    草稿记录同样带 remote_id（公众号存的是草稿 appmsgid）。
        #    2026-11-07 实跑：加了 `or remote_id` 会把公众号草稿箱里
        #    36 条 status=draft、远端已发表列表无同名的**待发稿**全误杀
        #    （可发候选 49→12）。逐条核三证才抓出来 ⇒ 这里只认状态。
        if state in PUBLISHED_STATES:
            out.add(norm(r["case_id"]))
    return {x for x in out if x}


def _manifest_ids(platform, data_dir):
    """`publish_manifest.json`：cid → {plat: {state}}。状态滞后时以 db 为准。"""
    d = _load(os.path.join(data_dir, "publish_manifest.json")) or {}
    out = set()
    for cid, per in (d.items() if isinstance(d, dict) else []):
        rec = (per or {}).get(platform) or {}
        if str(rec.get("state") or "").strip().lower() in PUBLISHED_STATES:
            out.add(norm(cid))
    return out


def _snapshot_ids(platform, data_dir):
    """各平台 JSON 快照（迁移期 db 不全时的兜底）。"""
    out = set(_manifest_ids(platform, data_dir))
    files = {
        "xhs": ("xhs_published.json",),
        "wechat": ("wechat_published.json", "wechat_standalone.json"),
        "bilibili": ("bili_published.json",),
        # ⚠ 别把 longtail_sync.json / yun_finalize.json 塞进来：前者是
        #   `<case>-<日期>-<序号>` 批次键、后者键虽是 case 但值里存的是
        #   tc_state/al_state 且含未发表草稿 ⇒ 会把没发过的稿全误杀。
        #   这两个平台也不在 FIFO_PLATFORMS，用 manifest 一路就够。
    }.get(platform, ())
    for name in files:
        out |= _ids_from_rows(_load(os.path.join(data_dir, name)))
    return {x for x in out if x}


def published_case_ids(platform, extra=(), db_path=None, data_dir=None):
    """已发表案例 id 的**并集**。extra 供调用方补自己平台的旁证。

    db_path / data_dir 可注入，单测不必碰真库真台账。
    """
    data_dir = data_dir or DATA
    ids = _db_ids(platform, db_path) | _snapshot_ids(platform, data_dir)
    ids |= {norm(x) for x in (extra or ()) if x}
    return {x for x in ids if x}


def case_is_published(platform, cid, **kw):
    """单个 case 是否已在该平台发表。认不出 case 返回 False（不拦孤儿）。"""
    cid = norm(cid)
    return bool(cid) and cid in published_case_ids(platform, **kw)


def filter_rows(rows, platform, case_of, case_ids=None, extra=(),
                label="FIFO", verbose=True):
    """剔掉「已发表案例的旧稿」。返回 (kept, skipped)。

    rows:     任意可迭代对象（草稿 dict / (ts, aid, item) 元组都行）
    case_of:  callable(row) -> case_id 或 ""；由各入口用**自己的稳定键**认亲
              （公众号用 appmsgid→台账、B站用产物 h1 / 游戏仓期次、
                小红书用 out/xhs/<id>/note.json）
    case_ids: 已算好的集合，避免同一轮重复读盘；None 时现算
    """
    pub = case_ids if case_ids is not None else published_case_ids(
        platform, extra=extra)
    kept, skipped = [], []
    for r in rows:
        cid = norm(case_of(r))
        if cid and cid in pub:
            skipped.append((cid, r))
        else:
            kept.append(r)
    if verbose and skipped:
        print("[%s] 跳过 %d 条已发表案例的旧稿：%s"
              % (label, len(skipped), "、".join(c for c, _ in skipped[:4])))
    return kept, skipped
