#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云登录态固化 / 只读自检（9228 + `chrome-yun-profile`，独立工具）。

⚠ 不改发布主链路：`yun_draft_cdp.py` / `yun_finalize.py` / `publish_one.py`
   一行都不动。本脚本只做「一次性固化 + 发布前可手动跑的自检」。

背景（2026-11-08 只读实测，日志 `.workbuddy/memory/2026-11-08.md`）：
  - 重开 9228 后 `developer.aliyun.com` 仍是登录态（昵称 / 第 2844 天，
    `/article/new` 不跳 passport）⇒ **不是每次都掉登录**。
  - 但四个登录票据 `login_aliyunid_ticket` / `login_aliyunid` / `hssid` /
    `cr_token` 全是 `session=True, expires=-1`；持久落盘的只有 `cna` / `isg` /
    `tfstk` / `login_current_pk`（设备指纹，不是凭证）。
  - `Preferences` = `exit_type=Crashed` / `exited_cleanly=None` / `session={}`
    ⇒ 现在还能用，只因本机 Chrome 每次被整组回收、**非正常退出没清会话 cookie**。
    哪天正常关一次窗口，就得重新扫码。
  - 反复要人工的其实是滑块 `aliyunCaptcha`（行为风控），**与 cookie 无关**，
    本脚本解决不了，也不尝试解决。

做法：在已登录实例里用 CDP `Network.setCookie` 给 `.aliyun.com` 的四个会话票据
显式带 `expires` 重写一遍（值不变，只是从 session cookie 变持久 cookie），
然后**正常退出 Chrome 再重启**，用页面 + CDP + 磁盘三处独立回读验证仍登录。

用法：
    python -X utf8 scripts/yun_login_keep.py --check    # 只读自检，退出码 0=在线
    python -X utf8 scripts/yun_login_keep.py --keep     # 固化 + 正常重启验证
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_draft_cdp as Y  # noqa: E402

PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-yun-profile")
COOKIE_DB = os.path.join(PROFILE, "Default", "Network", "Cookies")
HOME = "https://developer.aliyun.com/"
DOMAIN = ".aliyun.com"
TICKETS = ("login_aliyunid_ticket", "login_aliyunid", "hssid", "cr_token")
TTL_DAYS = 30
LOGIN_WORDS = ("请登录", "立即登录", "扫码登录", "登录后", "免费注册")


def expiry(now=None):
    """持久 cookie 的到期时间（Unix 秒，运行时算，不硬编码）。"""
    return (time.time() if now is None else now) + TTL_DAYS * 86400


def all_cookies(cdp):
    cdp.send("Network.enable")
    r = cdp.send("Network.getAllCookies")
    return (r.get("result") or {}).get("cookies") or []


def select_tickets(cookies, domain=DOMAIN, names=TICKETS):
    """只挑指定域、指定名字的票据；同域同名的重复项取最后一条。"""
    out = {}
    for c in cookies:
        if (c.get("domain") or "").lstrip(".") == domain.lstrip(".") \
                and c.get("name") in names:
            out[c.get("name")] = c
    return out


def set_params(cookie, expires):
    """把一条 session cookie 改写成持久 cookie 的 CDP 参数。

    值 / 域 / path / secure / httpOnly / sameSite 全部沿用原值，只加 expires；
    ⛔ 不传 `session` 字段（CDP 里没有这个参数，有 expires 即持久）。
    """
    p = {"name": cookie["name"], "value": cookie.get("value") or "",
         "domain": cookie.get("domain") or DOMAIN,
         "path": cookie.get("path") or "/",
         "expires": expires,
         "secure": bool(cookie.get("secure")),
         "httpOnly": bool(cookie.get("httpOnly"))}
    ss = cookie.get("sameSite")
    if ss in ("Strict", "Lax", "None"):
        p["sameSite"] = ss
    return p


def make_persistent(cdp, expires):
    """把四个会话票据改成持久 cookie。返回 (成功数, 明细)。"""
    found = select_tickets(all_cookies(cdp))
    done = []
    for name in TICKETS:
        c = found.get(name)
        if not c:
            done.append((name, "MISSING"))
            continue
        r = cdp.send("Network.setCookie", set_params(c, expires))
        ok = bool((r.get("result") or {}).get("success"))
        done.append((name, "ok" if ok else "FAIL %s" % json.dumps(
            r.get("error") or {}, ensure_ascii=False)[:120]))
    return sum(1 for _, s in done if s == "ok"), done


def ticket_state(cookies):
    """返回 {name: (session, expires)}，供 --check / --keep 回读比对。"""
    found = select_tickets(cookies)
    return {n: (bool(found[n].get("session")), found[n].get("expires"))
            for n in TICKETS if n in found}


def disk_state():
    """只读复制 Cookies DB，返回四个票据的 (is_persistent, expires_utc)。

    ⚠ Windows 上 Chrome 运行时独占 Cookies 文件，copy2 会 WinError 32。
      这不是「没落盘」，只是读不了；调用方必须区分 busy 与真空。
    """
    if not os.path.exists(COOKIE_DB):
        return {"__error__": "NO_DB"}
    dst = os.path.join(ROOT, "tmp", "_yun_login_keep_verify.db")
    try:
        shutil.copy2(COOKIE_DB, dst)
    except OSError as e:
        return {"__error__": "BUSY: %s" % type(e).__name__}
    con = sqlite3.connect("file:" + dst + "?mode=ro", uri=True)
    try:
        out = {}
        for n in TICKETS:
            row = list(con.execute(
                "select is_persistent, expires_utc from cookies "
                "where name=? and host_key like '%aliyun.com'", (n,)))
            if row:
                out[n] = tuple(row[0])
        return out
    finally:
        con.close()


