#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hacker News 线索增强器 —— 补 `harvest.py --source hn` 没做的那一层。

定位（先读这段，别把它当第二个采集器）
--------------------------------------
`harvest.py` 已经把 Show HN 捞进 inbox，并抓 `/items/<id>` 评论树，链路完整。
再写一个平行采集器只会造出第二套去重口径。本脚本只补三件它没有的事：

  1. **域名/产品回溯**：用 Algolia 的 `restrictSearchableAttributes=url` 查某个
     域名在 HN 的全部历史投稿，等价于 README 里 `/from?site=` 的效果，但可达。
  2. **排名机制打标**：把 minimaxir/hacker-news-undocumented 揭示的隐性降权
     规则（flame-war / 无 URL / 教程 / 拉票）打到每条线索上，让人工筛选时
     知道「分数高不等于健康」。
  3. **隐藏端点 URL 生成**：`/over?points=` `/front?day=` `/from?site=`
     `/latest?id=` 等生成可点击链接，交给人工或 CDP 在能连通的机器上打开。

独立线索池（2026-11-13 方案 A，改链路前必读）
---------------------------------------------
HN 是创始人自述（`self_reported`），而主候选池 `data/candidates.json` 是按
**TrustMRR 支付侧直读证据**形状写死的状态机：`promote_to_candidates.py` 只挑
`harvest_source=="trustmrr"` + 非空 `trustmrr_slug`；`ai_verify.py` 见
`metrics.headline="未获取"` 就跳过；`fetch_tm_md.py` 会给 `hn-*` id 拼出
`trustmrr.com/startup/hn-*.md` 的 404。把 HN 线索写进主池的后果是
`pipeline_status` 跳到 S3，而唯一能补草稿的预核把 30 条全跳过 —— **S3
永远清不掉的死循环**（#162 写入 / #163 迁移的实测）。

所以写盘**只落 `data/hn_leads.json`**，与主池物理隔离。存储实现仍复用
`candidate_store` 的通用层并**显式传 `path=`**（DATA_SCHEMA.md §428 的硬规矩），
不另造第二套格式/锁口径。`pipeline_status.py` 硬编码读 candidates.json，
迁出后主池回空、阶段自动回 S7，triage 不再被 30 条 0 分记录污染。

⛔ **转轨不许伪造来源**：HN 线索要进正式库，只能人工回源拿到一手收入证据后
按正常采集流程重新进 inbox、由 `promote_to_candidates` 判定；直接把
`harvest_source` 改成 `trustmrr` 绕过安全阀是数据造假，禁止。

网络事实（2026-11-13 本机实测，见 docs/PRD.md §159）
---------------------------------------------------
  * `hn.algolia.com/api/v1/*` 可达（走 `harvest.http_json`，绕开本机环境代理）。
  * `news.ycombinator.com/*` 即使绕过代理也全部超时 —— 所以**隐藏端点只生成
    URL，不假装抓到了**；`--probe-html` 只是可选的可达性探测，默认不跑。

事实裁定归代码，不改口径
------------------------
  * 不抽金额、不猜 MRR。`metrics` 恒为 `{"headline": "未获取"}`。
  * 一律 `verification=unverified` / `source_kind=self_reported` /
    `harvest_source=hn`；`blocking` 写明分数是发帖时快照、必须回源核数。
  * `id = hn-<objectID>`：按平台条目 ID 认亲，不按会变的标题认亲。
  * 域名回溯出来的历史讨论帖标 `hn_role=history`，`--apply` 时一律跳过，
    不把讨论帖当新案例入库。

读写边界
--------
  * **默认纯只读**：只打报告到 stdout，不写任何文件。
  * `--out <path>` 才写 JSON 产物（走 atomicio，属于产物不属台账）。
  * 只有 `--apply --yes` 才碰 **`data/hn_leads.json`**（独立线索池），且过三道
    硬闸：线索池格式自检通过、线索池无「未暂存的工作区改动」、记录是 lead
    而非 history。单条走 `candidate_store.upsert_candidate(path=LEADS_PATH)`
    （持锁），绝不整本覆盖写。**主候选池永远只读、一个字节都不写。**
  * `--migrate-from-candidates --yes` 是一次性迁移（#163）：把 #162 误写进
    主池的 HN 条目搬到线索池，主池留全量非 HN 记录。先逐字节备份、逐条
    upsert、逐键比对，全部通过才写主池；失败时主池不动、重跑按 id 幂等。

