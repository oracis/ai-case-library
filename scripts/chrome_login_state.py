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
import subprocess
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


def _silent_unlink(path):
    """删临时文件，失败也当成功。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _count_rows(db, tmp):
    """把 cookie 库复制到临时文件后统计行数。返回 (total, wechat)。

    ⚠⚠ **Chrome 运行时会把Cookies 库锁住**，这里必须多级降级：
    Chrome 的 cookie 库由 network service 持有，Windows 上可能不给
    共享读。实测 `shutil.copy2` 会直接抛
    `PermissionError: [WinError 32] 另一个程序正在使用此文件`。
    ⇒ 依次尝试三条路，任何一条成功即可，**都不行就报 unavailable
    而不是让整个脚本崩掉**（崩掉会掩盖真正的登录态判据）。

    降级顺序：
      1. `shutil.copy2`     —— 最快，Chrome 未运行时通常可用
      2. 手工 `open(rb).read()` —— 绕过 CopyFile2 的共享模式要求
      3. `sqlite3` backup()—— 直接从源库读，走 SQLite 自己的文件打开
    """
    # --- 1) shutil.copy2 ---
    try:
        shutil.copy2(db, tmp)
    except (OSError, PermissionError):
        # copy2 可能已经建出了半个文件，先清掉再换路子，
        # 否则后续 sqlite 会报 "unable to open database file"。
        _silent_unlink(tmp)
        # --- 2) 手工字节拷贝 ---
        try:
            with open(db, "rb") as fsrc:
                data = fsrc.read()
            with open(tmp, "wb") as fdst:
                fdst.write(data)
        except OSError:
            _silent_unlink(tmp)
            # --- 3) sqlite backup 直读 ---
            src = sqlite3.connect("file:%s?mode=ro"
                                  % db.replace("\\", "/"), uri=True)
            try:
                dst = sqlite3.connect(tmp)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

    conn = sqlite3.connect(tmp)
    try:
        total = conn.execute("select count(*) from cookies").fetchone()[0]
        wechat = conn.execute(
            "select count(*) from cookies where host_key like '%weixin%'"
        ).fetchone()[0]
    finally:
        conn.close()
    return total, wechat


def cookie_stats(profile_dir):
    """读 profile 的 cookie 库，返回统计。文件不存在返回 None。

    ⚠ 这个统计**仅供参考，不是登录态判据**。唯一判据是实拉能否拿到
    token（见 live_token_check）。文件大小尤其不可信：SQLite 文件大小
    由历史记录/已删除页/WAL 残留决定，与当前 cookie 数无正相关。
    """
    db = os.path.join(profile_dir, COOKIE_REL)
    if not os.path.exists(db):
        return None
    tmp = os.path.join(tempfile.gettempdir(), "ck_probe.db")
    try:
        total, wechat = _count_rows(db, tmp)
    except sqlite3.Error as e:
        return {"error": "sqlite: %s" % e}
    except OSError as e:
        # 拿不到就明说「不可读」，而不是抛栈让诊断中断。
        return {"error": "无法读取（Chrome 占用中）: %s" % e}
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {
        "path": db,
        # 仅供人参考，**不作判据**
        "bytes": os.path.getsize(db),
        "total": total,
        "wechat": wechat,
    }


def live_token_check(autostart=True):
    """实拉一次后台首页，看能不能解析出 token。

    这是唯一的充分判据：cookie 在磁盘上 ≠ 服务端认你这个会话。

    ⚠ `autostart=True` 时会**自己拉起 Chrome**（带 `--no-sandbox`），
    因为在本机不开这个标志Chrome 会在 2 秒内自杀（exit code 3），
    见 chrome_cdp_launch.py 的说明。
    """
    # 必须复用 wechat_publish 自己的登录函数，那里面包含了
    # 「原地等 → 点登录按钮 → 重导航」这套三级兜底，
    # 自己重写一套简化版会误报。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import wechat_publish as wp
    except Exception as e:  # pragma: no cover
        return {"ok": False, "why": "import 失败: %s" % e}

    proc = None
    if autostart:
        try:
            import chrome_cdp_launch as L
            L.clear_proxy()
            profile = os.path.expandvars(L.PROFILE)
            chrome, label = L.pick_chrome()
            if chrome is None:
                return {"ok": False, "why": "找不到 Chrome 可执行文件"}
            L.remove_lock(profile)
            proc = subprocess.Popen(L.build_args(chrome, profile),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if not L.wait_cdp(proc):
                code = proc.poll()
                return {"ok": False,
                        "why": "Chrome 启动失败（exit=%s）。"
                               "若为 exit 3，多半漏了 --no-sandbox" % code}
        except Exception as e:
            return {"ok": False, "why": "启动 Chrome 失败: %s: %s"
                    % (type(e).__name__, e)}

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
            #⚠ 拿不到统计**不代表登录态有问题**，只说明 Chrome 正占用该库。
            #    真正的判据是下面的实拉检测，别在这里下结论。
            print("    读取失败: %s" % s["error"])
            print("    （Chrome 运行时锁住该库属正常，不影响登录态判断）")
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
        why = live.get("why", "拿不到 token")
        print(" 实拉检测: [X] %s" % why)
        if "CDP" in why:
            print("\n 结论: Chrome 没起来 —— 这不是登录态问题。")
            print("       用 scripts\\chrome_cdp_check.bat 重跑（会自动带 --no-sandbox）。")
        else:
            print("\n 结论: 登录态不可用。请在Chrome 窗口里扫码登录 mp.weixin.qq.com，")
            print("       然后重跑本脚本。⚠ 注意区分：")
            print("       - 进程被收走 / CDP 连不上 → 不是掉登录，重跑即可")
            print("       - 实拉仍 NO_TOKEN（这里）→ 才要重新扫码")
    print("-" * 62)


if __name__ == "__main__":
    main()