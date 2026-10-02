#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站补封面：串行逐条跑，每条一个独立子进程。

## 为什么不用 `cover --all --force` 一次跑完

2026-10-03 凌晨实测：`cover --all` 跑到 30 条左右 Chrome 被环境回收
（`urllib.error.URLError: WinError 10061`），整批直接崩掉，
已完成的条目也一起丢了。而 `cover` 子命令**没有 `--only`**，
只有 `--case`，所以断点续跑只能靠外部逐条驱动。

每条一个子进程 ⇒ 单条崩了只影响那一条，下一条重新起 Chrome 即可。
这也符合本机硬限制：Chrome 活不过一次父进程调用，
所以「起 Chrome + 驱动」必须在**同一个** Python 进程里（子进程正好满足）。

用法：
    python -u -X utf8 scripts/bili_cover_repair.py --list          # 看待修
    python -u -X utf8 scripts/bili_cover_repair.py --run --batch 10
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "out", "bili_cover_repair.json")
PY = sys.executable
COVER = os.path.join(ROOT, "scripts", "bilibili_publish.py")


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, STATE)


def all_cases():
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        d = json.load(f)
    cases = d if isinstance(d, list) else d.get("cases") or []
    return [c["id"] for c in cases]


def _summarize(out):
    """从 cover_one 的输出里挑出判据行。

    ⚠ 两条成功信号都要认：
    - 「封面已设」= 接口回读确认
    - 「页面有封面图 …… 但接口仍无封面」= 上传成功、只是存草稿没回读确认。
      把它判成 FAIL 会让这批无限重跑（实测连跑几轮都是同一条）。
    """
    marks = [ln.strip() for ln in out.splitlines()
             if ("✓" in ln or "✗" in ln or "页面有封面图" in ln)]
    if marks:
        return marks[-1]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"


def run_one(cid, tries=2):
    """跑一条，返回 (ok, tail)。失败会重试一次。

    ⚠⚠ 三条铁律（都踩过）：
    1. **清掉代理环境变量** —— 父进程的 `http_proxy=127.0.0.1:2662`
       会被子进程继承，urllib 走代理连 9223 的 CDP WebSocket ⇒ `10053`/`10061`。
    2. **起 Chrome + 驱动必须在同一个进程里** —— 本机硬限制「Chrome 活不过
       一次父进程调用」。所以本脚本自己在这个进程里
       `_autostart_chrome_if_needed()` 然后立刻 `cover_one()`，不跨进程。
    3. **一条之后 Chrome 可能被回收** —— 同一进程里连跑第二条时
       WebSocket 会被掐断（`10053`）。此时 `/json/version` 往往**还通**
       （残留进程），所以 autostart 直接 return True 不会重启 Chrome
       ⇒ 必须先主动关掉所有 B站 tab、重建 CDP 对象，才有机会连上。
    """
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)

    import chrome_cdp_launch as cl
    import wechat_publish as wp
    import bilibili_publish as bp

    last = ""
    for attempt in range(tries):
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            _reset_cdp()
            wp._autostart_chrome_if_needed(bp.PUB_PORT,
                                           profile=cl.PLAT_PROFILE)
            ok = bp.cover_one(cid, force=True)
            out = buf.getvalue()
            sys.stdout = real
            if ok:
                return True, _summarize(out)
            last = _summarize(out)
            # 「页面有封面图但接口无封面」= 上传成功，存草稿没回读确认
            if "页面有封面图" in out:
                return True, last
        except BaseException as exc:      # noqa: BLE001
            # ⚠⚠ 必须是 BaseException —— `wechat_publish.CDP.__init__` 连不上
            # 端口时抛的是 **SystemExit**（不是普通 Exception），而
            # SystemExit 继承自 BaseException ⇒ `except Exception` 捕不住
            # ⇒ 整个进程被静默终止，表现为「run_one 什么都没返回就退出」。
            # 本机硬限制是「Chrome 活不过一次父进程调用」，所以这条很常见。
            sys.stdout = real
            last = "%s: %s" % (type(exc).__name__,
                               str(exc)[:90].replace("\n", " "))
        time.sleep(8)
    return False, last


def _reset_cdp():
    """关掉遗留的 B站 tab，让下一次 CDP 连接拿到干净的 target。

    `10053` 的成因：Chrome 进程还在（`/json/version` 通 ⇒ autostart 不重启），
    但上一条留下的 read-editor tab /半开的 WS 让新连接直接被 RST。
    只留草稿箱列表页就够了。
    """
    import bilibili_publish as bp
    try:
        cdp = bp.wp.CDP(bp.PUB_PORT)
    except BaseException:              # noqa: BLE001
        # ⚠ Chrome 还没起（CDP.__init__ 抛 SystemExit）—— 这时没有 tab
        # 可关，直接返回，让后面的 autostart 去拉 Chrome。
        return
    try:
        for t in cdp.list_targets() or []:
            url = (t.get("url") or "")
            if "member.bilibili.com" in url and "opus/management/drafts" not in url:
                try:
                    cdp.close_target(t["id"])
                except Exception:                          # noqa: BLE001
                    pass
    except Exception:                                      # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--only", default="", help="逗号分隔的 case id")
    args = ap.parse_args()

    st = load()
    if args.list:
        todo = [c for c in all_cases() if not (st.get(c) or {}).get("ok")]
        print("共%d 条｜已修 %d ｜待修 %d"
              % (len(all_cases()),
                 sum(1 for v in st.values() if v.get("ok")), len(todo)))
        print(",".join(todo))
        return 0

    if not args.run:
        ap.print_help()
        return 0

    if args.only:
        todo = [c for c in args.only.split(",") if c]
    else:
        todo = [c for c in all_cases() if not (st.get(c) or {}).get("ok")]
    if args.batch:
        todo = todo[:args.batch]
    print("待修%d 条：%s" % (len(todo), "、".join(todo)), flush=True)

    ok = fail = 0
    for i, cid in enumerate(todo, 1):
        t0 = time.time()
        try:
            good, msg = run_one(cid)
        except subprocess.TimeoutExpired:
            good, msg = False, "TIMEOUT 300s"
        except Exception as exc:                          # noqa: BLE001
            good, msg = False, str(exc)[:100]
        dt = int(time.time() - t0)
        st[cid] = {"ok": good, "msg": msg[:160], "at": time.strftime("%H:%M:%S")}
        save(st)
        print("[%d/%d] %-22s %s %ds  %s"
              % (i, len(todo), cid, "OK" if good else "FAIL", dt, msg[:90]),
              flush=True)
        ok += good
        fail += (not good)
        time.sleep(2)
    print("=" * 60, flush=True)
    print("成功 %d / 失败 %d" % (ok, fail), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
