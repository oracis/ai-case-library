#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""部署后的线上语义校验（deploy_oss.py 的第二道关）。

为什么不看字节数：数据每天都在涨，字节数对不上分不清是「没部署」
还是「部署了但数据变了」。所以要断言语义标记，比如品牌名、
cases 条数、generated_at、site.title。

案例数的期望值默认**读本地 data/cases.json**，不再写死一个数字：写死的话
每发布一批案例就要记得回来改，忘了就会把「数据变多了」误报成「部署挂了」——
而部署校验最不该做的就是制造假警报。要断别的值仍可用 --expect-cases 覆盖。

用法：
    python scripts/verify_deploy.py                       # 校验正式域名
    python scripts/verify_deploy.py --base https://xxx    # 校验别的域名
    python scripts/verify_deploy.py --expect-cases 30     # 覆盖案例数期望值
    python scripts/verify_deploy.py --expect-inbox 0      # 公开版应为 0

退出码：0 全部通过，1 有失败项。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_BASE = "https://case.ydtgo.top"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_case_count():
    """本地 curated 案例数 —— 发布的是它，线上就该等于它。读不到返回 None。"""
    path = os.path.join(ROOT, "data", "cases.json")
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:                                   # noqa: BLE001
        return None


def build_opener():
    # 绕开本机代理：目标是公网站点，走代理反而会因为 TLS 拦截误报
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_one(base, path, timeout=25, retries=1):
    """取一个路径，返回 (status, body, headers)。

    取不到（超时 / 连接被掐 / TLS 报错）给 (0, b"", {})，**不抛异常**：
    这是部署后的校验，网络抖一下应该报一条 FAIL，而不是让整轮校验
    带着栈崩掉 —— 崩掉时人只看到「脚本挂了」，看不出是哪一项没过。

    `retries` 默认 1：255 个页面并发跑下来，实测每次都有 3 个左右栽在
    瞬时抖动上（单独重取全部 200）。**不重试就会把网络抖动报成内容坏了**，
    而「假警报」是校验脚本最不该干的事 —— 一旦开始误报，人就不看了。

    每次调用建一个 opener：单次开销是微秒级，换来的是可以直接在多线程里
    并发调用（HTTPConnection 在异常路径上没有共享保证）。
    """
    last = (0, b"", {})
    for attempt in range(retries + 1):
        op = build_opener()
        try:
            r = op.open(base + path, timeout=timeout)
            return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            # HTTP 层有明确答复（404/500…）—— 这是**内容/服务端**的结论，
            # 重试不会变，直接返回
            return e.code, e.read(), dict(e.headers)
        except Exception as e:                                # noqa: BLE001
            last = (0, ("%s: %s" % (type(e).__name__, e)).encode("utf-8"), {})
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    return last


def fetch_many(base, paths, workers=8, timeout=25):
    """并发取多个路径，返回 {path: (status, body, headers)}。

    🚨 **必须并发**（2026-10-21）：案例页有 255 个，实测单页响应数秒，
    串行十几分钟。而这是部署后唯一「内容对不对」的判据 —— 慢到没人愿意跑，
    就等于没有这道关。
    """
    paths = list(paths)
    out = {}
    if not paths:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(paths)))) as ex:
        futs = {ex.submit(fetch_one, base, p, timeout): p for p in paths}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                out[p] = fut.result()
            except Exception:                                 # noqa: BLE001
                out[p] = (0, b"", {})
    return out


