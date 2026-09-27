#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一个通过的 case，一次发到公众号 + 小红书 + 今日头条（可只选其中几个）。

用法：
    python scripts/publish_both.py nitra       # 指定案例（认 id / 名字 / 标题片段）
    python scripts/publish_both.py              # 默认发最近 1 条「还有平台没发过」的
    python scripts/publish_both.py --near 3     # 最近 3 条
    python scripts/publish_both.py --all        # 其余全部
    python scripts/publish_both.py --queue      # 只看会发哪几条、各发哪个平台
    python scripts/publish_both.py nitra --dry  # 只打印计划，不碰浏览器
    python scripts/publish_both.py nitra --yes  # 小红书 / 头条真发布（公众号只能存草稿）

    --platforms wechat,xhs,toutiao   # 默认三个全发；想少发就显式列
    --platforms toutiao              # 只补头条（比如那 36 条只发过公众号+小红书的）

默认各平台都**只存草稿**，不群发 / 不上公开。

「新 case 发布」的正确姿势
--------------------------
新 case 入库 → 跑 `python scripts/publish_both.py <id>`（或 --near 3），
会按每个平台**各自的记录**判断：发过的平台跳过，没发过的补发。
所以一条 case 可以先只发了公众号、后来再补小红书和头条，不会重复发。

一边的步骤（失败不影响另一边，最后汇总）：
    公众号：make_article.py --id <id> --format html
            → wechat_publish.py build        （生成 公众号-<id>拆解.html）
            → wechat_publish.py publish --case <id>（存草稿 + 原创声明 + 赞赏）
    小红书：xhs_publish.py build --case <id> （缺卡片才现造，已存在则跳过）
            → xhs_publish.py publish --case <id> [--yes]
    今日头条：toutiao_publish.py build --case <id>（长文改写，缺稿才造）
            → toutiao_publish.py publish --case <id> [--yes]
              ⚠️ 头条没有个人号发布 API，走 CDP；**必须先登录头条号**
              （调试 Chrome 9222 打开 mp.toutiao.com 扫码一次）。本脚本开局会
              先跑 `toutiao_publish.py login` 自检：没登录就整轮跳过头条，
              只提示一次，不会让每条 case 都失败一遍。
              ⚠️ 头条侧 --dry 语义是「照常连浏览器填表、不点按钮」，所以本脚本
              的 --dry 一律拦掉不传给头条 / 小红书侧。
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import xhs_publish as x          # noqa: E402  只用它读 cases / 模糊匹配 / 草稿记录
import wechat_publish as wp      # noqa: E402
import toutiao_publish as tp     # noqa: E402  头条侧

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable

PLATFORMS = ("wechat", "xhs", "toutiao")
LABEL = {"wechat": "公众号", "xhs": "小红书", "toutiao": "今日头条"}


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


# ---- 三平台各自的「已发过」记录 -------------------------------------------
def _wechat_done_ids():
    """公众号已记录发过的 id（load_published 是 {id: {...}} 或 [{id:...}] 都可能）。"""
    d = wp.load_published() or {}
    if isinstance(d, dict):
        return set(d.keys())
    if isinstance(d, list):
        return {x_.get("id") for x_ in d if isinstance(x_, dict) and x_.get("id")}
    return set()


def _xhs_done_ids():
    return set(x._drafted_ids()) | set(x._published_ids()) | {"voklit"}


def _toutiao_done_ids():
    return set(tp._ids(tp.DRAFT_FILE)) | set(tp._ids(tp.PUB_FILE)) | {"voklit"}


def done_ids(platform):
    return {
        "wechat": _wechat_done_ids,
        "xhs": _xhs_done_ids,
        "toutiao": _toutiao_done_ids,
    }[platform]()


def pending_platforms(c):
    """这条 case 还有哪些平台没发过。"""
    return [p for p in PLATFORMS if c["id"] not in done_ids(p)]


def both_pending():
    """两边（公众号 + 小红书）都没碰过、且公众号已有发布就绪稿的案例。

    保留旧语义给老脚本/测试用；三平台队列看 pending_cases()。
    """
    wp_done = _wechat_done_ids()
    x_done = _xhs_done_ids()
    out = []
    for c in reversed(x.load_cases()):              # 与 xhs 队列同口径：最新在前
        if c["id"] in wp_done or c["id"] in x_done:
            continue
        if not article_html(c["id"]):
            continue                                # 公众号没稿，交给 --case 显式指定
        out.append(c)
    return out


def pending_cases(platforms):
    """三平台队列：至少一个指定平台还没发过的案例（库里最新在前）。

    公众号没稿的不自动出队（避免发空页），除非用 --case 显式点名。
    """
    want = [p for p in platforms if p in PLATFORMS]
    out = []
    for c in reversed(x.load_cases()):
        todo = [p for p in want if c["id"] not in done_ids(p)]
        if not todo:
            continue
        if not article_html(c["id"]):
            continue
        out.append(c)
    return out


def toutiao_login_ok(dry=False):
    """开局自检一次头条登录态。没登录 → 整轮跳过头条，只提示一次。"""
    if dry:
        return True
    rc, _ = run(["scripts/toutiao_publish.py", "login"])
    return rc == 0


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


