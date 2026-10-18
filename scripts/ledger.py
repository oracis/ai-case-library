#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""四平台台账统一读写层 —— 一条记录一个事实，远端 ID 当主键。

## 为什么要有这一层（2026-10-19，被迫总结）

当前四个平台的账本形状完全不一样，而**不一致就是这样长出来的**：

| 平台 | 文件 | 形状 | 状态怎么表达 | 远端 ID |
|---|---|---|---|---|
| 公众号 | `wechat_published.json` | **dict**（key=case_id） | value 里的 `status` | `appmsgid`（59/60） |
| 头条 | `toutiao_drafts.json` + `_published.json` | 两个 list | **在哪个文件里** | ❌ 只写在 note 文本里 |
| B站 | `bili_published.json` | list | 在哪个文件里 | ❌ 只写在 note 文本里 |
| 小红书 | `xhs_drafts.json` + `_published.json` | 两个 list | 在哪个文件里 | ✅ `note_id` |

两个结构性毛病：

1. **「状态」用「在哪个文件」表达** ⇒ 草稿升格为发布时，必须在两个文件
   各改一次，而两个文件是两次独立 IO，中间失败就永久不一致
   （2026-10-19 对账：头条草稿台账 55 条里 16 条早该迁走，全是这么漏的）。
2. **没有远端 ID** ⇒ 对账只能靠标题语义匹配。而标题有两种口径
   （早期 `月收$X：…` / 现在 `产品名：…`），匹配必然出错
   （同一次对账，纯标题匹配把 13 篇老文章误判成幽灵）。

这一层把两边都收掉：对外只有 `{case_id: {state, remote_id, ...}}` 一个视图，
`state` 是字段不是位置 ⇒ 迁移是一次原子写；`remote_id` 是主键 ⇒ 对账退化成
集合差，毫秒级、零歧义。

**文件格式一个字节都不动**（缩进/行尾/dict-vs-list 全按原样探测），
所以替换调用方是纯内部重构，git diff 里看不到数据搬家。

## 用法

```python
import ledger
ledger.mark("toutiao", "vid-ai", "published",
            remote_id="7694945062837076522",
            title="两年做到累计 $162 万，然后峰值留存掉到 46%",
            evidence="feed_api")
rows = ledger.load("toutiao")          # {case_id: rec}，rec["state"] ∈ draft/published/unverified
ledger.ids("toutiao", "published")     # set(case_id)
```

## `unverified` 是什么

