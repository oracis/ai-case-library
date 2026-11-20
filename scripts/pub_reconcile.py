#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""已发侧台账对账（两朵云 / CSDN / 博客园 / 知乎）。

`bili_published_reconcile.py` / `toutiao_published_reconcile.py` 只覆盖
B站与头条（`ledger.py` 里那四个平台）。剩下这五个平台的台账只存在
SQLite `platform_posts` 里，一直没有回读工具，于是它们在
`ledger_health` 的线上口径里是**盲区**——「线上发了、本地没记」和
「本地记着、线上没了」都发现不了。本脚本补上这一段。

## 两类平台，两种判据

**A. 能拉已发列表的（CSDN / 博客园 / 知乎）** —— 全类对账：
  · `NO_ID`   台账记 published 却没有远端 id → 按标题唯一命中补 id
  · `STALE`   台账的 id 不在线上已发列表 → 按标题唯一命中换真 id
  · `PROMOTE` 台账记 draft/auditing，线上其实已发表 → 改 published
               （最危险的一类：`publish_slot` 只认 published，会重复发）
  · `ORPHAN`  线上有、本地没有任何一条指着它 → 按标题唯一命中补登
               （case 不在 `cases` 表的不补 —— 会造 stub 污染案例库）
  · `DUP_PUB` 同 case 已登记在线 id，线上又出现同标题 → 重复稿，交人裁定
  · `DUP_ID`  同一平台里**两条不同 case 记着同一个 id** → 至少一条是错的，
               纯报告，不动（十有八九是建稿时串号，如 aliyun 草稿 1699450）

**B. 拉不到列表的（阿里云 / 腾讯云）** —— 逐篇公开页核验：
  两朵云没有「我的文章」列表接口（只有 CDP 才能翻的 UI 页），所以做不到
  ORPHAN。能做的、也是唯一可信的判据是：**对台账里每条 published 的
  article id 走匿名公开页**（复用 `yun_state.judge`：200 + 标题命中 + 无
  审核横幅）。
  · `UNCONFIRMED` 公开页看不到（404 / 标题不符 / 有审核横幅）⇒ 该行不是
                 published。**默认只报告不写盘**，因为一次探测可能撞风控；
                 确认后加 `--apply-cloud` 才落 `auditing`。
  · `PROBE_FAIL`  网络失败或风控插页 —— 「读不到」≠「线上没有」，一律不算证据。

## 认领判据

归一化后完全相等，或一方是另一方前缀且长度差 ≤ 12（`title_match.py`），
**并且候选唯一**。绝不按相似度投票。台账 `title` 为空的（如 trustmrr）
无法认亲 ⇒ 落 unclaimed 等人看。

用法::

    python scripts/pub_reconcile.py                      # 五个平台全跑一遍（只读）
    python scripts/pub_reconcile.py --platform csdn      # 只跑一个（可重复）
    python scripts/pub_reconcile.py --apply              # 写盘（A 类）
    python scripts/pub_reconcile.py --apply --apply-cloud # 连 B 类降级一起写
    python scripts/pub_reconcile.py --json tmp/rc.json   # 机器可读结果

退出码：0 = 没有待处理；1 = 有 unclaimed / 探测失败 / 写盘失败。
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import atomicio                                                       # noqa: E402
import db                                                             # noqa: E402
from title_match import title_match                                   # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")

LIST_PLATFORMS = ("csdn", "cnblogs", "zhihu")     # 能拉已发列表
CLOUD_PLATFORMS = ("aliyun", "tencentcloud")      # 只能逐篇公开页核验
PLATFORMS = LIST_PLATFORMS + CLOUD_PLATFORMS