def toutiao_side(c, dry, yes):
    """今日头条：缺稿才 build → 填发文页（默认存草稿，--yes 才点发布）。

    ⚠️ 不能把 --dry 透传给 toutiao_publish（它的 --dry 会照常连浏览器填表），
    所以 --dry 由这里拦掉，只打印计划。
    """
    cid = c["id"]
    print("  [今日头条] %s —— %s" % (cid, c.get("name", "")))
    meta = os.path.join(ROOT, "out", "toutiao", cid, "meta.json")
    if not dry and not os.path.isfile(meta):
        rc, _ = run(["scripts/toutiao_publish.py", "build", "--case", cid], not dry)
        if rc != 0:
            print("    [warn] toutiao build 退出码 %s（仍然继续发）" % rc)
    cmd = ["scripts/toutiao_publish.py", "publish", "--case", cid]
    if yes:
        cmd.append("--yes")
    rc, _ = run(cmd, not dry)
    return rc == 0


def run_side(platform, c, dry, yes):
    if platform == "wechat":
        return wechat_side(c, dry)
    if platform == "xhs":
        return xhs_side(c, dry, yes)
    return toutiao_side(c, dry, yes)


def pick(args):
    """按参数决定这一轮要发谁。返回 (cases, mode)。"""
    if args.case:
        return [x.find_case_fuzzy(args.case)], "case"
    cases = pending_cases(getattr(args, "platforms", PLATFORMS))
    if args.near:
        return cases[:args.near], "near"
    return cases, "queue"


def cmd_queue(args):
    cases, _ = pick(args)
    if not cases:
        print("没有待发的案例（所选平台都发过了）。")
        return 0
    print("待发 %d 条（最新在前）：" % len(cases))
    for i, c in enumerate(cases, 1):
        todo = pending_platforms(c)
        todo = [p for p in todo if p in args.platforms]
        print("  %2d. %-24s %-26s → %s"
              % (i, c["id"], c.get("name", ""),
                 " + ".join(LABEL[p] for p in todo) or "（都发过了）"))
    return 0


def _parse_platforms(s):
    if not s:
        return list(PLATFORMS)
    out = []
    for p in s.replace("，", ",").split(","):
        p = p.strip().lower()
        if p in PLATFORMS and p not in out:
            out.append(p)
        elif p:
            raise SystemExit("不认识的平台：%s（可选 %s）" % (p, "/".join(PLATFORMS)))
    return out or list(PLATFORMS)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="一个通过的 case 同时发公众号 + 小红书 + 今日头条")
    ap.add_argument("case", nargs="?", help="案例 id / 名字 / 标题片段（不写 = 发最近 1 条）")
    ap.add_argument("--near", type=int, default=0, help="发最近几条还没发全的，默认 1")
    ap.add_argument("--all", action="store_true", help="把剩下的全发")
    ap.add_argument("--queue", action="store_true", help="只看会发哪几条（不连浏览器）")
    ap.add_argument("--dry", action="store_true", help="只打印要跑的子命令")
    ap.add_argument("--yes", action="store_true",
                    help="小红书 / 头条真发布（默认只存草稿；公众号侧只能存草稿）")
    ap.add_argument("--platforms", default="",
                    help="只发这几个平台，逗号分隔（默认 %s）" % ",".join(PLATFORMS))
    ap.add_argument("--toutiao", action="store_true",
                    help="（兼容旧参数，现在头条默认就在队列里）")
    args = ap.parse_args(argv)
    args.platforms = _parse_platforms(args.platforms)

    if args.queue:
        return cmd_queue(args)
    if not args.case and not args.all and not args.near:
        args.near = 1                               # 不带参数 = 发一个
    cases, mode = pick(args)
    if args.all:
        all_cases = pending_cases(args.platforms)
        cases = all_cases if not args.case else cases
    if not cases:
        print("没有待发的案例（所选平台都发过了）。要看清单跑 `--queue`。")
        return 0

    # 头条登录态只自检一次：没登录就整轮跳过，别让每条 case 都撞一次墙
    use_tt = "toutiao" in args.platforms
    if use_tt and not toutiao_login_ok(args.dry):
        print("  ! 头条没登录，本轮跳过今日头条（其它平台照发）")
        args.platforms = [p for p in args.platforms if p != "toutiao"]
        use_tt = False

    print("=" * 62)
    print("发 %d 条（%s）%s" %
          (len(cases), "/".join(c["id"] for c in cases),
           "  [dry-run]" if args.dry else "  " + ("[真发]" if args.yes else "[存草稿]")))
    print("平台：" + " + ".join(LABEL[p] for p in args.platforms))
    ok = []
    bad = []
    for i, c in enumerate(cases, 1):
        print("\n[%d/%d] %s —— %s" % (i, len(cases), c["id"], c.get("name", "")))
        done = True
        for p in args.platforms:
            if c["id"] in done_ids(p):
                print("    [%s] 已发过，跳过" % LABEL[p])
                continue
            try:
                if not run_side(p, c, args.dry, args.yes):
                    done = False
            except Exception as e:                   # noqa: BLE001
                print("    [%s] 异常：%s：%s" % (LABEL[p], type(e).__name__, e))
                done = False
        (ok if done else bad).append(c["id"])

    print("\n" + "=" * 62)
    print("完成 %d 条，全平台都过了 %d 条" % (len(cases), len(ok)))
    if bad:
        print("有问题 %d 条：%s" % (len(bad), ", ".join(bad)))
        print("（单条重跑：python scripts/publish_both.py <id>）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
