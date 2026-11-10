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
import fifo_dedupe as FD  # noqa: E402
import wechat_publish as wp  # noqa: E402
import wechat_sync as ws  # noqa: E402

PUBLISHED_PATH = os.path.join(ROOT, "data", "wechat_published.json")
STANDALONE = wp.STANDALONE_PATH
PY = sys.executable


def norm_title(s):
    """标题归一：去空白 + 小写。与 wechat_sync.match_remote 的认亲口径一致。"""
    return re.sub(r"\s+", "", (s or "").lower())


def pick_oldest(drafts, published, published_cases=None, case_of=None):
    """纯函数：挑「最久且未发表过」的一条。

    drafts:    {appmsgid: {title, update_time, ...}}（fetch_draft_remote 的返回）
    published: [{title, appmsg_id, ...}]（fetch_published_remote 的返回）
    published_cases: 已发案例集合（`fifo_dedupe.published_case_ids("wechat")`）
    case_of:   callable(appmsgid) -> case_id；用 **appmsgid → 本地台账** 这个
               稳定键认亲，不要用标题（标题会改，见 PRD #90）

    返回 (chosen, rows, stats)：chosen=(aid, item) 或 (None, None)；
    rows 是按存入时间升序排好的**全部可发候选**，供 --list 用。

    ⚠ 缺 `update_time` 的排到最后（不知道时间就不能自称「最久」）；
    ⚠ 两道剔重缺一不可：
       ① 远端**已发表列表按标题**剔同名（发布模式发完列表可能滞后）；
       ② 本地**按 (case_id, wechat)** 剔已发案例 —— ① 只翻得到接口给的
          45 条，更早的旧稿、或标题被改过的旧稿（11-02 改过 xhs 那个）
          全靠 ②。11-07 Freedoom 就是 ①没拦住、当时又没有 ② 才重复。
    ⚠ 认不出 case_id 的孤儿草稿**保留**（`ensure_local_record` 补登的那些
       `wx-<appmsgid>` 没有案例归属，拦了它们等于把箱锁死）。
    """
    pub_titles = {t for t in (norm_title(p.get("title")) for p in published) if t}
    stats = {"draft": len(drafts or {}), "published": len(published or []),
             "dup": 0, "dup_case": 0, "no_title": 0, "no_time": 0}
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
    # 第二道：按 (case_id, wechat) 剔已发案例的旧标题稿
    if published_cases is not None and case_of:
        cache = {}

        def _cid(_aid):
            if _aid not in cache:
                try:
                    cache[_aid] = FD.norm(case_of(_aid))
                except Exception:                       # noqa: BLE001
                    cache[_aid] = ""
            return cache[_aid]

        before = len(rows)
        rows = [r for r in rows
                if not (_cid(r[1]) and _cid(r[1]) in published_cases)]
        stats["dup_case"] = before - len(rows)
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
    """只读拉远端草稿箱 + 已发表列表。两侧都必须读成功，否则拒绝对账。

    返回 `(drafts, pubs, cdp, tok)` —— 比原来多两个：11-10 起过形态闸时要用
    `wp.read_draft_body(cdp, tok, aid)` 读**平台草稿实际正文**（本地干净、
    平台脏时只有它能拦住，见 `fifo_content_gate` 的 text 参数）。

    ⚠ 调用方不要自己再 `CDP(port)`：本机 Chrome 是命令内拉起的，命令结束就
    被回收，重复连接会拿到空 target 列表。
    """
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
    return drafts, pubs, cdp, tok


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

    drafts, pubs, cdp, tok = read_remote(a.port)
    # 远端标题剔重之外，再按 (case_id, wechat) 剔一次：本地两本台账里的
    # case_id 是稳定键，标题改过 / 远端列表翻不全时只有它认得出来。
    pub_cases = FD.published_case_ids("wechat")
    (aid, item), rows, st = pick_oldest(
        drafts, pubs, published_cases=pub_cases,
        case_of=lambda _aid: local_cid_of(_aid)[0])
    print("远端对账（只读）：草稿 %d 条 ｜ 已发表 %d 条 ｜ 同名已发剔除 %d 条 ｜ "
          "已发案例剔除 %d 条 ｜ 无标题 %d ｜ 无时间戳 %d ⇒ 可发候选 %d 条"
          % (st["draft"], st["published"], st["dup"], st["dup_case"],
             st["no_title"], st["no_time"], st["candidates"]))
    if a.list:
        for ts, _aid, _it in rows[:10]:
            print("   %s  %s  《%s》" % (fmt_ts(ts), _aid, _it.get("title") or ""))
    if not aid:
        print("✗ 草稿箱没有可发候选（空箱或全已发表）⇒ 箱空才该走 --new 按档期新写")
        return 2
    print("→ 最久未发：appmsgid=%s ｜ %s ｜《%s》"
          % (aid, fmt_ts(item.get("update_time")), item.get("title") or ""))
    if not a.go:
        # 干跑也把闸结果打出来，但**只按本地产物**（不读平台正文，省掉一次
        # 开编辑页的 20 秒）。所以这里必须说清它不代表平台草稿。
        import fifo_content_gate as FCG
        print("（干跑）本地产物预检（不代表平台草稿；真发时会读平台正文再验）：")
        FCG.report(FCG.guard("wechat", local_cid_of(aid)[0],
                             item.get("title") or ""),
                   "wechat", item.get("title") or "")
        print("（干跑）真发命令：python -X utf8 scripts/wechat_fifo.py --go"
              "%s" % (" --notify" if a.notify else ""))
        print("⚠ 终点需管理员微信扫码；默认发布模式不推送、不占每日群发额度。")
        return 0

    # 🚨 PRD #124：调 `send` 之前过内容形态闸（判据复用 publish_guard）。
    #    ⛔ 必须放在 ensure_local_record **之前**：拦下时不许留下「补登」写盘。
    #    case 用 appmsgid → 本地两本台账这个稳定键反查；认不出的孤儿保留，
    #    guard 会 checked=False 明确 warn，不假装验过（同 #90 的孤儿口径）。
    #    🚨 11-10 起闸验的是**平台草稿实际正文**（开编辑页读 DOM），不是本地
    #    产物：本地干净、平台脏正是 B站 `promptmonitor-io` 那次出事的原因。
    #    读失败就退回本地产物并**明说降级**，不许假装验过平台正文。
    import fifo_content_gate as FCG
    _hint = local_cid_of(aid)[0]
    _title = item.get("title") or ""
    _body = ""
    try:
        _body = wp.read_draft_body(cdp, tok, aid, expect_title=_title)
    except Exception as e:                               # noqa: BLE001
        print("  ⚠ [FIFO 形态闸] 读平台草稿正文失败（%s），退回本地产物"
              % str(e)[:80])
    if not _body:
        print("  ⚠ [FIFO 形态闸] 平台草稿正文为空/未读到（appmsgid=%s），"
              "退回本地产物" % aid)
    _verdict = FCG.guard("wechat", _hint, _title,
                         text=_body or None, text_src="platform:appmsg_edit")
    FCG.report(_verdict, "wechat", _title)
    if not _verdict["ok"]:
        print("✗ " + FCG.block_reason(_verdict, "wechat", _title))
        return 3

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