用法
----
    python scripts/hn_leads.py                              # 默认干跑
    python scripts/hn_leads.py --min-points 100 --limit 30
    python scripts/hn_leads.py --query "AI agent" --since 2026-10-01
    python scripts/hn_leads.py --domain trustmrr.com        # 域名历史回溯
    python scripts/hn_leads.py --probe-html                 # 只探测 HN 主站可达性
    python scripts/hn_leads.py --self-test                  # 离线断言，不联网不写盘
    python scripts/hn_leads.py --out tmp/hn_leads/today.json
    python scripts/hn_leads.py --apply --yes               # 写独立线索池（不写主池）
    python scripts/hn_leads.py --migrate-from-candidates --yes  # 一次性迁移，可重跑

依赖：仅 Python 标准库 + 仓库内 harvest / candidate_store / atomicio。
"""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import atomicio  # noqa: E402
import candidate_store as CSt  # noqa: E402
import harvest as H  # noqa: E402

HN_WEB = "https://news.ycombinator.com"
ALGOLIA = "https://hn.algolia.com/api/v1"

#: 独立线索池（方案 A）：HN 自述线索与 TrustMRR 支付侧证据物理隔离。
#: 所有 candidate_store 调用都显式传 path=，绝不靠模块级 CAND_PATH 兜底。
LEADS_PATH = os.path.join(ROOT, "data", "hn_leads.json")
#: 主候选池只在「已在库」交叉核对时**只读**，任何写盘都不许指向它。
CAND_PATH = CSt.CAND_PATH

#: README「Hacker News Lists / Public URLs」里那些导航看不到的入口。
#: 只做 URL 拼装，不请求 —— 本机 HN 主站不通，别让「生成了」冒充「读到了」。
HIDDEN_LABELS = {
    "over": "高分榜（阈值可换）",
    "front": "任意一天的首页快照",
    "from": "某域名在 HN 的全部投稿",
    "latest": "按时间倒序看长讨论",
    "item": "原帖讨论页",
    "best": "近期高票链接",
    "pool": "主持人二次机会池",
    "shownew": "最新 Show HN",
}

FLAG_FLAME = "flame_war:评论数超过分数，疑似过热讨论（README: 会被降权）"
FLAG_NO_URL = "no_url:纯文字帖（README: 无 URL 隐性降权）"
FLAG_TUTORIAL = "tutorial:教程腔标题（README: 教程类被版主降权）"
FLAG_POLITICS = "politics:政治相关（README: 易被 flag 至死）"
FLAG_VOTE_BAIT = "vote_bait:标题含拉票/自投信号（README: 投票环检测会拦下）"
FLAG_HEALTHY = "healthy:分数高于评论数且带外链"
FLAG_LOW_SIGNAL = "low_signal:低分低讨论，只证明该域名被投过，不代表关注度"

TUTORIAL_RE = re.compile(r"\b(tutorial|how to|guide to|step by step|101|beginner'?s guide)\b", re.I)
POLITICS_RE = re.compile(r"\b(election|trump|biden|political|politics|censorship bill)\b", re.I)
# 真实语料：Algolia 高分 Show HN 第一条就是 "This up votes itself"（分开写），
# 只认连写的 upvote 会漏掉它 —— 这正是 README 说的「自投/拉票」样本。
VOTE_BAIT_RE = re.compile(
    r"\b(upvotes?|up\s+votes?|upvoting|please vote|vote for|self[- ]?upvot)", re.I)
SHOW_HN_RE = re.compile(r"^(Show|Ask|Launch)\s+HN\s*[:\-–]\s*", re.I)


# ---------------------------------------------------------------- 隐藏端点

def hidden_urls(object_id, domain="", created_at="", points=100):
    """生成 README 里那些未文档化端点的 URL（纯字符串，不联网）。

    `created_at` 用帖子自身日期，这样 `/front?day=` 打开就是它当天的首页，
    拿它判断「这产品当年在 HN 是什么位置」比看今天的分数有用。
    """
    oid = str(object_id or "").strip()
    day = (created_at or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = {
        "over": "%s/over?points=%d" % (HN_WEB, int(points)),
        "front": "%s/front?day=%s" % (HN_WEB, day),
        "best": "%s/best" % HN_WEB,
        "pool": "%s/pool" % HN_WEB,
        "shownew": "%s/shownew" % HN_WEB,
    }
    if oid:
        urls["item"] = "%s/item?id=%s" % (HN_WEB, oid)
        urls["latest"] = "%s/latest?id=%s" % (HN_WEB, oid)
    if domain:
        urls["from"] = "%s/from?site=%s" % (HN_WEB, urllib.parse.quote(domain))
    return urls


def probe_html(urls, timeout=8):
    """可选：逐个 GET 看 HN 主站通不通。只报可达性，不解析内容。

    `harvest.http_json` 绕开环境代理的口径在这里保持一致：本机代理对
    news.ycombinator.com 返回 CONNECT 502，直连也超时，两条都试过。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out = {}
    for key, url in urls.items():
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": H.UA}),
                             timeout=timeout) as r:
                body = r.read()
                out[key] = "HTTP %d / %d bytes" % (r.status, len(body))
        except Exception as e:                                 # noqa: BLE001
            out[key] = "UNREACHABLE %s" % type(e).__name__
    return out


