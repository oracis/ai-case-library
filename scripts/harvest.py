#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集脚本：把「可能值得看的新项目」捞进采集队列（inbox）。

只写采集队列，绝不写精写库——因为这一步的数字都还没核过。

用法：
    python scripts/harvest.py                  # 默认跑 Hacker News（免 key）
    python scripts/harvest.py --source all     # 跑所有可用源
    python scripts/harvest.py --source hn --limit 60 --dry-run
    python scripts/harvest.py --source indiehackers --limit 20
    python scripts/harvest.py --source arrclub --limit 20   # 只做校对，不进候选池
    python scripts/harvest.py --source ph --token <ProductHunt token>
    python scripts/harvest.py --max-inbox 400  # inbox 上限，超额自动归档（不删）
    python scripts/harvest.py --source hn --hn-comments 5   # 给新条目补评论正文

来源可信度（写进每条 source_kind，前端显示徽章）：
    verified       TrustMRR —— 支付网关 API 直读，不接受截图自报
    self_reported  Indie Hackers / Hacker News —— 创始人自述
    secondary      ARR Club —— 二手转述（只当校对参照，不进候选池）
    discovery      Product Hunt —— 榜单热度 ≠ 有人在付钱

依赖：仅 Python 标准库。
"""

import argparse
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import atomicio
import text_clean as TC

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
    """写 data/<name>.json。

    🚨 **必须保留原文件的换行格式**（红线 23，2026-10-10 实测踩到）。
    `open(path, "w")` 在 Windows 上是文本模式，会把 json 里的 `\n`
    翻译成 `\r\n` —— 而`data/` 下两种格式**并存**（2026-10-21 复核）：
        inbox.json                            = CRLF
        cases.json / candidates.json / inbox_archive.json = LF
    实测后果：跑一次 harvest 就把 623 条的 `inbox_archive.json`
    从 LF 悄悄改成 CRLF，13286 行全变、diff 全红，但**数据一个字没变**
    ⇒ 极易误判成「采集把队列搞乱了」，白排查半小时。

    所以先探原文件字节形态（不存在时按 LF 新建），再用对应 `newline`
    写。`newline=""` 会让 json 的 `\n` 原样落盘，不做任何翻译。

    原子性由 atomicio 保证（tmp + fsync + os.replace）：原地截断写在被
    Ctrl-C / 断电打断时会留下半截 JSON，而半截 JSON 会被上层当成空库。
    """
    path = os.path.join(DATA_DIR, name + ".json")
    atomicio.write_json(path, payload, indent=2, newline=detect_newline(path))


def detect_newline(path, default="\n"):
    """读文件头几百字节判断换行风格（实现已移到 atomicio，这里保留入口）。

    harvest 自己也要按原格式写盘：`open(path, "w")` 在 Windows 上会把
    `\n` 译成 `\r\n`，而 `data/` 下两种格式并存（inbox.json 是 CRLF，
    inbox_archive.json 是 LF）—— 换错格式的后果是整文件 diff 全红、
    数据却一个字没变，极易误判成「采集把队列搞乱了」。
    """
    return atomicio.detect_newline(path, default)


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


# 去重名单覆盖的四张表。**inbox_archive 必须在里面**（2026-10-21 修）：
# archive 装的是「队列满了溢出的」和「已晋升的」两类留底，两者都不该再被采回来。
# 漏掉它时，溢出归档的条目下一轮又会被原样采进队列 —— 实测 19 条 id 同时在
# inbox 与 archive（archive.archived_at 2026-09-17、inbox.added_at 2026-10-13），
# 另有 35 条仅大小写 / 标点不同。净效果是队列被老条目反复占满、新素材挤不进来，
# 而 last_harvest 的 added 数字虚高。
DEDUP_FILES = ("inbox", "inbox_archive", "candidates", "cases")


def seen_keys(inbox=None):
    """已有的条目，返回 {"ids", "names", "slugs"} 三个集合。

    `inbox` 传已读进来的列表可以省一次 IO（调用方本来就要用它）。
    名称走 text_clean.norm_name —— 与 triage 的重复检测同一个口径。

    `slugs` 专给 TrustMRR sitemap 通道：那条通道按 slug 判重（比名称准，
    同名不同 slug 的两个项目不该互相拦）。
    """
    if inbox is None:
        inbox = load_json("inbox", []) or []
    out = {"ids": set(), "names": set(), "slugs": set()}
    rows = [inbox] + [load_json(n, []) or [] for n in DEDUP_FILES[1:]]
    for group in rows:
        for c in group:
            if c.get("id"):
                out["ids"].add(c["id"])
            if c.get("trustmrr_slug"):
                out["slugs"].add(c["trustmrr_slug"])
            k = TC.norm_name(c.get("name"))
            if k:
                out["names"].add(k)
    return out


def clean(text):
    """去 HTML 标签、反转义、压空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- 过滤器

class Filter:
    """照 sources.json 里的 filter_rules 去掉广告与新闻稿。

    2026-10-23 修（howtomakemi 增量暴露的误杀）：
    - 词边界匹配：裸子串会把 facebook/notebook/writebook 里的 "ebook"、
      西语 tipos 里的 "ipo" 当成丢弃词——TrustMRR 一手档案里 14 条过门槛的
      被砍 13 条，全是这么死的。
    - 否定前缀保护："no free trial" 说的是「没有试用」，恰是正面信号，
      不能反过来把它当广告词毙掉。
    """

    # 否定前缀：出现在丢弃词前面时，说明该词被否定，不算命中
    NEG_PREFIX = re.compile(
        r"(?:\bno|\bnot|\bwithout|\bnever|\bzero)\s+$"
    )

    def __init__(self, rules):
        self.drop = [k.lower() for k in rules.get("drop_keywords", [])]
        keep = [k.lower() for k in rules.get("keep_signals", [])]
        # 词边界匹配：避免 "source-bootstrapped OpenJDK" 这类技术术语误命中创业信号
        self.keep_re = re.compile(
            "|".join(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(k) for k in keep), re.I
        ) if keep else None
        # drop 词同样按词边界编译（修子串误杀；"we're hiring" 这类含空格的
        # 句式词整串匹配）
        self.drop_re = re.compile(
            "|".join(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(k) for k in self.drop),
            re.I,
        ) if self.drop else None

    def verdict(self, text):
        if not text:
            return False, "空文本"
        low = text.lower()
        if self.drop_re:
            for m in self.drop_re.finditer(low):
                kw = m.group(0)
                # 否定前缀保护：往前看几个词是不是 "no/without/never" 等
                head = low[max(0, m.start() - 24):m.start()]
                if self.NEG_PREFIX.search(head):
                    continue
                return False, "命中丢弃词: %s" % kw
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
                "url": hit.get("url")
                       or ("https://news.ycombinator.com/item?id=%s" % hit.get("objectID")),
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
                # 留着给 enrich_added 抓评论区正文用（HN 的真话都在评论里）
                "object_id": str(hit.get("objectID") or ""),
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


