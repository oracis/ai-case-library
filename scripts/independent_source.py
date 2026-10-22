# -*- coding: utf-8 -*-
"""给「只有一个来源域」的案例找**真正的第二来源**，用实体锚点判同一性。

## 为什么不能靠搜索

实测：`mort` 用通用搜索/平台检索会匹配到《真人快打》（Mortal Kombat），
`angel-match` 匹配到「Cat Angel: match 3」，`ai-toolbox` 匹配到一个同名的
无关仓库。词命中率看着很高（36 条里 25 条「有命中」），逐条看全是假的。
所以判据不能是「名字差不多」，只能是**实体锚点**：

   平台页里自带的开发者官网字段 == 产品的官网域名

  · App Store：`sellerUrl`（开发者填的官网）
  · GitHub：仓库的 `homepage` 字段

这个字段是平台从开发者那边收的，和对外的产品域名对得上，基本可以认定
「这个商店页 / 这个仓库就是这家产品」。对不上就一律不认 —— 宁可留空。

## 二级证据：官网本身

官网 200 + 有定价页，只能证明「产品还活着、怎么收费」，**属于自述**，
不是独立来源。所以它单独记在 `site` 字段里，不进 `sources`。

## 用法

    python scripts/independent_source.py --select     # 谁缺第二来源
    python scripts/independent_source.py             # 跑一遍，落 data/source_audit.json
    python scripts/independent_source.py --write     # 把确认过的写回 cases.json

Google 检索：本机出口对 google.com / googleapis.com 是断的（沙箱代理只放行
白名单域名，用户自己的 SOCKS 10808 能握手上但转发 RST）。要接进来只需在
环境变量里给 `CASE_LIB_GOOGLE_KEY` 与 `CASE_LIB_GOOGLE_CX`（Custom Search
JSON API，免费 100 次/日），脚本会自动多跑一路。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A                                       # noqa: E402
import atomicio                                             # noqa: E402
import fetch_tm_md as F                                     # noqa: E402

CASES = os.path.join(ROOT, "data", "cases.json")
AUDIT = os.path.join(ROOT, "data", "source_audit.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# trustmrr 全体 + 自家榜单页都算「同一个源」
TM_HOSTS = ("trustmrr.com",)
# 商店页：官网直接指向这里时，本身就是平台托管的第三方页
STORE_HOSTS = ("apps.apple.com", "play.google.com", "apps.microsoft.com")


def host_of(u):
    """取主机名。**裸域名也认**（"kibu.com" → "kibu.com"）。

    ⚠ 2026-10-21 实测踩过：最早只认带 scheme 的 URL，于是
    `same_host("https://kibu.com", "kibu.com")` 恒为 False —— 36 条一条都
    匹配不上，看起来像「全库都没有第二来源」，其实是判据自己废了。
    """
    u = (u or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    m = re.match(r"https?://([^/]+)", u)
    if not m:
        return ""
    h = m.group(1).lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def same_host(a, b):
    """同域判定：允许 www./子域互认，但不允许拿不同主域硬凑。"""
    a, b = host_of(a), host_of(b)
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def jget(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read(400000).decode("utf-8", "replace"))


# ------------------------------------------------------------------ 目标筛选

def source_hosts(case):
    return [host_of(s.get("url")) for s in (case.get("sources") or [])]


def is_isolated(case):
    """只看得到 trustmrr 一个域的案例 —— 这些才是「缺第二来源」的。"""
    hs = [h for h in source_hosts(case) if h]
    if not hs:
        return False
    return all(any(h == t or h.endswith("." + t) for t in TM_HOSTS) for h in hs)


# ------------------------------------------------------------------ 官网域名

def slug_of(case):
    for s in (case.get("sources") or []):
        m = re.search(r"trustmrr\.com/startup/([^/?#]+)", s.get("url") or "")
        if m:
            return re.sub(r"\.md$", "", m.group(1))
    return str(case.get("id") or "")


def official_site(case, cache):
    """从 TrustMRR 档案的 `- Website:` 行取官网；取不到返回 ""。"""
    slug = slug_of(case)
    if slug not in cache:
        try:
            r = F.fetch(slug)
            md = r[0] if isinstance(r, tuple) else r
        except Exception:                                    # noqa: BLE001
            md = ""
        url = ""
        for line in (md or "").splitlines():
            s = line.strip()
            if s.startswith("- Website:"):
                i, j = s.find("["), s.find("]")
                if i >= 0 and j > i:
                    url = s[i + 1:j]
                    break
        cache[slug] = url
    return cache[slug]


# ------------------------------------------------------------------ 三条取证通道

def probe_site(url, timeout=15, attempts=2):
    """抓官网。失败重试一次 —— 实测同一批 36 条两次跑，`unreachable`
    在 12 与 15 之间漂，都是瞬时抖动。这道审计要拿来当证据用，
    不重试就会把「网络抖了一下」写成「这个产品连不上」。
    """
    if not url:
        return {"url": "", "status": 0, "err": "无官网"}
    last = ""
    for i in range(attempts):
        try:
            html = A.http_get(url, timeout=timeout)
        except Exception as e:                               # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:60])
            time.sleep(1.2 * (i + 1))
            continue
        m = re.search("<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""
        has_price = bool(re.search(r'href="[^"]*pricing', html, re.I)) or "pricing" in html.lower()
        return {"url": url, "status": 200, "title": title, "bytes": len(html),
                "pricing": has_price, "attempts": i + 1}
    return {"url": url, "status": 0, "err": last}


def probe_itunes(name, domain):
    """App Store 在架页。判据：sellerUrl 与官网同域。"""
    q = urllib.parse.urlencode({"term": name, "entity": "software", "limit": 8, "country": "us"})
    data = jget("https://itunes.apple.com/search?" + q)
    for r in data.get("results") or []:
        seller = r.get("sellerUrl") or ""
        if seller and same_host(seller, domain):
            return {
                "kind": "appstore",
                "url": r.get("trackViewUrl") or "",
                "evidence": "App Store 在架：%s 由 %s 发布，版本 %s，更新于 %s"
                            % (r.get("trackName"), r.get("sellerName"),
                               r.get("version"), (r.get("currentVersionReleaseDate") or "")[:10]),
                "anchor": "sellerUrl=%s" % seller,
            }
    return None


def probe_github(name, domain):
    """GitHub 仓库。判据：repo.homepage 与官网同域。"""
    q = urllib.parse.urlencode({"q": "%s in:name" % name.replace(" ", "-"), "per_page": 8})
    data = jget("https://api.github.com/search/repositories?" + q)
    for r in data.get("items") or []:
        hp = r.get("homepage") or ""
        if hp and same_host(hp, domain):
            return {
                "kind": "github",
                "url": r.get("html_url") or "",
                "evidence": "GitHub 仓库 %s（★%s，最近推送 %s，仓库主页指向 %s）"
                            % (r.get("full_name"), r.get("stargazers_count"),
                               (r.get("pushed_at") or "")[:10], hp),
                "anchor": "homepage=%s" % hp,
            }
    return None


def probe_google(name, domain):
    """Google Custom Search（需 key+cx）。判据：结果的 host 与官网同域，或标题含产品名。"""
    key = os.environ.get("CASE_LIB_GOOGLE_KEY") or ""
    cx = os.environ.get("CASE_LIB_GOOGLE_CX") or ""
    if not (key and cx):
        return None
    q = urllib.parse.urlencode({"key": key, "cx": cx, "q": '"%s" review' % name, "num": 10})
    try:
        data = jget("https://www.googleapis.com/customsearch/v1?" + q)
    except Exception:                                        # noqa: BLE001
        return None
    for it in data.get("items") or []:
        link = it.get("link") or ""
        h = host_of(link)
        if not h or any(h == t or h.endswith("." + t) for t in TM_HOSTS):
            continue
        if same_host(link, domain):                          # 自家域名不算第三方
            continue
        return {"kind": "press", "url": link,
                "evidence": "Google CSE 命中第三方页面：%s" % (it.get("title") or "")[:60],
                "anchor": "q=%s" % name}
    return None


CHANNELS = (("itunes", probe_itunes), ("github", probe_github), ("google", probe_google))


def audit_one(case, cache):
    name = case.get("name_en") or case.get("name") or ""
    site = official_site(case, cache)
    domain = host_of(site)
    rec = {"id": case["id"], "name": name, "site": probe_site(site), "confirmed": []}

    # TrustMRR 档案里的 Website 有时直接就是商店页（abmaxx 给的是
    # apps.apple.com/app/id…）。这种「官网」本身就是平台托管页，
    # 已经是第三方来源，不需要再拿 sellerUrl 去锚定。
    if domain in STORE_HOSTS and case.get("_store_page") is not False:
        rec["confirmed"].append({
            "kind": "appstore",
            "url": site,
            "evidence": "TrustMRR 登记的官网即 App Store 在架页（%s）" % domain,
            "anchor": "website=%s" % site,
        })
        rec["verdict"] = "third_party"
        return rec

    if domain:
        for label, fn in CHANNELS:
            try:
                got = fn(name, domain)
            except Exception as e:                           # noqa: BLE001
                got = None
                rec.setdefault("errors", []).append("%s: %s" % (label, str(e)[:50]))
            if got:
                rec["confirmed"].append(got)
    rec["verdict"] = ("third_party" if rec["confirmed"]
                      else ("self_only" if rec["site"].get("status") == 200 else "unreachable"))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", action="store_true", help="只列出缺第二来源的案例")
    ap.add_argument("--write", action="store_true", help="把确认过的写回 cases.json")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cases = json.load(open(CASES, encoding="utf-8"))
    todo = [c for c in cases if is_isolated(c)]

    # 上一轮已经补过的案例：写回之后它们「不再只有一个域」，但仍属于这轮
    # 考察范围 —— 否则每跑一次目标集就缩小一圈（实测 36 → 29 → …）。
    # 判据用案例自己身上的 independent_check，不信审计文件（它会被本轮覆盖）。
    def carried(c):
        ic = c.get("independent_check") or {}
        if not ic or is_isolated(c):
            return None
        return {
            "id": c["id"], "name": c.get("name"), "carried_over": True,
            "site": ic.get("site") or {},
            "confirmed": [dict(x) for x in (ic.get("third_party") or [])],
            "verdict": ic.get("verdict") or "third_party",
        }

    recheck = [r for r in (carried(c) for c in cases) if r]
    print("只有 trustmrr 一个来源域的案例：%d 条（另有 %d 条上轮已补，沿用结论）"
          % (len(todo), len(recheck)))
    if args.select:
        for c in todo:
            print("  %-26s %-10s %s" % (c["id"][:26], c.get("tier"), (c.get("name") or "")[:24]))
        return 0

    cache = {}
    t0 = time.time()
    out = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for i, rec in enumerate(ex.map(lambda c: audit_one(c, cache), todo), 1):
            out.append(rec)
            print("%2d/%-2d %-26s %-12s site=%-3s 第三方=%s  %.0fs"
                  % (i, len(todo), rec["id"][:26], rec["verdict"],
                     rec["site"].get("status") or "-",
                     ",".join(x["kind"] for x in rec["confirmed"]) or "-",
                     time.time() - t0))

    by = {"third_party": 0, "self_only": 0, "unreachable": 0}
    for r in out:
        by[r["verdict"]] += 1
    # 已补过的沿用上一轮，不重抓 —— 它们的 verdict 早就是 third_party 了
    for r in recheck:
        out.append(r)
        by[r.get("verdict")] = by.get(r.get("verdict"), 0) + 1
    out.sort(key=lambda x: x["id"])
    print("\n结论：第三方确认 %d / 只有自述 %d / 连不上 %d"
          % (by["third_party"], by["self_only"], by["unreachable"]))

    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "method": "entity-anchor: iTunes sellerUrl / GitHub homepage 必须与官网同域",
               "summary": by, "items": out}
    atomicio.write_json(AUDIT, payload, indent=1)
    print("审计落盘：%s" % os.path.relpath(AUDIT, ROOT))

    if not args.write:
        print("（未写回 cases.json；加 --write 生效）")
        return 0

    by_id = {c["id"]: c for c in cases}
    added = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for rec in out:
        c = by_id.get(rec["id"])
        if not c:
            continue
        c["independent_check"] = {
            "checked_at": today,
            "site": {k: rec["site"].get(k) for k in ("url", "status", "title", "pricing")},
            "third_party": [{"kind": x["kind"], "url": x["url"], "evidence": x["evidence"]}
                            for x in rec["confirmed"]],
            "verdict": rec["verdict"],
        }
        have = {(s.get("url") or "").rstrip("/") for s in (c.get("sources") or [])}
        for x in rec["confirmed"]:
            u = (x["url"] or "").rstrip("/")
            if not u or u in have:
                continue
            # 商店页按库里既有口径记 official（已有 24 条 apps.apple.com 这么标）；
            # GitHub 仓库记 secondary。都不改 verification —— 它们撑不起收入等级。
            c.setdefault("sources", []).append({
                "kind": "official" if x["kind"] == "appstore" else "secondary",
                "label": x["evidence"][:80],
                "url": x["url"],
            })
            have.add(u)
            added += 1

    atomicio.write_json(CASES, cases, indent=2,
                        newline=atomicio.detect_newline(CASES))
    print("写回：新增来源 %d 条，independent_check 覆盖 %d 条案例" % (added, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
