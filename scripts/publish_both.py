#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一个通过的 case，同时发公众号 + 小红书。

用法：
    python scripts/publish_both.py nitra       # 指定案例（认 id / 名字 / 标题片段）
    python scripts/publish_both.py              # 默认发最近 1 条两边都没发过的
    python scripts/publish_both.py --near 3     # 最近 3 条
    python scripts/publish_both.py --all        # 其余全部
    python scripts/publish_both.py --queue      # 只看会发哪几条（不连浏览器）
    python scripts/publish_both.py nitra --dry  # 只打印计划，不碰浏览器
    python scripts/publish_both.py nitra --yes  # 小红书真发布（公众号只能存草稿）

默认两边都**只存草稿**，不群发 / 不上公开。

一边的步骤（失败不影响另一边，最后汇总）：
    公众号：make_article.py --id <id> --format html
            → wechat_publish.py build        （生成 公众号-<id>拆解.html）
            → wechat_publish.py publish --case <id>（存草稿 + 原创声明 + 赞赏）
    小红书：xhs_publish.py build --case <id> （缺卡片才现造，已存在则跳过）
            → xhs_publish.py publish --case <id> [--yes]
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import xhs_publish as x          # noqa: E402  只用它读 cases / 模糊匹配 / 草稿记录
import wechat_publish as wp      # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable


def run(args, enabled=True):
    """跑一条子命令，返回 (rc, cmd)。enabled=False 时只打印计划（--dry）。"""
    cmd = [PY] + args
    print("    $ %s" % " ".join(args))
    if not enabled:
        return 0, cmd
    p = subprocess.run(cmd, cwd=ROOT, encoding="utf-8", errors="replace")
    return p.returncode, cmd


def article_html(cid):
    """公众号发布就绪版是否已存在（公众号-<id|name>拆解.html 或 out/articles/<id>.html）。

    注意 find_article 扫的是 wechat_publish 模块自己的 ROOT，这里不能改它的
    变量来造假（测试里踩过：pb.ROOT 换了，find_article 仍扫真实项目根目录）。
    """
    if os.path.isfile(os.path.join(ROOT, "out", "articles", cid + ".html")):
        return True
    try:
        return wp.find_article(cid, None) is not None
    except Exception:                                 # noqa: BLE001
        return False


def wechat_side(c, dry):
    """公众号：生成 HTML → 发布就绪版 → 存草稿。返回 True 表示过了一道。"""
    cid = c["id"]
    print("  [公众号] %s —— %s" % (cid, c.get("name", "")))
    rc, _ = run(["scripts/make_article.py", "--id", cid, "--format", "html"], not dry)
    if rc != 0:
        print("    [warn] make_article 退出码 %s" % rc)
    rc, _ = run(["scripts/wechat_publish.py", "build", "--case", cid], not dry)
    if rc != 0:
        print("    [warn] wechat build 退出码 %s" % rc)
    # 公众号侧 --dry 是「不打开浏览器」，可以直接透传
    rc, _ = run(["scripts/wechat_publish.py", "publish", "--case", cid] +
                (["--dry"] if dry else []))
    return rc == 0


def xhs_side(c, dry, yes):
    """小红书：缺卡片才造 → 存草稿（--yes 真发）。

    ⚠️ 不能把 --dry 透传给 xhs_publish：它的 --dry 语义是「照常连浏览器填表，
    只是不点发布」，会真的把表单填进编辑页（2026-09-26 实测污染了 bustem）。
    所以 --dry 一律由这里拦掉，只打印计划。
    """
    cid = c["id"]
    print("  [小红书] %s —— %s" % (cid, c.get("name", "")))
    note_json = os.path.join(ROOT, "out", "xhs", cid, "note.json")
    if not dry and not os.path.isfile(note_json):
        rc, _ = run(["scripts/xhs_publish.py", "build", "--case", cid], not dry)
        if rc != 0:
            print("    [warn] xhs build 退出码 %s（卡片可能没造全，仍然继续发）" % rc)
    cmd = ["scripts/xhs_publish.py", "publish", "--case", cid]
    if yes:
        cmd.append("--yes")
    rc, _ = run(cmd, not dry)
    return rc == 0


