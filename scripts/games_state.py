#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏仓台账（`open-source-games/data/publish_state.json`）的唯一读口 + 唯一写口。

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

2026-11-06 追加 `record_published()` 作为唯一写口：二号进 FIFO 后发布不再
经过游戏仓 bridge，得有人把这一笔补进游戏仓那本账，否则
`ledger.blocks_republish()` 与 `published()` 会同时漏判。写口只许挂在
「已发表列表按 aid 回读命中」之后，读坏了绝不写盘，重跑幂等。

⚠ 路径可由 `GAMES_STATE`（整文件）或 `GAMES_ROOT`（仓根）覆盖 ——
  单测必须显式传 `path=`，别读到真台账（同 `candidate_store` 那条纪律）。
"""

import json
import os
import re
import sys
import time

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


# ---------------------------------------------------------------- 写口

def _stamp(at=None):
    """时间统一成游戏仓契约 `%Y-%m-%d %H:%M:%S`。

    本仓 db 的 `published_at` 是 `%Y-%m-%d %H:%M`（少一位秒），回填时只能
    补 `:00`，**不许拿 now() 覆盖** —— 补账时刻当发布时间会把上周的稿算成
    今天发的（`publish_slot._rows_list` 里同一条红线）。格式非法直接抛，
    免得脏时间写进去让游戏仓 `ledger.validate()` 整本报红。
    """
    s = str(at or "").strip()
    if not s:
        return time.strftime("%Y-%m-%d %H:%M:%S")
    if len(s) == 16:
        s += ":00"
    time.strptime(s, "%Y-%m-%d %H:%M:%S")
    return s


def record_published(platform, case, remote_id="", title="", url="",
                     note="", at=None, path=None):
    """把一次「平台侧回读命中的发布」补写进游戏仓台账（**唯一写口**）。

    为什么要有（2026-11-06）
    ------------------------
    二号进 FIFO 后 `publish_one` 直接调 `bilibili_publish.py send-draft`，
    不经过 bridge，发完只写本仓 `platform_posts(platform='bilibili2')`。
    游戏仓 `data/publish_state.json` 不会多一条 ⇒ 游戏仓
    `ledger.blocks_republish()` 看不见这次发布，手动
    `bridge publish --slug <同一期>` 就会重发；`games_state.published()`
    也少一条证据。读写都收口到本模块，口径不许再各写一份。

    🚨 三条红线
    ------------
    1. 调用方必须**已经拿到平台侧回读证据**（已发表列表按 aid 命中）才能调。
       toast「提交成功」/ 接口 200 / 跳转编辑页都不算。写成 published 后
       `check_dupe` 会永久拦住这一期，写错的代价是一篇稿再也发不出去。
    2. 读坏了**绝不写盘**。这本台账长期 dirty、人也会手改；解析失败时返回
       初始结构再 save 会把历史整本覆盖（游戏仓 `ledger.load` 同样红线）。
    3. `path` 必须可注入（同 `candidate_store` 纪律），单测不许碰真台账。

    字段契约与 `open-source-games/publish/ledger.py::record` 一致：
    `state / remote_id / url / attempted_at / verified_at / note`。
    额外在 entry 里放 `title`（`entries()` 优先读它），但**不覆盖
    `articles.<slug>.title`** —— 那是该期主标题，别的平台也在读，二号
    用的是 `meta.title_alt` 变体，写进去会污染主标题。

    返回 `(action, entry)`，action ∈ {"written", "skipped"}；重跑幂等。
    """
    if platform not in GAMES_PLATFORMS:
        raise ValueError("游戏仓台账只收 %s，不收 %r"
                         % ("/".join(GAMES_PLATFORMS), platform))
    slug = norm(case)
    if not slug:
        raise ValueError("record_published 缺 case")
    if not remote_id:
        raise ValueError("record_published 缺 remote_id（不许记无 ID 的 published）")
    stamp = _stamp(at)

    try:
        import atomicio
    except ImportError:                     # 独立跑本模块时 sys.path 没带 scripts/
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import atomicio

    p = state_path(path)
    with atomicio.file_lock(p + ".lock"):
        try:
            data = atomicio.read_json(p)
        except FileNotFoundError:
            data = {"schema_version": 1, "updated_at": "", "articles": {}}
        # 其它异常（坏 JSON / 编码错）直接抛：绝不拿空结构覆盖历史
        if not isinstance(data, dict):
            raise ValueError("游戏仓台账顶层不是对象：%s" % p)
        arts = data.setdefault("articles", {})
        if not isinstance(arts, dict):
            raise ValueError("游戏仓台账 articles 不是对象：%s" % p)
        art = arts.get(slug)
        if not isinstance(art, dict):
            art = {"title": str(title or ""), "platforms": {}}
            arts[slug] = art
        plats = art.setdefault("platforms", {})
        if not isinstance(plats, dict):
            raise ValueError("游戏仓台账 %s/platforms 不是对象：%s" % (slug, p))
        prev = plats.get(platform)
        if not isinstance(prev, dict):
            prev = {}
        if prev.get("state") == "published":
            return "skipped", prev

        old_rid = str(prev.get("remote_id") or "")
        trace = ("；草稿 aid %s → 发布 aid %s" % (old_rid, remote_id)
                 if old_rid and old_rid != str(remote_id) else "")
        entry = {
            "state": "published",
            "remote_id": str(remote_id),
            "url": str(url or ""),
            "attempted_at": stamp,
            "verified_at": stamp,
            "note": (str(note or "平台侧回读命中，补写 published") + trace)[:500],
        }
        if title:
            entry["title"] = str(title)
        plats[platform] = entry
        data.setdefault("schema_version", 1)
        data["updated_at"] = stamp
        # 与游戏仓 ledger.save() 完全同形：indent=2 / LF / 末尾换行
        atomicio.write_json(p, data, indent=2, trailing_newline=True)
        return "written", entry