TODAY = "11-20"
TIP = {
    "noid": "｜%s 对账：补线上 id %s（按标题唯一命中）",
    "stale": "｜%s 对账：id 由 %s 纠正为线上真 id %s",
    "promote": "｜%s 对账：台账原记 %s，线上已发表 %s ⇒ 改 published",
    "orphan": "｜%s 对账：线上已发表但本地漏登，按标题唯一命中补登",
    "unconf": "｜%s 对账：匿名公开页看不到（%s）⇒ 由 published 落 auditing",
    "title": "｜%s 对账：原 title 为空，按线上页标题回填「%s」",
}


# --------------------------------------------------------------- 台账读取
def load_rows(platform):
    """{case_id: row}。db 是这批平台的唯一真值（没有 per-platform JSON）。"""
    conn = db.get_conn()
    try:
        rows = db.get_posts_by_platform(conn, platform)
    finally:
        db.close_conn()
    return {r["case_id"]: r for r in rows}


def known_cases():
    """案例库现有 case id —— ORPHAN 补登前必须在这里面，否则会造 stub。"""
    conn = db.get_conn()
    try:
        return {r[0] for r in conn.execute("SELECT id FROM cases")}
    finally:
        db.close_conn()


def title_corpus():
    """{case_id: [该 case 在各平台用过的所有标题]}。

    为什么不能只用本平台台账的 title：博客园 10-29~11-05 那批稿是**建台账
    之前**发的，本地 cnblogs 行根本没有对应记录；而它们的标题与当时
    知乎/公众号用的营销体标题一字不差。拿全平台标题做语料才认得出
    「线上这篇属于哪个 case」。没有这份语料，20 条真实的漏登只能
    一律落 unclaimed（2026-11-20 首次跑出来的样子）。
    """
    corpus = {}
    conn = db.get_conn()
    try:
        for r in conn.execute(
                "SELECT case_id, title FROM platform_posts WHERE title<>''"):
            t = (r[1] or "").strip()
            if t and not _looks_like_url(t):
                corpus.setdefault(r[0], []).append(t)
    finally:
        db.close_conn()
    return corpus


def _looks_like_url(s):
    return bool(s) and ("://" in s or s.startswith("www.") or
                        ("/" in s and " " not in s and len(s) < 120))


def match_cases(title, corpus):
    """线上标题 → 可能属于哪些 case（要求调用方再验唯一）。"""
    return [cid for cid, titles in corpus.items()
            if any(title_match(title, t) for t in titles)]


def id_from_url(s):
    """从 url（或历史上被写进 title 的裸地址）里抠出文章 id。"""
    m = re.search(r"/(?:p|details|article)/(\d{5,})", s or "")
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{6,})(?!\d)", s or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------- 拉远端列表
# 每个 plataform 返回 {远端 id: {"title": 标题, "at": 发表时点}}。
# `at` 必须带回来：补登/改状态时如果让 `upsert_post` 默认填 `now`，
# 这批历史稿会被算成「今天发的」，直接吃掉当日档期（`publish_slot` 按
# published_at 数今日配额）。
def pull_csdn(pages=4):
    import csdn_publish as C
    arts = C.list_published(pages=pages)
    return ({str(a["id"]): {"title": a.get("title") or "",
                            "at": str(a.get("postTime") or "")}
             for a in arts if a.get("id")},
            {"pub_ok": True, "why": "", "n": len(arts)})


def _cnblogs_ts(s):
    """'20261120T07:14:00' → '2026-11-20 07:14:00'。"""
    s = str(s or "")
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2}):(\d{2}):(\d{2})", s)
    return "%s-%s-%s %s:%s:%s" % m.groups() if m else s


def pull_cnblogs(count=200):
    import cnblogs_publish as B
    cfg = B.load_config()
    items = B.recent_posts(cfg, count=count)
    return ({str(it["post_id"]): {"title": it.get("title") or "",
                                  "at": _cnblogs_ts(it.get("dateCreated"))}
             for it in items if it.get("post_id")},
            {"pub_ok": True, "why": "", "n": len(items)})