def _wechat_done_ids():
    """公众号已记录发过的 id（load_published 是 {id: {...}} 或 [{id:...}] 都可能）。"""
    d = wp.load_published() or {}
    if isinstance(d, dict):
        return set(d.keys())
    if isinstance(d, list):
        return {x.get("id") for x in d if isinstance(x, dict) and x.get("id")}
    return set()


def both_pending():
    """两条链路都没碰过、且公众号已有发布就绪稿的案例（库里最新在前）。"""
    wp_done = _wechat_done_ids()
    x_done = set(x._drafted_ids()) | set(x._published_ids()) | {"voklit"}
    out = []
    for c in reversed(x.load_cases()):              # 与 xhs 队列同口径：最新在前
        if c["id"] in wp_done or c["id"] in x_done:
            continue
        if not article_html(c["id"]):
            continue                                # 公众号没稿，交给 --case 显式指定
        out.append(c)
    return out


def pick(args):
    """按参数决定这一轮要发谁。返回 (cases, mode)。"""
    if args.case:
        return [x.find_case_fuzzy(args.case)], "case"
    cases = both_pending()
    if args.near:
        return cases[:args.near], "near"
    return cases, "queue"


def cmd_queue(args):
    cases, _ = pick(args)
    if not cases:
        print("两边都没发过的案例已经清空了（要重发请显式 --case）。")
        return 0
    print("待发 %d 条（公众号 + 小红书各一份，均存草稿）：" % len(cases))
    for i, c in enumerate(cases, 1):
        print("  %2d. %-24s %s" % (i, c["id"], c.get("name", "")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="一个通过的 case 同时发公众号 + 小红书")
    ap.add_argument("case", nargs="?", help="案例 id / 名字 / 标题片段（不写 = 发最近 1 条）")
    ap.add_argument("--near", type=int, default=0, help="发最近几条两边都没发过的，默认 1")
    ap.add_argument("--all", action="store_true", help="把剩下的全发")
    ap.add_argument("--queue", action="store_true", help="只看会发哪几条（不连浏览器）")
    ap.add_argument("--dry", action="store_true", help="只打印要跑的子命令")
    ap.add_argument("--yes", action="store_true",
                    help="小红书真发布（默认只存草稿；公众号侧只能存草稿）")
    args = ap.parse_args(argv)

    if args.queue:
        return cmd_queue(args)
    if not args.case and not args.all and not args.near:
        args.near = 1                               # 不带参数 = 发一个
    cases, mode = pick(args)
    if args.all:
        all_cases = both_pending()
        cases = all_cases if not args.case else cases
    if not cases:
        print("没有待发的案例（两边记录都齐了）。要看清单跑 `publish_both.py --queue`。")
        return 0

    print("=" * 62)
    print("同时发 %d 条（%s）%s" %
          (len(cases), "/".join(c["id"] for c in cases),
           "  [dry-run]" if args.dry else "  " + ("[真发]" if args.yes else "[存草稿]")))
    ok = []
    bad = []
    for i, c in enumerate(cases, 1):
        print("\n[%d/%d] %s —— %s" % (i, len(cases), c["id"], c.get("name", "")))
        w = xhs_ok = False
        try:
            w = wechat_side(c, args.dry)
        except Exception as e:
            print("    [公众号] 异常：%s：%s" % (type(e).__name__, e))
        try:
            xhs_ok = xhs_side(c, args.dry, args.yes)
        except Exception as e:
            print("    [小红书] 异常：%s：%s" % (type(e).__name__, e))
        (ok if w and xhs_ok else bad).append(c["id"])

    print("\n" + "=" * 62)
    print("完成 %d 条，两边都成功 %d 条" % (len(cases), len(ok)))
    if bad:
        print("有问题 %d 条：%s" % (len(bad), ", ".join(bad)))
        print("（单条重跑：python scripts/publish_both.py <id>）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