# ---------------------------------------------------------------- 排名打标

def rank_flags(hit):
    """把 README 的隐性规则打到一条 hit 上。

    这里只做**提示**，不做筛选：降权信号不等于内容没价值，把线索删掉是
    人工该做的裁定。分数与评论数都来自 Algolia 的抓取时快照，不是现值。
    """
    title = H.clean(hit.get("title") or "")
    url = hit.get("url") or ""
    points = int(hit.get("points") or 0)
    comments = int(hit.get("num_comments") or 0)
    flags = []
    if comments > points and points > 0:
        flags.append(FLAG_FLAME)
    if not url:
        flags.append(FLAG_NO_URL)
    if TUTORIAL_RE.search(title):
        flags.append(FLAG_TUTORIAL)
    if POLITICS_RE.search(title):
        flags.append(FLAG_POLITICS)
    if VOTE_BAIT_RE.search(title):
        flags.append(FLAG_VOTE_BAIT)
    if not flags and url and points >= comments:
        # 域名回溯里 1 分 0 评论的帖子只是「存在过」，说它健康是误导。
        flags.append(FLAG_HEALTHY if points >= 30 else FLAG_LOW_SIGNAL)
    return flags


def _website_of(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return "", ""
    if host.startswith("www."):
        host = host[4:]
    return ("https://" + host) if host else "", host


def to_lead(hit, role="lead"):
    """一条 Algolia hit → 一条与候选池同形状的只读记录。

    字段刻意对齐 `harvest._entry_skeleton()`，这样将来真要入库时不用改口径。
    多出来的 `hn_*` 字段是附加线索，候选池允许保留未知键。
    """
    oid = str(hit.get("objectID") or "").strip()
    title = H.clean(hit.get("title") or "")
    body = H.clean(hit.get("story_text") or "")
    name = H.split_title(SHOW_HN_RE.sub("", title))[0][:90] or ("hn-%s" % oid)
    url = hit.get("url") or ("%s/item?id=%s" % (HN_WEB, oid))
    website, domain = _website_of(url)
    created = (hit.get("created_at") or "")[:10]
    points = int(hit.get("points") or 0)
    comments = int(hit.get("num_comments") or 0)

    return {
        "id": "hn-%s" % oid,
        "name": name,
        "name_en": name,
        "origin": "",
        "one_liner": (body[:240] or title)[:240],
        "category": "未分类",
        "verification": "unverified",
        "metrics": {"headline": "未获取"},
        "models": [],
        "note": ("HN 线索：发布于 %s，抓取时 %d 分 / %d 条讨论。"
                 "分数与评论数是发帖后某个时刻的快照，不是现值，也不是收入证据。"
                 % (created or "—", points, comments)),
        "blocking": "尚未人工核实：金额/定价/客户数一律回源官网或原帖核对，HN 自述不可直接采信",
        "source_url": url,
        "website": website,
        "harvest_source": "hn",
        "source_kind": "self_reported",
        "added_at": datetime.now().strftime("%Y-%m-%d"),
        "hn_id": oid,
        "hn_role": role,
        "hn_title": title,
        "hn_points": points,
        "hn_comments": comments,
        "hn_created_at": created,
        "hn_discussion": "%s/item?id=%s" % (HN_WEB, oid),
        "hn_flags": rank_flags(hit),
        "hidden_urls": hidden_urls(oid, domain, created),
    }


# ---------------------------------------------------------------- Algolia 取数

def _since_ts(text):
    if not text:
        return 0
    try:
        dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit("--since 需要 YYYY-MM-DD，收到：%s" % text)
    return int(dt.timestamp())


def _search(params, what):
    url = "%s/search?%s" % (ALGOLIA, urllib.parse.urlencode(params))
    try:
        return H.http_json(url, timeout=20)
    except Exception as e:                                     # noqa: BLE001
        print("[!] %s 请求失败：%s" % (what, e), file=sys.stderr)
        return {"hits": [], "nbHits": 0}


def _search_by_date(params, what):
    url = "%s/search_by_date?%s" % (ALGOLIA, urllib.parse.urlencode(params))
    try:
        return H.http_json(url, timeout=20)
    except Exception as e:                                     # noqa: BLE001
        print("[!] %s 请求失败：%s" % (what, e), file=sys.stderr)
        return {"hits": [], "nbHits": 0}


def fetch_leads(args):
    """主通道：高分 Show HN。返回 (leads, meta)。"""
    mp = 100 if args.min_points is None else args.min_points
    filters = []
    if mp > 0:
        filters.append("points>%d" % mp)
    if args.since:
        filters.append("created_at_i>%d" % _since_ts(args.since))
    nf = ",".join(filters) or "无（仅 tags=show_hn）"
    p = {"tags": "show_hn", "hitsPerPage": args.limit}
    if filters:
        p["numericFilters"] = nf
    if args.query:
        p["query"] = args.query
        data = _search(p, "Show HN 关键词检索")
    else:
        data = _search_by_date(p, "Show HN 高分榜")
    hits = data.get("hits") or []
    meta = {"channel": "Algolia /search" if args.query else "Algolia /search_by_date",
            "nbHits": data.get("nbHits", 0), "returned": len(hits), "filters": nf}
    return _dedup(hits), meta


def fetch_domain_history(domain, limit, min_points):
    """域名回溯：`restrictSearchableAttributes=url` 等价于 /from?site=，但可达。"""
    p = {"query": domain, "restrictSearchableAttributes": "url", "tags": "story",
         "hitsPerPage": limit}
    if min_points:
        p["numericFilters"] = "points>%d" % min_points
    data = _search(p, "域名历史回溯")
    hits = data.get("hits") or []
    meta = {"channel": "Algolia /search (url only)", "nbHits": data.get("nbHits", 0),
            "returned": len(hits),
            "filters": ("domain=%s / points>%d" % (domain, min_points)) if min_points
                       else ("domain=%s / 无分数闸" % domain)}
    return _dedup(hits, role="history"), meta


def _dedup(hits, role="lead"):
    """按 objectID 去重；lead 模式额外硬校验 `show_hn` tag。

    不信任查询参数：万一 Algolia 侧 tag 过滤失效，普通 story 会被当成
    Show HN 线索混进来。这里按返回的 `_tags` 再判一次，属于事实裁定归代码。
    """
    out, seen, skipped = [], set(), 0
    for h in hits:
        oid = str(h.get("objectID") or "").strip()
        if not oid or oid in seen:
            continue
        if role == "lead" and "show_hn" not in (h.get("_tags") or []):
            skipped += 1
            continue
        seen.add(oid)
        out.append(to_lead(h, role=role))
    if skipped:
        print("[!] %d 条无 show_hn tag，已丢弃（防普通 story 混入）" % skipped,
              file=sys.stderr)
    out.sort(key=lambda c: (-c["hn_points"], c["hn_created_at"]))
    return out


# ---------------------------------------------------------------- 只读去重视图

def existing_ids():
    """只读已有 id，用来标「已在库」。绝不写，也绝不拿子集去整本覆盖。

    三个只读来源：独立线索池（本脚本的写盘目标）、inbox（采集队列）、
    主候选池（**只读交叉核对**：万一某条 HN 线索已经人工回源、走正常流程
    晋升进主池，就别再当新线索重复采集；写盘永远只落 LEADS_PATH）。
    """
    ids = set()
    for label, p in (("线索池", LEADS_PATH), ("主候选池（只读）", CAND_PATH)):
        try:
            ids |= set(CSt.load_ids(path=p))
        except (OSError, ValueError) as e:
            print("[!] %s 读取失败（不阻断干跑）：%s" % (label, e), file=sys.stderr)
    inbox = os.path.join(ROOT, "data", "inbox.json")
    if os.path.isfile(inbox):
        try:
            with open(inbox, "r", encoding="utf-8") as f:
                for it in json.load(f):
                    if isinstance(it, dict) and it.get("id"):
                        ids.add(it["id"])
        except (OSError, ValueError) as e:
            print("[!] inbox 读取失败（不阻断干跑）：%s" % e, file=sys.stderr)
    return ids


# ---------------------------------------------------------------- 报告

def print_report(leads, meta, ids, show=15):
    print("通道：%s ｜ 命中总数 %s ｜ 本轮取回 %d ｜ 过滤 %s"
          % (meta["channel"], meta.get("nbHits", "?"), meta.get("returned", 0),
             meta.get("filters", "—")))
    known = sum(1 for c in leads if c["id"] in ids)
    print("线索 %d 条 ｜ 按 (hn_id) 已在库/队列 %d 条 ｜ 新线索 %d 条"
          % (len(leads), known, len(leads) - known))
    counts = {}
    for c in leads:
        for f in c["hn_flags"]:
            counts[f.split(":")[0]] = counts.get(f.split(":")[0], 0) + 1
    if counts:
        print("排名标记：" + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print("-" * 72)
    for c in leads[:show]:
        mark = "[已在库]" if c["id"] in ids else "[新]"
        # 显示平台原标题：name 是切掉 "Show HN:" 的产品名，报告里若只显示它，
        # 看上去会像普通 story，人工核对时容易误判来源。
        origin_title = c.get("hn_title") or c["name"]
        print("%s %5d分/%3d评 %s  %s"
              % (mark, c["hn_points"], c["hn_comments"],
                 c["hn_created_at"] or "—", origin_title[:62]))
        print("       %s" % c["hn_discussion"])
        if c["hn_flags"]:
            print("       标记：%s" % "；".join(f.split(":", 1)[1] for f in c["hn_flags"]))
    if len(leads) > show:
        print("... 另 %d 条（--show N 调整）" % (len(leads) - show))
    print("-" * 72)
    print("提示：所有记录 verification=unverified / source_kind=self_reported；"
          "隐藏端点链接在 hidden_urls 字段，本机 HN 主站不可达，未做抓取。")


# ---------------------------------------------------------------- 来源分流（纯函数）

def is_hn_lead(rec):
    """是否属于「#162 误写进主候选池的 HN 自述线索」。

    判定要两个条件同时成立：只看 `harvest_source=="hn"` 会误伤
    `harvest.py --source hn` 正常采进 inbox / 主池的条目；只看 `hn-` 前缀
    又会漏掉将来换 id 规则的记录。这批 #162 数据的特征正是两者兼有。
    """
    return (isinstance(rec, dict)
            and str(rec.get("harvest_source") or "").strip() == "hn"
            and str(rec.get("id") or "").startswith("hn-"))


def split_by_source(cands):
    """把主池**全量**拆成 (迁出到线索池的 HN 条目, 原样保留的其余条目)。"""
    move, keep = [], []
    for c in cands:
        (move if is_hn_lead(c) else keep).append(c)
    return move, keep


# ---------------------------------------------------------------- 写线索池（硬闸）

def _pool_dirty(path):
    """只拦「未暂存的工作区改动」，返回拦截说明（空串 = 放行）。

    `A `（已暂存的新文件）与 `??`（未跟踪的新池）没有历史基线可比，且
    `upsert_candidate()` 是持锁读-改-写，不会覆盖别人尚未落盘的改动；
    真正危险的是 ` M` / `MM`：上一轮刚写还没定稿，或字段被洗还没分辨。
    """
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel],
                             cwd=ROOT, capture_output=True, check=False)
    except (OSError, ValueError):
        return ""
    lines = [l for l in (out.stdout or b"").decode("utf-8", "replace").splitlines()
             if l.strip() and not l.startswith("??")]
    return "\n".join(l for l in lines if len(l) >= 2 and l[1] not in (" ", "?"))


