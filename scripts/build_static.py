#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把案例库构建成纯静态站点，产物在 dist/，可以直接扔到阿里云 OSS / GitHub Pages /
任意静态托管，不需要服务器、不需要 Python 运行环境。

用法：
    python scripts/build_static.py                  # 全量构建到 dist/
    python scripts/build_static.py --no-inbox       # 精简版：不含采集队列（对外发布更合适）
    python scripts/build_static.py --out public     # 换输出目录
    python scripts/build_static.py --pretty         # data.json 排版展开（便于人肉 diff，体积翻倍）
    python scripts/build_static.py --site-url https://你的域名    # 生成 sitemap / canonical

产物：
    dist/index.html        注入了 window.__STATIC__ = true，前端据此切换成只读模式；
                           末尾另注了一段 <noscript> 案例清单（无 JS 时的降级导航）
    dist/style.css
    dist/app.js
    dist/data.js           window.__CASE_LIB_DATA__ = {...}   ← 页面实际加载这个
    dist/data.json         同一份数据，给第三方程序抓取
    dist/404.html          OSS 静态网站托管可以把它配成「默认 404 页」
    dist/case/<id>.html    每条案例一个独立页面，正文写死在 HTML 里（给搜索引擎用）
    dist/case/index.html   静态总目录
    dist/sitemap.xml       需要 --site-url 或 data/site.json 里配了 url 才生成
    dist/robots.txt

为什么要预渲染出 case/<id>.html（见 scripts/prerender.py 的详细说明）：
    index.html 是纯客户端渲染的 SPA，爬虫拿到的只是个空壳；案例正文只有预渲染成
    静态页面才能被收录、被分享预览，也才能当公众号「阅读原文」的落地页。

为什么要生成 data.js 而不是只放 data.json：
直接双击 dist/index.html 打开时协议是 file://，fetch 本地文件会被浏览器 CORS 拦掉；
<script> 不受这个限制。所以页面走 data.js，这样整个 dist/ 拷到哪儿都能双击打开。
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import server                                             # noqa: E402
except Exception as e:                                        # noqa: BLE001
    print("[!] 无法导入 server.py：%s" % e)
    sys.exit(1)

sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import prerender                                          # noqa: E402
except Exception as e:                                        # noqa: BLE001
    print("[!] 无法导入 scripts/prerender.py：%s" % e)
    sys.exit(1)

STATIC_DIR = os.path.join(ROOT, "static")