def pull_zhihu():
    import zhihu_ledger_sync as Z
    _token, pubs = Z.fetch_published()
    return ({str(k): {"title": v.get("title") or "",
                      "at": str(v.get("published_ts") or "")}
             for k, v in pubs.items()},
            {"pub_ok": True, "why": "", "n": len(pubs)})


PULLERS = {"csdn": pull_csdn, "cnblogs": pull_cnblogs, "zhihu": pull_zhihu}


# --------------------------------------------------------------- 分类（纯函数）
def _t(row):
    """本行可用的标题（历史上 url 被误写进 title 的，那不算标题）。"""
    t = (row.get("title") or "").strip()
    return "" if _looks_like_url(t) else t


def _rt(remote, rid):
    """远端某条的标题（remote 值是 {"title","at"}）。"""
    v = remote.get(rid)
    return (v or {}).get("title") if isinstance(v, dict) else (v or "")


def plan(recs, remote, cases, corpus=None):
    """算出 A 类要动的五桶。纯函数，不写盘。

    ⚠ 顺序敏感：先把「已登记且在线」的 id 全部认领（`claimed`），
      再算 STALE / NO_ID（它们只能认领**没被别的 case 认领过**的线上 id），
      最后才算 ORPHAN。否则一个 case 的 id 会被另一个 case 抢走，
      第二次对账就会把正确台账改错（B站那轮踩过）。
    """
    corpus = dict(corpus or {})
    for cid, r in recs.items():                   # 本平台自己的标题也算语料
        t = _t(r)
        if t:
            corpus.setdefault(cid, []).append(t)
    stale, noid, promote, orphan, unclaimed = [], [], [], [], []
    title_fix = []                                # 标题为空、线上有 → 回填

    claimed = {}                                  # remote_id -> case_id
    for cid, r in recs.items():
        rid = str(r.get("remote_id") or "")
        if r.get("state") == "published" and rid and (not remote or rid in remote):
            claimed[rid] = cid

    # ---- 1. 记着 published 的：id 对不对
    for cid in sorted(recs):
        r = recs[cid]
        if r.get("state") != "published":
            continue
        rid = str(r.get("remote_id") or "")
        if rid and rid in remote:
            if not _t(r) and _rt(remote, rid):
                # 标题空着：线上有现成的，回填。不做它，下一次 STALE/ORPHAN
                # 都认不出这个 case（trustmrr 在 csdn / cnblogs 就是这样）
                title_fix.append((cid, rid, _rt(remote, rid)))
            continue
        t = _t(r)
        # ① 先看 url / 被写进 title 的裸地址里有没有 id（博客园早期两行就是这样）
        guid = id_from_url(r.get("url") or "") or id_from_url(r.get("title") or "")
        if guid and guid != rid and guid in remote and guid not in claimed:
            noid.append((cid, guid, _rt(remote, guid)))
            claimed[guid] = cid
            claimed.pop(rid, None) if rid and rid not in remote else None
            continue
        # ② 再按标题认亲
        cand = [i for i in remote
                if i not in claimed and title_match(_rt(remote, i), t)]
        if not rid:
            if len(cand) == 1:
                noid.append((cid, cand[0], _rt(remote, cand[0])))
                claimed[cand[0]] = cid
            else:
                unclaimed.append(("NO_ID", cid, t or r.get("title") or "",
                                  "线上同标题候选 %d 个（%s）"
                                  % (len(cand), cand[:4])))
            continue
        if len(cand) == 1:
            stale.append((cid, rid, cand[0], _rt(remote, cand[0])))
            claimed.pop(rid, None)
            claimed[cand[0]] = cid
        else:
            unclaimed.append(("STALE", cid, t or "(标题为空)",
                              "本地 id %s 线上没有；线上同标题候选 %d 个（%s）"
                              % (rid, len(cand), cand[:4])))

    # ---- 2. 线上多出来的：谁认领
    for aid in sorted(remote):
        if aid in claimed:
            continue
        atitle = _rt(remote, aid)
        cand = match_cases(atitle, corpus)
        if len(cand) != 1:
            unclaimed.append(("ORPHAN", "", atitle,
                              "本地同标题候选 %d 个（%s）" % (len(cand), cand[:4])))
            continue
        cid = cand[0]
        r = recs.get(cid) or {}
        cur = str(r.get("remote_id") or "")
        st = r.get("state")
        if st == "published" and cur and cur in remote:
            unclaimed.append(("DUP_PUB", cid, atitle,
                              "已登记线上 id=%s，这条 %s 疑似重复稿" % (cur, aid)))
            continue
        if st == "deleted":
            unclaimed.append(("ORPHAN", cid, atitle,
                              "本地记 deleted，线上却有 %s（重发还是没删干净？）" % aid))
            continue
        if cur == aid or st in ("draft", "auditing"):
            promote.append((cid, cur or st or "(无)", aid, atitle))
            claimed[aid] = cid
            continue
        if cid not in cases:
            unclaimed.append(("ORPHAN", cid, atitle,
                              "case 不在 cases 表（补登会造 stub）"))
            continue
        orphan.append((cid, aid, atitle))
        claimed[aid] = cid

    return {"stale": stale, "noid": noid, "promote": promote,
            "orphan": orphan, "title_fix": title_fix, "unclaimed": unclaimed}


