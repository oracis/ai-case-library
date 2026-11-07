#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号草稿箱 FIFO：按**远端存入时间**挑最久且未发表的一条，交给 send 发表。

为什么要这个脚本（2026-11-07）
------------------------------
用户 11-03 定「草稿箱是 FIFO 队列，先发存入最久的，箱空才新写」，但当时只把
B站/小红书接成 `send-draft`；`publish_one` 的公众号分支仍是 `publish --file`
（无脑新写）⇒ 11-07 又往箱里塞了第 12 条，而且和 10-31 已发表的
《游戏厅：Freedoom》**同标题重复**（`ep-*` 与 `ext-ep-*` 两套 id 让 dedupe 没拦住）。

公众号还不能照抄 B站的 `send-draft`：
* `wechat_publish.py send --all` 按**篇号**排、只认主台账 `wechat_published.json`，
  游戏稿那批记在 `wechat_standalone.json`，它一条也看不见；
* 远端草稿箱里混着**已发表过的同名旧稿**（发布模式发完草稿箱列表可能滞后），
  盲按「最久」会重发。

所以这里先做一次只读对账：拉远端草稿箱 + 已发表列表 → 按标题剔除已发表 →
按 `update_time` 升序挑最久的一条 → 本地台账缺记录就补登（否则 send 认不到亲）
→ 再交给原有的 `send --appmsgid` 发表。**发布逻辑一行没重写**。

⚠ 终点仍需管理员微信扫码：默认「发布模式」不推粉丝、不占群发额度；
`--notify` 才是群发。两种都要扫码，`--go` 会起有头 Chrome。

用法
----
    python -X utf8 scripts/wechat_fifo.py             # 只读：对账 + 打印候选（默认）
    python -X utf8 scripts/wechat_fifo.py --list      # 再打印剔重后的前 10 条候选
    python -X utf8 scripts/wechat_fifo.py --go        # 真发一篇（需扫码）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import atomicio  # noqa: E402
import wechat_publish as wp  # noqa: E402
import wechat_sync as ws  # noqa: E402

PUBLISHED_PATH = os.path.join(ROOT, "data", "wechat_published.json")
STANDALONE = wp.STANDALONE_PATH
PY = sys.executable


def norm_title(s):
    """标题归一：去空白 + 小写。与 wechat_sync.match_remote 的认亲口径一致。"""
    return re.sub(r"\s+", "", (s or "").lower())


