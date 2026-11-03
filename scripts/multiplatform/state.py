#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布清单：状态机 + 幂等 + 断点续传。

**为什么必须自己写这一层。** 参考的 CSDN 那套多平台发布示例把结果记在内存
数组里，`pending` 也算成功、没有重试、进程一挂全丢 —— 批量发 37 条时必然
中途断，而「到底哪几条发出去了」只能靠人肉翻草稿箱。本项目此前正是这个
状态：头条 37 条记录、草稿箱只有 35 条，kibu / pieter-levels 去哪了说不清。

所以这里的清单是**落盘**的，每条每平台一个状态，规则：

| 状态 | 含义 | 默认是否跳过 |
|---|---|---|
| `pending` | 还没发 | 否 |
| `building` | 正在生成平台稿 | 否（视为未完成，会重试） |
| `draft_saved` | 远端草稿箱已确认有它 | 是 |
| `published` | 已公开发布 | 是 |
| `failed` | 上次失败，带 attempt/error | 是（要 --retry 才重试） |
| `blocked` | 平台拦/封，改一版仍不过 | 是（终止态，需人工 reset 才重试） |

`blocked` 是 **2026-10-28 用户定的规则**落点：某平台把稿子拦下来，按
改进意见改一版仍然无效时，就该**顺延发下一篇补上**、并把被拦的稿从该
平台草稿箱剔除。它不能复用 `failed` —— `failed` 会被 `--retry` 自动重
捡，被拦的稿重捡一次还是被拦，只会把队列拖死。处置流程见
`multiplatform/blocked.py`。

写盘用「临时文件 + os.replace」原子替换：批量跑到第 20 条被 Ctrl-C 时，
前 19 条的状态不会因为半个 JSON 而全丢。
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")

if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db  # noqa: E402  SQLite 统一台账：Manifest.load/save 切到 platform_posts 表

STATES = ("pending", "building", "draft_saved", "published", "failed",
          "blocked", "deleted")
# `deleted`：平台侧文章已被删（不可恢复），台账置此态，**不自动重发**。
# 2026-11-03 归位：以前它是 JSON 里的合法值却不在状态机里，`is_done` 认不出，
# 差点把 magicslides 的今日头条稿重新发一遍。
DONE_STATES = ("draft_saved", "published", "deleted")
# 终态里「不会再自动进队列」的那些：failed 要 --retry 才回队，blocked 同理
# 但必须人工 reset —— 见模块头注释。
TERMINAL_STATES = ("published", "blocked", "deleted")


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


# `platform_posts` 里的 state 是各平台脚本的原生叫法（draft / auditing /
# 审核中 …），Manifest 的状态机只有 STATES 那几个值。进出 db 时做一次映射，
# 免得两套词汇混在一张表里：
#   draft / auditing / 审核中  → draft_saved（草稿已进箱、或已送审 ⇒ 不会再发）
_DB_TO_MANIFEST = {
    "draft": "draft_saved",
    "draft_saved": "draft_saved",
    "auditing": "draft_saved",
    "审核中": "draft_saved",
    "published": "published",
    "pending": "pending",
    "building": "building",
    "failed": "failed",
    "blocked": "blocked",
    "deleted": "deleted",
}
# 反向：Manifest 写回 db 时用平台原生词汇（draft_saved → draft）
_MANIFEST_TO_DB = {
    "pending": "pending",
    "building": "building",
    "draft_saved": "draft",
    "published": "published",
    "failed": "failed",
    "blocked": "blocked",
    "deleted": "deleted",
}


def _norm_state(raw):
    """db 里的 state → Manifest 状态机取值。未知值原样返回（不静默吞）。"""
    s = (raw or "").strip()
    return _DB_TO_MANIFEST.get(s, s or "pending")