def dup_ids(recs):
    """同一平台里两条不同 case 记着同一个 id。纯报告。"""
    seen, out = {}, []
    for cid, r in recs.items():
        for field in ("remote_id", "draft_id"):
            v = str(r.get(field) or "")
            if not v:
                continue
            key = (field, v)
            if key in seen and seen[key] != cid:
                out.append((field, v, seen[key], cid))
            else:
                seen[key] = cid
    return out


# --------------------------------------------------------------- B 类：云端核验
_SUFFIX = (re.compile(r"[-–—]\s*阿里云开发者社区\s*$"),
           re.compile(r"[-–—]\s*腾讯云开发者社区([-–—]腾讯云)?\s*$"))


def _clean_page_title(t):
    t = (t or "").strip()
    for pat in _SUFFIX:
        t = pat.sub("", t).strip()
    return t


def probe_cloud(platform, recs, timeout=20, delay=1.5, retries=3):
    """逐条走匿名公开页，按证据分成六桶。

    ⚠ 判据必须比 `yun_state.judge` 更细：`judge` 只会把「标题没命中」一律
      算成未公开，而实测这堆老稿的病是**台账标题是空的**（`markdown-structure`
      / `shipfast` / `trustmrr` 三条 aliyun 行 `title=''`），公开页其实好好的。
      把它们判成「线上没有」就会把真发过的稿子改成未确认 —— 假警比漏报更贵。

    ⚠ 必须节流 + 退避：腾讯云连续打响会回 **987 字节的风控插页**（2026-11-20
      实测 31 条连打全军覆没，单条间隔 3 秒再打就正常 59KB）。插页一律算
      「没取到证据」，绝不算「线上没有」。
    """
    import time
    import yun_state as Y
    res = {"ok": 0, "title_missing": [], "title_drift": [],
           "gone": [], "auditing": [], "fail": []}
    for cid in sorted(recs):
        r = recs[cid]
        rid = str(r.get("remote_id") or "")
        if r.get("state") != "published" or not rid:
            continue
        t = _t(r)
        page = {}
        for attempt in range(1, retries + 1):
            page = Y.anon_get(Y.url_of(platform, rid), timeout=timeout,
                              platform=platform)
            if not (page.get("soft") or page.get("code") == 0):
                break
            if attempt < retries:
                time.sleep(3.0 * attempt)
        time.sleep(delay)
        if page.get("soft") or page.get("code") == 0:
            res["fail"].append((cid, rid, page.get("error")
                                or "风控插页：仅 %s bytes，重试 %d 次仍如此"
                                % (page.get("bytes"), retries)))
            continue
        if page.get("code") != 200:
            res["gone"].append((cid, rid, "HTTP %s" % page.get("code")))
            continue
        pt = _clean_page_title(page.get("title"))
        if page.get("banner"):
            res["auditing"].append((cid, rid, "公开页有审核横幅：%s" % pt[:30]))
            continue
        if not t:
            res["title_missing"].append((cid, rid, pt))
        elif Y.norm(t) in Y.norm(pt):
            res["ok"] += 1
        else:
            res["title_drift"].append((cid, rid, pt))
    return res