def _fetch_html_bounded(url, timeout=30, max_seconds=150, max_bytes=16 * 1024 * 1024):
    """带**总耗时**上限的抓取（urlopen 的 timeout 只管 socket 空闲）。

    为什么需要这个：urlopen(timeout=N) 只约束「连接/读取空闲超过 N 秒」，
    对方慢慢滴数据的话连接一直是活的，永远不会超时 —— 实测 IndieHackers 的
    sitemap 分片（~10.6MB）能拖到 4 分钟以上，无人值守的每日任务不能这么等。

    所以这里按块读，到 max_seconds 或 max_bytes 就停下，把已下到的部分交出去。
    对 sitemap 这种「能解析多少算多少」的场景，部分结果也照样有用
    （正则要求闭合标签，截断处的半条记录会自然被丢掉，不会产生脏 slug）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    proxies = {k: os.environ.get(k) for k in PROXY_ENV_KEYS if os.environ.get(k)}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if proxies \
        else urllib.request.build_opener()

    started = time.time()
    buf = bytearray()
    truncated = False
    with opener.open(req, timeout=timeout) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) >= max_bytes:
                truncated = True
                break
            if (time.time() - started) > max_seconds:
                truncated = True
                break
    if truncated:
        print("    [i] 下到上限（%.0fs / %.1fMB），用已拿到的部分继续"
              % (time.time() - started, len(buf) / 1048576.0))
    return buf.decode("utf-8", "replace")


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


TRUSTMRR_API = "https://trustmrr.com/api/ai"
TRUSTMRR_SITE = "https://trustmrr.com"
TRUSTMRR_DISCOVERY_API = TRUSTMRR_SITE + "/api/ai/discovery"

# TrustMRR 上低于这个月收入的条目没有案例价值（多为刚上线或已停摆）。
# 0 表示「要求有收入数据」，但确实拿不到收入的仍放行（宁可留线索也不漏）。
TRUSTMRR_MRR_FLOOR = 100

# 国家码 -> 中文。只列常见来源国，其余回落成原始码。
COUNTRY_CN = {
    "US": "美国", "GB": "英国", "UK": "英国", "CA": "加拿大", "AU": "澳大利亚",
    "DE": "德国", "FR": "法国", "NL": "荷兰", "ES": "西班牙", "IT": "意大利",
    "SE": "瑞典", "NO": "挪威", "DK": "丹麦", "FI": "芬兰", "CH": "瑞士",
    "IE": "爱尔兰", "PL": "波兰", "PT": "葡萄牙", "AT": "奥地利", "BE": "比利时",
    "IN": "印度", "JP": "日本", "KR": "韩国", "SG": "新加坡", "CN": "中国",
    "HK": "中国香港", "TW": "中国台湾", "BR": "巴西", "AR": "阿根廷", "MX": "墨西哥",
    "IL": "以色列", "AE": "阿联酋", "TR": "土耳其", "UA": "乌克兰", "RU": "俄罗斯",
    "HR": "克罗地亚", "CZ": "捷克", "RO": "罗马尼亚", "TH": "泰国", "VN": "越南",
    "ID": "印度尼西亚", "MY": "马来西亚", "PH": "菲律宾", "NG": "尼日利亚",
    "ZA": "南非", "EG": "埃及", "CL": "智利", "CO": "哥伦比亚", "PE": "秘鲁",
    "NZ": "新西兰", "EE": "爱沙尼亚", "LT": "立陶宛", "LV": "拉脱维亚",
    "HU": "匈牙利", "BG": "保加利亚", "GR": "希腊", "RS": "塞尔维亚",
    "CY": "塞浦路斯", "PK": "巴基斯坦", "BD": "孟加拉国", "LK": "斯里兰卡",
    "NP": "尼泊尔", "GE": "格鲁吉亚", "AM": "亚美尼亚", "AZ": "阿塞拜疆",
    "KZ": "哈萨克斯坦", "UZ": "乌兹别克斯坦", "MA": "摩洛哥", "TN": "突尼斯",
    "KE": "肯尼亚", "GH": "加纳", "ET": "埃塞俄比亚", "SA": "沙特阿拉伯",
    "QA": "卡塔尔", "KW": "科威特", "BH": "巴林", "OM": "阿曼", "JO": "约旦",
    "LB": "黎巴嫩", "IQ": "伊拉克", "IR": "伊朗", "SK": "斯洛伐克",
    "SI": "斯洛文尼亚", "IS": "冰岛", "LU": "卢森堡", "MT": "马耳他",
    "MC": "摩纳哥", "MD": "摩尔多瓦", "BY": "白俄罗斯", "AL": "阿尔巴尼亚",
    "MK": "北马其顿", "BA": "波黑", "ME": "黑山", "CR": "哥斯达黎加",
    "PA": "巴拿马", "GT": "危地马拉", "DO": "多米尼加", "JM": "牙买加",
    "TT": "特立尼达和多巴哥", "UY": "乌拉圭", "PY": "巴拉圭", "BO": "玻利维亚",
    "EC": "厄瓜多尔", "VE": "委内瑞拉", "CU": "古巴", "ISL": "冰岛",
}


def country_cn(code):
    """把 ISO 国家码转成中文；认不出来就把原码留着。"""
    c = (code or "").strip().upper()
    if not c:
        return ""
    return COUNTRY_CN.get(c, c)


# TrustMRR 的 category 是英文（Artificial Intelligence / Design Tools / SaaS…），
# 而这个库自己的分类体系一直是中文的（AI 工具 / 开发者工具 / 交易市场…）。
# 两者原先直接混在 cases.json 里，于是同一个概念有两个分类名，
# 分类统计被劈成两半，封面风格表还要专门给英文名补一套键。
#
# 这里在**采集入口**归一化，和 country_cn 同一层：源站给什么不重要，
# 落进库里的必须是本库的分类。认不出来的原样留着 —— 别硬凑，
# 硬凑会把「确实没有合适分类」的情况伪装成「已归类」。
CATEGORY_CN = {
    "artificial intelligence": "AI 工具",
    "ai": "AI 工具",
    "ai tools": "AI 工具",
    "saas": "垂直行业 SaaS",
    "business": "企业服务",
    "enterprise": "企业服务",
    "marketing": "营销工具",
    "marketing tools": "营销工具",
    "sales": "企业服务",
    "developer tools": "开发者工具",
    "development": "开发者工具",
    "productivity": "企业服务",
    "design tools": "创作者经济",
    "design": "创作者经济",
    "content creation": "创作者经济",
    "content": "创作者经济",
    "social media": "创作者经济",
    "social": "创作者经济",
    "video": "创作者经济",
    "audio": "创作者经济",
    "music": "创作者经济",
    "marketplace": "交易市场",
    "ecommerce": "电商",
    "e-commerce": "电商",
    "retail": "电商",
    "education": "垂直行业 SaaS",
    "health": "垂直行业 SaaS",
    "healthcare": "垂直行业 SaaS",
    "finance": "垂直行业 SaaS",
    "fintech": "垂直行业 SaaS",
    "legal": "垂直行业 SaaS",
    "real estate": "Real Estate",
    "entertainment": "Entertainment",
    "mobile app": "移动应用",
    "mobile apps": "移动应用",
    "customer support": "客户服务",
    "customer success": "客户服务",
}


def category_cn(raw):
    """把 TrustMRR 的英文分类归一到本库的中文分类；认不出来就原样留着。"""
    c = (raw or "").strip()
    if not c:
        return ""
    return CATEGORY_CN.get(c.lower(), c)


def _money_cell(n):
    """把 API 里的裸数字格式化成阅读用字符串，给 metrics 兜底显示。"""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    return "$%s" % format(int(round(v)), ",")


def _pct_cell(n):
    """增速：API 给的是百分数（-13.44 表示 -13.44%）。"""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return ""
    return "%+.1f%%" % v


def _unwrap_api_item(it):
    """把 /api/ai 的一条记录转成内部素材 dict。

    这是 TrustMRR 官方给 AI 用的公开端点，字段比首页 HTML 全得多
    （官网、排名、MRR、客户数、增速、分类、国家、创始人 X 账号都在里面），
    所以优先走这条路；拿不到才回落去解析 HTML。
    """
    name = (it.get("name") or "").strip()
    slug = (it.get("slug") or "").strip()
    if not name or not slug:
        return None

    website = (it.get("website") or "").strip()
    page_url = it.get("url") or "%s/startup/%s" % (TRUSTMRR_SITE, slug)
    rev = it.get("revenue") or {}
    mrr = rev.get("mrr") or 0
    last30 = rev.get("last30Days") or 0
    total = rev.get("total") or 0
    rank = it.get("rank")
    category = category_cn(it.get("category"))
    country = country_cn(it.get("country"))
    stealth = bool(it.get("stealthMode"))
    on_sale = bool(it.get("onSale"))
    asking = it.get("askingPrice")

    # 主指标文案：MRR 与近 30 天取较大的那个当门面。
    # 只写 MRR 会把「30 天收入几百美元但 MRR 只有几十」的项目显示成个位数，
    # 看着像没在运营，实际是订阅口径与流水口径的差别。
    lead_kind, lead_val = "", 0
    if mrr and last30:
        if last30 > mrr:
            lead_kind, lead_val = "近 30 天收入", last30
        else:
            lead_kind, lead_val = "MRR", mrr
    elif mrr:
        lead_kind, lead_val = "MRR", mrr
    elif last30:
        lead_kind, lead_val = "近 30 天收入", last30

    if lead_val:
        head = "%s %s" % (lead_kind, _money_cell(lead_val))
        if rank:
            head += "，TrustMRR 第 %s 名" % rank
    elif total:
        head = "累计收入 %s（当月无数据）" % _money_cell(total)
    else:
        head = "TrustMRR 收录（收入未公开）"

    bits = []
    if on_sale and asking:
        bits.append("挂牌出售中，要价 %s" % _money_cell(asking))
        if it.get("multiple"):
            bits.append("倍数 %.2fx" % float(it["multiple"]))
    if stealth:
        bits.append("隐身模式：域名与公司名未公开")

    desc = clean(it.get("description") or "")
    note = ("来自 TrustMRR 官方公开数据端点 /api/ai（收入由支付网关 API 直读，非截图自报）。"
            "本条同步了官网、排名、收入、分类等字段，仍需人工确认口径与时效"
            "（页面标注的同步时间可能已过期）。")
    if bits:
        note += " " + "；".join(bits) + "。"

    return {
        "name": name[:90],
        "one_liner": desc[:240],
        "url": page_url,
        "website": website,
        "points": 0, "comments": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "_blob": "%s %s verified revenue mrr %s" % (name, desc, category),
        "_verified_rank": True,
        "_origin": "trustmrr",
        "_api": {
            "slug": slug,
            "website": website,
            "rank": rank,
            "mrr": mrr,
            "revenue_last30d": last30,
            "revenue_total": total,
            "customers": it.get("customers"),
            "subscriptions": it.get("activeSubscriptions"),
            "growth": it.get("growth30d"),
            "growth_mrr": it.get("growthMRR30d"),
            "asking_price": asking,
            "multiple": it.get("multiple"),
            "profit_margin": it.get("profitMarginLast30Days"),
            "visitors": it.get("visitorsLast30Days"),
            "revenue_per_visitor": it.get("revenuePerVisitor"),
            "category": category,
            "country": country,
            "founded": (it.get("foundedDate") or "")[:10],
            "x_handle": it.get("xHandle") or "",
            "x_founder": it.get("xFounderName") or "",
            "markdown_url": it.get("markdownUrl") or "",
            "pageviews": it.get("pageviewCount"),
            "offers": it.get("offerCount"),
            "on_sale": on_sale,
            "stealth": stealth,
            "note": note,
        },
    }


def harvest_trustmrr_api(limit):
    """走官方公开端点 /api/ai —— TrustMRR 抓取的首选通道。

    一次请求拿到 recentlyListedStartups + bestDeals 两个列表，
    每条 30+ 字段（含官网）。这是目前信息量最大、最稳的一条路。
    """
    try:
        data = http_json(TRUSTMRR_API, timeout=30)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] /api/ai 请求失败: %s" % e)
        return []

    if not isinstance(data, dict):
        print("    [!] /api/ai 返回结构异常")
        return []

    out, seen = [], set()
    skipped_stealth = skipped_small = 0
    for key in ("recentlyListedStartups", "bestDeals"):
        for it in (data.get(key) or []):
            if not isinstance(it, dict):
                continue
            # 隐身公司：名字是「Anonymous startup」假名、域名不公开，
            # 官网必然为空，收进来只是噪音。
            if it.get("stealthMode"):
                skipped_stealth += 1
                continue
            # 收入门槛：MRR 个位数的项目没有案例价值（刚上线 / 已停摆）。
            # 但「完全没收入数据」的放行——那可能只是新收录还没同步。
            mrr = ((it.get("revenue") or {}).get("mrr") or 0)
            last30 = ((it.get("revenue") or {}).get("last30Days") or 0)
            if 0 < max(mrr, last30) < TRUSTMRR_MRR_FLOOR:
                skipped_small += 1
                continue
            rec = _unwrap_api_item(it)
            if not rec:
                continue
            # 两个列表可能重叠，按 slug 去重
            slug = rec["_api"]["slug"]
            if slug in seen:
                continue
            seen.add(slug)
            out.append(rec)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    if skipped_stealth or skipped_small:
        print("    [i] 跳过：隐身公司 %d 条、月收入低于 $%d 的 %d 条"
              % (skipped_stealth, TRUSTMRR_MRR_FLOOR, skipped_small))
    return out


def harvest_trustmrr_discovery(limit):
    """TrustMRR discovery 端点：recentlyAdded + fastestGrowing 两组各 25 条。

    与 /api/ai 的关系：
      - /api/ai 字段全（40 个，含 markdownUrl/askingPrice/customers/foundedDate…），
        discovery 只有 16 个字段、没有 markdownUrl，但有 growth30d/growthMRR30d；
      - 两组端点按 slug 只重叠 4 条，discovery 能多带来约 41 个新 slug；
      - 两边都带 website（46/50）。
    所以把它当成 /api/ai 的**增量补充** —— 主通道仍是 /api/ai，discovery
    只补那些还没在 /api/ai 里出现的 slug，避免重复录入。

    复用 _unwrap_api_item 转内部格式；markdownUrl 缺失时 page_url 回落到
    /startup/<slug>（collect_pages 会再转成官方 .md）。
    """
    try:
        data = http_json(TRUSTMRR_DISCOVERY_API, timeout=30)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] /api/ai/discovery 请求失败: %s" % e)
        return []

    if not isinstance(data, dict):
        print("    [!] /api/ai/discovery 返回结构异常")
        return []

    out, seen = [], set()
    for key in ("recentlyAddedStartups", "fastestGrowingStartups"):
        for it in (data.get(key) or []):
            if not isinstance(it, dict):
                continue
            if it.get("stealthMode"):
                continue
            mrr = ((it.get("revenue") or {}).get("mrr") or 0)
            last30 = ((it.get("revenue") or {}).get("last30Days") or 0)
            if 0 < max(mrr, last30) < TRUSTMRR_MRR_FLOOR:
                continue
            rec = _unwrap_api_item(it)
            if not rec:
                continue
            slug = rec["_api"]["slug"]
            if slug in seen:
                continue
            seen.add(slug)
            out.append(rec)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def harvest_trustmrr(limit):
    """抓 TrustMRR：官方 /api/ai 主通道 + /api/ai/discovery 增量补充，最后才回落 HTML。

    历史教训：早先只读首页 JSON-LD 的 ItemList，那里 url 恰好是榜单自身页，
    于是「官网」这个概念根本没进采集器，候选池里全是「未分类 / 数字待补」。
    现在主通道换成官方给 AI 用的 JSON 端点，官网与收入都直接带回来。

    2026-09-24 新增：/api/ai 与 /api/ai/discovery 按 slug 只重叠 4 条，
    discovery 能给主通道补约 41 个新 slug，并带 growth30d（增长榜信号），
    所以把它当增量而非替代 —— 主通道优先，discovery 只补未覆盖的 slug。
    """
    api_items = harvest_trustmrr_api(limit)
    api_slugs = {i["_api"]["slug"] for i in api_items}
    extra = []
    for d in harvest_trustmrr_discovery(limit):
        if d["_api"]["slug"] not in api_slugs:
            extra.append(d)
            if len(api_items) + len(extra) >= limit:
                break
    merged = api_items + extra
    if merged:
        if extra:
            print("    [i] /api/ai 主通道 %d 条 + discovery 补 %d 条（共 %d）"
                  % (len(api_items), len(extra), len(merged)))
        return merged[:limit]
    print("    [i] /api/ai 与 discovery 均无数据，回落解析首页 HTML")
    return harvest_trustmrr_html(limit)


def harvest_trustmrr_html(limit):
    """回落通道：解析首页 HTML（JSON-LD 榜单 + 市场挂牌卡片）。

    页面是服务端渲染的，页面里就有数据；但卡片信息在 img 的 alt 属性和
    Price / Multiple 的 <p> 里，所以要按卡片切分再逐个提取。
    """
    try:
        page = _fetch_html(TRUSTMRR_SITE + "/")
    except Exception as e:                                     # noqa: BLE001
        print("    [!] TrustMRR 请求失败: %s" % e)
        return []

    items = []

    # --- 1) JSON-LD 里的 Top startups by verified revenue ---
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S):
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
    cards = re.split(r"FOR SALE", page)
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
        price_m = re.search(r"Price</p>\s*<p[^>]*>\s*\$?([\d\.]+)\s*([km])?", card)
        mult_m = re.search(r"Multiple</p>\s*<p[^>]*>\s*([\d\.]+)x", card)
        rev_m = re.search(r"Revenue</p>\s*<p[^>]*>\s*\$?([\d\.]+)\s*([km])?", card)
        # 分类：卡片里第一个 uppercase 小标签
        cat_m = re.search(r"uppercase[^>]*>([A-Za-z &\-]{3,30})</", card)

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
            "_blob": "for sale %s %s %s" % (
                name, (cat_m.group(1) if cat_m else ""), " ".join(bits)),
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


# ---------------------------------------------------------------- TrustMRR sitemap 全量通道
#
# 2026-10-14 新增。背景：/api/ai 与 discovery 两个公开端点合计只有约 100 条
# （各 25×2 的固定榜单，无分页无搜索），而 robots 公示的 startup-sitemap.xml
# 有 1.1 万+ 个 slug。这个通道把「发现面」从榜单扩到全站。
#
# 🚨 边界（llms.txt 明文，2026-10-14 实测读过全文）：
#   "Do not scrape the website for full marketplace access. Use the public
#    discovery tools for bounded snapshots."
# 所以本通道的做法是：
#   ① sitemap 是 robots 公示的索引 —— 只用来枚举 slug（发现面）；
#   ② 每个 startup 的详情只取官方 .md（llms.txt 指定的公开 AI 入口），
#      **不碰渲染后的 HTML**；
#   ③ 每轮只抓有界的一批（游标轮换 + 本地缓存 + 断点续抓），
#      **不做一次性全量镜像** —— 全量市场数据的获取官方指向认证 API，
#      bulk mirror 等于绕开那道门，不做。
# 覆盖节奏：默认每轮 100 个 .md，跑满全池约 110 轮（按日跑约 4 个月），
# 需要加速时用 --tm-sweep 显式加量（用户主动发起，仍是有界批量）。

TRUSTMRR_SITEMAP = "https://trustmrr.com/startup-sitemap.xml"
TM_CACHE = "trustmrr_profiles"           # data/trustmrr_profiles.json
TM_STALE_DAYS = 14                       # 档案超过这么多天，轮换时顺手刷新
TM_WORKERS = 5                           # .md 是缓存快照，5 并发足够，也别太吵
TM_MD_TIMEOUT = 25
TM_ROUND_BUDGET = 100                    # 每轮 .md 抓取上限（日常跑默认值）


def _fetch_text(url, timeout=30, gzip_ok=True):
    """抓文本，**支持 gzip**（urllib 不会自动解压，得自己来）。

    sitemap 不压是 1.7MB（文档实测 120s+ 拉不完），压后 160KB / 6s。
    服务器不发 gzip 时原样返回明文，两条路都走。
    """
    hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if gzip_ok:
        hdrs["Accept-Encoding"] = "gzip"
    req = urllib.request.Request(url, headers=hdrs)
    proxies = {k: os.environ.get(k) for k in PROXY_ENV_KEYS if os.environ.get(k)}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if proxies \
        else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        if gzip_ok and (r.headers.get("Content-Encoding") or "").lower().strip() == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def tm_sitemap_slugs():
    """抓 startup-sitemap.xml → {slug: lastmod}。

    ⚠ lastmod **不能当新鲜度用**：实测 11578 条里 11545 条的 lastmod 就是当天
    （站点每日重生 sitemap），它只证明 slug 存在，不说明数据动过。
    真正的增量判据在本地缓存（这个 slug 抓过没有），不在 lastmod。
    """
    try:
        xml = _fetch_text(TRUSTMRR_SITEMAP, timeout=40)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] TrustMRR sitemap 失败: %s" % e)
        return {}
    out = {}
    for m in re.finditer(
            r"<url>\s*<loc>https?://(?:www\.)?trustmrr\.com/startup/"
            r"([a-z0-9\-]+)</loc>(?:<lastmod>([^<]*)</lastmod>)?", xml):
        out[m.group(1)] = (m.group(2) or "")[:19]
    return out


def tm_cache_load():
    st = load_json(TM_CACHE, {}) or {}
    st.setdefault("sitemap", {})        # slug -> lastmod（sitemap 快照）
    st.setdefault("profiles", {})       # slug -> 精简档案（.md 解析结果）
    st.setdefault("gone", [])           # sitemap 里列着但 .md 404：真下架，别反复抓
    st.setdefault("unlisted", [])       # 线索源给的 slug 本站没有：名称未对齐，非下架
    st.setdefault("cursor", 0)          # 未抓池的轮换游标
    st.setdefault("fetched_at", "")
    return st


def _md_first_link(s):
    """取 markdown 链接的 (文本, 目标)；没有链接返回 ("", "")。"""
    m = re.search(r"\[([^\]]+)\]\(([^)]+)\)", s or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def _md_unescape(s):
    """还原 markdown 转义字符（\\| → | 等）。

    创始人名常带「名 \\| 公司」形态（表格里的转义竖线），不清会带着
    反斜杠进库。只动标点，不动正文。
    """
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", s or "")


def parse_trustmrr_md(text):
    """解析 /startup/<slug>.md（llms.txt 指定的公开 AI 档案）。

    比 /api/ai 多三样东西，每样都直接对应本库的红线：
      ① 出处分层（Verification Sources：哪些字段由外部 provider 验证）；
      ② Revenue last synced 时间戳（数据时效，红线：页面标注可能过期）；
      ③ 12 个月收入流水（红线 2「连续多月真实爬升」的唯一判据）。
    结构不认识的响应（404 兜底页等）返回 None —— 宁可漏抓不可错解析。
    """
    if not text or "## Profile" not in text:
        return None
    if "AI-agent Markdown profile" not in text:
        return None

    def field(label):
        m = re.search(r"^- %s:\s*(.+)$" % re.escape(label), text, re.M)
        return m.group(1).strip() if m else ""

    def money(pat, flags=0):
        m = re.search(pat, text, flags)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    p = {"stealth": False}
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    p["name"] = _md_unescape(clean(m.group(1))) if m else ""
    if not p["name"]:
        return None
    # .md 尾部注明：「Stealth Company is a placeholder label」——真名不公开
    if p["name"].lower() in ("stealth company", "anonymous startup"):
        p["stealth"] = True

    sm = re.search(r"^- Slug:\s*`?([a-z0-9\-]+)`?\s*$", text, re.M)
    p["slug"] = sm.group(1) if sm else ""
    _t, website = _md_first_link(field("Website"))
    p["website"] = website or clean(field("Website"))
    p["desc"] = clean(field("Description"))[:240]
    p["country"] = field("Country")
    p["founded"] = field("Founded date")[:10]

    _ft, furl = _md_first_link(field("Founder"))
    p["founder"] = _md_unescape(clean(_ft)) or _md_unescape(clean(field("Founder")))
    xm = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", furl or "")
    p["x_handle"] = xm.group(1) if xm else ""
    p["x_followers"] = field("X followers")

    sy = re.search(r"^- Revenue last synced:\s*([0-9T:\-\.Z]+)", text, re.M)
    p["synced"] = sy.group(1) if sy else ""
    rf = re.search(r"next refresh expected around ([0-9T:\-\.]+Z)", text)
    p["refresh"] = rf.group(1) if rf else ""

    # 收入表：周期 -> 金额（同时留快照行，两者应一致；不一致以快照行为准）
    rev = {}
    for row in re.finditer(
            r"^\|\s*(Last 24 hours|Last 7 days|Last 30 days|Last 3 months|"
            r"Last 6 months|Last 12 months|All time)\s*\|\s*\$?([\d,]+(?:\.\d+)?)\s*\|",
            text, re.M):
        rev[row.group(1)] = float(row.group(2).replace(",", ""))
    p["rev"] = rev
    p["last30"] = money(r"^- Last 30 days revenue snapshot:\s*\$?([\d,]+(?:\.\d+)?)", re.M)
    if p["last30"] is None:
        p["last30"] = rev.get("Last 30 days", 0)
    g = re.search(r"^- Last 30 days revenue growth:\s*([+-]?[\d.]+)%", text, re.M)
    p["growth"] = float(g.group(1)) if g else None
    p["total"] = money(r"^- All-time revenue snapshot:\s*\$?([\d,]+(?:\.\d+)?)", re.M)
    if p["total"] is None:
        p["total"] = rev.get("All time", 0)

    p["mrr"] = money(r"^- Current MRR:\s*\$?([\d,]+(?:\.\d+)?)", re.M) or 0
    subs = re.search(r"^- Current active subscriptions:\s*([\d,]+)", text, re.M)
    p["subs"] = int(subs.group(1).replace(",", "")) if subs else 0
    vis = re.search(r"^- Last 30 days traffic or visitors:\s*([\d,]+)", text, re.M)
    p["visitors"] = int(vis.group(1).replace(",", "")) if vis else 0
    p["rpv"] = money(r"^- Last 30 days revenue per visitor:\s*\$?([\d.]+)", re.M)

    # 月度流水。⚠ 正则只认 `| 2025-11 | $20,174 |` 形态：日流水表的
    # `| 2026-09-15 |` 后面跟的是 `-15` 再才是 `|`，天然不会误匹配。
    monthly = []
    for row in re.finditer(r"^\|\s*(20\d{2}-\d{2})\s*\|\s*\$?([\d,]+(?:\.\d+)?)\s*\|",
                           text, re.M):
        monthly.append([row.group(1), float(row.group(2).replace(",", ""))])
    p["monthly"] = monthly

    p["pricing"] = clean(field("Pricing model"))
    p["team"] = clean(field("Team size"))
    p["funding"] = clean(field("Funding"))

    # Markets 链接形如 /category/developer-tools —— 取 URL slug 映射本库分类
    #（链接文本 "Dev Tools" 不在 CATEGORY_CN 里，URL slug 在）
    p["category"] = ""
    for _lt, lurl in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", field("Markets")):
        cm = re.search(r"/category/([a-z0-9\-]+)", lurl)
        if cm:
            p["category"] = category_cn(cm.group(1).replace("-", " "))
            break
    if not p["category"]:
        p["category"] = category_cn(field("Markets"))

    acq = re.search(r"^## Acquisition\s*\n(.*?)(?=\n## |\Z)", text, re.S | re.M)
    acq_text = acq.group(1) if acq else ""
    p["on_sale"] = "Status: Listed for sale" in acq_text
    p["asking"] = money(r"^- Asking price:\s*\$?([\d,]+(?:\.\d+)?)", re.M)
    mm = re.search(r"^- Revenue multiple:\s*([\d.]+)x", acq_text, re.M)
    p["multiple"] = float(mm.group(1)) if mm else None
    om = re.search(r"^- Acquisition offers received:\s*(\d+)", acq_text, re.M)
    p["offers"] = int(om.group(1)) if om else None
    return p


def tm_models(p):
    """从 .md 推断商业模式。MRR>0 即订阅；否则看定价文案。

    /api/ai 那边是 `["订阅制"] if mrr else []` —— 对买断制产品（如 ShipFast：
    MRR 恒 0 但 all-time $1.27M）会丢信息。.md 有 Pricing model 原文，能补上。
    """
    if p.get("on_sale"):
        return ["交易市场"]
    if (p.get("mrr") or 0) > 0:
        return ["订阅制"]
    pricing = (p.get("pricing") or "").lower()
    if re.search(r"lifetime|one[- ]time|一次性|买断", pricing):
        return ["买断制"]
    if re.search(r"subscri|/mo|per month|monthly|/yr|annual|recurring", pricing):
        return ["订阅制"]
    return []


def tm_pick_batch(st, budget, seeds=None):
    """选出本轮要抓的 slug：**种子优先，其次没抓过的**（游标轮换），全覆盖后才刷新陈旧的。

    与 IH 分片池同一套路：游标记住上次抓到哪，每轮接着走，到尾部环绕。
    把偶发的慢抓摊到多次运行，不阻塞单次采集，也不需要一次性全量下载。

    seeds：外部线索源给的 slug 清单（如 howtomakemi 索引与 TrustMRR 的交叉集）。
    它们只是「排队优先级」，不改变抓取边界——仍受 budget 有界约束，
    已抓过/已 gone 的自动跳过。种子本身不进 st，避免污染轮换池。

    🚨 种子必须**先与 sitemap 求交**再排队。线索站（howtomakemi）的 slug 是
    别的站的命名，跨站不一定对齐（`cursor` / `ollama` / `openrouter` 这类在
    TrustMRR 根本没有同名条目）。若拿未收录的 slug 去请求 .md，服务端同样
    回 404，会被误判成「档案下架」写进 gone —— 2026-10-28 实测 233 条 gone
    全是这样攒出来的（与 profiles、sitemap 零交集，无一真下架）。
    「不存在这个 slug」≠「档案下架」，两者必须分开记。
    """
    slugs = list(st["sitemap"].keys())
    if not slugs:
        return []
    gone = set(st.get("gone") or [])
    seed_fresh = []
    for s in (seeds or []):
        if (s in st["sitemap"] and s not in st["profiles"]
                and s not in gone and s not in seed_fresh):
            seed_fresh.append(s)
    if seed_fresh:
        batch = seed_fresh[:budget]
        print("    [i] 种子队列 %d 个新 slug 优先抓取" % len(seed_fresh))
        return batch
    if seeds:
        # 种子模式：只抓种子。种子抓完就停，不轮换 sitemap 其他 slug——
        # 那会稀释「这个线索源发现了什么」。定期全面轮换请走不带
        # --tm-seeds 的普通模式。
        return []
    fresh = [s for s in slugs if s not in st["profiles"] and s not in gone]
    if fresh:
        cur = int(st.get("cursor") or 0) % len(fresh)
        batch = (fresh + fresh)[cur:cur + budget]
        st["cursor"] = (cur + len(batch)) % len(fresh)
        return batch
    stale = sorted((s for s in st["profiles"] if s not in gone),
                   key=lambda s: (st["profiles"][s].get("fetched") or "",
                                  st["profiles"][s].get("synced") or ""))[:budget]
    return stale


def tm_fetch_profiles(slugs, workers=TM_WORKERS, timeout=TM_MD_TIMEOUT,
                      checkpoint=None):
    """并发抓 .md 并解析。checkpoint(profiles) 每 100 个回调一次供落盘续抓。

    429 退避重试；404/410 记 gone（实测返回干净的 404 "Not found"，别下一轮再抓）；
    其余错误重试 3 次。**解析不出档案不算 gone**——那可能是瞬时故障或半截响应，
    下一轮轮换会重试；只有 HTTP 404/410 才判「真没了」。
    """
    results = {}
    gone = set()

    def one(slug):
        last_err = ""
        for attempt in range(3):
            try:
                text = _fetch_text("%s/startup/%s.md" % (TRUSTMRR_SITE, slug),
                                   timeout=timeout)
                p = parse_trustmrr_md(text)
                if p:
                    p["fetched"] = datetime.now().strftime("%Y-%m-%d")
                return slug, p, False
            except urllib.error.HTTPError as e:
                last_err = "HTTP %s" % e.code
                if e.code in (404, 410):
                    return slug, None, True
                if e.code == 429:
                    time.sleep(5 * (attempt + 1))     # 被限流就退避，别硬刚
                    continue
            except Exception as e:                                  # noqa: BLE001
                last_err = "%s: %s" % (type(e).__name__, e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        print("    [!] .md 抓取失败 (%s): %s" % (slug, last_err))
        return slug, None, False

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s) for s in slugs]
        done = 0
        for fut in as_completed(futs):
            slug, p, is_gone = fut.result()
            if p:
                results[slug] = p
            elif is_gone:
                gone.add(slug)
            done += 1
            if checkpoint and done % 100 == 0:
                checkpoint(results)
    if checkpoint:
        checkpoint(results)
    return results, gone


def harvest_trustmrr_sitemap(limit, budget=TM_ROUND_BUDGET, dry_run=False,
                             seeds_file=None):
    """TrustMRR sitemap 全量通道：游标轮换抓 .md 档案，按收入挑 Top 进队列。

    与 harvest_trustmrr（/api/ai 榜单）的分工：
      - /api/ai：字段全、带排名，但只有约 100 条固定榜单，无分页；
      - 本通道：发现面 1.1 万 slug，逐轮轮换覆盖，每轮有界。
    两边产物按 slug/name 双重去重（profiles 缓存 + 入库时再判一次）。

    seeds_file：外部线索源的 slug 清单文件（每行一个，# 开头为注释）。
    用来把别的站点的索引（如 howtomakemi）当发现面——slug 命中 TrustMRR
    的按一手档案抓，没命中的线索另行核验。种子只影响排队先后，抓取量与
    边界（llms.txt：有界批量、不全量镜像）不变。

    排序口径：max(MRR, 近30天) 降序；当前收入为 0 但 all-time 非零的
    （买断制/已衰退产品）按 all-time 参排 —— 证明「真收到过钱」，
    但 headline 会写明口径，不会伪装成当前收入（红线 1）。
    """
    seeds = []
    if seeds_file:
        try:
            with open(seeds_file, encoding="utf-8") as f:
                seeds = [ln.strip() for ln in f
                         if ln.strip() and not ln.startswith("#")]
            print("    [i] 种子清单 %s：%d 个 slug" % (seeds_file, len(seeds)))
        except OSError as e:
            print("    [!] 读不到种子清单 %s: %s（按普通轮换跑）" % (seeds_file, e))
    st = tm_cache_load()
    sitemap = tm_sitemap_slugs()
    if sitemap:
        fresh_n = sum(1 for s in sitemap if s not in st["sitemap"])
        st["sitemap"].update(sitemap)
        print("    [i] sitemap %d 个 slug（新增 %d）" % (len(sitemap), fresh_n))
    if not st["sitemap"]:
        print("    [!] sitemap 没拿到，本地也无缓存")
        return []

    batch = tm_pick_batch(st, budget, seeds=seeds)
    # 种子未对齐：线索源有、TrustMRR 没这个 slug（跨站命名差异）。单独记账，
    # 绝不能进 gone —— 那语义是「档案下架」，会把诊断和一键清库带偏。
    if seeds:
        unlisted_now = [s for s in seeds if s not in st["sitemap"]]
        if unlisted_now:
            st["unlisted"] = sorted(set(st.get("unlisted") or [])
                                    | set(unlisted_now))
            print("    [i] 种子中 %d 个 slug 未收录于 TrustMRR sitemap"
                  "（名称未对齐，非下架，跳过）" % len(unlisted_now))
    if not batch:
        # 种子已抓完：档案池里可能还有上轮被 limit 截断、没进队列的，
        # 直接走选拔（不抓取）。种子全进过库时才真没事干。
        if not (seeds and any(s in st["profiles"] for s in seeds)):
            print("    [i] 没有待抓的 slug")
            return []
        print("    [i] 种子已抓完，直接从档案池选拔")
    print("    [i] 本轮抓 %d 个 .md 档案（池 %d，已抓 %d，游标 %s）"
          % (len(batch), len(st["sitemap"]), len(st["profiles"]), st["cursor"]))

    def ckpt(partial):
        if not dry_run:
            st["profiles"].update(partial)
            save_json(TM_CACHE, st)

    got, gone = tm_fetch_profiles(batch, checkpoint=ckpt)
    st["profiles"].update(got)
    if gone:
        known = set(st.get("gone") or [])
        known.update(gone)
        st["gone"] = sorted(known)
    st["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not dry_run:
        save_json(TM_CACHE, st)
    # 抓取结果可观察：成功/失败分开报。失败的slug不记 gone，下轮轮换重试
    # （429 限流是主因，退避后多数能补上；实测一轮 250 个成功率约 94%）。
    print("    [i] 本轮抓到 %d 份档案，失败 %d 个（失败的留待下轮重试）"
          % (len(got), len(batch) - len(got) - len(gone)))

    # ---- 选拔：已在库的、隐身的、全零收入的不占位 ----
    # 判重口径与主流程同源（seen_keys 覆盖 inbox / archive / candidates / cases），
    # 名称归一化也共用 text_clean.norm_name —— 此前这里单独写了一套 .lower()，
    # 与主流程的「后缀/标点差异」判定不一致。
    seen = seen_keys()
    taken_slugs, taken_names = seen["slugs"], seen["names"]

    pool, skipped = [], {"dup": 0, "stealth": 0, "zero": 0, "floor": 0}
    seed_set = set(seeds)
    for slug, p in st["profiles"].items():
        if seed_set and slug not in seed_set:
            continue                      # 种子模式：只从本线索源的档案里选拔
        if p.get("gone"):
            continue
        if slug in taken_slugs or TC.norm_name(p.get("name")) in taken_names:
            skipped["dup"] += 1
            continue
        if p.get("stealth"):
            skipped["stealth"] += 1
            continue
        lead = max(p.get("mrr") or 0, p.get("last30") or 0)
        if lead == 0 and (p.get("total") or 0) == 0:
            # 全零：.md 收入表是全的，全零就是真没有收入，没有案例价值。
            # （/api/ai 那边放行零收入是因为「可能只是没同步」，.md 没有这个问题）
            skipped["zero"] += 1
            continue
        if 0 < lead < TRUSTMRR_MRR_FLOOR:
            skipped["floor"] += 1
            continue
        pool.append((slug, p, lead if lead else (p.get("total") or 0)))
    pool.sort(key=lambda t: -t[2])
    picked = pool[:limit]
    if skipped["dup"] or skipped["stealth"] or skipped["zero"] or skipped["floor"]:
        print("    [i] 档案池选拔：跳过 已在库 %d / 隐身 %d / 全零收入 %d / 低于 $%d %d"
              % (skipped["dup"], skipped["stealth"], skipped["zero"],
                 TRUSTMRR_MRR_FLOOR, skipped["floor"]))

    items = []
    for slug, p, _lead in picked:
        note = ("来自 TrustMRR 官方公开 Markdown 档案 %s/startup/%s.md"
                "（llms.txt 指定的 AI 入口；收入由支付提供商 API 直接读取验证，"
                "非截图自报）。" % (TRUSTMRR_SITE, slug))
        if p.get("synced"):
            note += "收入数据同步于 %s" % p["synced"]
        if p.get("refresh"):
            note += "，档案为缓存快照（下次刷新约 %s）" % p["refresh"]
        note += ("。口径：MRR = 当前月经常性收入（非订阅制项目恒 0）；"
                 "近 30 天 = 滚动 30 天已验证流水，含一次性与用量收入，"
                 "别当经常性收入引用；累计 = all-time。仍需人工确认口径与时效。")
        if p.get("monthly"):
            note += "已带 12 个月收入流水（monthly_timeline），可判连续多月是否真实爬升。"

        items.append({
            "name": p["name"][:90],
            "one_liner": (p.get("desc") or "")[:240],
            "url": "%s/startup/%s" % (TRUSTMRR_SITE, slug),
            "website": p.get("website") or "",
            "points": 0, "comments": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d"),
            "_blob": "%s %s verified revenue mrr" % (p["name"], p.get("desc") or ""),
            "_verified_rank": False,
            "_origin": "trustmrr",
            "_api": {
                "slug": slug,
                "website": p.get("website") or "",
                "rank": None,
                "mrr": p.get("mrr") or 0,
                "revenue_last30d": p.get("last30") or 0,
                "revenue_total": p.get("total") or 0,
                "customers": None,
                "subscriptions": p.get("subs") or 0,
                "growth": p.get("growth"),
                "growth_mrr": None,
                "asking_price": p.get("asking"),
                "multiple": p.get("multiple"),
                "profit_margin": None,
                "visitors": p.get("visitors") or 0,
                "revenue_per_visitor": p.get("rpv"),
                "category": p.get("category") or "",
                "country": country_cn(p.get("country")),
                "founded": p.get("founded") or "",
                "x_handle": p.get("x_handle") or "",
                "x_founder": p.get("founder") or "",
                "markdown_url": "%s/startup/%s.md" % (TRUSTMRR_SITE, slug),
                "pageviews": None,
                "offers": p.get("offers"),
                "on_sale": bool(p.get("on_sale")),
                "stealth": False,
                "note": note,
            },
            "_models": tm_models(p),
            "_freshness": p.get("synced") or "",
            "_monthly": p.get("monthly") or [],
        })
    return items


# ---------------------------------------------------------------- 来源性质
#
# 五个信息源的 A/B/B- 分级落到代码里就是这四个值。前端据此显示徽章，
# 避免把「支付网关验过的收入」和「公众号二手转述的数字」混进同一列。

SOURCE_KIND = {
    "trustmrr":     "verified",       # 支付网关 API 直读，不接受截图自报
    "indiehackers": "self_reported",  # 创始人自己发帖公布里程碑
    "hn":           "self_reported",  # 技术圈自曝，评论区才是真话
    "hackernews":   "self_reported",
    "arrclub":      "secondary",      # 二手转述，还自贴「Certificate」徽章
    "ph":           "discovery",      # 榜单热度 ≠ 有人在付钱
    "producthunt":  "discovery",
}

KIND_LABEL = {
    "verified":      "已验证收入",
    "self_reported": "自报数字",
    "secondary":     "二手转述",
    "discovery":     "仅线索",
}

# 可信度排序：数字越小越可信。给简报排序用。
KIND_RANK = {"verified": 0, "self_reported": 1, "secondary": 2, "discovery": 3}


def source_kind_of(origin):
    """采集来源 -> 可信度性质。认不出来的按最低档处理，宁可保守。"""
    return SOURCE_KIND.get((origin or "").strip().lower(), "discovery")


# URL 主机名 -> 采集源 id。存量条目往往没有 harvest_source，只有 source_url。
HOST_SOURCE = (
    ("trustmrr.com", "trustmrr"),
    ("indiehackers.com", "indiehackers"),
    ("news.ycombinator.com", "hn"),
    ("ycombinator.com", "hn"),
    ("hn.algolia.com", "hn"),
    ("producthunt.com", "producthunt"),
    ("arr.club", "arrclub"),
)


def infer_source(rec):
    """猜一条记录的来源。

    存量条目是手工录入的，没有 harvest_source，但通常有 source_url 或
    来源专属字段。不猜的话它们会全被当成 discovery（「仅线索」）——
    那会把一条 TrustMRR 验证过的收入错误地降级成线索。
    """
    src = (rec.get("harvest_source") or "").strip().lower()
    if src:
        return src
    if rec.get("trustmrr_slug"):
        return "trustmrr"
    if rec.get("indiehackers_slug"):
        return "indiehackers"
    if rec.get("hn_id"):
        return "hn"

    m = re.match(r"https?://([^/\s]+)", (rec.get("source_url") or "").strip())
    if not m:
        return ""
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]
    for dom, sid in HOST_SOURCE:
        if host == dom or host.endswith("." + dom):
            return sid
    return ""


def ensure_source_kind(records, force=False):
    """给老数据补 source_kind（按 harvest_source / source_url 推导）。

    存量条目是在加这个字段之前采的；没有它前端画不出徽章、排序也算不出可信度。
    推导是幂等的：跑多少遍结果都一样。

    force=True 时重算（用于修正早期版本把一切都当成 discovery 的误判），
    否则只补空缺的。返回改动条数。
    """
    filled = 0
    for c in records or []:
        if not isinstance(c, dict):
            continue
        if c.get("source_kind") and not force:
            continue
        kind = source_kind_of(infer_source(c))
        if c.get("source_kind") != kind:
            c["source_kind"] = kind
            filled += 1
    return filled


def parse_abbrev_money(text):
    """'$125K' / '$1.2M' / '$100M' / '$100' -> 整数。认不出来返回 None。

    与 extract_money() 的区别：这个不做金额下限过滤，也不返回原文片段，
    专给「已经是结构化字段」的场景用（IH 的 metrics 块、ARR Club 的 FAQ）。
    """
    if text is None:
        return None
    m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmMbB])?", str(text))
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1)
    return int(val * mult)


# ---------------------------------------------------------------- Indie Hackers
#
# 无官方 API：/product/<slug>.json 与 /api/product/<slug> 实测都是 404。
# 列表页的分页与排序是前端 JS 做的（?page=2 与 ?page=1 返回完全相同字节），
# 所以枚举只能靠 sitemap —— 5 个分片，第 3 片里就有 1.1 万个唯一产品 slug。
# 好处是详情页是服务端渲染的，metrics 区块 markup 稳定，正则够用。

IH_SITE = "https://www.indiehackers.com"
IH_SITEMAP = "https://www.indiehackers.com/sitemap.xml"
IH_DELAY = 1.5            # 礼貌间隔（秒）
IH_MAX_DETAIL = 40        # 单轮详情页上限：缓存里有上万条，不设限会跑几小时

# 每个分片实测约 10.6MB，5 个分片合计 ~53MB；而干净产品 slug 均匀散布在
# 分片里（实测每片前 2MB 里一个都没有），所以「下一点就停」省不了时间。
# 对策：**一次只下一个分片**，结果并进本地缓存，靠 next_shard 轮换慢慢覆盖全部；
# 日常运行直接读缓存，零下载。这样把偶发的慢抓摊到多次，不阻塞每次采集。
IH_SLUG_CACHE = "ih_slugs"
IH_SLUG_STALE_DAYS = 14   # 缓存超过这个天数就顺手再抓一个分片
IH_SHARD_MAX_SECONDS = 150   # 单分片最多下这么久（实测网络差时能拖到 4 分钟以上）

# 列表页：每页 ~88KB、实测 1.2–3.6s，各给 21 个产品。
# 只有 highest-revenue 和默认（最新）两个排序结果不同，
# newest / most-posts / trending / 无参数 四者返回同一批。
# 这是**首选通道**：便宜、每天有新东西，而且高收入榜本身就是最值得看的。
# sitemap 分片（~10.6MB、可能 150s 一片，且第 1 片实测 0 个产品）只作广度补充。
IH_LIST_URLS = (
    ("高收入榜", "https://www.indiehackers.com/products?sorting=highest-revenue"),
    ("最新", "https://www.indiehackers.com/products"),
)


def parse_ih_metrics(html_text):
    """从 IH 产品页抠 metrics 区块。

    服务端渲染的 markup 长这样（属性顺序不保证，所以先取属性串再逐个找）：
      <a class="product-metrics__stat product-metrics__stat--revenue" href="...">
        <span class="product-metrics__stat-label">Revenue</span>
        <div class="product-metrics__stat-value">
          <span class="product-metrics__stat-number">$125K</span>
          <span class="product-metrics__stat-slash">/</span>
          <span class="product-metrics__stat-unit">mo</span>
        </div>
      </a>
    """
    out = {"revenue_raw": "", "revenue": None, "period": "", "website": "", "posts": ""}
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html_text, re.S):
        attrs, inner = m.group(1), m.group(2)
        cm = re.search(r'class="([^"]*)"', attrs)
        if not cm or "product-metrics__stat" not in cm.group(1):
            continue
        cls = cm.group(1)
        hm = re.search(r'href="([^"]*)"', attrs)
        href = hm.group(1) if hm else ""

        nm = re.search(r'stat-number">([^<]*)<', inner)
        num = nm.group(1).strip() if nm else ""
        um = re.search(r'stat-unit">([^<]*)<', inner)
        unit = um.group(1).strip() if um else ""

        if "product-metrics__stat--revenue" in cls:
            out["revenue_raw"] = (num + (" / " + unit if unit else "")).strip()
            out["revenue"] = parse_abbrev_money(num)
            out["period"] = unit
        elif "product-metrics__stat--website" in cls:
            out["website"] = href
        elif "product-metrics__stat--posts" in cls:
            out["posts"] = num
    return out


# 一句话里同时出现「金额」与「收入词」，才算得上是里程碑自述。
# 注意两个坑（都踩过）：
#   1) MRR / ARR 必须加词边界，否则 narrative / arrow 里的 arr 会被当成收入词
#   2) 不能收 `$100 / mo` 这种形态 —— 那是页面上的 metrics 挂件，不是自述句
IH_STORY_RE = re.compile(
    r"[^.!?]{0,140}(?:\$[\d.,]+\s*[KMB]?\s*(?:MRR|ARR)\b"
    r"|\b(?:MRR|ARR)\b\s*(?:of|is|was|hit|passed|reached|to|at)?\s*\$[\d.,]+\s*[KMB]?)"
    r"[^.!?]{0,140}[.!?]",
    re.I,
)

# 导航与按钮文案很容易被卷进「一句话」，命中就丢掉这一句
IH_JUNK_RE = re.compile(
    r"sign in|sign up|log in|visit website|book a demo|add yours|cookie", re.I)

# 评论区/动态流里的 UI 前缀，例如：
#   「:) Dozey · 6 years ago · Reply June 26, 2020 We've passed $20K MRR!」
# 真正有用的是后半句，前面这些是渲染出来的界面文字。
IH_UI_PREFIX_RE = re.compile(
    r"^.*?(?:·\s*)?Reply\s+(?:[A-Z][a-z]+ \d{1,2},\s*\d{4}\s*)?", re.S)


def _strip_scripts(html_text):
    """去掉 <script> / <style> 块。

    不先做这一步，clean() 只去标签却留下内联 JS 正文，
    抽出来的「一句话摘要」会变成 `'false'); } } });` 这种垃圾。
    """
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    return text


def _ih_story(html_text, limit=280):
    """从正文里抽一句「我们做到了 X」—— IH 唯一别的源拿不到的东西。

    宁可返回空也不返回被导航文案污染的句子：这个字段只是补充材料，
    一句假摘要把整条案例的可信度拉低，不划算。

    注意：只有「过程自述渲染在产品页上」的那些条目才拿得到（实测 userguiding
    有，remoteworkhub 的 39 条帖子不在页面里）。拿不到就是空，不硬凑。
    """
    plain = clean(_strip_scripts(html_text))
    for m in IH_STORY_RE.finditer(plain):
        s = m.group(0).strip(" .")
        if len(s) < 20 or IH_JUNK_RE.search(s):
            continue
        s = IH_UI_PREFIX_RE.sub("", s).strip(" .")
        if len(s) < 20:
            continue
        return s[:limit]
    return ""


def parse_ih_product(html_text, slug=""):
    """IH 产品页 HTML -> 内部素材 dict。"""
    tm = re.search(r"<title>([^<]*)</title>", html_text)
    title = clean(tm.group(1)) if tm else ""
    name = re.sub(r"\s*[-–—|]\s*Indie Hackers\s*$", "", title).strip() or slug

    dm = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html_text, re.I)
    desc = clean(html.unescape(dm.group(1))) if dm else ""

    m = parse_ih_metrics(html_text)
    rev = m["revenue"]
    if rev:
        head = "自报收入 %s / %s（Indie Hackers）" % (_money_cell(rev), m["period"] or "mo")
    else:
        head = "Indie Hackers 收录（未公开收入）"

    story = _ih_story(html_text)

    bits = []
    if m["posts"]:
        bits.append("%s 条里程碑帖" % m["posts"])
    bits.append("数字为创始人自报，未经支付网关验证")

    # 口径只在真有收入时才提，否则会写出一句看不出矛盾的假话
    calib = ("口径是平台自己标注的「%s」。" % m["period"]) if (rev and m["period"]) else ""
    note = ("来自 Indie Hackers 产品页。收入为创始人自报，未经支付网关验证，"
            "进精写库前必须与 TrustMRR 或官方口径交叉核对。")
    if rev:
        note += "本条的复用价值在过程描述（story_excerpt），不在数字。"
    note += " " + "；".join(bits) + "。"

    return {
        "name": name[:90],
        "one_liner": (desc or story)[:240],
        "url": "%s/product/%s" % (IH_SITE, slug) if slug else "",
        "website": m["website"],
        "points": 0, "comments": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "_blob": "%s %s revenue mrr self reported" % (name, desc),
        "_origin": "indiehackers",
        "_ih": {
            "slug": slug,
            "revenue": rev,
            "revenue_raw": m["revenue_raw"],
            "period": m["period"],
            "posts": m["posts"],
            "website": m["website"],
            "story": story,
            "headline": head,
            "caliber": calib,
            "note": note,
        },
    }


def ih_slugs_from_xml(xml, limit):
    """从 sitemap 分片 XML 里提「干净产品页」的 slug（纯函数，可离线测）。

    sitemap 里有三种 URL，必须区分：
      /product/<slug>                  ← 干净产品页，133KB，metrics 齐全：要这个
      /product/<slug>/<milestoneId>    ← 单条里程碑，42KB，没 metrics：丢掉
      /post/<id>                       ← 帖子：丢掉
    """
    slugs, seen = [], set()
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        if "/product/" not in loc:
            continue
        tail = loc.split("/product/", 1)[1].strip("/")
        if not tail or "/" in tail:          # 带里程碑 id 的，跳过
            continue
        if tail in seen:
            continue
        seen.add(tail)
        slugs.append(tail)
        if len(slugs) >= limit:
            break
    return slugs


def parse_ih_listing(html_text):
    """从产品列表页提 slug（纯函数，可离线测）。

    列表页是服务端渲染的，一页 21 个 `/product/<slug>` 链接。
    注意这里刻意把 `/product/<slug>/<milestoneId>` 也只取到 `<slug>`：
    列表页偶尔会直接链到里程碑，我们仍要的是产品本体。
    """
    out, seen = [], set()
    for m in re.finditer(r'href="/product/([a-z0-9\-_]+)(?:[/?"])', html_text, re.I):
        s = m.group(1).lower()
        if s in seen or s in ("", "new"):
            continue
        seen.add(s)
        out.append(s)
    return out


def ih_listing_slugs():
    """抓两个快速列表页：高收入榜 + 最新榜。约 88KB/页，几秒钟。"""
    out, seen = [], set()
    for label, url in IH_LIST_URLS:
        try:
            html = _fetch_html(url, timeout=25)
        except Exception as e:                                 # noqa: BLE001
            print("    [!] IH 列表页「%s」失败: %s" % (label, e))
            continue
        got = parse_ih_listing(html)
        print("    [i] 列表页「%s」→ %d 个产品" % (label, len(got)))
        for s in got:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def ih_slug_cache_stale(state, days=IH_SLUG_STALE_DAYS):
    """缓存是不是放太久了（该顺手抓一个新分片）。"""
    ts = (state or {}).get("fetched_at") or ""
    if not ts:
        return True
    try:
        when = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except ValueError:
        return True
    return (datetime.now() - when).days >= days


def ih_pool_slugs(limit, refresh=False, shards_per_fetch=1, state=None):
    """从 sitemap 分片池里取 slug：轮换 + 缓存 + 游标。

    返回 (slugs, state)。state 由调用方落盘（便于测试时注入）。
    这是**慢通道**（一片 ~10.6MB），只在需要广度时用；日常走 ih_listing_slugs。

    设计取舍：分片太大不能每轮全下，所以用「轮换 + 缓存 + 游标」：
      - next_shard 记住下次该抓哪片，一次一片
      - barren 记住哪些片一个产品都没有（实测第 1 片就是），别反复浪费时间
      - cursor 记住发到哪了，每轮取接着的 limit 条，到尾部环绕
    """
    st = dict(state) if isinstance(state, dict) else (load_json(IH_SLUG_CACHE, {}) or {})
    st.setdefault("slugs", [])
    st.setdefault("cursor", 0)
    st.setdefault("next_shard", 0)
    st.setdefault("barren", [])
    st.setdefault("fetched_at", "")

    # 什么时候该去抓分片：没有缓存 / 强制刷新 / 游标已绕完一圈 / 缓存过期
    need_fetch = (refresh or not st["slugs"]
                  or st["cursor"] >= len(st["slugs"])
                  or ih_slug_cache_stale(st))

    if need_fetch:
        try:
            idx = _fetch_html(IH_SITEMAP, timeout=30)
        except Exception as e:                                 # noqa: BLE001
            print("    [!] IH sitemap 索引失败: %s" % e)
            idx = ""
        shards = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", idx or "")
        if not shards:
            print("    [!] IH sitemap 索引里没有分片，只能用已有缓存")
        else:
            known = set(st["slugs"])
            barren = set(st["barren"])
            tried = 0
            while tried < max(1, shards_per_fetch) + len(barren) and tried < len(shards):
                i = st["next_shard"] % len(shards)
                st["next_shard"] = (i + 1) % len(shards)
                if i in barren:
                    tried += 1
                    continue
                print("    [i] 抓 IH sitemap 分片 %d/%d（约 10MB，较慢）…"
                      % (i + 1, len(shards)))
                try:
                    xml = _fetch_html_bounded(shards[i], timeout=30,
                                              max_seconds=IH_SHARD_MAX_SECONDS)
                except Exception as e:                         # noqa: BLE001
                    print("    [!] 分片 %d 失败: %s" % (i + 1, e))
                    tried += 1
                    continue
                fresh = [s for s in ih_slugs_from_xml(xml, 10 ** 9) if s not in known]
                known.update(fresh)
                st["slugs"].extend(fresh)
                st["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if fresh:
                    print("    [i] 分片 %d 贡献 %d 个新 slug（池共 %d 个）"
                          % (i + 1, len(fresh), len(st["slugs"])))
                else:
                    barren.add(i)
                    st["barren"] = sorted(barren)
                    print("    [i] 分片 %d 一个产品都没有，记下来不再浪费时间" % (i + 1))
                tried += 1
                if len(fresh) >= limit * 3:
                    break

    slugs = st["slugs"]
    if not slugs:
        return [], st
    cur = int(st["cursor"]) % len(slugs)
    out = (slugs + slugs)[cur:cur + limit]           # 到尾部就环绕
    st["cursor"] = (cur + len(out)) % len(slugs)
    return out, st


def harvest_indiehackers(limit, delay=IH_DELAY, refresh_slugs=False, persist=True,
                         breadth=False):
    """抓 Indie Hackers：列表页枚举 + 详情页解析。

    这是唯一能拿到「他们怎么做到的」的源，但数字全是自报，所以每条都打上
    self_reported 标，且在 note 里写死「进精写库前必须交叉核对」。

    两条枚举通道：
      列表页（默认）  ~88KB/页、几秒、42 个产品，高收入榜 + 最新榜 —— 便宜且每天有新东西
      sitemap 分片    一片 ~10.6MB、可能上百秒 —— 只作广度补充（breadth=True）

    persist=False 时只读不写缓存（--dry-run 用：试跑不该落任何文件）。
    """
    cap = min(limit, IH_MAX_DETAIL)
    slugs = ih_listing_slugs()
    state = None

    if breadth or len(slugs) < cap:
        pool, state = ih_pool_slugs(max(1, cap - len(slugs)),
                                    refresh=refresh_slugs)
        seen = set(slugs)
        for s in pool:
            if s not in seen:
                seen.add(s)
                slugs.append(s)

    if state is not None and persist:
        save_json(IH_SLUG_CACHE, state)

    if not slugs:
        print("    [!] 没有可用的 IH slug（列表页与 sitemap 都没拿到）")
        return []

    items = []
    for i, slug in enumerate(slugs[:cap]):
        try:
            page = _fetch_html("%s/product/%s" % (IH_SITE, slug), timeout=30)
        except Exception as e:                                 # noqa: BLE001
            print("    [!] IH 详情页失败 (%s): %s" % (slug, e))
            continue
        rec = parse_ih_product(page, slug)
        # IH 的全部价值是「怎么做到的」。既没有收入、也没有过程自述的条目
        # （常见于 SEO 垃圾站，实测有一条把自己官网填成了 jpost.com），
        # 收进来只会占位，不如直接丢掉。
        if rec["name"] and (rec["_ih"]["revenue"] or rec["_ih"]["story"]):
            items.append(rec)
        elif rec["name"]:
            print("    [i] 跳过 %s（既无收入也无过程自述）" % slug)
        if i < len(slugs[:cap]) - 1:
            time.sleep(delay)
    return items


# ---------------------------------------------------------------- ARR Club
#
# 定位是「校对」，不是「发现」——所以只写 data/arrclub.json 当参照系，
# 绝不进候选池。理由：它的数字是二手转述（页面还自贴 Certificate 徽章、
# 在 FAQ 里自引「according to ARR Club's verified data」）。

ARR_SITE = "https://arr.club"
ARR_SITEMAP = "https://arr.club/sitemap.xml"
ARR_DELAY = 2.0           # 响应本身就要 2.6–3.9s，别催
ARR_MAX_DETAIL = 30

# sitemap 里混着工具页，这些不是公司
ARR_NON_COMPANY = {
    "database", "signal", "investors", "weekly", "pricing", "submit",
    "rankings", "about", "login", "account", "blog", "privacy", "terms",
    "contact", "faq", "changelog", "og",
}


def parse_arr_club_page(html_text):
    """ARR Club 公司页 -> {name, website, industry, founded, arr_by_year, growth}。

    只认 FAQPage 结构化数据，**不读 RSC 里的 `arr` 字段** —— 实测 /notion 上
    那个字段是空的（/linear 才有），不可靠；FAQPage 里才是带年份的权威值。

    年份必须和数字成对存：<title> 写 2026 而 FAQ 写 2025，只存数字会串年份。
    """
    out = {"name": "", "website": "", "industry": "", "founded": "",
           "arr_by_year": {}, "growth": "", "current_arr": None}
    blocks = re.findall(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.S)
    for b in blocks:
        try:
            d = json.loads(b)
        except Exception:                                      # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue

        if d.get("@type") == "Organization":
            out["name"] = clean(d.get("name") or "")
            out["website"] = (d.get("url") or "").strip()
            out["industry"] = clean(d.get("industry") or "")
            out["founded"] = str(d.get("foundingDate") or "").strip()

        elif d.get("@type") == "FAQPage":
            for q in (d.get("mainEntity") or []):
                qn = clean(q.get("name") or "")
                ans = clean((q.get("acceptedAnswer") or {}).get("text") or "")
                if not qn or not ans:
                    continue
                amt = parse_abbrev_money(ans)
                # 「What is X's ARR in 2025?」→ 带年份的年度值
                ym = re.search(r"\b(?:ARR|revenue)\b[^?]{0,20}?\b(20\d{2})\b", qn, re.I)
                if ym and amt:
                    out["arr_by_year"][ym.group(1)] = amt
                    continue
                if re.search(r"current\s+ARR", qn, re.I) and amt:
                    out["current_arr"] = amt
                    continue
                gm = re.search(r"growth rate is\s*([+-]?\d+(?:\.\d+)?%)", ans, re.I)
                if gm:
                    out["growth"] = gm.group(1)
    return out


def arr_slugs_from_xml(xml, limit):
    """从 ARR Club sitemap XML 提公司页 slug（纯函数，可离线测）。

    实测 3889 条里只有 1002 个是公司页，其余是工具页（database / signal /
    investors / weekly / pricing …）。这些必须排掉，否则会去抓一堆非公司页。
    """
    slugs, seen = [], set()
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        m = re.match(r"https?://(?:www\.)?arr\.club/([a-z0-9\-]+)/?$", loc, re.I)
        if not m:
            continue
        slug = m.group(1).lower()
        if slug in ARR_NON_COMPANY or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
        if len(slugs) >= limit:
            break
    return slugs


def arr_list_slugs(limit, sitemap=ARR_SITEMAP):
    """抓 ARR Club sitemap 提公司页 slug。"""
    try:
        xml = _fetch_html(sitemap, timeout=40)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] ARR Club sitemap 失败: %s" % e)
        return []
    return arr_slugs_from_xml(xml, limit)


def harvest_arr_club(limit, delay=ARR_DELAY):
    """抓 ARR Club 公司页 -> 校对用参照表（不写候选池）。"""
    cap = min(limit, ARR_MAX_DETAIL)
    slugs = arr_list_slugs(cap)
    if not slugs:
        return []

    out = []
    for i, slug in enumerate(slugs):
        try:
            page = _fetch_html("%s/%s" % (ARR_SITE, slug), timeout=35)
        except Exception as e:                                 # noqa: BLE001
            print("    [!] ARR Club 详情页失败 (%s): %s" % (slug, e))
            continue
        rec = parse_arr_club_page(page)
        if rec["name"] and (rec["arr_by_year"] or rec["current_arr"]):
            rec["slug"] = slug
            rec["source_url"] = "%s/%s" % (ARR_SITE, slug)
            rec["source_kind"] = "secondary"
            out.append(rec)
        if i < len(slugs) - 1:
            time.sleep(delay)
    return out


# ---------------------------------------------------------------- 详情页补充

HN_ITEM_API = "https://hn.algolia.com/api/v1/items/%s"


def fetch_hn_comments(object_id, top_n=5, max_chars=1000):
    """抓 HN 条目的评论区正文。

    评论区才是 HN 最值钱的地方：标题只是「我做了个 X」，真话（成本、获客、
    翻车）都在下面。免费、免 key、实测连打 6 次无限流。
    """
    if not object_id:
        return ""
    try:
        data = http_json(HN_ITEM_API % object_id, timeout=25)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] HN 评论抓取失败 (%s): %s" % (object_id, e))
        return ""

    collected = []

    def walk(node, depth=0):
        for ch in (node.get("children") or []):
            txt = clean(ch.get("text") or "")
            if len(txt) >= 60:
                collected.append((depth, txt))
            walk(ch, depth + 1)

    walk(data)
    collected.sort(key=lambda t: t[0])          # 浅层评论更醒目
    picked = [t for _d, t in collected[:top_n]]
    return "\n".join(picked)[:max_chars]


def fetch_trustmrr_md(slug, timeout=25):
    """抓官方 AI 读本 /startup/<slug>.md。

    比 /api/ai 多两样东西：
      1) 出处分层 —— 哪些字段由外部 provider 验证、哪些是用户生成内容
      2) refresh 时间戳 —— 用来判断这份数据到底有多新
    """
    if not slug:
        return {}
    try:
        text = _fetch_html("%s/startup/%s.md" % (TRUSTMRR_SITE, slug), timeout=timeout)
    except Exception as e:                                     # noqa: BLE001
        print("    [!] TrustMRR .md 抓取失败 (%s): %s" % (slug, e))
        return {}

    out = {"freshness": "", "provenance": "", "x_followers": ""}
    m = re.search(r"next refresh expected around ([0-9T:\-\.]+Z)", text)
    if m:
        out["freshness"] = m.group(1)
    m = re.search(r">\s*(Data identified in the Verification Sources.*?)content\.", text, re.S)
    if m:
        out["provenance"] = clean(m.group(1))
    m = re.search(r"X followers:\s*([\d,]+)", text)
    if m:
        out["x_followers"] = m.group(1)
    return out


def enrich_added(added_pairs, hn_comments=0, trustmrr_md=0):
    """给本次新增的条目补富字段。

    只跑「新条目」，所以日常稳态运行时请求量很小（老条目不会重复抓）。
    """
    hn_budget, tm_budget = hn_comments, trustmrr_md
    for cand, _p in added_pairs:
        src = (cand.get("harvest_source") or "").lower()

        if src == "hn" and hn_budget > 0 and cand.get("hn_id"):
            text = fetch_hn_comments(cand["hn_id"])
            if text:
                cand.setdefault("metrics", {})["hn_discussion"] = text
                hn_budget -= 1
                time.sleep(0.5)

        elif src == "trustmrr" and tm_budget > 0 and cand.get("trustmrr_slug"):
            info = fetch_trustmrr_md(cand["trustmrr_slug"])
            if info:
                if info.get("freshness"):
                    cand["data_freshness"] = info["freshness"]
                if info.get("provenance"):
                    cand.setdefault("metrics", {})[
                        "provenance"] = info["provenance"][:300]
                if info.get("x_followers"):
                    cand["founder_x_followers"] = info["x_followers"]
                tm_budget -= 1
                time.sleep(0.25)


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


# ---------------------------------------------------------------- 入队条目组装
# 四种来源（TrustMRR API / IndieHackers / TrustMRR 榜单 / 一般列表）各自一套
# 字段，但骨架只有一份。2026-10-21 重构：此前这四个分支并排写在 main() 里
# 约 200 行，每个分支各抄一遍 id/name/name_en/origin/one_liner/source_url/
# website/harvest_source/added_at —— 加一个字段要改四处，漏一处就是「某类
# 来源静默少一个字段」，而这类缺失不会报错，只会让前端某一列空着。
#
# 等价性用 tmp/snap_harvest.py 做过前后 diff（五源 fixture → inbox.json
# 逐字节一致），不是靠肉眼比对。


def _entry_skeleton(it, taken, **over):
    """四种来源共用的骨架字段，`over` 里的差异字段覆盖上去。"""
    name = (it.get("name") or "").strip()
    cand = {
        "id": slugify(name, taken),
        "name": name, "name_en": name,
        "origin": "",
        "one_liner": it.get("one_liner") or "",
        "category": "未分类",
        "verification": "unverified",
        "metrics": {"headline": "未获取"},
        "models": [],
        "note": "",
        "blocking": "",
        "source_url": it.get("url") or "",
        "website": it.get("website") or "",
        "harvest_source": it.get("_origin") or "",
        "added_at": datetime.now().strftime("%Y-%m-%d"),
    }
    cand.update(over)
    return cand


def _metric_note_api(api):
    """TrustMRR 口径说明。三个口径（MRR / 近 30 天 / 累计）必须一起讲清楚。"""
    return (
        "收入由 TrustMRR 通过支付网关 API 直读（非截图自报）。"
        "口径：MRR = 当前月经常性收入；"
        "近 30 天 = 滚动 30 天的已验证流水，含一次性与用量收入 ——"
        "实测有佣金/流水型业务此项高出 MRR 三十余倍，别当经常性收入引用；"
        "累计 = all-time。"
        "注意页面标注的同步时间可能已过期。"
        + ("排名来自 TrustMRR 榜单，随榜单实时变动。" if api.get("rank") else "")
    )


def _headline_api(api, mrr):
    """门面指标：MRR 与近 30 天取较大的那个。

    只写 MRR 会把佣金/流水型业务显示成个位数（实测 MRR $63 / 近 30 天 $2k+
    的真实条目），看着像没在运营 —— 与 _unwrap_api_item 同一口径。
    """
    if mrr and mrr >= (api.get("revenue_last30d") or 0):
        headline = "MRR " + _money_cell(mrr)
        if api.get("rank"):
            headline += "，TrustMRR 第 %s 名" % api["rank"]
        return headline
    if api.get("revenue_last30d"):
        return "近 30 天收入 " + _money_cell(api["revenue_last30d"])
    if api.get("revenue_total"):
        return ("累计收入 %s（当前 MRR 与近 30 天均为 0，口径见 metric_note）"
                % _money_cell(api["revenue_total"]))
    return "TrustMRR 收录（收入未公开）"


def _entry_from_api(it, api, taken):
    """TrustMRR 官方 API：字段齐全，直接落盘，不再写「待补」。"""
    mrr = api.get("mrr") or 0
    metrics = {}
    if api.get("mrr"):
        metrics["mrr"] = api["mrr"]
    if api.get("revenue_total"):
        metrics["all_time"] = api["revenue_total"]
    # 近 30 天收入落盘用契约名 last_30d_revenue（见 data/sources.json 的 fields
    # 清单）。内部暂存 _api 里叫 revenue_last30d，那只是原样转发；对外一律用
    # 契约名，否则 triage.revenue_of() 取不到数。
    # 漏写这一格的代价：TrustMRR 的 Current MRR 对非订阅制项目恒为 0，
    # 于是「月流水几万、MRR 为零」的条目全被当成没有收入数据。
    if api.get("revenue_last30d"):
        metrics["last_30d_revenue"] = api["revenue_last30d"]
    if api.get("customers"):
        metrics["customers"] = api["customers"]
    if api.get("subscriptions"):
        metrics["customers"] = metrics.get("customers") or api["subscriptions"]
    g = api.get("growth")
    if g not in (None, ""):
        metrics["growth"] = _pct_cell(g) + "（近 30 天）"
    if api.get("visitors"):
        metrics["visitors"] = api["visitors"]
    if api.get("asking_price"):
        metrics["price_point"] = _money_cell(api["asking_price"])
    if api.get("profit_margin") is not None:
        metrics["margin"] = "%s%%" % api["profit_margin"]
    metrics["headline"] = _headline_api(api, mrr)
    metrics["metric_note"] = _metric_note_api(api)

    cand = _entry_skeleton(
        it, taken,
        origin=api.get("country") or "",
        category=api.get("category") or "未分类",
        verification="partial",
        metrics=metrics,
        # sitemap 通道带 _models（买断制/订阅制从 Pricing model 推断），
        # 没有时沿用旧口径；_freshness/_monthly 同理（.md 才有）
        models=it.get("_models") or (["订阅制"] if mrr else []),
        note=api.get("note") or "",
        blocking="需人工确认收入口径与数据时效（TrustMRR 同步时间可能已过）",
        website=api.get("website") or "",
        harvest_source=it.get("_origin") or "trustmrr",
    )
    if api.get("founded"):
        cand["founded_at"] = api["founded"]
    if api.get("x_handle"):
        cand["founder_x"] = api["x_handle"]
    if api.get("x_founder"):
        cand["founder_name"] = api["x_founder"]
    if api.get("on_sale"):
        cand["models"] = ["交易市场"]
        cand["trade"] = {
            "price": _money_cell(api.get("asking_price")) or None,
            "multiple": ("%.2fx" % float(api["multiple"])) if api.get("multiple") else None,
            "revenue": _money_cell(api.get("revenue_last30d")) or None,
        }
    return cand


def _entry_from_ih(it, ih, taken):
    """Indie Hackers：数字是自报的，但「怎么做到的」只有这里有。"""
    metrics = {"headline": ih.get("headline") or "Indie Hackers 收录"}
    if ih.get("revenue"):
        metrics["mrr"] = ih["revenue"]
        metrics["self_reported"] = True
    if ih.get("posts"):
        metrics["posts"] = ih["posts"]
    if ih.get("story"):
        metrics["story_excerpt"] = ih["story"]
    metrics["metric_note"] = (
        "收入为创始人在 Indie Hackers 上自报，未经支付网关验证。"
        + (ih.get("caliber") or "")
        + "本条的复用价值在过程描述（story_excerpt），不在数字。")

    cand = _entry_skeleton(
        it, taken,
        verification="unverified",
        metrics=metrics,
        note=ih.get("note") or "",
        blocking="自报数字，需与 TrustMRR 或公司官方口径交叉核对后才可进精写库",
        website=ih.get("website") or "",
        harvest_source=it.get("_origin") or "indiehackers",
    )
    if ih.get("slug"):
        cand["indiehackers_slug"] = ih["slug"]
    return cand


def _entry_from_rank(it, taken):
    """TrustMRR 首页「按已验证收入排行」的结构化数据：有名字没金额。"""
    return _entry_skeleton(
        it, taken,
        verification="partial",
        metrics={"headline": "TrustMRR 验证收入榜条目（具体数字待补）"},
        note="来自 TrustMRR 首页的「Top startups by verified revenue」结构化数据。"
             "收入本身由支付网关验证，但榜单未直接给出金额，需打开公司页取数。"
             "注意区分 MRR 与 all-time 两个口径。",
        blocking="需打开公司详情页取 MRR 与 all-time，并确认口径",
        website="",
        harvest_source=it.get("_origin") or "trustmrr",
    )


def _entry_from_listing(it, taken, frag, listing, default_source):
    """一般列表（HN / PH / 交易市场）：只有一个可能骗人的「疑似金额」。"""
    return _entry_skeleton(
        it, taken,
        category=(listing or {}).get("category") or "未分类",
        verification="unverified",
        metrics={"headline": ("疑似金额 " + frag) if frag else "未获取"},
        note="自动采集于 %s（来源 %s，发布于 %s，%s 分 / %s 条讨论）。"
             "自动抽到的金额只作线索，可能是售价、举例或行价，不是收入。"
             % (datetime.now().strftime("%Y-%m-%d"), it.get("_origin") or "unknown",
                it.get("created_at") or "—", it.get("points", 0), it.get("comments", 0)),
        blocking="尚未人工核实：需确认公司真实存在、收入口径、是否有公开披露",
        harvest_source=it.get("_origin") or default_source,
    )


def build_entry(it, taken, frag, default_source):
    """一条原始素材 → 一条采集队列条目（纯函数：不写盘、不碰 args）。

    `taken` 是已占用的 id 集合，用于 slug 去重（只读，调用方自己 add）。
    """
    api = it.get("_api") or {}
    ih = it.get("_ih") or {}
    listing = it.get("_listing")

    if api:
        cand = _entry_from_api(it, api, taken)
    elif ih:
        cand = _entry_from_ih(it, ih, taken)
    elif it.get("_verified_rank"):
        cand = _entry_from_rank(it, taken)
    else:
        cand = _entry_from_listing(it, taken, frag, listing, default_source)

    # 来源性质：每条都必须带，且是唯一权威赋值点。
    # 前端据此显示徽章——「支付网关验过的收入」和「二手转述」不能混进同一列。
    cand["source_kind"] = cand.get("source_kind") or source_kind_of(cand.get("harvest_source"))
    # 给 enrich_added 用的句柄：HN 要 objectID 才能抓评论；TrustMRR 要 slug 才能抓 .md
    if it.get("object_id"):
        cand["hn_id"] = it["object_id"]
    if api.get("slug"):
        cand["trustmrr_slug"] = api["slug"]
    # .md 档案独有的两样：收入同步时间（数据时效）+ 12 个月流水（红线 2 判据）。
    # monthly_timeline 是 list，triage.has_numbers() 的 isinstance 守卫会跳过它，
    # 不会把「历史月度收入」误判成「当前有收入」。
    if it.get("_freshness"):
        cand["data_freshness"] = it["_freshness"]
    if it.get("_monthly"):
        # 写 cand["metrics"] 而不是分支里的局部变量 —— 旧写法在「榜单」分支上
        # 根本没有 metrics 变量（只有 ih 分支定义了它），靠「那个分支不会带
        # _monthly」才没炸，属于变量泄漏到分支外的隐性依赖。
        cand["metrics"]["monthly_timeline"] = it["_monthly"]

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
    return cand


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="采集候选项目到采集队列")
    ap.add_argument("--source", default="hn",
                    choices=["hn", "ph", "trustmrr", "indiehackers", "arrclub",
                             "trustmrr_sitemap", "all"],
                    help="要跑的采集源（默认 hn）")
    ap.add_argument("--limit", type=int, default=50, help="每个源最多取多少条")
    ap.add_argument("--token", default=os.environ.get("PH_TOKEN", ""),
                    help="Product Hunt token")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    ap.add_argument("--max-inbox", type=int, default=INBOX_CAP_DEFAULT,
                    help="inbox 条数上限，超出的移入归档文件（不删除）。0 = 不限")
    ap.add_argument("--hn-comments", type=int, default=5,
                    help="给新增的 HN 条目抓几条评论正文（0 = 不抓）。评论才是真话所在地")
    ap.add_argument("--trustmrr-md", type=int, default=10,
                    help="给新增的 TrustMRR 条目抓几份官方 .md（0 = 不抓），补数据新鲜度")
    ap.add_argument("--refresh-ih-slugs", action="store_true",
                    help="强制重抓一个 IndieHackers sitemap 分片（约 10MB，慢）。"
                         "平时不需要：slug 有本地缓存，轮换补充")
    ap.add_argument("--ih-breadth", action="store_true",
                    help="IndieHackers 走 sitemap 广度模式（慢，但覆盖上万产品）。"
                         "默认只用两个列表页，几秒拿 42 个高收入/最新产品")
    ap.add_argument("--tm-budget", type=int, default=TM_ROUND_BUDGET,
                    help="TrustMRR sitemap 通道每轮抓多少个 .md 档案（默认 %d）。"
                         "抓取有界、断点续抓，跑满全池约 110 轮" % TM_ROUND_BUDGET)
    ap.add_argument("--tm-sweep", type=int, default=0,
                    help="一次性加大本轮 .md 抓取量（覆盖 --tm-budget）。"
                         "仍是有界批量，不做全量镜像（llms.txt 边界）")
    ap.add_argument("--tm-seeds", default="",
                    help="外部线索源的 slug 清单文件（每行一个，# 注释）。"
                         "命中 TrustMRR 的 slug 优先抓一手 .md 档案——"
                         "用来把别的站点的索引当发现面（如 howtomakemi）")
    ap.add_argument("--empty-ok", action="store_true",
                    help="无素材时退出码 0 而非 1。种子复查模式下「无新增」是"
                         "正常结果（如 htm_increment.py 的重跑），不是故障")
    ap.add_argument("--fix-source-kind", action="store_true",
                    help="给存量数据（采集队列 + 候选池）补 source_kind 后退出，不采集")
    args = ap.parse_args()

    if not os.path.isdir(DATA_DIR):
        print("[!] 找不到 data 目录: %s" % DATA_DIR)
        return 1

    src_cfg = load_json("sources", {}) or {}
    rules = src_cfg.get("filter_rules", {}) if isinstance(src_cfg, dict) else {}
    flt = Filter(rules)

    # 存量回填：只补字段、不采集。单独成一个入口，避免和采集混在一起
    if args.fix_source_kind:
        print("=" * 60)
        print("  回填 source_kind（只补字段，不采集）")
        print("=" * 60)
        for name in ("inbox", "candidates"):
            recs = load_json(name, []) or []
            # 这里用 force：早期版本会把没有 harvest_source 的条目一律判成
            # discovery，把 TrustMRR 验证过的收入错误降级成「仅线索」。
            n = ensure_source_kind(recs, force=True)
            if n and not args.dry_run:
                save_json(name, recs)
            print("  %-11s %d 条，修正 %d 条%s"
                  % (name, len(recs), n, "" if not args.dry_run else "（dry-run 未写入）"))
        print()
        return 0

    keywords = ["MRR", "ARR", "bootstrapped", "solo founder", "side project",
                "indie hacker", "my SaaS"]

    print("=" * 60)
    print("  采集候选项目 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)

    raw = []
    want = {"hn", "ph", "trustmrr", "indiehackers", "trustmrr_sitemap"} \
        if args.source == "all" else {args.source}

    # ARR Club 单独处理：它是二手转述，只写参照表，不进候选池（避免污染）。
    if "arrclub" in want:
        print("[*] ARR Club（校对用参照表，不进候选池）")
        companies = harvest_arr_club(args.limit)
        print("    → 拿到 %d 家公司页" % len(companies))
        if companies and not args.dry_run:
            save_json("arrclub", {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_kind": "secondary",
                "note": "ARR Club 为二手转述渠道（页面自贴 Certificate、FAQ 里自引"
                        "「according to ARR Club's verified data」）。本表只作 ARR 时间点"
                        "的校对参照，不进候选池、不作为一手来源。",
                "count": len(companies),
                "companies": companies,
            })
            print("    → 已写入 data/arrclub.json")
        elif companies:
            for c in companies[:5]:
                yrs = "、".join("%s %s" % (y, _money_cell(v))
                                for y, v in sorted(c["arr_by_year"].items()))
                print("       · %-24s %s" % (c["name"][:24], yrs or "-"))

    if "hn" in want:
        print("[*] Hacker News (Algolia，免 key)")
        got = harvest_hn(args.limit, keywords)
        print("    → 拉到 %d 条原始素材" % len(got))
        raw += got

    if "indiehackers" in want:
        print("[*] Indie Hackers（%s；数字为自报，须交叉核对）"
              % ("sitemap 广度模式" if args.ih_breadth else "列表页枚举 + 详情页"))
        got = harvest_indiehackers(args.limit, refresh_slugs=args.refresh_ih_slugs,
                                   persist=not args.dry_run, breadth=args.ih_breadth)
        print("    → 拉到 %d 条" % len(got))
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

    if "trustmrr_sitemap" in want:
        print("[*] TrustMRR sitemap 全量通道（游标轮换 + 官方 .md 档案）")
        got = harvest_trustmrr_sitemap(
            args.limit, budget=args.tm_sweep or args.tm_budget,
            dry_run=args.dry_run, seeds_file=args.tm_seeds or None)
        print("    → 拉到 %d 条" % len(got))
        raw += got

    if not raw:
        if "arrclub" in want:
            if args.dry_run:
                print("\n--dry-run：ARR Club 参照表未写入（它不进采集队列，不算空跑）。")
            else:
                print("\nARR Club 参照表已更新（它不进采集队列，所以这里不算「空跑」）。")
            return 0
        if args.empty_ok:
            # 种子复查模式：没新素材是正常结果（种子已全部处置过、选拔无斩获）
            print("\n本次无新增素材（--empty-ok：按正常结果处理）。")
            return 0
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
    # 已经采过 / 进过候选池 / 已发布 / 已归档留底的，一律不再重复采
    # （含 inbox_archive 的理由见 DEDUP_FILES 的注释）
    seen = seen_keys(cands)
    taken, taken_names = seen["ids"], seen["names"]

    added = 0
    added_pairs = []                     # (条目, 热度) 供简报排序
    by_source_added = {}
    for it in kept:
        name = (it.get("name") or "").strip()
        if not name or TC.norm_name(name) in taken_names:
            continue
        # 组装全在 build_entry 里（四种来源的字段差异都在那儿）。
        # main 只负责去重、计数、排序。
        frag = extract_money(it.get("_blob", ""))[1]
        cand = build_entry(it, taken, frag, args.source)

        taken.add(cand["id"])
        taken_names.add(TC.norm_name(name))
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

    # 补富：只给本次新增的条目抓详情（HN 评论区 / TrustMRR .md）。
    # 放在 dry-run 之后 —— 试跑不该产生额外请求。
    if args.hn_comments > 0 or args.trustmrr_md > 0:
        print("\n[*] 给新增条目补详情页…")
        enrich_added(added_pairs,
                     hn_comments=args.hn_comments,
                     trustmrr_md=args.trustmrr_md)
        got_disc = sum(1 for c, _ in added_pairs if (c.get("metrics") or {}).get("hn_discussion"))
        got_fresh = sum(1 for c, _ in added_pairs if c.get("data_freshness"))
        if got_disc:
            print("    补到 %d 条 HN 评论正文" % got_disc)
        if got_fresh:
            print("    补到 %d 条 TrustMRR 数据新鲜度" % got_fresh)

    # 容量控制：inbox 只留最近 N 条，溢出的归档留底（永不删除）
    kept_list, spill, archive_total = prune_inbox(cands, args.max_inbox)
    # 顺手补 source_kind（幂等）：老条目也能在前端画出可信度徽章
    healed = ensure_source_kind(kept_list)
    if healed:
        print("\n  [i] 顺便给 %d 条存量队列条目补了 source_kind" % healed)
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
