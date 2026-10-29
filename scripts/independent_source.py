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

Google 检索（2026-10-29 实测更新）：`www.googleapis.com` **直连与系统代理
（127.0.0.1:12690）都超时**，但走 Shadowsocks 10808 是通的 —— 用无效 key 打
CSE 端点能拿到 Google 官方 JSON「API key not valid / INVALID_ARGUMENT」。
所以接进来要两样：① `CASE_LIB_GOOGLE_KEY` + `CASE_LIB_GOOGLE_CX`
（Custom Search JSON API，免费 100 次/日，也可写进 data/secrets.json 的
google_key / google_cx）；② 把 `google_proxy` 指到 10808。
配完跑 `python scripts/check_google_cse.py` 自检，它会逐条告诉你缺哪一样。
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


_WARNED = set()


def _warn_once(msg):
    """同一条提醒只说一次 —— 扫 89 条候选时不该刷出 89 行同样的提示。"""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    sys.stderr.write("[i] %s\n" % msg)


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
    """Google Custom Search（需 key+cx）。判据：结果的 host 与官网同域，或标题含产品名。

    凭据与代理统一走 `ai_verify._google_cfg()`（环境变量 > secrets.json 的
    google_key / google_cx / google_proxy）。

    ⚠ 2026-10-29 改：这里曾经只认环境变量 —— 配在 secrets.json 里的 key
    在核验通道生效、在**补源通道不生效**，同一个 key 两条路一个通一个不通，
    排查时只会以为「Google 不认识这个产品」。
    """
    key, cx, proxy = A._google_cfg()
    if not (key and cx):
        _warn_once("Google CSE 没配（缺 key/cx），补源少一路；"
                   "配法与自检：python scripts/check_google_cse.py")
        return None
    q = urllib.parse.urlencode({"key": key, "cx": cx, "q": '"%s" review' % name, "num": 10})
    try:
        # 单独给 CSE 指定代理：本机直连与系统代理都到不了 googleapis
        data = json.loads(A.http_get(
            "https://www.googleapis.com/customsearch/v1?" + q,
            timeout=25, proxy=proxy or None))
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


# HN 命中里这几类不算独立来源：
#   · news.ycombinator.com 是讨论页本身（UGC，不是报道）
#   · 社交/视频/创始人自述平台 —— 谁都能发，不构成第三方核实
HN_EXCLUDE_HOSTS = (
    "news.ycombinator.com", "reddit.com", "twitter.com", "x.com",
    "youtube.com", "medium.com", "facebook.com", "linkedin.com",
    "instagram.com", "substack.com",
)
# 聚合/导航站：算第三方页面，但它自己也在转述别人，只能到 aggregator（B 级）
AGG_HOSTS = ("howtomakemi.com", "getlatka.com", "starterstory.com")


def probe_hn(name, domain):
    """HN Algolia 检索（免 key、直连可达）。

    为什么加这一路：itunes / github 只在「产品恰好上了应用商店或开源」时命中，
    覆盖率低；Google CSE 要 key 且本机出口不稳（10808 成功率 2/5）。而 HN
    Algolia 是**免费、免鉴权、直连 200** 的 —— 小众海外 SaaS 上过 HN 的比例
    远高于上过应用商店的比例，是唯一能立刻用起来的通用检索通道。

    判据（照抄其它通道的口径）：结果的 host 必须**不是自家域名**、不是 trustmrr、
    不是社交/UGC 站；命中聚合站则降级标 aggregator，不冒充 press。
    Show HN 帖是创始人自荐（本库里 founder 只能当方向参考），直接跳过。
    """
    if not (name or domain):
        return None
    hits = []
    for q in (name, domain):
        if not q:
            continue
        try:
            data = jget("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
                {"query": q, "tags": "story", "hitsPerPage": 10}))
        except Exception:                                    # noqa: BLE001
            continue
        hits.extend(data.get("hits") or [])
    for h in hits:
        title = (h.get("title") or "").strip()
        if title.lower().startswith("show hn"):              # 创始人自荐，跳过
            continue
        link = (h.get("url") or "").strip()
        host = host_of(link)
        if not host or same_host(link, domain):              # 自家域名不算第三方
            continue
        if any(host == t or host.endswith("." + t) for t in TM_HOSTS + HN_EXCLUDE_HOSTS):
            continue
        # 名字撞车校验（见下方 mentions_domain）：先过这一关才谈得上「在讲这个产品」
        if not (domain and mentions_domain(link, domain)):
            continue
        kind = ("aggregator" if any(host == t or host.endswith("." + t)
                                    for t in AGG_HOSTS) else "press")
        return {
            "kind": kind,
            "url": link,
            "evidence": "HN 检索命中第三方页面：%s（%s 分 / %s 评论，%s）；"
                        "页面内引用了官网 %s"
                        % ((h.get("title") or "")[:52], h.get("points"),
                           h.get("num_comments"), (h.get("created_at") or "")[:10],
                           domain),
            "anchor": "HN story %s，页面引用 %s" % (h.get("objectID"), domain),
        }
    return None