# --------------------------------------------------------------- 写盘
def patch_manifest(changes):
    """按 (cid, platform) 单条patch manifest；**绝不整本重刷**。

    `Manifest.save()` 那条路会先 `load()`（按 db 覆盖 state/at）再整本 dump，
    一次无关写就能把别的记录的真值冲掉（2026-11-20 事故：11 条 state 回退 +
    24 条 at 回退）。这里只改被对账动过的几条，其余原样保留。
    """
    d = atomicio.read_json(MANIFEST, default={}) or {}
    n = 0
    for (cid, plat), rec in changes.items():
        cur = dict((d.get(cid) or {}).get(plat) or {})
        for k, v in rec.items():
            if k == "at":
                if not cur.get("at") and v:
                    cur["at"] = v
            else:
                cur[k] = v
        d.setdefault(cid, {})[plat] = cur
        n += 1
    atomicio.write_json(MANIFEST, d, indent=1, sort_keys=True)
    return n


def write_rows(actions):
    """actions: [(cid, platform, row_dict, force_state)] → 返回改动条数。"""
    conn = db.get_conn()
    n = 0
    try:
        for cid, plat, row, force in actions:
            if force:
                row = dict(row)
                row["force_state"] = True
            db.upsert_post(conn, cid, plat, row)
            n += 1
    finally:
        db.close_conn()
    return n


