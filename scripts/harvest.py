#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集脚本：把「可能值得看的新项目」捞进采集队列（inbox）。

只写采集队列，绝不写精写库——因为这一步的数字都还没核过。

用法：
    python scripts/harvest.py                  # 默认跑 Hacker News（免 key）
    python scripts/harvest.py --source all     # 跑所有可用源
    python scripts/harvest.py --source hn --limit 60 --dry-run
    python scripts/harvest.py --source ph --token <ProductHunt token>
    python scripts/harvest.py --max-inbox 400  # inbox 上限，超额自动归档（不删）

依赖：仅 Python 标准库。
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
UA = "Mozilla/5.0 (compatible; CaseLibraryHarvester/1.0)"

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "ALL_PROXY", "all_proxy")


# ---------------------------------------------------------------- 基础设施

def load_json(name, default=None):
    path = os.path.join(DATA_DIR, name + ".json")
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name, payload):
    path = os.path.join(DATA_DIR, name + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def http_json(url, timeout=25, headers=None, data=None):
    """带重试的 JSON 请求。默认绕过环境代理（本地代理常带 TLS 问题）。"""
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, data=data)

    proxies = {k: os.environ.get(k) for k in PROXY_ENV_KEYS if os.environ.get(k)}
    if proxies:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()

    last = None
    for attempt in range(3):
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (401, 403, 404):
                break
        except Exception as e:                                  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "unknown error")


def slugify(text, taken):
    base = re.sub(r"[^a-z0-9]+", "-", (text or "cand").lower()).strip("-") or "cand"
    if base not in taken:
        return base
    i = 2
    while "%s-%d" % (base, i) in taken:
        i += 1
    return "%s-%d" % (base, i)