def apply_to_pool(leads, ids):
    """写独立线索池 `data/hn_leads.json`。主候选池永不触碰。"""
    if os.path.isfile(LEADS_PATH) and not CSt.check(path=LEADS_PATH)[0]:
        raise SystemExit("拒绝写入：线索池格式自检未通过，先跑 "
                         "python scripts/candidate_store.py --path data/hn_leads.json --check")
    dirty = _pool_dirty(LEADS_PATH)
    if dirty:
        raise SystemExit("拒绝写入：data/hn_leads.json 有未暂存改动（%s）。"
                         "先提交或人工确认，别绕过。" % dirty.strip())
    todo = [c for c in leads if c["hn_role"] == "lead" and c["id"] not in ids]
    if not todo:
        print("没有需要写入的新线索。")
        return 0
    for c in todo:
        # 显式声明目标池：不靠文件名猜，路径被重定向到 tmp 探针时同样过闸
        CSt.upsert_candidate(c, path=LEADS_PATH, pool=CSt.POOL_LEADS)
    print("已写入 %d 条新线索到 %s"
          "（单条 upsert / 持锁 / 未整本覆盖；主候选池未触碰）。未自动提交。"
          % (len(todo), os.path.relpath(LEADS_PATH, ROOT).replace("\\", "/")))
    return len(todo)