# --------------------------------------------------------------- 主流程
def run_list_platform(platform, args, report):
    recs = load_rows(platform)
    print("\n=== %s  本地台账 %d 条（published %d / draft %d / auditing %d）"
          % (platform, len(recs),
             sum(1 for r in recs.values() if r.get("state") == "published"),
             sum(1 for r in recs.values() if r.get("state") == "draft"),
             sum(1 for r in recs.values() if r.get("state") == "auditing")))

    try:
        remote, meta = PULLERS[platform](**({"pages": args.pages}
                                            if platform == "csdn" else {}))
    except Exception as e:                            # noqa: BLE001
        print("  ⛔ 拉不到线上已发列表：%s: %s" % (type(e).__name__, str(e)[:200]))
        print("  ⇒ 读不到 ≠ 线上没有，本平台跳过（不改任何记录）")
        report[platform] = {"pub_ok": False, "error": "%s: %s"
                            % (type(e).__name__, str(e)[:200])}
        return 0

    print("  线上已发表 %d 条；本地台账 %d 条" % (len(remote), len(recs)))
    cases = known_cases()
    buckets = plan(recs, remote, cases, title_corpus())
    dups = dup_ids(recs)

    for key, label in (("noid", "NO_ID   台账记已发但没 id → 补 id"),
                       ("stale", "STALE   本地 id 线上没有 → 换真 id"),
                       ("title_fix", "TITLE   台账 title 为空、线上有 → 回填"),
                       ("promote", "PROMOTE 台账记草稿、线上已发 → 改 published"),
                       ("orphan", "ORPHAN  线上有本地没登 → 补一条")):
        items = buckets[key]
        print("  [%s]  %d" % (label, len(items)))
        for it in items:
            if key == "noid":
                print("      %-30s → %-16s %s" % (it[0][:30], it[1], it[2][:30]))
            elif key == "title_fix":
                print("      %-30s %-12s ← %s" % (it[0][:30], it[1][:12], it[2][:34]))
            elif key == "stale":
                print("      %-30s %-12s → %-12s %s"
                      % (it[0][:30], it[1][:14], it[2][:14], it[3][:26]))
            elif key == "promote":
                print("      %-30s %-10s(%s) → %-12s %s"
                      % (it[0][:30], str(it[1])[:10], "draft", it[2][:14], it[3][:24]))
            else:
                print("      %-30s ← %-14s %s" % (it[0][:30], it[1], it[2][:30]))
    if dups:
        print("  [DUP_ID 两条 case 记着同一个 id（报告，不动）]  %d" % len(dups))
        for field, v, a, b in dups:
            print("      %-10s %-12s %s ⇄ %s" % (field, v, a[:28], b[:28]))
    if buckets["unclaimed"]:
        print("  [!] 不敢认，要人看  %d" % len(buckets["unclaimed"]))
        for kind, cid, t, why in buckets["unclaimed"]:
            print("      [%s] %-26s %s | %s"
                  % (kind, str(cid)[:26], str(t)[:28], why))

    report[platform] = {"pub_ok": True, "remote": len(remote),
                        "dups": dups, "unclaimed": buckets["unclaimed"],
                        "plan": {k: v for k, v in buckets.items() if k != "unclaimed"}}

    if not args.apply:
        return 1 if (buckets["unclaimed"] or dups) else 0

    actions, man, changed = [], {}, 0

    def _u(cid, aid):
        r0 = recs.get(cid) or {}
        return _url(platform, aid, r0.get("url") or r0.get("title") or "")

    def _pat(cid, aid):
        """写盘行必须显式带 published_at：`upsert_post` 在没带这个字段时
        会给非 draft 行默认填 **now** ⇒ 历史稿补账会被算成「今天发的」，
        直接吃掉当日档期（`publish_slot` 按 published_at 数今日配额）。"""
        r = recs.get(cid) or {}
        return (r.get("published_at")
                or (remote.get(aid) or {}).get("at") or "")

    for cid, rid, aid, atitle in buckets["stale"]:
        actions.append((cid, platform, {
            "state": "published", "remote_id": aid, "url": _u(cid, aid),
            "title": _t(recs[cid]) or atitle,
            "published_at": _pat(cid, aid),
            "note": _note(recs[cid], TIP["stale"] % (TODAY, rid, aid))}, False))
        man[(cid, platform)] = {"state": "published", "remote_id": aid,
                                "url": _u(cid, aid)}
        changed += 1
    for cid, aid, atitle in buckets["noid"]:
        actions.append((cid, platform, {
            "state": "published", "remote_id": aid, "url": _u(cid, aid),
            "title": _t(recs[cid]) or atitle,
            "published_at": _pat(cid, aid),
            "note": _note(recs[cid], TIP["noid"] % (TODAY, aid))}, False))
        man[(cid, platform)] = {"state": "published", "remote_id": aid,
                                "url": _u(cid, aid)}
        changed += 1
    for cid, old, aid, atitle in buckets["promote"]:
        actions.append((cid, platform, {
            "state": "published", "remote_id": aid, "url": _u(cid, aid),
            "title": _t(recs[cid]) or atitle,
            "published_at": _pat(cid, aid),
            "note": _note(recs[cid], TIP["promote"] % (TODAY, old, aid))}, False))
        man[(cid, platform)] = {"state": "published", "remote_id": aid,
                                "url": _u(cid, aid)}
        changed += 1
    for cid, aid, atitle in buckets["orphan"]:
        r0 = recs.get(cid) or {}
        at = (remote.get(aid) or {}).get("at") or ""
        actions.append((cid, platform, {
            "state": "published", "remote_id": aid, "url": _u(cid, aid),
            "title": _t(r0) or atitle, "published_at": at,
            "note": _note(r0, TIP["orphan"] % TODAY)}, False))
        man[(cid, platform)] = {"state": "published", "remote_id": aid,
                                "url": _u(cid, aid), "at": at}
        changed += 1
    for cid, rid, rtitle in buckets["title_fix"]:
        actions.append((cid, platform, {
            "title": rtitle,
            "published_at": recs[cid].get("published_at") or "",
            "note": _note(recs[cid], TIP["title"] % (TODAY, rtitle[:40]))}, False))
        man[(cid, platform)] = {"title": rtitle}
        changed += 1

    if not changed:
        print("  写盘 0 处")
        return 1 if (buckets["unclaimed"] or dups) else 0

    write_rows(actions)
    patch_manifest(man)
    bad = _verify(platform, actions)
    print("  写盘 %d 处；回读核验 %s" % (changed, "全部对上" if not bad else "失败：%s" % bad))
    report[platform]["written"] = changed
    report[platform]["verify_bad"] = bad
    return 0 if (not bad and not buckets["unclaimed"] and not dups) else 1