def clean(text):
    """去 HTML 标签、反转义、压空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- 过滤器

class Filter:
    """照 sources.json 里的 filter_rules 去掉广告与新闻稿。"""

    def __init__(self, rules):
        self.drop = [k.lower() for k in rules.get("drop_keywords", [])]
        keep = [k.lower() for k in rules.get("keep_signals", [])]
        # 词边界匹配：避免 "source-bootstrapped OpenJDK" 这类技术术语误命中创业信号
        self.keep_re = re.compile(
            "|".join(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(k) for k in keep), re.I
        ) if keep else None

    def verdict(self, text):
        if not text:
            return False, "空文本"
        low = text.lower()
        for k in self.drop:
            if k in low:
                return False, "命中丢弃词: %s" % k
        if self.keep_re and self.keep_re.search(low):
            return True, ""
        # 含收入信号也放行
        if re.search(r"\b(mrr|arr|revenue|paying|subscribers?)\b", low):
            return True, ""
        return False, "无保留信号"


def extract_money(text):
    """从文本里抽出金额，返回 (数字, 原文片段)。

    注意：这只是「疑似金额」。可能是价格、可能是收入，也可能只是举例。
    所以返回值一律标注为待核，不能直接写进精写库。
    """
    if not text:
        return None, ""
    m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s?([kKmMbB])?", text)
    if not m:
        return None, ""
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(unit, 1)
    if val * mult < 50:                       # 太小的数多半是行价，不是收入
        return None, ""
    return int(val * mult), m.group(0)


# 看起来像「提问 / 讨论」而不是「产品」的标题
QUESTION_RE = re.compile(
    r"^(are|is|why|how|what|should|does|do|can|would|who|when|which|"
    r"anyone|thoughts|ask\s+hn|who\s+else|tell\s+hn)\b",
    re.I,
)


def split_title(title):
    """把 'Name – tagline' / 'Name: tagline' 拆成 (名称, 补充说明)。"""
    t = title.strip()
    m = re.match(r"^(.{2,60}?)\s*[–—\-:|·]\s+(.{4,})$", t)
    if m:
        head = m.group(1).strip(" -–—:|")
        # 名称看起来应该像专有名词：不含句末标点、词数不多
        if head and len(head.split()) <= 5 and not QUESTION_RE.match(head):
            return head, t
    return t[:90], t


def looks_like_product(item):
    """判断这条素材是不是「有人在为自己的产品说话」。"""
    title = item.get("title") or ""
    url = item.get("url") or ""
    if not title:
        return False, "无标题"
    if title.rstrip().endswith("?"):
        return False, "提问帖"
    if QUESTION_RE.match(title):
        return False, "讨论帖"
    # Ask HN 且没有外链 → 是讨论，不是产品
    if re.match(r"^(Ask|Tell)\s+HN", title, re.I) and "news.ycombinator.com" in url:
        return False, "Ask/Tell HN 讨论"
    return True, ""


# ---------------------------------------------------------------- 采集源

def harvest_hn(limit, keywords):
    """Hacker News via Algolia —— 免费、免 key、当前唯一零门槛可跑通的源。"""
    items = []
    queries = [
        # 最新发布：抢先看到刚上线的东西（分数通常还没起来）
        ("https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=%d" % limit),
        # 已跑出热度：分数有区分度，简报排序才有意义（光靠"最新"全是 0 分噪声）
        ("https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=points>15&hitsPerPage=%d"
         % max(20, limit // 2)),
    ]
    for kw in keywords:
        queries.append(
            "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=%s&hitsPerPage=%d"
            % (urllib.parse.quote(kw), max(10, limit // 2))
        )

    for url in queries:
        try:
            data = http_json(url)
        except Exception as e:                                 # noqa: BLE001
            print("    [!] HN 请求失败: %s" % e)
            continue
        for hit in data.get("hits", []):
            title = clean(hit.get("title") or "")
            if not title:
                continue
            body = clean(hit.get("story_text") or "")
            raw_item = {
                "title": title,
                "url": hit.get("url") or ("https://news.ycombinator.com/item?id=%s" % hit.get("objectID")),
            }
            ok, _why = looks_like_product(raw_item)
            if not ok:
                continue
            name, _ = split_title(re.sub(r"^(Show|Ask)\s+HN\s*[:\-–]\s*", "", title, flags=re.I))
            items.append({
                "name": name[:90],
                "one_liner": (body[:240] or title)[:240],
                "url": raw_item["url"],
                "points": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
                "created_at": (hit.get("created_at") or "")[:10],
                "_blob": title + " " + body,
                "_origin": "hn",
            })
    return items


def harvest_producthunt(limit, token):
    """Product Hunt GraphQL —— 需要 token。"""
    if not token:
        print("    [skip] Product Hunt 需要 token，跳过（--token 或在环境变量 PH_TOKEN 里给）")
        return []
    query = """
    query($n:Int){ posts(first:$n, order:VOTES){ edges{ node{
      name tagline url votesCount createdAt
      topics{ edges{ node{ name } } }
      makers{ name }
    } } } }
    """
    payload = json.dumps({"query": query, "variables": {"n": limit}}).encode("utf-8")
    try:
        data = http_json("https://api.producthunt.com/v2/api/graphql",
                         headers={"Authorization": "Bearer " + token,
                                  "Content-Type": "application/json"},
                         data=payload)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] PH 请求失败: %s" % e)
        return []

    items = []
    edges = (((data.get("data") or {}).get("posts") or {}).get("edges")) or []
    for e in edges:
        n = e.get("node") or {}
        topics = " ".join(
            (t.get("node") or {}).get("name", "")
            for t in ((n.get("topics") or {}).get("edges") or [])
        )
        items.append({
            "name": (n.get("name") or "")[:90],
            "one_liner": clean(n.get("tagline") or "")[:240],
            "url": n.get("url") or "",
            "points": n.get("votesCount") or 0,
            "comments": 0,
            "created_at": (n.get("createdAt") or "")[:10],
            "_blob": "%s %s %s" % (n.get("name") or "", n.get("tagline") or "", topics),
            "_origin": "ph",
        })
    return items


def _fetch_html(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    proxies = {k: os.environ.get(k) for k in PROXY_ENV_KEYS if os.environ.get(k)}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if proxies \
        else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _walk_jsonld(node, out):
    """递归收集 JSON-LD 里的 ItemList 条目。"""
    if isinstance(node, list):
        for x in node:
            _walk_jsonld(x, out)
    elif isinstance(node, dict):
        if node.get("@type") == "ItemList":
            for it in (node.get("itemListElement") or []):
                out.append(it)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_jsonld(v, out)


def harvest_trustmrr(limit):
    """抓 TrustMRR 首页：JSON-LD 验证收入榜 + 市场挂牌卡片。

    页面是服务端渲染的，页面里就有数据；但卡片信息在 img 的 alt 属性和
    Price / Multiple 的 <p> 里，所以要按卡片切分再逐个提取。
    """
    try:
        page = _fetch_html("https://trustmrr.com/")
    except Exception as e:                                     # noqa: BLE001
        print("    [!] TrustMRR 请求失败: %s" % e)
        return []

    items = []

    # --- 1) JSON-LD 里的 Top startups by verified revenue ---
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except Exception:                                      # noqa: BLE001
            continue
        found = []
        _walk_jsonld(data, found)
        for it in found:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or (it.get("item") or {}).get("name")
            if not name:
                continue
            desc = clean(it.get("description") or (it.get("item") or {}).get("description") or "")
            url = it.get("url") or (it.get("item") or {}).get("url") or "https://trustmrr.com/"
            items.append({
                "name": str(name)[:90],
                "one_liner": desc[:240],
                "url": url,
                "points": 0, "comments": 0,
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "_blob": "%s %s verified revenue mrr" % (name, desc),
                "_verified_rank": True,
                "_origin": "trustmrr",
            })

    # --- 2) 市场挂牌卡片 ---
    cards = re.split(r'FOR SALE', page)
    seen = set()
    for c in cards[1:]:
        card = c[:2200]                     # 只看卡片内部，避免串到下一张
        name_m = re.search(r'alt="([^"]{2,40})"', card)
        if not name_m:
            continue
        name = html.unescape(name_m.group(1)).strip()
        if name.lower() in seen or name.lower() == "anonymous startup":
            continue

        # Price / Multiple
        price_m = re.search(r'Price</p>\s*<p[^>]*>\s*\$?([\d\.]+)\s*([km])?', card)
        mult_m = re.search(r'Multiple</p>\s*<p[^>]*>\s*([\d\.]+)x', card)
        rev_m = re.search(r'Revenue</p>\s*<p[^>]*>\s*\$?([\d\.]+)\s*([km])?', card)
        # 分类：卡片里第一个 uppercase 小标签
        cat_m = re.search(r'uppercase[^>]*>([A-Za-z &\-]{3,30})</', card)

        def fmt(m):
            if not m:
                return None
            v = m.group(1)
            u = (m.group(2) or "").lower()
            return "$" + v + (u if u else "")

        bits = []
        if rev_m:
            bits.append("月收入 " + fmt(rev_m))
        if price_m:
            bits.append("要价 " + fmt(price_m))
        if mult_m:
            bits.append("倍数 " + mult_m.group(1) + "x")
        if not bits:
            continue

        seen.add(name.lower())
        items.append({
            "name": name[:90],
            "one_liner": "挂牌出售中（TrustMRR 市场）：" + "、".join(bits),
            "url": "https://trustmrr.com/marketplace",
            "points": 0, "comments": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d"),
            "_blob": "for sale %s %s %s" % (name, (cat_m.group(1) if cat_m else ""), " ".join(bits)),
            "_origin": "trustmrr",
            "_listing": {
                "revenue": fmt(rev_m), "price": fmt(price_m),
                "multiple": (mult_m.group(1) + "x") if mult_m else None,
                "category": (cat_m.group(1).strip() if cat_m else None),
            },
        })
        if len(items) >= limit * 3:
            break

    return items


# ---------------------------------------------------------------- 容量与简报

# inbox 默认保留最近这么多条；超出的移入 inbox_archive.json（留底，不删除）。
INBOX_CAP_DEFAULT = 400

# 商业信号：给简报排序加权——「在收钱」的排前面，「纯好玩」的排后面
REV_WORD_RE = re.compile(
    r"\b(mrr|arr|revenue|paying|subscribers?|customers?|pricing|monetiz\w*)\b", re.I)
PRICE_RE = re.compile(r"\$[\d,]+")


def prune_inbox(items, cap, archive_name="inbox_archive"):
    """限制 inbox 条数：只保留最近 cap 条，超出的移入归档文件（永不删除）。

    items 由新到旧排列（采集时 insert(0)），所以列表尾部就是最旧的。
    返回 (保留列表, 本次归档条数, 归档累计条数)。
    """
    existing = load_json(archive_name, []) or []
    if cap <= 0 or len(items) <= cap:
        return items, 0, len(existing)

    keep, spill = items[:cap], items[cap:]
    today = datetime.now().strftime("%Y-%m-%d")
    for it in spill:
        it["archived_at"] = today
    archive = existing + spill
    save_json(archive_name, archive)
    return keep, len(spill), len(archive)


def write_report(added_pairs, by_source, inbox_total, archived_now,
                 archive_total, name="last_harvest"):
    """写本次采集简报，供定时任务直接读取汇报，不必去解析 stdout。

    added_pairs 是 (条目, 热度) 列表。排序优先「有真实金额的挂牌」，
    其次按 HN 热度——这样简报顶部就是最值得先核的。
    """
    def rank(pair):
        cand, points = pair
        text = "%s %s" % ((cand.get("metrics") or {}).get("headline") or "",
                          cand.get("one_liner") or "")
        has_trade = 1 if (cand.get("trade") or {}).get("revenue") else 0
        has_price = 1 if PRICE_RE.search(text) else 0
        has_rev_word = 1 if REV_WORD_RE.search(text) else 0
        # 有挂牌金额 > 文本里出现金额 > 出现收入词 > HN 热度
        return (has_trade, has_price, has_rev_word, points or 0)

    top = sorted(added_pairs, key=rank, reverse=True)[:8]
    payload = {
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "added": len(added_pairs),
        "by_source": by_source,
        "inbox_total": inbox_total,
        "archived_this_run": archived_now,
        "archive_total": archive_total,
        "top": [
            {
                "name": c.get("name") or "",
                "headline": (c.get("metrics") or {}).get("headline") or "",
                "heat": p or 0,
                "category": c.get("category") or "",
                "source": c.get("harvest_source") or "",
                "url": c.get("source_url") or "",
            }
            for c, p in top
        ],
    }
    save_json(name, payload)
    return payload


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="采集候选项目到采集队列")
    ap.add_argument("--source", default="hn",
                    choices=["hn", "ph", "trustmrr", "all"],
                    help="要跑的采集源（默认 hn）")
    ap.add_argument("--limit", type=int, default=50, help="每个源最多取多少条")
    ap.add_argument("--token", default=os.environ.get("PH_TOKEN", ""),
                    help="Product Hunt token")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--max-inbox", type=int, default=INBOX_CAP_DEFAULT,
                    help="inbox 条数上限，超出的移入归档文件（不删除）。0 = 不限")
    args = ap.parse_args()

    if not os.path.isdir(DATA_DIR):
        print("[!] 找不到 data 目录: %s" % DATA_DIR)
        return 1

    src_cfg = load_json("sources", {}) or {}
    rules = src_cfg.get("filter_rules", {}) if isinstance(src_cfg, dict) else {}
    flt = Filter(rules)

    keywords = ["MRR", "ARR", "bootstrapped", "solo founder", "side project",
                "indie hacker", "my SaaS"]

    print("=" * 60)
    print("  采集候选项目 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)

    raw = []
    want = {"hn", "ph", "trustmrr"} if args.source == "all" else {args.source}

    if "hn" in want:
        print("[*] Hacker News (Algolia，免 key)")
        got = harvest_hn(args.limit, keywords)
        print("    → 拉到 %d 条原始素材" % len(got))
        raw += got

    if "ph" in want:
        print("[*] Product Hunt")
        got = harvest_producthunt(args.limit, args.token)
        print("    → 拉到 %d 条" % len(got))
        raw += got

    if "trustmrr" in want:
        print("[*] TrustMRR 榜单 / 市场")
        got = harvest_trustmrr(args.limit)
        print("    → 拉到 %d 条" % len(got))
        raw += got

    if not raw:
        print("\n没有拿到任何素材。检查网络，或换个源试试。")
        return 1

    # 过滤
    print("\n[*] 过滤广告与新闻稿…")
    kept, dropped = [], 0
    reasons = {}
    for it in raw:
        ok, why = flt.verdict(it.get("_blob", ""))
        if ok:
            kept.append(it)
        else:
            dropped += 1
            reasons[why] = reasons.get(why, 0) + 1
    print("    保留 %d 条，丢弃 %d 条" % (len(kept), dropped))
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:6]:
        print("      - %s ×%d" % (why, n))

    # 组装候选 —— 写入「采集队列」(inbox)，不是候选池。
    # 三级漏斗：原始素材(inbox) → 候选池(candidates，人工筛过) → 精写案例(cases，核过数字)
    cands = load_json("inbox", []) or []
    taken = {c.get("id") for c in cands}
    taken_names = {(c.get("name") or "").lower() for c in cands}
    # 已经进过候选池或精写库的，不再重复采
    for other in ("candidates", "cases"):
        for c in (load_json(other, []) or []):
            taken.add(c.get("id"))
            if c.get("name"):
                taken_names.add(c["name"].lower())

    added = 0
    added_pairs = []                     # (条目, 热度) 供简报排序
    by_source_added = {}
    for it in kept:
        name = (it.get("name") or "").strip()
        if not name or name.lower() in taken_names:
            continue
        amt, frag = extract_money(it.get("_blob", ""))
        listing = it.get("_listing")
        verified_rank = it.get("_verified_rank")

        if verified_rank:
            cand = {
                "id": slugify(name, taken),
                "name": name, "name_en": name, "origin": "",
                "one_liner": it.get("one_liner") or "",
                "category": "未分类",
                "verification": "partial",
                "metrics": {"headline": "TrustMRR 验证收入榜条目（具体数字待补）"},
                "models": [],
                "note": "来自 TrustMRR 首页的「Top startups by verified revenue」结构化数据。"
                        "收入本身由支付网关验证，但榜单未直接给出金额，需打开公司页取数。"
                        "注意区分 MRR 与 all-time 两个口径。",
                "blocking": "需打开公司详情页取 MRR 与 all-time，并确认口径",
                "source_url": it.get("url") or "",
                "harvest_source": it.get("_origin") or "trustmrr",
                "added_at": datetime.now().strftime("%Y-%m-%d"),
            }
        else:
            cand = {
                "id": slugify(name, taken),
                "name": name, "name_en": name, "origin": "",
                "one_liner": it.get("one_liner") or "",
                "category": (listing or {}).get("category") or "未分类",
                "verification": "unverified",
                "metrics": {"headline": ("疑似金额 " + frag) if frag else "未获取"},
                "models": [],
                "note": "自动采集于 %s（来源 %s，发布于 %s，%s 分 / %s 条讨论）。"
                        "自动抽到的金额只作线索，可能是售价、举例或行价，不是收入。"
                        % (datetime.now().strftime("%Y-%m-%d"), it.get("_origin") or "unknown",
                           it.get("created_at") or "—", it.get("points", 0), it.get("comments", 0)),
                "blocking": "尚未人工核实：需确认公司真实存在、收入口径、是否有公开披露",
                "source_url": it.get("url") or "",
                "harvest_source": it.get("_origin") or args.source,
                "added_at": datetime.now().strftime("%Y-%m-%d"),
            }

        if listing:
            cand["metrics"] = {"headline": " · ".join(
                x for x in [("月收入 " + listing["revenue"]) if listing.get("revenue") else None,
                            ("要价 " + listing["price"]) if listing.get("price") else None,
                            ("倍数 " + listing["multiple"]) if listing.get("multiple") else None]
                if x)}
            cand["models"] = ["交易市场"]
            cand["trade"] = listing
            cand["verification"] = "partial"
            cand["blocking"] = "挂牌数据来自 TrustMRR 市场，收入由支付网关验证但口径需确认（月收入 or 30 天）"

        taken.add(cand["id"])
        taken_names.add(name.lower())
        cands.insert(0, cand)
        added += 1
        added_pairs.append((cand, it.get("points") or 0))
        src = cand.get("harvest_source") or "unknown"
        by_source_added[src] = by_source_added.get(src, 0) + 1

    print("\n[*] 新增采集队列条目 %d 条（去重后）" % added)
    if by_source_added:
        print("    来源分布：" + "、".join(
            "%s %d" % (k, v) for k, v in sorted(by_source_added.items(), key=lambda kv: -kv[1])))

    if args.dry_run:
        print("\n--dry-run：未写入。以下是预览：")
        for c, _p in added_pairs[:8]:
            print("   · %-30s %s" % (c["name"][:30], (c.get("metrics") or {}).get("headline", "")))
        return 0

    if not added:
        print("没有新条目，未改动文件。")
        return 0

    # 容量控制：inbox 只留最近 N 条，溢出的归档留底（永不删除）
    kept_list, spill, archive_total = prune_inbox(cands, args.max_inbox)
    save_json("inbox", kept_list)

    report = write_report(added_pairs, by_source_added,
                          len(kept_list), spill, archive_total)

    print("\n已写入 data/inbox.json（采集队列 %d 条）" % len(kept_list))
    if spill:
        print("  已归档 %d 条到 data/inbox_archive.json（留底不删，累计 %d 条）"
              % (spill, archive_total))
    print("  已写入 data/last_harvest.json（本次简报）")

    if report["top"]:
        print("\n[*] 本次最值得先看的 %d 条：" % len(report["top"]))
        for t in report["top"]:
            print("   · %-28s [%s] %s" % (t["name"][:28], t["source"], t["headline"]))

    print("\n三级漏斗的下一步：")
    print("  1. 打开案例库 →「采集队列」，扫一眼挑出值得看的")
    print("  2. 点「转入候选池」，它会进人工候选池")
    print("  3. 逐条核实数字后，再「提升为精写案例」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