class Manifest:
    """{cid: {platform: {state, at, title, error, attempt}}}"""

    def __init__(self, path=MANIFEST):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        if self.path == MANIFEST:
            # 从 SQLite ledger 读（platform_posts 全平台），转成 Manifest 格式
            conn = db.get_conn()
            try:
                cur = conn.execute("SELECT * FROM platform_posts ORDER BY updated_at DESC")
                rows = cur.fetchall()
                if rows:
                    data = {}
                    for r in rows:
                        cid = r["case_id"]
                        plat = r["platform"]
                        rec = {
                            "state": _norm_state(r["state"]),
                            "at": r["updated_at"] or r["published_at"] or "",
                            "title": r["title"] or "",
                            "attempt": r["attempt"] or 0,
                        }
                        if r["note"]:
                            rec["error"] = r["note"]
                        data.setdefault(cid, {})[plat] = rec
                    self.data = data
                    return self
            except Exception:  # noqa: BLE001
                pass
            finally:
                db.close_conn()
        # fallback：从 JSON 读（测试用临时文件或迁移期兼容）
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.data = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        return self

    def save(self):
        """JSON 快照落盘（原子替换）。

        ⚠ 2026-11-03 起真值在 SQLite（`platform_posts` 表）：`set()` / `remove()`
        会直接把**单条**记录写进 db，这里的 `save()` 只负责维护
        `publish_manifest.json` 快照，供仍直接读 JSON 的旧脚本兼容。
        不做「整份 dump 回 db」——那是 O(全表) 写，每 `set()` 一次就全表重刷，
        433 条案例撑不住。
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1,
                      sort_keys=True)
        os.replace(tmp, self.path)

    # ---- SQLite 单条落盘（真值） -------------------------------------------
    def _db_write(self, cid, plat, rec):
        """把单条 (cid, plat) 记录写进 `platform_posts`。仅对默认清单生效。

        `path` 被换成临时文件（测试注入）时不碰真库。
        """
        if self.path != MANIFEST:
            return
        conn = db.get_conn()
        try:
            try:
                db.upsert_post(conn, cid, plat, {
                    "state": _MANIFEST_TO_DB.get(rec.get("state") or "pending",
                                                 rec.get("state") or "pending"),
                    "remote_id": rec.get("remote_id") or "",
                    "url": rec.get("url") or "",
                    "title": rec.get("title") or "",
                    "draft_id": rec.get("draft_id") or "",
                    "attempt": int(rec.get("attempt") or 0),
                    "published_at": rec.get("at") or "",
                    "updated_at": rec.get("at") or "",
                    "note": rec.get("error") or rec.get("note") or "",
                })
                conn.commit()
            except Exception as e:                     # noqa: BLE001
                # db 写失败不能吞掉发布流程：JSON 快照已经落了，至少还能续跑
                sys.stderr.write("⚠ manifest 写 db 失败(%s/%s)：%s\n"
                                 % (cid, plat, e))
        finally:
            db.close_conn()

    def _db_remove(self, cid, plat=None):
        if self.path != MANIFEST:
            return
        conn = db.get_conn()
        try:
            if plat:
                conn.execute(
                    "DELETE FROM platform_posts WHERE case_id=? AND platform=?",
                    (cid, plat))
            else:
                conn.execute("DELETE FROM platform_posts WHERE case_id=?",
                             (cid,))
            conn.commit()
        except Exception as e:                         # noqa: BLE001
            sys.stderr.write("⚠ manifest 删 db 失败(%s/%s)：%s\n"
                             % (cid, plat or "*", e))
        finally:
            db.close_conn()

    # ---- 读 ---------------------------------------------------------------
    def get(self, cid, plat):
        return (self.data.get(cid) or {}).get(plat) or {}

    def state(self, cid, plat):
        return self.get(cid, plat).get("state", "pending")

    def is_done(self, cid, plat):
        return self.state(cid, plat) in DONE_STATES

    def is_published(self, cid, plat):
        return self.state(cid, plat) == "published"

    def is_blocked(self, cid, plat):
        return self.state(cid, plat) == "blocked"

    def blocked_reason(self, cid, plat):
        return self.get(cid, plat).get("error", "")

    def attempts(self, cid, plat):
        return int(self.get(cid, plat).get("attempt", 0) or 0)

    def title_of(self, cid, plat):
        return self.get(cid, plat).get("title", "")

    def error_of(self, cid, plat):
        return self.get(cid, plat).get("error", "")

    # ---- 写 ---------------------------------------------------------------
    def set(self, cid, plat, state, title="", error="", bump=False):
        if state not in STATES:
            raise ValueError("未知状态：%s" % state)
        rec = self.data.setdefault(cid, {})
        old = rec.get(plat) or {}
        rec[plat] = {
            "state": state,
            "at": _now(),
            "title": title or old.get("title", ""),
            "attempt": (int(old.get("attempt", 0) or 0) + 1)
            if bump else int(old.get("attempt", 0) or 0),
        }
        if error:
            rec[plat]["error"] = error[:300]
        elif state not in ("failed", "blocked"):
            rec[plat].pop("error", None)
        self.save()
        self._db_write(cid, plat, rec[plat])
        return rec[plat]

    def mark_pending(self, cid, plat, title=""):
        return self.set(cid, plat, "pending", title)

    def mark_building(self, cid, plat, title="", bump=False):
        return self.set(cid, plat, "building", title, bump=bump)

    def mark_draft(self, cid, plat, title=""):
        return self.set(cid, plat, "draft_saved", title)

    def mark_published(self, cid, plat, title=""):
        return self.set(cid, plat, "published", title)

    def mark_failed(self, cid, plat, error="", title=""):
        return self.set(cid, plat, "failed", title, error=error, bump=True)

    def mark_blocked(self, cid, plat, reason="", title=""):
        """被平台拦/封（改一版仍不过）→ 终止态，不再进队列。

        `reason` 必须写清平台给的原话，否则几个月后没人说得清这条为什么
        停在 blocked。
        """
        return self.set(cid, plat, "blocked", title, error=reason, bump=True)

    def remove(self, cid, plat=None):
        """--reset 用：清掉一条（或一条的全部平台）记录，强制重发。"""
        if plat:
            (self.data.get(cid) or {}).pop(plat, None)
            if not self.data.get(cid):
                self.data.pop(cid, None)
        else:
            self.data.pop(cid, None)
        self.save()
        self._db_remove(cid, plat)

    # ---- 统计 -------------------------------------------------------------
    def cids(self):
        return sorted(self.data)

    def summary(self, plats):
        out = {}
        for p in plats:
            cnt = {s: 0 for s in STATES}
            for cid in self.data:
                cnt[self.state(cid, p)] = cnt.get(self.state(cid, p), 0) + 1
            out[p] = cnt
        return out


# ---- 旧状态文件导入 -------------------------------------------------------
def import_legacy(plat, ids, as_state="draft_saved", path=None):
    """把 data/*.json 里已有的记录灌进清单，让幂等对存量生效。

    没有这一步，清单是空的 → 所有 37 条都会被当成没发过 → 一次 `--all`
    就会把整个库重新灌一遍草稿箱。

    `path` 供测试注入临时文件；默认写真清单。
    """
    m = Manifest(path) if path else Manifest()
    added = 0
    for cid in sorted(ids or ()):
        if m.is_done(cid, plat):
            continue
        m.set(cid, plat, as_state, title=m.title_of(cid, plat))
        added += 1
    return added