# ---------------------------------------------------------------- 一次性迁移（#163）

def migrate_from_candidates():
    """把 #162 误写进主候选池的 HN 条目搬到独立线索池（可重跑、幂等）。

    安全顺序：**逐字节备份 → 逐条 upsert → 逐键比对 → 才写主池**。
    任何一步失败都不写主池；线索池的半成品按 id 幂等，重跑即可补齐。
    主池此刻的 dirty 状态正是本轮要迁出的数据，故不做 dirty 闸，
    改由「备份成功 + 全量读取 + 保留项原样」三重约束兜底。
    """
    if not CSt.check(path=CAND_PATH)[0]:
        raise SystemExit("拒绝迁移：主候选池格式自检未通过")
    cands = CSt.load_candidates(path=CAND_PATH)      # 全量，不是 --filter 子集视图
    move, keep = split_by_source(cands)
    if not move:
        print("主候选池已无 hn-<id> 线索，无需迁移（当前 %d 条原样保留）。" % len(keep))
        return 0
    print("迁移预演：主池全量 %d 条 → 迁出 %d 条 / 保留 %d 条"
          % (len(cands), len(move), len(keep)))

    # ① 逐字节备份（原字节，不重新序列化；DATA_SCHEMA.md §431 的刻意例外）
    bk = os.path.join(ROOT, "tmp", "candidates_before_hn_migrate_%s.json"
                      % datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(bk), exist_ok=True)
    with open(CAND_PATH, "rb") as f:
        orig_bytes = f.read()
    shutil.copy2(CAND_PATH, bk)
    with open(bk, "rb") as f:
        if f.read() != orig_bytes:
            raise SystemExit("拒绝迁移：备份逐字节校验失败 %s" % bk)
    print("备份：%s（%d bytes，逐字节一致）"
          % (os.path.relpath(bk, ROOT), len(orig_bytes)))

    # ② 逐条 upsert 到线索池（持锁，默认字段合并，不整本覆盖）
    for c in move:
        CSt.upsert_candidate(c, path=LEADS_PATH, pool=CSt.POOL_LEADS)

    # ③ 逐键比对：原记录每个键都必须在线索池里逐字相同（允许多出人工批注键）
    written = CSt.load_by_id(path=LEADS_PATH)
    for c in move:
        w = written.get(c["id"])
        if not w or any(w.get(k) != v for k, v in c.items()):
            raise SystemExit("拒绝写主池：线索池 %s 与原记录不一致（主池未动）"
                             % c["id"])
    if not CSt.check(path=LEADS_PATH)[0]:
        raise SystemExit("拒绝写主池：线索池格式自检未通过（主池未动）")

    # ④ 才写主池：keep = 全量减去迁出项，仍是完整列表，不是子集视图。
    # 显式开写盘准入（PRD #164）：万一还有 hn 线索混在 keep 里，这里再挡一次。
    CSt.save_candidates(keep, path=CAND_PATH, validate=True)

    # ⑤ 独立双回读
    after = CSt.load_candidates(path=CAND_PATH)
    if after != keep:
        raise SystemExit("迁移后主池回读不一致，请从备份 %s 逐字节恢复" % bk)
    if not CSt.check(path=CAND_PATH)[0]:
        raise SystemExit("迁移后主池格式自检未通过，请检查 %s" % bk)
    print("已迁移 %d 条 → data/hn_leads.json；主候选池 %d → %d 条；"
          "线索池现共 %d 条；备份 %s"
          % (len(move), len(cands), len(after), len(written),
             os.path.relpath(bk, ROOT)))
    return len(move)


