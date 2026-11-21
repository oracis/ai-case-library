#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动端到端：把发布脚本里的「独立双回读」判据抽成一次可复核的验证。

背景（为什么要这个脚本）
------------------------
「发布成功」在本项目的唯一判据是两路独立证据同时命中：
  第一路  已发表列表按标题命中（远端确实把它列进了已发表）
  第二路  公开页 200 + 正文关键词命中（读者真的读得到，不是 404 / 审核中）
接口返回 200、articleId、rc=0 都不算证据（PRD 反复踩过假绿）。

这一层过去散落在各 publish 脚本的尾巴里，只有「那一次发布」会跑，事后
想复核一篇已发的稿子没有统一入口。本脚本把它抽成独立工具：

  · 第二路直接复用 `verify_page.py`（真浏览器 CDP，已处理 WAF 假 404、
    headless 假绿、登录墙 400 字门槛）——不重写协议、不引入 Playwright；
  · 第一路接受一个「已发表列表标题」的文本（由各平台脚本导出，或人工从
    后台复制），用 `title_match.py` 的唯一命中判据，和台账对账同一把尺子。

它不联网自动抓列表（各平台列表接口形态不同且要登录态，硬做会返假绿），
所以是**人工触发的验证工具**，不进 CI。这正是「复用现有 CDP 双回读」的
口径：把已有的两路判据收口成一个入口，而不是再造一套自动化。

用法
----
  # 只有公开页这一路（最常见：两朵云 / CSDN / 掘金）
  python -X utf8 scripts/e2e_platform_readback.py \
      --url https://blog.csdn.net/u/article/details/123 \
      --expect "新写的小标题,新结论短语" --title 招聘

  # 两路都验：已发表列表标题来自文件（每行一个标题）
  python -X utf8 scripts/e2e_platform_readback.py \
      --url ... --expect ... --title ... \
      --published-titles tmp/wechat_published_titles.txt

  # 只验列表、不验公开页（如头条已发表只能导航式读）
  python -X utf8 scripts/e2e_platform_readback.py \
      --title ... --published-titles tmp/titles.txt --skip-page

退出码：0 两路（或启用路）都命中 / 1 有路未命中 / 2 浏览器起不来。
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from title_match import title_match                       # noqa: E402


def check_published_list(path, title):
    """第一路：标题在已发表列表里唯一命中。返回 (ok, detail)。"""
    if not os.path.isfile(path):
        return False, "已发表列表文件不存在：%s" % path
    with open(path, encoding="utf-8") as f:
        rows = [line.strip() for line in f if line.strip()]
    hits = [r for r in rows if title_match(title, r)]
    if not hits:
        return False, "已发表列表 %d 行里没有匹配「%s」的标题" % (len(rows), title)
    if len(hits) > 1:
        return False, "标题「%s」在已发表列表命中 %d 行（不唯一，不能认领）：%s" % (
            title, len(hits), hits[:3])
    return True, "已发表列表唯一命中：%s" % hits[0]


def check_public_page(args):
    """第二路：直接调 verify_page.py，复用它全部回读判据。返回 (ok, detail)。"""
    cmd = [sys.executable, "-X", "utf8", os.path.join(SCRIPTS, "verify_page.py"),
           "--url", args.url, "--wait", str(args.wait)]
    if args.expect:
        cmd += ["--expect", args.expect]
    if args.title:
        cmd += ["--title", args.title]
    if args.chrome:
        cmd += ["--chrome", args.chrome]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 0:
        return True, "公开页回读命中（%d 关键词）" % len(
            [w for w in args.expect.replace("，", ",").split(",") if w.strip()])
    if p.returncode == 2:
        return False, "浏览器起不来，第二路无法验证"
    return False, "公开页回读失败：\n" + out[-800:]


def main():
    ap = argparse.ArgumentParser(description="发布后独立双回读（手动验证）")
    ap.add_argument("--title", default="", help="要认领的发布标题")
    ap.add_argument("--published-titles", default="",
                    help="已发表标题列表文件（每行一个），提供则验第一路")
    ap.add_argument("--url", default="", help="公开页 URL，提供则验第二路")
    ap.add_argument("--expect", default="", help="公开页正文必须命中的关键词，逗号分隔")
    ap.add_argument("--chrome", default="", choices=["", "tc", "csdn"],
                    help="verify_page 用哪个浏览器 profile")
    ap.add_argument("--wait", type=float, default=14.0)
    ap.add_argument("--skip-page", action="store_true", help="只验已发表列表")
    a = ap.parse_args()

    if not a.published_titles and (not a.url or a.skip_page):
        ap.error("至少要验一路：给 --published-titles，或给 --url（公开页）")

    results = []
    if a.published_titles:
        if not a.title:
            ap.error("验已发表列表必须给 --title")
        ok, detail = check_published_list(a.published_titles, a.title)
        results.append(("第一路·已发表列表", ok, detail))
    if a.url and not a.skip_page:
        ok, detail = check_public_page(a)
        results.append(("第二路·公开页", ok, detail))

    print("=" * 60)
    print("独立双回读结果")
    print("=" * 60)
    for name, ok, detail in results:
        print("  [%s] %s：%s" % ("PASS" if ok else "FAIL", name, detail))
    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print("\n✗ 未通过：%s —— 先按标题查已发表列表，别原地重发" % "、".join(bad))
        return 1
    print("\n✓ %d 路证据全部命中（接口 200 / articleId 不算证据）" % len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