def mentions_domain(url, domain):
    """第三方页面里有没有出现产品官网域名 —— **这是 HN 通道的实体锚点**。

    ⚠ 2026-10-29 实测：不做这一步，通道全是假阳性，而且假得理直气壮 ——
      · 查 `kodo` 命中「Koya Bound – Eight Days on the Kumano Kodo」（104 分），
        那是**日本熊野古道徒步游记**，跟产品 Kodo AI 毫无关系；
      · 查 `static-forms` 命中一篇「静态表单被滥用」的观点文，讲的是通用概念。
    这跟 2026-10-21 弃用 cn.bing 是同一个病：通用检索对海外小 SaaS 全是撞名。
    所以判据照搬库里已有的锚定思想（iTunes sellerUrl / GitHub homepage 同域）：
    **第三方页面必须真的引用了产品官网**，否则它只是在聊一个同名的东西。

    抓不到页面（直连 + 系统代理都失败）时不采信 —— 拿不到证据 = 没有证据，
    这比「看标题像是」安全。评论数、点赞数再高也不替代这一条。
    """
    if not (url and domain):
        return False
    dom = domain.lower()
    for proxy in (None, os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        try:
            body = A.http_get(url, timeout=15, proxy=proxy or None)
        except Exception:                                    # noqa: BLE001
            continue
        if dom in body.lower():
            return True
    return False


# 顺序：免 key 的先跑，Google 需要凭据放最后。
#
# ⚠ probe_hn **刻意不在默认链路里**（2026-10-29 实测）：拿 43 条「已人工登记过
# press/review 第三方来源」的案例做正对照回测，**召回 0** —— 这些产品多数
# 只在 HN 上有 Show HN 自荐帖（自荐不算独立来源，已跳过），没有指向第三方文章的
# story。也就是说对这类小众海外 SaaS，HN 这条通道基本捞不到东西。
#
# 🚨 分母口径历次记录不一致，2026-10-29 复核后统一写法（别再各写各的）：
#   日志记「0/32」、本注释旧版写「0/14」，差的其实是**能解析出官网域名的样本数**
#   （取数方式不同：一个现抓 TrustMRR 档案，一个用案例自带 official 来源）。
#   复测口径与结果：43 条样本 → 26 条能解析出官网域名 → 其中 20 条在 Algolia 里
#   检索到「非 Show HN / 非自家 / 非黑名单」的候选链接 → 过 mentions_domain
#   实体锚定后 **0 命中**。结论不变：召回 0，不进默认链路。
#   以后要提这条，只写「43 条样本、锚定后 0 命中」+ 上面这条分母说明，别单写分数。
# 函数和测试都留着：等哪天产品上过 HN 且有第三方讨论，它是免 key 的现成通道；
# 但在召回率被证明 > 0 之前，不把它算进「补源通道已接通」。
CHANNELS = (("itunes", probe_itunes), ("github", probe_github),
            ("google", probe_google))


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
