#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wechat_backfill_aid.py —— 按远端草稿箱回填 appmsgid（台账修复工具）
=====================================================================

背景（2026-11-04 实修）
----------------------
`data/wechat_published.json` 里 37 条 `status=draft` 的 `appmsgid` 全是空串，
而 `build_send_todo()` 会 `continue` 掉没有 appmsgid 的记录 ⇒ `send --all`
返回 0 条待办，表现成「草稿箱里明明有一堆稿，脚本却说没得发」。

远端才是真相源。本脚本拉 `appmsgid?action=list_ex&type=77`（**type=77 才是
图文草稿**，type=10 返回空列表），按标题精确认亲（去空白后比较），把
appmsgid 写回台账。

⚠ 两处必须都写，缺一不可（2026-11-04 踩过）
------------------------------------------
`wechat_publish.load_published()` **优先读 SQLite** `platform_posts`，只有 db
里没有 wechat 记录时才回退到 JSON。所以只改 `data/wechat_published.json`
不会生效 —— 必须走 `ledger.mark()` 让写出口同步 db（顺带写文件台账）。

用法
----
    python scripts/wechat_backfill_aid.py           # 干跑，只打印
    python scripts/wechat_backfill_aid.py --apply   # 写台账（JSON + SQLite）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 127.0.0.1 走系统代理会被掐（WinError 10053/10054），先清干净
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import wechat_publish as W      # noqa: E402
import ledger as ld            # noqa: E402


def _norm(s):
    return "".join(str(s or "").split())


def _load(p):
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f) or {}


def main():
    apply_ = "--apply" in sys.argv
    # 端口没开就由**本进程**代拉 Chrome：Chrome 是子进程，随本进程退出一起收掉，
    # 这样不会留下「锁还在、进程已死」的半死状态（2026-11-04 踩过）。
    W._autostart_chrome_if_needed()
    cdp = W.CDP(9222)
    _tid, tok = W._connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认 9222 窗口里 mp.weixin.qq.com 已登录")
    items = W._draft_list(cdp, tok, count=50)
    print("远端草稿：%d 条" % len(items))

    pub = _load(W.PUBLISHED_PATH)
    sa = _load(W.STANDALONE_PATH)
    index = {}
    for name, tab in (("cases", pub), ("standalone", sa)):
        for k, rec in tab.items():
            t = (rec or {}).get("title")
            if t:
                index.setdefault(_norm(t), (name, k, rec))

    hit, same, miss = [], [], []
    for it in items:
        aid = str(it.get("appmsgid") or it.get("app_id") or "")
        t = str(it.get("title") or "").strip()
        got = index.get(_norm(t))
        if not got:
            miss.append((aid, t))
            continue
        _name, cid, rec = got
        old = str(rec.get("appmsgid") or "")
        if old == aid:
            same.append(cid)
        elif old:
            # 台账已有**另一个** appmsgid：可能是重存过草稿，别静默覆盖
            miss.append((aid, "%s ← 与台账 appmsgid=%s 冲突" % (t[:26], old)))
        else:
            rec["appmsgid"] = aid
            hit.append((aid, cid, str(rec.get("title") or "")[:30]))

    print("可回填 %d / 已一致 %d / 未匹配 %d" % (len(hit), len(same), len(miss)))
    for aid, cid, t in sorted(hit):
        print("  + %-12s %-26s %s" % (aid, cid, t))
    for aid, t in miss:
        print("  ? %-12s %s" % (aid, t))

    if not hit:
        return
    if not apply_:
        print("[dry] 加 --apply 才写台账")
        return

    # ⚠ 只回填 status=draft 的记录：已发表的那批 remote_id 语义不同
    #   （appmsgid=草稿 id、published_appmsgid=发表后 id），别混改。
    import atomicio
    atomicio.write_json(W.PUBLISHED_PATH, pub)
    atomicio.write_json(W.STANDALONE_PATH, sa)
    n = 0
    for aid, cid, _t in hit:
        rec = pub.get(cid) or {}
        if rec.get("status") != "draft":
            continue
        ld.mark("wechat", cid, ld.DRAFT, remote_id=aid,
                title=rec.get("title") or "", evidence="remote_draft_list",
                note="｜按远端草稿箱 list_ex 回填 appmsgid")
        n += 1
    print("已写台账：JSON %d 条、SQLite %d 条" % (len(hit), n))


if __name__ == "__main__":
    main()
