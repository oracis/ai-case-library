#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP profile 登录态体检。

⚠ 为什么要这个脚本：曾用「Cookies 文件大小」当登录态判据，**这是错的**。

实测证据（本机，2026-10-02）：
    CDP profile  文件 = 32768 字节  总行数 = 38   微信相关 = 12  ← 登录态完好
    日常 Chrome   文件 = 1474560 字节 总行数 = 900  微信相关 =  8

SQLite 文件大小取决于历史记录 / WAL / 已删除页的残留膨胀，
**跟当前有多少条 cookie 没有正相关**。日常 Chrome 那个 1.4MB 的文件里
微信 cookie 反而比CDP profile 少。拿大小判登录态会得出完全相反的结论。

真正的判据是「能不能拿到 token」，即：
  1) sqlite 里mp.weixin.qq.com 的 cookie 行数 > 0（必要不充分）
  2) 实拉一次 cgi-bin/home，能解析出 token（充分必要）

用法：
    python scripts/chrome_login_state.py
    python scripts/chrome_login_state.py --json
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

CDP_PROFILE = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\ChromeCDP"
)
MAIN_PROFILE = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\Chrome\User Data"
)
# Chrome 127+ 的 cookie 库在 Network 子目录，不在 profile 根。
COOKIE_REL = os.path.join("Default", "Network", "Cookies")


def cookie_stats(profile_dir):
    """读 profile 的 cookie 库，返回统计。文件不存在返回 None。

    ⚠ 必须先拷到临时文件再打开：Chrome 运行时源库被锁，且直接读会
    干扰 Chrome 自己的写入。
    """
    db = os.path.join(profile_dir, COOKIE_REL)
    if not os.path.exists(db):
        return None
    tmp = os.path.join(tempfile.gettempdir(), "ck_probe.db")
    try:
        shutil.copy2(db, tmp)
        conn = sqlite3.connect(tmp)
        try:
            total = conn.execute(
                "select count(*) from cookies"
            ).fetchone()[0]
            wechat = conn.execute(
                "select count(*) from cookies "
                "where host_key like '%weixin%'"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {
        "path": db,
        #仅供人参考，**不作判据**
        "bytes": os.path.getsize(db),
        "total": total,
        "wechat": wechat,
    }


def live_token_check():
    """实拉一次后台首页，看能不能解析出 token。

    这是唯一的充分判据：cookie 在磁盘上 ≠ 服务端认你这个会话。
    """
    # 必须复用 wechat_publish 自己的登录函数，那里面包含了
    # 「原地等 → 重导航 cgi-bin/home → 最多 10 轮」这套兜底，
    # 自己重写一套简化版会误报。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import wechat_publish as wp
    except Exception as e:  # pragma: no cover
        return {"ok": False, "why": "import 失败: %s" % e}
    try:
        cdp = wp.CDP()
        # ⚠ _connect_mp 返回的是 **(tid, token)** 二元组，不是裸 token。
        # 早先这里按单值接，得到的是 tid，token 恒为 None ——
        # 于是无论登录态多好都会误报「拿不到 token」。
        res = wp._connect_mp(cdp)
        tid, tok = res if isinstance(res, tuple) else (None, res)
        return {"ok": bool(tok), "token": tok, "tid": tid}
    except SystemExit:
        return {"ok": False, "why": "CDP 连不上（Chrome 没起？）"}
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--skip-live",
        action="store_true",
        help="只查磁盘 cookie，不连CDP（Chrome 没起时用）",
    )
    args = ap.parse_args()

    report = {
        "cdp_profile": cookie_stats(CDP_PROFILE),
        "main_profile": cookie_stats(MAIN_PROFILE),
        "live": None,
    }
    if not args.skip_live:
        report["live"] = live_token_check()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("=" * 62)
    print(" CDP profile 登录态体检")
    print("=" * 62)
    for label, key in (
        ("CDP profile ", "cdp_profile"),
        ("日常 Chrome ", "main_profile"),
    ):
        s = report[key]
        print("\n[%s] %s" % (label, os.path.join(
            {"cdp_profile": CDP_PROFILE,
             "main_profile": MAIN_PROFILE}[key], COOKIE_REL)))
        if s is None:
            print("    文件不存在")
        elif "error" in s:
            print("    读取失败: %s" % s["error"])
        else:
            print("    文件大小   = %d 字节   ← 仅供参考，不是判据" % s["bytes"])
            print("    cookie 总数= %d" % s["total"])
            print("    微信相关   = %d" % s["wechat"])

    print("\n" + "-" * 62)
    live = report["live"]
    if live is None:
        print(" 实拉检测已跳过（--skip-live）")
    elif live.get("ok"):
        print(" 实拉检测: [OK] 拿到 token=%s" % live.get("token"))
        print("\n 结论: 登录态可用，可以直接跑 refresh。")
    else:
        print(" 实拉检测: [X] %s" % live.get("why", "拿不到 token"))
        print("\n 结论: 登录态不可用。请在Chrome 窗口里扫码登录 mp.weixin.qq.com，")
        print("       然后重跑本脚本。⚠ 注意区分：")
        print("       - 进程被收走（10061/10054）→ 重跑一次即可，不是掉登录")
        print("       - Cookie 真丢（这里会报拿不到 token）→ 才要重新扫码")
    print("-" * 62)


if __name__ == "__main__":
    main()