def run_cloud_platform(platform, args, report):
    recs = load_rows(platform)
    pub = [cid for cid, r in recs.items() if r.get("state") == "published"]
    print("\n=== %s  本地台账 %d 条（published %d）" % (platform, len(recs), len(pub)))
    print("  ⚠ 两朵云没有「我的文章」列表接口 ⇒ 只能逐篇匿名公开页核验，"
          "做不到 ORPHAN 认领")

    res = probe_cloud(platform, recs, timeout=args.timeout, delay=args.delay)
    print("  公开页核验：确认公开 %d / 标题待补 %d / 标题不符 %d / 页面不在 %d"
          " / 审核横幅 %d / 没取到证据 %d"
          % (res["ok"], len(res["title_missing"]), len(res["title_drift"]),
             len(res["gone"]), len(res["auditing"]), len(res["fail"])))
    if res["title_missing"]:
        print("  [TITLE_MISSING 台账标题是空的，公开页有标题 → 回填]  %d"
              % len(res["title_missing"]))
        for cid, rid, pt in res["title_missing"]:
            print("      %-30s %-9s ← %s" % (cid[:30], rid, pt[:50]))
    if res["title_drift"]:
        print("  [TITLE_DRIFT 页面在、标题与本行不一致（多半发后改过标题）]  %d"
              % len(res["title_drift"]))
        for cid, rid, pt in res["title_drift"]:
            print("      %-30s %-9s 线上：%s" % (cid[:30], rid, pt[:50]))
    if res["gone"]:
        print("  [GONE 公开页打不开 ⇒ 这篇可能已被平台删除]  %d" % len(res["gone"]))
        for cid, rid, why in res["gone"]:
            print("      %-30s %-9s %s" % (cid[:30], rid, why))
    if res["auditing"]:
        print("  [AUDITING 公开页出现审核横幅 ⇒ 状态应为 auditing]  %d"
              % len(res["auditing"]))
        for cid, rid, why in res["auditing"]:
            print("      %-30s %-9s %s" % (cid[:30], rid, why[:60]))
    if res["fail"]:
        print("  [PROBE_FAIL 网络失败/风控插页，不算证据]  %d" % len(res["fail"]))
        for cid, rid, why in res["fail"]:
            print("      %-30s %-9s %s" % (cid[:30], rid, str(why)[:60]))
    dups = dup_ids(recs)
    if dups:
        print("  [DUP_ID 两条 case 记着同一个 id（报告，不动）]  %d" % len(dups))
        for field, v, a, b in dups:
            print("      %-10s %-10s %s ⇄ %s" % (field, v, a[:28], b[:28]))

    report[platform] = {"pub_ok": True, "cloud": True, "ok": res["ok"],
                        "title_missing": res["title_missing"],
                        "title_drift": res["title_drift"],
                        "gone": res["gone"], "auditing": res["auditing"],
                        "probe_fail": res["fail"], "dups": dups}

    todo = (res["title_missing"] or res["title_drift"] or res["gone"]
            or res["auditing"] or res["fail"] or dups)
    if not a_cloud_write(args):
        if res["title_missing"] and not args.apply:
            print("  （标题回填只报告。加 --apply 才写）")
        if (res["gone"] or res["auditing"]) and not (args.apply and args.apply_cloud):
            print("  （状态降级只报告。确认无误后加 --apply --apply-cloud）")
        return 1 if todo else 0

    actions, man = [], {}
    for cid, rid, pt in res["title_missing"]:
        actions.append((cid, platform, {"title": pt,
                                        "note": _note(recs[cid],
                                                      TIP["title"] % (TODAY, pt[:40]))},
                        False))
        man[(cid, platform)] = {"title": pt}
    if args.apply_cloud:
        for cid, rid, _why in res["gone"] + res["auditing"]:
            actions.append((cid, platform, {
                "state": "auditing",
                "note": _note(recs[cid], TIP["unconf"] % (TODAY, _why[:50]))}, True))
            man[(cid, platform)] = {"state": "auditing"}
    if not actions:
        print("  写盘 0 处")
        return 1 if todo else 0
    write_rows(actions)
    patch_manifest(man)
    bad = _verify(platform, actions, check_state=False)
    print("  写盘 %d 处；回读核验 %s" % (len(actions), "全部对上" if not bad else "失败：%s" % bad))
    report[platform]["written"] = len(actions)
    report[platform]["verify_bad"] = bad
    return 0 if (not bad and not todo) else 1