def probe_page(cdp, wait=7):
    """页面级登录态探针（只读）。返回 dict，绝不以「昵称字符串」当唯一判据。"""
    cdp.open(HOME)
    time.sleep(wait)
    href = cdp.eval("location.href") or ""
    body = cdp.eval("(document.body ? (document.body.innerText||'') : '')") or ""
    marks = [w for w in LOGIN_WORDS if w in body]
    logged = (not marks and "passport.aliyun.com" not in href
              and "login" not in href.lower()
              and ("发布" in body or "消息" in body))
    return {"url": href, "marks": marks, "logged": bool(logged),
            "excerpt": body[:100].replace("\n", " ")}


def port_offline(timeout=20):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            op.open("http://127.0.0.1:%d/json/version" % Y.PORT,
                    timeout=1).read()
        except Exception:
            return True
        time.sleep(0.5)
    return False


def shutdown(cdp):
    """正常退出 Chrome（会 flush cookie 到磁盘），不重启。

    ⚠ 会关掉 9228 上的所有窗口；调用方须先确认没有别人的页面。
    """
    try:
        cdp.send_browser("Browser.close")
    except Exception as e:
        print("  [warn] Browser.close:", type(e).__name__, e)
    try:
        cdp.close()
    except Exception:
        pass
    if not port_offline():
        raise SystemExit("✗ Browser.close 后 9228 仍在线，停止（不硬杀）")
    time.sleep(1)


def relaunch():
    """关掉之后重新起 9228（会自动清 stale LOCK）。"""
    return Y._connect()


def print_state(tag, cdp):
    p = probe_page(cdp)
    st = ticket_state(all_cookies(cdp))
    print("\n=== %s ===" % tag)
    print("  页面登录态 =", "✅ 在线" if p["logged"] else "✗ 需登录",
          "| URL =", p["url"], "| 文案命中 =", p["marks"])
    print("  正文片段   =", repr(p["excerpt"]))
    for n in TICKETS:
        v = st.get(n)
        print("  %-24s %s" % (n, ("session=True  expires=%s" % v[1])
                              if v and v[0] else
                              ("持久 expires=%s" % v[1] if v else "MISSING")))
    return p, st


def cmd_check(args):
    proc, cdp = Y._connect()
    print("9228 =", "本次新起" if proc is not None else "复用已有实例")
    try:
        p, st = print_state("只读自检 --check", cdp)
        persistent = sum(1 for v in st.values() if not v[0])
        print("\n  持久票据 = %d/%d" % (persistent, len(TICKETS)))
        disk = disk_state()
        print("  磁盘 is_persistent =", disk if "__error__" not in disk
              else "跳过（Chrome 运行中独占 Cookies 文件：%s）" % disk["__error__"])
        if not p["logged"]:
            print("\n✗ 需要人工登录一次（有头窗口扫码），之后跑 --keep")
            return 1
        if persistent < len(TICKETS):
            print("\n⚠ 票据仍是会话级；建议跑 --keep 固化")
        return 0
    finally:
        try:
            cdp.close()
        except Exception:
            pass


def cmd_keep(args):
    proc, cdp = Y._connect()
    print("9228 =", "本次新起" if proc is not None else "复用已有实例")
    print("⚠ --keep 会正常关闭并重启 9228（只关本 profile，不动其它浏览器）")
    try:
        p, st = print_state("固化前", cdp)
        if not p["logged"]:
            raise SystemExit("✗ 当前不是登录态，停止（不固化未登录的票据）")

        exp = expiry()
        n, done = make_persistent(cdp, exp)
        print("\n=== 写入持久 cookie（expires=%.0f，%d 天后） ===" % (exp, TTL_DAYS))
        for name, state in done:
            print("  %-24s %s" % (name, state))
        if n != len(TICKETS):
            raise SystemExit("✗ 只有 %d/%d 写入成功，停止" % (n, len(TICKETS)))

        after = ticket_state(all_cookies(cdp))
        bad = [k for k, v in after.items() if v[0] or not v[1] or v[1] <= time.time()]
        if bad:
            raise SystemExit("✗ CDP 回读仍是会话级: %s" % bad)
        print("  CDP 回读：4/4 已是持久 cookie")

        # 正常退出 → 磁盘落盘（Chrome 关着才能读库）→ 重启，这是硬证据
        shutdown(cdp)
        disk = disk_state()
        print("\n  正常退出后磁盘 is_persistent =", disk)
        cdp2 = relaunch()[1]
        p2, st2 = print_state("正常退出并重启后", cdp2)
        disk_ok = ("__error__" not in disk and len(disk) == len(TICKETS)
                   and all(v[0] == 1 for v in disk.values()))
        runtime_ok = all(not v[0] for v in st2.values()) and len(st2) == len(TICKETS)
        if not (p2["logged"] and runtime_ok and disk_ok):
            print("\n✗ 固化未通过三处回读（页面 / CDP / 磁盘）")
            try:
                cdp2.close()
            except Exception:
                pass
            return 1
        print("\n✅ 固化成功：正常退出 Chrome 后重开，页面仍登录、4/4 票据持久落盘")
        try:
            cdp2.close()
        except Exception:
            pass
        return 0
    except SystemExit:
        raise
    except Exception:
        try:
            cdp.close()
        except Exception:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description="阿里云登录态固化/自检（9228 独立工具）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只读自检（默认手动跑）")
    g.add_argument("--keep", action="store_true", help="固化会话票据 + 正常重启验证")
    args = ap.parse_args(argv)
    return cmd_keep(args) if args.keep else cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