def pick_oldest(drafts, published):
    """纯函数：从远端草稿里挑「最久且未在已发表列表出现同名」的一条。

    drafts:    {appmsgid: {title, update_time, ...}}（fetch_draft_remote 的返回）
    published: [{title, appmsg_id, ...}]（fetch_published_remote 的返回）

    返回 (chosen, rows, stats)：chosen=(aid, item) 或 (None, None)；
    rows 是按存入时间升序排好的**全部可发候选**，供 --list 用。

    ⚠ 缺 `update_time` 的排到最后（不知道时间就不能自称「最久」）；
    ⚠ 已发表列表只覆盖接口能翻到的页数（实测 45 条），更早的同名旧稿
      剔不掉 —— 这是已知边界，send_one 里还有一道「编辑器说已发表」兜底。
    """
    pub_titles = {t for t in (norm_title(p.get("title")) for p in published) if t}
    stats = {"draft": len(drafts or {}), "published": len(published or []),
             "dup": 0, "no_title": 0, "no_time": 0}
    rows = []
    for aid, item in (drafts or {}).items():
        item = item or {}
        t = norm_title(item.get("title"))
        if not t:
            stats["no_title"] += 1
            continue
        if t in pub_titles:
            stats["dup"] += 1
            continue
        try:
            ts = int(item.get("update_time") or item.get("create_time") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts <= 0:
            stats["no_time"] += 1
        rows.append((ts, str(aid), item))
    rows.sort(key=lambda r: (r[0] <= 0, r[0], r[1]))
    stats["candidates"] = len(rows)
    return (rows[0][1], rows[0][2]) if rows else (None, None), rows, stats


def local_cid_of(aid):
    """按 appmsgid 反查两本本地台账，返回 (cid, 哪本账)；找不到返回 (None, None)。"""
    for path, label in ((PUBLISHED_PATH, "cases"), (STANDALONE, "standalone")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            continue
        for cid, rec in data.items():
            if str((rec or {}).get("appmsgid")) == str(aid):
                return cid, label
    return None, None


def ensure_local_record(aid, item):
    """远端草稿在本地两本账里都没有 ⇒ 补登一条最小 draft 到独立稿台账。

    不补的话 `send --appmsgid` 会直接报「台账里找不到 appmsgid 的记录」。
    补的是**远端真实存在**的草稿，不是编造记录；send 发完由 `_mark_done`
    在同一条上改 status=published。
    """
    cid, _ = local_cid_of(aid)
    if cid:
        return cid, False
    cid = "wx-" + str(aid)
    try:
        with open(STANDALONE, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    data[cid] = {
        "title": item.get("title") or "",
        "desc": item.get("digest") or "",
        "tag": "",
        "published_at": time.strftime("%Y-%m-%d %H:%M"),
        "status": "draft",
        "appmsgid": str(aid),
        "file": "",
        "note": "wechat_fifo 对账补登（远端草稿箱实存）",
    }
    atomicio.write_json(STANDALONE, data, indent=2)  # 原文件 LF + indent2 + 无尾换行
    return cid, True


def fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return "(无时间戳)"


def read_remote(port):
    """只读拉远端草稿箱 + 已发表列表。两侧都必须读成功，否则拒绝对账。"""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    # ⚠ 直接 import 模块不会走 `wechat_publish.main()` 的自动拉起分支；
    #   命令一结束本机 Chrome 就会被回收，端口常常是空的。
    wp._autostart_chrome_if_needed(port=port)
    cdp = wp.CDP(port)
    _tid, tok = wp._connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
    drafts, dok = ws.fetch_draft_remote(cdp, tok)
    pubs, pok = ws.fetch_published_remote(cdp, tok)
    if not (dok and pok):
        raise SystemExit("✗ 草稿箱/已发表列表没读全 ⇒ 拒绝按 FIFO 选稿（宁可不发，不盲发）")
    return drafts, pubs


def main(argv=None):
    ap = argparse.ArgumentParser(description="公众号草稿箱 FIFO 对账 + 发最久一条")
    ap.add_argument("--port", type=int, default=wp.CDP_PORT)
    ap.add_argument("--go", action="store_true", help="真发（需管理员微信扫码）")
    ap.add_argument("--list", action="store_true", help="打印剔重后的前 10 条候选")
    ap.add_argument("--notify", action="store_true",
                    help="群发模式（推粉丝、占每日额度；默认发布模式不推送）")
    ap.add_argument("--timeout", type=int, default=300, help="等待扫码秒数")
    a = ap.parse_args(argv)

    # 真发必须有头窗口才看得见「微信验证」二维码；只读对账沿用无头即可。
    if a.go:
        os.environ["CHROME_HEADED"] = "1"

    drafts, pubs = read_remote(a.port)
    (aid, item), rows, st = pick_oldest(drafts, pubs)
    print("远端对账（只读）：草稿 %d 条 ｜ 已发表 %d 条 ｜ 同名已发剔除 %d 条 ｜ "
          "无标题 %d ｜ 无时间戳 %d ⇒ 可发候选 %d 条"
          % (st["draft"], st["published"], st["dup"], st["no_title"],
             st["no_time"], st["candidates"]))
    if a.list:
        for ts, _aid, _it in rows[:10]:
            print("   %s  %s  《%s》" % (fmt_ts(ts), _aid, _it.get("title") or ""))
    if not aid:
        print("✗ 草稿箱没有可发候选（空箱或全已发表）⇒ 箱空才该走 --new 按档期新写")
        return 2
    print("→ 最久未发：appmsgid=%s ｜ %s ｜《%s》"
          % (aid, fmt_ts(item.get("update_time")), item.get("title") or ""))
    if not a.go:
        print("（干跑）真发命令：python -X utf8 scripts/wechat_fifo.py --go"
              "%s" % (" --notify" if a.notify else ""))
        print("⚠ 终点需管理员微信扫码；默认发布模式不推送、不占每日群发额度。")
        return 0

    cid, created = ensure_local_record(aid, item)
    print("本地台账：%s ← appmsgid=%s%s"
          % (cid, aid, "（对账补登）" if created else "（已有记录）"))
    env = dict(os.environ, CHROME_HEADED="1")
    cmd = [PY, "-X", "utf8", os.path.join(HERE, "wechat_publish.py"),
           "send", "--appmsgid", str(aid), "--timeout", str(a.timeout)]
    if a.notify:
        cmd.append("--notify")
    print("执行：%s" % " ".join(cmd))
    print("⚠ 页面弹出「微信验证」后请用管理员/运营者微信扫码；不要重跑命令。")
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
