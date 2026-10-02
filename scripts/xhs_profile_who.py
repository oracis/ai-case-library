#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查清「小红书草稿到底在哪个 profile / 哪个账号」。

为什么需要这个（2026-10-02 用户反馈）
--------------------------------------
用户打开 creator.xiaohongshu.com，右上角显示「小红薯6474A1BD」，
草稿箱(0) —— **空的**。但项目 `data/xhs_drafts.json` 记着 37 条草稿。

根因候选（逐个排除，别猜）：
  A. profile 用错：项目 `xhs_publish.py` 里 `PUB_PORT=9222` 配的是
     `C:\\Users\\DELL\\chrome-debug-profile`，而公众号 autostart 用的是
     `%LOCALAPPDATA%\\Google\\ChromeCDP` —— **两个不同profile**，
     小红书登录态在 debug-profile，不在 ChromeCDP。
  B. 账号不同：同一个 profile 里换过账号。
  C. 草稿被删了。

⚠ **本项目记忆里的血泪教训**：Cookies 文件大小 / 行数**不能**当登录态判据
（日常Chrome 1.4MB/900 条但微信 cookie 只有 8 条，CDP profile 32KB/38 条
却登录完好）。所以这里**不拿大小下结论**，只打印事实：
  · profile 路径 + Cookies 文件大小/行数（仅参考）
  · 每个 profile 里 `xiaohongshu.com` 的 cookie 条数与关键 cookie 名
  · CDP 端口是否开着

真正的判据永远是：**那个 profile 打开 creator.xiaohongshu.com 后，
右上角显示的是哪个账号、草稿箱几篇**。

用法：
    python -X utf8 scripts/xhs_profile_who.py            # 只查磁盘
    python -X utf8 scripts/xhs_profile_who.py --live     # 顺带用 CDP 实拉
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

PROFILES = [
    ("项目 xhs 用的（xhs_publish.py PUB_PORT=9222）",
     r"C:\Users\DELL\chrome-debug-profile"),
    ("公众号 autostart 用的（%LOCALAPPDATA%）",
     os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP")),
]

XHS_HOSTS = ("xiaohongshu.com",)


def read_cookies(db_path):
    """读 cookie 为 list。Chrome 运行时文件可能被锁 ⇒ 三级降级。"""
    if not os.path.exists(db_path):
        return None, "文件不存在"
    tmp = None
    for how in ("copy", "read", "backup"):
        try:
            if how == "copy":
                tmp = os.path.join(tempfile.gettempdir(),
                                   "ck_%d.db" % os.getpid())
                shutil.copy2(db_path, tmp)
                con = sqlite3.connect(tmp)
            elif how == "read":
                con = sqlite3.connect("file:%s?mode=ro&immutable=1" % db_path,
                                      uri=True)
            else:
                src = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
                tmp = os.path.join(tempfile.gettempdir(),
                                   "ckb_%d.db" % os.getpid())
                dst = sqlite3.connect(tmp)
                src.backup(dst)
                con = dst
            rows = con.execute(
                "SELECT host_key,name,length(encrypted_value) "
                "FROM cookies WHERE host_key LIKE ?",
                ("%" + XHS_HOSTS[0],)).fetchall()
            con.close()
            return rows, "ok(%s)" % how
        except Exception as e:
            err = "%s: %s" % (how, e)
            continue
    return None, "读取失败(%s)" % err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="顺便看 CDP 端口开着没")
    args = ap.parse_args()

    print("=" * 74)
    print("小红书草稿在哪个 profile？—— 只报事实，不猜")
    print("=" * 74)

    for label, prof in PROFILES:
        print("\n■ %s" % label)
        print("  路径: %s" % prof)
        if not os.path.isdir(prof):
            print("  ✗ 目录不存在")
            continue
        db = os.path.join(prof, "Default", "Network", "Cookies")
        if os.path.exists(db):
            print("  Cookies 文件: %.1f KB（仅供参考，不是登录态判据）"
                  % (os.path.getsize(db) / 1024.0))
        rows, how = read_cookies(db)
        if rows is None:
            print("  小红书 cookie: %s" % how)
            continue
        print("  小红书 cookie: %d 条（读取方式 %s）" % (len(rows), how))
        names = sorted({r[1] for r in rows})
        show = [n for n in names
                if n in ("web_session", "webId", "a1", "webId", "gid",
                         "customerClientId", "xhsappid", "abRequestId",
                         "sec_poison_id", "loadts", "xsecappid")]
        if show:
            print("  关键 cookie: %s" % ", ".join(show))
        others = [n for n in names if n not in show][:12]
        if others:
            print("  其他: %s" % ", ".join(others))

    if args.live:
        print("\n" + "=" * 74)
        print("CDP 端口")
        print("=" * 74)
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for port in (9222, 9333):
            try:
                d = json.loads(op.open(
                    "http://127.0.0.1:%d/json/version" % port,
                    timeout=2).read())
                print("  %d: %s" % (port, d.get("Browser")))
            except Exception:
                print("  %d: 未开" % port)

        print("\n⚠ 提醒：Cookies 大小/条数都不是登录态判据。")
        print("  唯一判据 = 那个 profile 打开 creator.xiaohongshu.com,")
        print("  右上角显示哪个账号、草稿箱几篇。")


if __name__ == "__main__":
    main()