# ---------------------------------------------------------------- 离线自测

def self_test():
    checks = []

    def chk(label, cond):
        checks.append((label, bool(cond)))

    u = hidden_urls("42009039", "example.com", "2019-05-01", 100)
    chk("over 端点带 points 参数", u["over"].endswith("/over?points=100"))
    chk("front 端点用帖子当天日期", "day=2019-05-01" in u["front"])
    chk("from 端点带域名", "site=example.com" in u["from"])
    chk("latest 端点带条目 id", u["latest"].endswith("latest?id=42009039"))
    chk("item 端点带条目 id", u["item"].endswith("item?id=42009039"))
    chk("hidden 端点集合齐全", set(HIDDEN_LABELS) <= set(u))

    hit = {"objectID": "123", "title": "Show HN: Foo — a tiny tool",
           "url": "https://foo.example.com/app", "points": 120,
           "num_comments": 30, "created_at": "2026-10-01T10:00:00Z",
           "story_text": "I built this."}
    lead = to_lead(hit)
    chk("id 按 objectID 稳定", lead["id"] == "hn-123")
    chk("同一条目两次构造 id 相同", lead["id"] == to_lead(hit)["id"])
    chk("去掉 Show HN 前缀", not lead["name"].startswith("Show HN"))
    chk("verification 恒 unverified", lead["verification"] == "unverified")
    chk("source_kind 恒 self_reported", lead["source_kind"] == "self_reported")
    chk("harvest_source 恒 hn", lead["harvest_source"] == "hn")
    chk("不抽金额，metrics 未获取", lead["metrics"] == {"headline": "未获取"})
    chk("blocking 要求回源", "回源" in lead["blocking"])
    chk("note 声明是快照", "快照" in lead["note"])
    chk("健康讨论标记", lead["hn_flags"] and lead["hn_flags"][0].startswith("healthy"))
    chk("低分历史帖不冒充健康", any(f.startswith("low_signal")
                                    for f in rank_flags(dict(hit, points=1, num_comments=0))))
    chk("字段覆盖候选池骨架", {"id", "name", "one_liner", "category", "metrics",
                              "note", "blocking", "source_url", "added_at"} <= set(lead))

    flame = dict(hit, points=10, num_comments=90)
    chk("评论数>分数 判 flame_war", any(f.startswith("flame_war") for f in rank_flags(flame)))
    chk("无 URL 判 no_url", any(f.startswith("no_url") for f in rank_flags(dict(hit, url=""))))
    chk("教程腔判 tutorial", any(f.startswith("tutorial")
                                 for f in rank_flags(dict(hit, title="Show HN: A how to guide"))))
    chk("拉票判 vote_bait", any(f.startswith("vote_bait")
                                for f in rank_flags(dict(hit, title="Show HN: This up votes itself"))))
    hist = to_lead(hit, role="history")
    chk("域名回溯标 history", hist["hn_role"] == "history")
    chk("since 日期解析按 UTC 且相邻两天差 86400 秒",
        _since_ts("1970-01-02") - _since_ts("1970-01-01") == 86400)
    chk("原文标题保留", lead["hn_title"] == hit["title"])
    chk("线索池与主候选池物理隔离",
        LEADS_PATH != CAND_PATH
        and LEADS_PATH.endswith(os.path.join("data", "hn_leads.json")))
    chk("is_hn_lead 只认 hn 来源 + hn- 前缀",
        is_hn_lead({"id": "hn-1", "harvest_source": "hn"})
        and not is_hn_lead({"id": "hn-1", "harvest_source": "trustmrr"})
        and not is_hn_lead({"id": "c-1", "harvest_source": "hn"}))
    mv, kp = split_by_source([
        {"id": "hn-1", "harvest_source": "hn"},
        {"id": "c-1", "harvest_source": "trustmrr"},
        {"id": "c-2"},
    ])
    chk("split_by_source 迁出/保留原样且保序",
        [c["id"] for c in mv] == ["hn-1"]
        and [c["id"] for c in kp] == ["c-1", "c-2"])
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        no_tag = _dedup([dict(hit, _tags=["story"])], role="lead")
    chk("无 show_hn tag 的混入条目被丢弃", no_tag == [])

    ok = sum(1 for _l, c in checks if c)
    for label, cond in checks:
        print("%s %s" % ("PASS" if cond else "FAIL", label))
    print("%d/%d PASS" % (ok, len(checks)))
    return 0 if ok == len(checks) else 1


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="HN 线索增强器：Algolia 取数 + 排名机制打标 + 隐藏端点 URL（默认纯只读）")
    ap.add_argument("--query", default="", help="主题关键词（走 /search；缺省按日期取高分榜）")
    ap.add_argument("--domain", default="", help="域名历史回溯（等价 /from?site=，只报不入库）")
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD（created_at_i 过滤）")
    ap.add_argument("--min-points", type=int, default=None,
                    help="最低分数：高分榜默认 100；域名回溯默认不过滤，"
                         "要过滤才显式给（否则会把低频但相关的讨论全滤掉）")
    ap.add_argument("--limit", type=int, default=30, help="取回条数，默认 30，上限 100")
    ap.add_argument("--show", type=int, default=15, help="报告里展示条数")
    ap.add_argument("--out", default="", help="把线索写成 JSON 产物（不写则只打报告）")
    ap.add_argument("--probe-html", action="store_true", help="探测 HN 主站隐藏端点可达性")
    ap.add_argument("--apply", action="store_true",
                    help="写独立线索池 data/hn_leads.json（不碰主候选池），必须同时给 --yes")
    ap.add_argument("--migrate-from-candidates", action="store_true",
                    help="一次性：把误写进主候选池的 HN 条目迁到线索池，必须同时给 --yes")
    ap.add_argument("--yes", action="store_true", help="写盘动作的二次确认")
    ap.add_argument("--self-test", action="store_true", help="离线断言，不联网不写盘")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if a.migrate_from_candidates:
        if not a.yes:
            raise SystemExit("--migrate-from-candidates 必须同时给 --yes")
        if a.apply:
            raise SystemExit("--migrate-from-candidates 与 --apply 互斥")
        migrate_from_candidates()
        return 0
    if a.limit < 1 or a.limit > 100:
        raise SystemExit("--limit 需要 1..100（Algolia 单次 hitsPerPage 上限），收到 %d" % a.limit)
    if a.apply and not a.yes:
        raise SystemExit("--apply 必须同时给 --yes（写前先跑干跑核报告）")
    if a.apply and a.domain:
        raise SystemExit("域名回溯是历史讨论，不许写线索池（去掉 --apply）")

    if a.domain:
        dm = 0 if a.min_points is None else a.min_points
        leads, meta = fetch_domain_history(a.domain, a.limit, dm)
    else:
        leads, meta = fetch_leads(a)

    ids = existing_ids()
    print_report(leads, meta, ids, show=a.show)

    if a.probe_html:
        print("HN 主站可达性探测（只报状态，不解析）：")
        sample = hidden_urls(leads[0]["hn_id"] if leads else "1",
                             a.domain, "", max(1, 100 if a.min_points is None
                                               else a.min_points))
        for key, status in probe_html(sample).items():
            print("  %-8s %s" % (key, status))

    if a.out:
        atomicio.write_json(a.out,
                            {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "meta": meta, "leads": leads},
                            indent=2)
        print("产物：%s（%d 条）" % (a.out, len(leads)))

    if a.apply:
        apply_to_pool(leads, ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