# 前端里 app.js 的引入点 —— 静态标记就注在它前面
ANCHOR = '<script src="./app.js"></script>'
# 由本脚本（含 prerender.py）生成的东西。清理输出目录时只认这些，避免误删别人的文件。
OUR_FILES = ("index.html", "style.css", "app.js", "data.js", "data.json",
             "404.html", "sitemap.xml", "robots.txt", "case")

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404 · 拆解海外</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0e1116; color: #e6edf3;
         font: 15px/1.7 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  .box { text-align: center; padding: 32px; }
  .n { font-size: 64px; font-weight: 700; letter-spacing: -.03em; color: #3d444d; }
  a { color: #e5b567; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="box">
  <div class="n">404</div>
  <p>这个地址没有内容。</p>
  <p><a href="./">回到案例库首页</a></p>
</div>
</body>
</html>
"""


def human(n):
    return "%.1f KB" % (n / 1024.0) if n < 1024 * 1024 else "%.2f MB" % (n / 1048576.0)


def prepare_out(out_dir):
    """清空并重建输出目录。做了防误删检查。"""
    abs_out = os.path.abspath(out_dir)

    if abs_out == ROOT:
        print("[!] 输出目录不能是项目根目录")
        return None
    # 别把源码目录当构建产物删了
    for guard in ("static", "data", "scripts", ".git", ".github"):
        if abs_out == os.path.join(ROOT, guard):
            print("[!] 拒绝把 %s 当作输出目录（那是源码目录）" % guard)
            return None
    # 已经有 .git 的目录说明是个仓库，别动
    if os.path.isdir(os.path.join(abs_out, ".git")):
        print("[!] %s 里有 .git，看起来是个仓库，不敢清空" % out_dir)
        return None

    if os.path.isdir(abs_out):
        # 只删自己生成的文件；目录里还有别的文件就一并清掉但给出提示
        leftovers = [f for f in os.listdir(abs_out) if f not in OUR_FILES]
        if leftovers:
            print("    清理输出目录（会删除其中 %d 个非构建文件）" % len(leftovers))
        shutil.rmtree(abs_out)
    os.makedirs(abs_out)
    return abs_out


def build(out_dir, include_inbox=True, pretty=False, site_url=""):
    abs_out = prepare_out(out_dir)
    if not abs_out:
        return 1

    # ---- 1. 取数据（复用服务端那套聚合逻辑，保证线上线下结构完全一致）
    print("[1/5] 聚合数据…")
    payload = server.build_payload()
    if not include_inbox:
        payload["inbox"] = []
        st = payload.get("stats") or {}
        st["inbox_included"] = False
        payload["stats"] = st

    cases = payload.get("cases") or []
    if not cases:
        print("    [!] cases.json 是空的，构建出来会是个空站")

    # ---- 2. 写数据文件
    print("[2/5] 写数据文件…")
    if pretty:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # data.js 走 <script>，所以要把 </script> 之类的提前打断，避免提前结束标签
    safe = body.replace("</", "<\\/")
    data_js = "/* 由 scripts/build_static.py 生成，请勿手改 */\n" \
              "window.__CASE_LIB_DATA__ = %s;\n" % safe
    with open(os.path.join(abs_out, "data.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(data_js)
    with open(os.path.join(abs_out, "data.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(body)

    # ---- 3. 拷静态资源并注入静态标记
    print("[3/5] 拷资源 + 注入静态标记…")
    for name in ("style.css", "app.js"):
        src = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(src):
            print("    [!] 缺少 static/%s" % name)
            return 1
        shutil.copy2(src, os.path.join(abs_out, name))

    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()

    if ANCHOR not in html:
        print("    [!] static/index.html 里找不到 %s" % ANCHOR)
        print("        前端改过结构？静态标记注不进去就没法切成只读模式，先修这里。")
        return 1

    stats = payload.get("stats") or {}
    desc = ("%d 个已拆解的海外软件生意，每个都标了数字可信到什么程度；"
            "外加国内移植可行性与个人可做性双轴评分。" % stats.get("curated", len(cases)))
    meta = (
        '<meta name="description" content="%s">\n'
        '<meta property="og:title" content="拆解海外 · %d 个已拆解的海外软件生意">\n'
        '<meta property="og:description" content="%s">\n'
        '<meta property="og:type" content="website">\n'
    ) % (desc, stats.get("curated", len(cases)), desc)

    inject = ("<!-- 静态部署标记：前端据此切只读模式，并改用 data.js 取数 -->\n"
              "<script>window.__STATIC__ = true;</script>\n")
    html = html.replace("</head>", meta + "</head>", 1)
    html = html.replace(ANCHOR, inject + ANCHOR, 1)

    # 给没有 JS 的访客和爬虫留一条进得去每条案例的路（SPA 首页对它们是空壳）
    site = prerender.load_site(ROOT)
    if site_url:
        site["url"] = site_url.rstrip("/")
    noscript = prerender.noscript_block(cases, site)
    if "</body>" in html:
        html = html.replace("</body>", noscript + "</body>", 1)
    else:
        print("    [!] index.html 里没有 </body>，降级清单注不进去")

    with open(os.path.join(abs_out, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    with open(os.path.join(abs_out, "404.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(NOT_FOUND_HTML)

    # ---- 4. 预渲染每条案例的独立页面
    print("[4/5] 预渲染案例独立页…")
    pr = prerender.build_all(cases, abs_out, site)

    # ---- 5. 自检
    print("[5/5] 自检…")
    problems = []

    built = open(os.path.join(abs_out, "index.html"), encoding="utf-8").read()
    if "window.__STATIC__ = true" not in built:
        problems.append("index.html 里没有静态标记")
    for asset in ("./style.css", "./app.js", "data.js"):
        if asset not in built:
            problems.append("index.html 里没引用 " + asset)
    # 绝对路径在子目录部署时会 404
    for bad in ('href="/', 'src="/'):
        if bad in built:
            problems.append("index.html 里还有根路径引用（%s），子目录部署会 404" % bad)

    js = open(os.path.join(abs_out, "data.js"), encoding="utf-8").read()
    if "window.__CASE_LIB_DATA__" not in js:
        problems.append("data.js 里没有赋值 __CASE_LIB_DATA__")
    if "__STATIC__" in js:
        problems.append("data.js 被写脏了")

    try:
        back = json.loads(body)
        if len(back.get("cases") or []) != len(cases):
            problems.append("data.json 回读后案例数对不上")
    except Exception as e:                                    # noqa: BLE001
        problems.append("data.json 不是合法 JSON：%s" % e)

    # 预渲染产物：独立案例页是 SEO / 分享 / 「阅读原文」的落地页，坏了要当场发现
    if "<noscript" not in built:
        problems.append("index.html 里没有 noscript 降级清单")
    page_problems = prerender.check_pages(abs_out, cases, verbose=False)
    problems += page_problems
    if pr and pr.get("pages") != len(cases):
        problems.append("预渲染页面数 %s 与案例数 %d 不一致" % (pr.get("pages"), len(cases)))

    if problems:
        for p in problems:
            print("    [FAIL] %s" % p)
        return 1
    print("    全部通过")

    # ---- 汇总
    total = 0
    print()
    print("构建完成 · %s" % os.path.relpath(abs_out, ROOT).replace(os.sep, "/") + "/")
    print("-" * 54)
    for name in sorted(os.listdir(abs_out)):
        p = os.path.join(abs_out, name)
        if os.path.isfile(p):
            sz = os.path.getsize(p)
            total += sz
            print("  %-16s %10s" % (name, human(sz)))
        elif os.path.isdir(p) and name == prerender.CASE_DIR:
            n = len([f for f in os.listdir(p) if f.endswith(".html")])
            sz = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p))
            total += sz
            print("  %-16s %10s   （%d 个静态页面）" % (name + "/", human(sz), n))
    print("-" * 54)
    print("  %-16s %10s" % ("合计", human(total)))
    print()
    print("数据：案例 %s · 候选 %s · 采集队列 %s%s" % (
        stats.get("curated", len(cases)), stats.get("candidates", "?"),
        stats.get("inbox", "?"), "" if include_inbox else "（本次未包含）"))
    print("静态页：case/<id>.html × %d · case/index.html · %s · %s" % (
        pr.get("pages", 0) if pr else 0,
        "sitemap.xml" if (pr or {}).get("sitemap") else "无 sitemap（没配站点域名）",
        "robots.txt" if (pr or {}).get("robots") else "无 robots.txt"))
    if not site.get("url"):
        print("        ↳ 想生成 sitemap 和 canonical：加 --site-url https://你的域名，")
        print("          或把域名写进 data/site.json 的 url 字段")
    print("生成时间：%s" % payload.get("generated_at"))
    print()
    print("本地预览：")
    print("  双击 %s/index.html 直接看" % os.path.relpath(abs_out, ROOT).replace(os.sep, "/"))
    print("  或 python -m http.server 8080 --directory %s"
          % os.path.relpath(abs_out, ROOT).replace(os.sep, "/"))
    print()
    print("部署到阿里云 OSS：")
    print("  python scripts/deploy_oss.py --bucket <你的bucket>")
    return 0


def main():
    ap = argparse.ArgumentParser(description="构建静态站点")
    ap.add_argument("--out", default="dist", help="输出目录（默认 dist）")
    ap.add_argument("--no-inbox", action="store_true",
                    help="不含采集队列（未核实的原始素材，对外发布建议带上这个参数）")
    ap.add_argument("--pretty", action="store_true", help="data.json 展开排版")
    ap.add_argument("--site-url", default="", metavar="URL",
                    help="站点域名（如 https://abc.com），用来生成 sitemap 和 canonical；"
                         "也可以写进 data/site.json 的 url 字段，命令行优先")
    args = ap.parse_args()

    print("=" * 54)
    print("  构建静态站点%s" % ("（精简版，不含采集队列）" if args.no_inbox else ""))
    print("=" * 54)
    return build(args.out, include_inbox=not args.no_inbox, pretty=args.pretty,
                 site_url=args.site_url)


if __name__ == "__main__":
    sys.exit(main())