接口返 `code:0` 只代表"受理了"，不代表落库（头条 delete 对假 gid 也返
code:0）。所以**没有回读拿到远端 ID 的写入，不许记成 published** ——
记成 `unverified`，由 `ledger_health.py` 下次巡检补 ID。
这样"假成功"进不了账本，也不会挡住后续重发（`unverified` 可以重试）。
"""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

PLATFORMS = ("wechat", "toutiao", "bilibili", "xiaohongshu")

DRAFT = "draft"
PUBLISHED = "published"
UNVERIFIED = "unverified"

# 平台 → 文件规格。layout=dict 的（公众号）状态与 ID 都在 value 里。
_SPEC = {
    "wechat": {
        "layout": "dict",
        "published": "wechat_published.json",
        "draft": None,                       # 草稿也在同一个 dict，靠 status 区分
    },
    "toutiao": {
        "layout": "list",
        "published": "toutiao_published.json",
        "draft": "toutiao_drafts.json",
    },
    "bilibili": {
        "layout": "list",
        "published": "bili_published.json",
        "draft": None,                       # 本地没有 B 站草稿台账
    },
    "xiaohongshu": {
        "layout": "list",
        "published": "xhs_published.json",
        "draft": "xhs_drafts.json",
    },
}

# 平台 → 该平台远端 ID 在记录里的字段名（读远端时用，写账本时统一叫 remote_id）
REMOTE_ID_FIELD = {
    "wechat": ("appmsgid", "appmsg_id", "msgid"),
    "toutiao": ("gid",),
    # B站文章 ID 就是数字 article_id。
    # 🚨 绝不要把 "id" 放进来 —— list 布局里 `id` 是 **case id**，
    #    2026-10-19 踩过：加了 "id" 当候选，结果把 "rezi" 当成文章 ID 写进台账。
    "bilibili": ("article_id", "bvid"),
    "xiaohongshu": ("note_id",),
}


# ------------------------------------------------------------------ 底层 IO

def _path(platform, state):
    spec = _SPEC[platform]
    name = spec.get(state) if state != UNVERIFIED else spec.get(PUBLISHED)
    if not name:
        raise ValueError("%s 没有 %s 台账（该平台不记这一侧）" % (platform, state))
    return os.path.join(DATA, name)


def _read_file(path):
    """返回 (raw_text, entries, newline, indent)。entries 是 list 或 dict。"""
    if not os.path.isfile(path):
        return "", None, "\n", 1
    with open(path, "rb") as f:
        raw = f.read()
    lf = raw.count(b"\n") - raw.count(b"\r\n")
    crlf = raw.count(b"\r\n")
    nl = "\r\n" if crlf > lf else "\n"
    # 缩进按第一处换行后的空格数探测（本仓库两种并存：1 空格 / 2 空格）
    indent = 1
    i = raw.find(b"\n")
    if i >= 0:
        j = i + 1
        while j < len(raw) and raw[j:j + 1] == b" ":
            j += 1
        d = j - i - 1
        indent = d if 1 <= d <= 4 else 1
    try:
        entries = json.loads(raw.decode("utf-8"))
    except ValueError:
        entries = None
    return raw.decode("utf-8", "replace"), entries, nl, indent


def _write_file(path, entries, nl, indent):
    """原子写：先写临时文件再 replace（半截文件 = 整本台账失效）。

    ⚠ 末尾换行要跟着原文件走：仓库里有的 json 以 "\n]" 结尾、有的没有，
      不判就会凭空多/少一个换行（diff 里显示 "No newline at end of file"）。
    """
    tail = ""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read().endswith(b"\n"):
                tail = nl
    text = json.dumps(entries, ensure_ascii=False, indent=indent) + tail
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", nl))
    os.replace(tmp, path)


# ------------------------------------------------------------------ 归一化

def _rec_from_wechat(cid, v):
    st = v.get("status") or DRAFT
    rec = dict(v)
    rec["id"] = cid
    rec["state"] = PUBLISHED if st == PUBLISHED else DRAFT
    rec["at"] = v.get("published_at") or v.get("at") or ""
    rid = ""
    for k in REMOTE_ID_FIELD["wechat"]:
        if v.get(k):
            rid = str(v[k])
            break
    rec["remote_id"] = rid
    rec["title"] = v.get("title") or ""
    return rec


def _rec_from_item(item, platform):
    rec = dict(item)
    rec["id"] = item.get("id") or ""
    rec["state"] = item.get("state") or PUBLISHED     # list 布局默认已发布
    rec["at"] = item.get("at") or ""
    rid = str(item.get("remote_id") or "")
    for k in REMOTE_ID_FIELD.get(platform, ()):
        if not rid and item.get(k):
            rid = str(item[k])
    rec["remote_id"] = rid
    rec["title"] = item.get("title") or item.get("remote_title") or ""
    return rec


def load(platform, state=None):
    """读该平台全部记录 → {case_id: rec}。

    state 给定时只返回该状态的（None = 全部）。
    """
    if platform not in _SPEC:
        raise ValueError("未知平台 %r，可选 %s" % (platform, "/".join(PLATFORMS)))
    spec = _SPEC[platform]
    out = {}
    if spec["layout"] == "dict":
        p = _path(platform, PUBLISHED)
        _, entries, _, _ = _read_file(p)
        if isinstance(entries, dict):
            for cid, v in entries.items():
                if isinstance(v, dict):
                    out[cid] = _rec_from_wechat(cid, v)
    else:
        # ⚠ 先草稿后已发布：同一 case 同时挂在两个文件里（历史冗余）时，
        #   **已发布优先** —— 它离远端真相更近。合并后的统一视图只有一条，
        #   下次 save 会把它规整到 published，冗余自然消失。
        states = [DRAFT, PUBLISHED] if spec.get("draft") else [PUBLISHED]
        for st in states:
            p = _path(platform, st)
            _, entries, _, _ = _read_file(p)
            if isinstance(entries, list):
                for it in entries:
                    if isinstance(it, str):
                        it = {"id": it}
                    if not isinstance(it, dict) or not it.get("id"):
                        continue
                    rec = _rec_from_item(it, platform)
                    rec["state"] = st          # 文件位置即状态，覆盖默认
                    out[rec["id"]] = rec
    if state:
        return {k: v for k, v in out.items() if v["state"] == state}
    return out


def _serialize(platform, recs):
    """把 {cid: rec} 拆回该平台的文件形状，**保持原有键序**。

    - dict 布局：key 顺序沿用旧文件，新记录追加到末尾；
    - list 布局：按 state 分到两个文件，各自保持旧顺序。
    """
    spec = _SPEC[platform]
    if spec["layout"] == "dict":
        p = _path(platform, PUBLISHED)
        _, old, _, _ = _read_file(p)
        old = old if isinstance(old, dict) else {}
        out = {}
        for cid in list(old) + [c for c in recs if c not in old]:
            if cid not in recs:
                continue
            r = recs[cid]
            v = dict(old.get(cid) or {})
            v["title"] = r.get("title") or v.get("title") or ""
            v["status"] = r["state"] if r["state"] in (DRAFT, PUBLISHED) else PUBLISHED
            if r.get("at"):
                v["published_at"] = r["at"]
            rid = r.get("remote_id") or ""
            if rid and not str(rid).startswith("tmp-"):
                v["appmsgid"] = rid
            if r.get("note"):
                v["note"] = r["note"]
            if r.get("verified_at"):
                v["verified_at"] = r["verified_at"]
            out[cid] = v
        return {PUBLISHED: out}

    buckets = {PUBLISHED: [], DRAFT: []}
    for st, fname in ((PUBLISHED, spec.get("published")),
                      (DRAFT, spec.get("draft"))):
        if not fname:
            continue
        _, old, _, _ = _read_file(os.path.join(DATA, fname))
        old_list = [x for x in (old or []) if isinstance(x, (dict, str))]
        seen = set()
        for it in old_list:
            cid = it if isinstance(it, str) else it.get("id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            r = recs.get(cid)
            if not r or r["state"] != st:
                continue
            buckets[st].append(_item_out(r, platform))
        for cid, r in recs.items():           # 新记录追加
            if cid in seen or r["state"] != st:
                continue
            buckets[st].append(_item_out(r, platform))
    return buckets


def _item_out(r, platform):
    """list 布局的落盘形态：id/at 在最前（diff 稳定），其余字段照留。

    🚨 远端 ID 要写回**该平台自己的字段名**（小红书 `note_id`、头条 `gid`、
       B站 `bvid`），不能写内部名 `remote_id` —— 否则 round-trip 一趟
       整个文件字段全变，git diff 型同数据搬家。
    """
    out = {"id": r["id"], "at": r.get("at") or ""}
    id_field = (REMOTE_ID_FIELD.get(platform) or ("remote_id",))[0]
    rid = r.get("remote_id") or ""
    if rid:
        out[id_field] = rid
    for k in ("remote_title", "title", "verified_at", "evidence", "note"):
        v = r.get(k)
        if v:
            out["title" if k == "remote_title" else k] = v
    for k, v in r.items():
        if k in out or k in ("id", "at", "state", "remote_id",
                             "remote_title", "title"):
            continue
        out[k] = v
    return out


def save(platform, recs):
    """把统一视图写回该平台的文件。原子写、保格式。"""
    buckets = _serialize(platform, recs)
    spec = _SPEC[platform]
    if spec["layout"] == "dict":
        p = _path(platform, PUBLISHED)
        _, _, nl, ind = _read_file(p)
        _write_file(p, buckets[PUBLISHED], nl, ind)
        return
    for st, fname in ((PUBLISHED, spec.get("published")), (DRAFT, spec.get("draft"))):
        if not fname:
            continue
        p = os.path.join(DATA, fname)
        _, _, nl, ind = _read_file(p)
        _write_file(p, buckets[st], nl, ind)


# ------------------------------------------------------------------ 写入口

def mark(platform, cid, state, remote_id="", title="", evidence="",
         note="", at=None, keep_at=True):
    """写一条记录（不存在则创建，存在则**改状态**）。

    state 只能是 draft / published / unverified：
      · published  必须带 remote_id；没带就降级成 unverified 并留痕，
                   免得「接口返 OK」假成功混进已发布；
      · unverified 表示「写动作发了、但没回读确认」，下次巡检补 ID。
    """
    if state not in (DRAFT, PUBLISHED, UNVERIFIED):
        raise ValueError("state 只能是 %s/%s/%s" % (DRAFT, PUBLISHED, UNVERIFIED))
    recs = load(platform)
    rec = recs.get(cid) or {"id": cid}
    if keep_at and not rec.get("at"):
        rec["at"] = at or time.strftime("%Y-%m-%d %H:%M")
    if at:
        rec["at"] = at
    if remote_id:
        rec["remote_id"] = str(remote_id)
    if title:
        rec["title"] = title
    if evidence:
        rec["evidence"] = evidence
    if note:
        rec["note"] = note
    if state == PUBLISHED and not rec.get("remote_id"):
        rec["state"] = UNVERIFIED
        rec["note"] = (rec.get("note") or "") + \
            "｜无远端 ID ⇒ 记 unverified（接口 OK 不等于落库，等巡检补证）"
    else:
        rec["state"] = state
    if rec.get("remote_id"):
        rec["verified_at"] = time.strftime("%Y-%m-%d %H:%M")
    recs[cid] = rec
    save(platform, recs)
    return rec


def promote(platform, cid, remote_id, title="", evidence="", note=""):
    """草稿 → 已发布（一次原子写，不再有「两个文件各改一次」的中间态）。"""
    return mark(platform, cid, PUBLISHED, remote_id=remote_id, title=title,
                evidence=evidence or "remote_readback", note=note)


def remove(platform, cid):
    recs = load(platform)
    if cid in recs:
        del recs[cid]
        save(platform, recs)
        return True
    return False


def ids(platform, state=None):
    return set(load(platform, state))


def get(platform, cid):
    return load(platform).get(cid)


def missing_remote_id(platform, state=PUBLISHED):
    """已发布但没有远端 ID 的记录 —— 历史欠账清单，巡检要逐个补。"""
    return [r for r in load(platform, state).values() if not r.get("remote_id")]


def duplicates(platform):
    """同时挂在「草稿」和「已发布」两处的 case（历史两本账的遗留冗余）。

    统一视图里这类 case 只存一条（已发布优先），所以不会再造成状态歧义，
    但值得列出来 —— 它们是下一次 `save` 时会被规整掉的对象。
    """
    spec = _SPEC[platform]
    if not spec.get("draft"):
        return []
    out = []
    for st in (DRAFT, PUBLISHED):
        _, entries, _, _ = _read_file(_path(platform, st))
        ids_ = set()
        for it in (entries or []):
            cid = it if isinstance(it, str) else (it or {}).get("id")
            if cid:
                ids_.add(cid)
        out.append(ids_)
    return sorted(out[0] & out[1])