def a_cloud_write(args):
    return bool(args.apply)


def _note(row, tip):
    return (row.get("note") or "") + tip


_TMPL = {
    "csdn": "https://blog.csdn.net/sinat_32396037/article/details/%s",
    "cnblogs": "https://www.cnblogs.com/thing-explainer/p/%s.html",
    "zhihu": "https://zhuanlan.zhihu.com/p/%s",
    "aliyun": "https://developer.aliyun.com/article/%s",
    "tencentcloud": "https://cloud.tencent.com/developer/article/%s",
}


def _url(platform, aid, old=""):
    """文章公开地址。CSDN 路径里带作者名（各账号不同），优先沿用旧行的前缀。"""
    old = old or ""
    if platform == "csdn" and "/article/details/" in old:
        return old.split("/article/details/")[0] + "/article/details/%s" % aid
    return (_TMPL.get(platform) or "%s") % aid


def _verify(platform, actions, check_state=True):
    """回读核验：不信自报，重新从 db 读一遍。"""
    after = load_rows(platform)
    bad = []
    for cid, _plat, row, _force in actions:
        cur = after.get(cid) or {}
        if check_state and row.get("state") and str(cur.get("state")) != str(row.get("state")):
            bad.append("%s state=%s" % (cid, cur.get("state")))
        elif row.get("remote_id") and str(cur.get("remote_id")) != str(row.get("remote_id")):
            bad.append("%s remote_id=%s" % (cid, cur.get("remote_id")))
        elif row.get("title") and str(cur.get("title")) != str(row.get("title")):
            bad.append("%s title" % cid)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", action="append", default=[],
                    choices=list(PLATFORMS))
    ap.add_argument("--apply", action="store_true", help="A 类写盘")
    ap.add_argument("--apply-cloud", action="store_true",
                    help="B 类（两朵云）也写盘：published → auditing")
    ap.add_argument("--pages", type=int, default=4, help="CSDN 翻页数（每页 20）")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--delay", type=float, default=1.5,
                    help="两朵云逐篇核验的间隔秒数（打太快会回 987 字节风控页）")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    plats = a.platform or list(PLATFORMS)
    report, rc = {}, 0
    for p in plats:
        if p in CLOUD_PLATFORMS:
            rc |= run_cloud_platform(p, a, report)
        else:
            rc |= run_list_platform(p, a, report)

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("\nJSON → %s" % a.json)

    print("\n%s" % ("有需要人看的项" if rc else "五个平台都没有需要人工处理的项"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
