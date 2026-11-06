#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏仓台账（`open-source-games/data/publish_state.json`）的唯一读口。

为什么单独一个模块（2026-11-06）
--------------------------------
B站二号的**真账簿不在本仓**：它由 `open-source-games/publish/bridge.py
--platform bilibili2` 发布，记录写在
`<GAMES_ROOT>/data/publish_state.json` 的
`articles.<slug>.platforms.bilibili2`。
本仓 `data/bili_published.json` 是**一号**的 —— 混读会把一号发过的期次
当成二号发过（2026-11-06 实拍过这个形状）。

此前这本账只有 `publish_slot._rows_games_bili2()` 一个人在读（只为算日配额），
而 `publish_one` 的 `dedupe` 闸压根不读它 ⇒ 二号「发过没发过」只能靠候选池
先排除 + bridge 自己的 `blocks_republish` 兜底，闸门形同虚设。
现在两处都读这里，口径只有一份。

三条口径（照抄了旧实现的定论，别改）
------------------------------------
* **只认 `state`**：`published` = 真发出去了；`drafted` = 草稿建好但没发
  （**不是**「发过」，但也不该再建第二份）；`failed` / `pending` 一律不算。
* **时间取 `published_at` → `verified_at` → `attempted_at`**：
  `verified_at` 是补账时刻，拿它排期会把上周的稿算成今天发的
  （`publish_slot._rows_list` 里同一条注释）。
* **case 归一**：本仓写法 `ext-ep-004-quake`、二号产物 id
  `ext-ep-004-quake-b2`、游戏仓 slug `ep-004-quake` 三者等价。
  `norm()` 统一剥掉 `ext-` 前缀与 `-b2` 后缀，比的是同一个东西。

⚠ 路径可由 `GAMES_STATE`（整文件）或 `GAMES_ROOT`（仓根）覆盖 ——
  单测必须显式传 `path=`，别读到真台账（同 `candidate_store` 那条纪律）。
"""

import json
import os
import re

#: 游戏仓根目录。默认写死的是**本机**布局，换机器用 GAMES_ROOT 覆盖。
GAMES_ROOT = os.environ.get("GAMES_ROOT", r"D:/open-source-games")

#: 哪些平台的真账簿在游戏仓。扩展时要连 `publish_slot.READERS` 一起改。
GAMES_PLATFORMS = ("bilibili2",)

#: 时间字段优先级：补账时刻不能当发布时刻。
_AT_FIELDS = ("published_at", "verified_at", "attempted_at")


def state_path(path=None):
    """台账文件路径。`path` > `GAMES_STATE` > `<GAMES_ROOT>/data/publish_state.json`。"""
    if path:
        return path
    p = os.environ.get("GAMES_STATE")
    if p:
        return p
    return os.path.join(GAMES_ROOT, "data", "publish_state.json")


def norm(case):
    """`ext-ep-004-quake-b2` / `ext-ep-004-quake` / `ep-004-quake` → `ep-004-quake`。"""
    c = str(case or "").strip().lower()
    c = re.sub(r"^ext-", "", c)
    c = re.sub(r"-b2$", "", c)
    return c


def load(path=None):
    """读整本账；读不到/坏了返回 `{}`（**不**替调用方建文件）。"""
    try:
        with open(state_path(path), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _at(rec):
    for k in _AT_FIELDS:
        v = str(rec.get(k) or "").strip()
        if v:
            return v
    return ""


def entries(platform="bilibili2", path=None):
    """该平台的全部记录（含未发布的），逐条返回 dict。

    字段：`case` / `state` / `at` / `remote_id` / `url` / `title`。
    """
    arts = load(path).get("articles") or {}
    out = []
    for slug, art in arts.items():
        rec = ((art or {}).get("platforms") or {}).get(platform) or {}
        if not isinstance(rec, dict) or not rec:
            continue
        out.append({
            "case": slug,
            "state": str(rec.get("state") or "").strip(),
            "at": _at(rec),
            "remote_id": str(rec.get("remote_id") or "").strip(),
            "url": str(rec.get("url") or "").strip(),
            "title": str(rec.get("title") or (art or {}).get("title") or "").strip(),
        })
    out.sort(key=lambda r: (r["at"], r["case"]))
    return out


def published(platform="bilibili2", path=None):
    """只留 `state=published` 的（判重、算配额用这一份）。"""
    return [r for r in entries(platform, path=path) if r["state"] == "published"]


def find(platform, case, path=None):
    """按 case 找一条记录；没有返回 `{}`。

    ⚠ 返回**未发布**的记录也算命中 —— 由调用方决定是「已发过」还是
    「已有草稿」。找不到 ≠ 没发过：账读坏了也是找不到，呼叫方要自己
    把 `load()` 的失败与「确实没有」分开说。
    """
    want = norm(case)
    if not want:
        return {}
    for r in entries(platform, path=path):
        if norm(r["case"]) == want:
            return r
    return {}
