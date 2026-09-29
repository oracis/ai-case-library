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

写盘用「临时文件 + os.replace」原子替换：批量跑到第 20 条被 Ctrl-C 时，
前 19 条的状态不会因为半个 JSON 而全丢。
"""

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")

STATES = ("pending", "building", "draft_saved", "published", "failed")
DONE_STATES = ("draft_saved", "published")


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


class Manifest:
    """{cid: {platform: {state, at, title, error, attempt}}}"""

    def __init__(self, path=MANIFEST):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.data = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        return self

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1,
                      sort_keys=True)
        os.replace(tmp, self.path)

    # ---- 读 ---------------------------------------------------------------
    def get(self, cid, plat):
        return (self.data.get(cid) or {}).get(plat) or {}

    def state(self, cid, plat):
        return self.get(cid, plat).get("state", "pending")

    def is_done(self, cid, plat):
        return self.state(cid, plat) in DONE_STATES

    def is_published(self, cid, plat):
        return self.state(cid, plat) == "published"

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
        elif state not in ("failed",):
            rec[plat].pop("error", None)
        self.save()
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

    def remove(self, cid, plat=None):
        """--reset 用：清掉一条（或一条的全部平台）记录，强制重发。"""
        if plat:
            (self.data.get(cid) or {}).pop(plat, None)
            if not self.data.get(cid):
                self.data.pop(cid, None)
        else:
            self.data.pop(cid, None)
        self.save()

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