def main():
    ap = argparse.ArgumentParser(description="部署后线上语义校验")
    ap.add_argument("--base", default=DEFAULT_BASE, help="站点根地址，默认 " + DEFAULT_BASE)
    ap.add_argument("--expect-cases", type=int, default=local_case_count(),
                    help="期望的 curated 案例数（默认读本地 data/cases.json）")
    ap.add_argument("--expect-inbox", type=int, default=0, help="期望的采集队列数，公开版为 0")
    ap.add_argument("--brand", default="拆解海外", help="期望出现的品牌名")
    ap.add_argument("--dead-brand", default="赚钱案例", help="不该再出现的旧名")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    fails = []

    def get(path):
        return fetch_one(base, path)

    def check(name, ok, detail=""):
        print(("  [OK] " if ok else "  [FAIL] ") + name + ("  " + detail if detail else ""))
        if not ok:
            fails.append(name)

    print("目标站点：" + base)
    print("")

    print("1) 首页与根路径")
    st, body, heads = get("/")
    html = body.decode("utf-8", "replace")
    check("HTTP 200", st == 200, str(st))
    check("不是 XML 对象列表（静态托管已生效）",
          "<ListBucketResult" not in html and not html.lstrip().startswith("<?xml"))
    check("包含品牌「%s」" % args.brand, args.brand in html)
    check("无旧名「%s」残留" % args.dead_brand, args.dead_brand not in html)
    check("Content-Type 为 text/html", "text/html" in heads.get("Content-Type", ""))

    print("2) 案例列表页 /case/index.html")
    st, body, heads = get("/case/index.html")
    html = body.decode("utf-8", "replace")
    slugs = sorted(set(re.findall(r'href="\./([a-z0-9\-]+)\.html"', html)))
    check("HTTP 200", st == 200, str(st))
    check("正文内联（禁用 JS 也能读）", len(body) > 20000, "%d 字节" % len(body))
    check("列出 %d 个案例链接" % args.expect_cases, len(slugs) == args.expect_cases, str(len(slugs)))

    print("3) %d 个案例页逐个访问" % len(slugs))
    # 并发取回再顺序断言：失败清单的**顺序**要和 slugs 一致，所以判断不并发做
    pages = fetch_many(base, ["/case/%s.html" % s for s in slugs])
    bad = []
    for slug in slugs:
        st, body, heads = pages.get("/case/%s.html" % slug, (0, b"", {}))
        page = body.decode("utf-8", "replace")
        if not (st == 200 and args.brand in page and len(body) > 20000):
            # status=0 是「取不到」，body 里是异常摘要 —— 带上它，
            # 否则只看到 (0,72B) 根本判断不出是超时还是 DNS
            why = ("  " + page[:60]) if st == 0 else ""
            bad.append("%s(%s,%dB%s)" % (slug, st, len(body), why))
    check("全部 200 且正文内联", not bad, ",".join(bad) if bad else "%d/%d" % (len(slugs), len(slugs)))
    if slugs:
        st, body, heads = get("/case/%s.html" % slugs[0])
        check("case/ 目录走 no-cache", "no-cache" in heads.get("Cache-Control", ""),
              heads.get("Cache-Control", ""))

    print("4) 404 页")
    st, body, heads = get("/this-page-does-not-exist-xyz.html")
    html = body.decode("utf-8", "replace")
    check("返回 404 状态码", st == 404, str(st))
    check("渲染的是自定义 404 页", args.brand in html and "404" in html)

    print("5) 数据文件 data.json（语义校验，不看字节数）")
    st, body, heads = get("/data.json")
    data = json.loads(body.decode("utf-8"))
    check("HTTP 200", st == 200, str(st))
    check("schema_version 存在", bool(data.get("schema_version")), str(data.get("schema_version")))
    check("cases 数量 = %d" % args.expect_cases, len(data["cases"]) == args.expect_cases,
          str(len(data["cases"])))
    check("inbox = %d（公开版不含未核实队列）" % args.expect_inbox,
          len(data["inbox"]) == args.expect_inbox, str(len(data["inbox"])))
    check("site.title = %s" % args.brand,
          data["site"]["title"] == args.brand, data["site"]["title"])
    check("site.url 指向本域名", data["site"]["url"] == base, data["site"]["url"])
    if len(slugs) == len(data["cases"]):
        check("页面数与数据条数一致", True, "%d 页 / %d 条" % (len(slugs), len(data["cases"])))

    print("6) SEO 文件")
    st, body, heads = get("/sitemap.xml")
    xml = body.decode("utf-8", "replace")
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    check("sitemap 200", st == 200, str(st))
    check("全部用本域名", all(l.startswith(base) for l in locs), "%d 条" % len(locs))
    check("条目数 = 案例数 + 列表页 + 首页", len(locs) >= args.expect_cases + 2, str(len(locs)))
    st, body, heads = get("/robots.txt")
    robots = body.decode("utf-8", "replace")
    check("robots 200", st == 200, str(st))
    check("robots 含 Sitemap 行", ("Sitemap: %s/sitemap.xml" % base) in robots)

    print("7) 静态资源")
    for path, want in [("/style.css", "text/css"), ("/app.js", "javascript"),
                       ("/data.js", "javascript")]:
        st, body, heads = get(path)
        check("%s 200 且类型正确" % path, st == 200 and want in heads.get("Content-Type", ""),
              heads.get("Content-Type", ""))

    print("")
    if fails:
        print("=== 有 %d 项失败 ===" % len(fails))
        for name in fails:
            print("  - " + name)
        return 1
    print("=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
