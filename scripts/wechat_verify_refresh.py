# -*- coding: utf-8 -*-
"""公众号 refresh 的**独立回读对账**（2026-10-01 新增）。

为什么单独一个脚本
------------------
`wechat_publish.py refresh` 自己的「保存: OK」判据只是**保存后URL 出现了
appmsgid=**，那是编辑器跳转信号，**不保证正文落库**。
历史教训：整轮 37 条全报「成功」，用户看到的还是旧排版。

所以刷完必须**另起一个进程、按篇号回读远端**核对。
放同一个进程里不行 —— 那个进程的 CDP 连接与页面状态都是热的，
无法代表「服务端现在到底是什么」。

用法
----
    # 1) 刷完先看台账里有哪些带 appmsgid 的条目
    python -X utf8 scripts/wechat_verify_refresh.py --list

    # 2) 回读远端核对（默认只报异常）
    python -X utf8 scripts/wechat_verify_refresh.py

    # 3) 全量明细（含已确认 OK 的）
    python -X utf8 scripts/wechat_verify_refresh.py --all

    # 4) 只看某几条
    python -X utf8 scripts/wechat_verify_refresh.py --case voklit,mort

⚠ **不要用标题当键**。按标题反查时 `0/N` 是「没匹配上远端」，
不是「远端是空的」—— 曾据此误报「32 条都不需要改」，事后证明32/32 全是旧版。

可选判据
--------
    --fresh-seconds 1800   update_time 距今多少秒内算「刚刷过」
    --since <unix时间戳>    批次开始时间。**长批次必用这个** ——
                          跑 40 分钟刷 37 条时，最后一条距批次开始
                          已 40 分钟，用「距今N秒」会误判成没刷。

`MISSING` 的两种成因（2026-10-01 实测，别混为一谈）
------------------------------------------------
    1. 远端草稿列表里真的没有这个篇号（`count` 拉到 100 也是没有）；
    2. 篇号在列表里，但**编辑页打不开** —— 表现为 `系统错误(320003)`。
       ⚠ 320003 有两种含义（限流 / 草稿不可访问），**交替测试能区分**：
       MISSING 的稳定失败、正常条目稳定成功 ⇒ 是这条草稿自己的问题，
       等五分钟重试没用，得在后台草稿箱里人工看。
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_publish as W                                        # noqa: E402


def probe_editor(cdp, tok, appmsgid, wait=5):
    """直接打开编辑页看是不是打得开。返回 (能打开?, 页面文本前60 字)。

    用来区分 320003 的两种成因：**这是唯一可靠的判别方式**，
    因为错误页文案一模一样。
    """
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit"
           "&action=edit&reprint_confirm=0&type=77&appmsgid=%s&token=%s"
           "&lang=zh_CN" % (appmsgid, tok))
    tid = cdp.new_target(url)["id"]
    try:
        cdp.connect_target(tid)
        for _ in range(wait):
            time.sleep(1)
            if W._editor_ready(cdp):
                return True, "编辑器就绪"
        txt = cdp.eval("document.body.innerText.slice(0,80)") or ""
        return False, " ".join(str(txt).split())[:60]
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="公众号 refresh 回读对账")
    ap.add_argument("--port", type=int, default=W.CDP_PORT)
    ap.add_argument("--case", default="", help="逗号分隔的 case id")
    ap.add_argument("--all", action="store_true", help="连已确认 OK 的也打印")
    ap.add_argument("--fresh-seconds", type=int, default=1800,
                    help="update_time 距今多少秒内算刚刷过（默认 1800）")
    ap.add_argument("--since", type=float, default=0,
                    help="批次开始 unix 时间戳；给了就优先用它（长批次必用）")
    ap.add_argument("--probe-missing", action="store_true",
                    help="对 MISSING 的条目逐个试开编辑页，区分限流与草稿损坏")
    ap.add_argument("--no-autostart", action="store_true",
                    help="do not launch Chrome, only probe an existing one")
    ap.add_argument("--list", action="store_true", help="只列台账里的条目")
    ap.add_argument("--out", default="", help="把结果写成 json")
    args = ap.parse_args()

    cases = W.load_cases()
    published = W.load_published()
    only = set(x.strip() for x in (args.case or "").split(",") if x.strip())

    # 台账里带 appmsgid 且没发表的，才可能被 refresh 刷到
    expect = {}
    for cid, rec in published.items():
        if only and cid not in only:
            continue
        if rec.get("status") == "published":
            continue
        aid = rec.get("appmsgid")
        if not aid:
            continue
        expect[str(aid)] = {
            "case": cid,
            "title": rec.get("title") or "",
            "refreshed_at": rec.get("refreshed_at") or "",
        }

    if args.list:
        print("台账里带 appmsgid 的条目：%d 条" % len(expect))
        for aid, e in sorted(expect.items(), key=lambda kv: kv[1]["case"]):
            print("  %-18s %-12s %-40s %s"
                  % (e["case"], aid, (e["title"] or "")[:40], e["refreshed_at"] or "(未记时间)"))
        return 0

    if not expect:
        print("台账里没有带 appmsgid 的条目 —— 跑 refresh 时会顺带补记，跑完再来对账。")
        return 1

    # ⚠ 本机 Chrome 不带 `--no-sandbox` 会在 2 秒内自杀（exit code 3），
    # 所以这里自己拉起 Chrome，而不是要求用户先手工启动。
    chrome_proc = None
    if not args.no_autostart:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import chrome_cdp_launch as L
            L.clear_proxy()
            profile = os.path.expandvars(L.PROFILE)
            chrome, label = L.pick_chrome()
            if chrome is None:
                raise SystemExit("✗ 找不到 Chrome 可执行文件")
            L.remove_lock(profile)
            chrome_proc = subprocess.Popen(
                L.build_args(chrome, profile),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not L.wait_cdp(chrome_proc):
                raise SystemExit(
                    "✗ Chrome 启动失败（exit=%s）。\n"
                    "  若为 exit 3，多半漏了 --no-sandbox。"
                    % chrome_proc.poll())
        except SystemExit:
            raise
        except Exception as e:
            raise SystemExit("✗ 启动 Chrome 失败：%s: %s"
                             % (type(e).__name__, e))

    cdp = W.CDP(args.port)
    _tid, tok = W._connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")

    res = W.verify_refreshed(cdp, tok, expect, fresh_seconds=args.fresh_seconds,
                             since=(args.since or None))

    by_status = {}
    for aid, (st, msg) in res.items():
        by_status.setdefault(st, []).append((aid, msg))

    print("\n回读 %d 条（判据：篇号命中 + 标题一致 + %s）"
          % (len(res),
             ("update_time ≥ 批次起点 %d" % int(args.since)) if args.since
             else ("update_time 距今 ≤ %ds" % args.fresh_seconds)))
    print("-" * 66)
    for st in ("OK", "STALE", "TITLE_MISMATCH", "MISSING"):
        items = by_status.get(st) or []
        if not items:
            continue
        if st == "OK" and not args.all:
            print("  OK            %d 条（不显示，用 --all 看明细）" % len(items))
            continue
        print("  %-14s %d 条" % (st, len(items)))
        for aid, msg in sorted(items, key=lambda x: expect[x[0]]["case"]):
            print("     %-18s %-12s %s"
                  % (expect[aid]["case"], aid, msg))

    # MISSING 的成因诊断：320003 有「限流」和「草稿不可访问」两种含义，
    # 错误页文案一模一样，**只能靠实际试开区分**。
    if args.probe_missing and (by_status.get("MISSING") or []):
        print("\n试开 MISSING 条目的编辑页（区分限流 / 草稿不可访问）")
        print("-" * 66)
        for aid, _ in sorted(by_status["MISSING"],
                             key=lambda x: expect[x[0]]["case"]):
            ok, detail = probe_editor(cdp, tok, aid)
            cid = expect[aid]["case"]
            if ok:
                print("     %-18s %-12s 能打开！刚才可能是限流，重刷即可" % (cid, aid))
                res[aid] = ("OK", "编辑页可打开（重刷即可）")
            else:
                print("     %-18s %-12s 打不开：%s" % (cid, aid, detail))
                print("        → 服务端认定这条草稿不可访问，等多久都没用，"
                      "需在后台草稿箱人工确认")
            time.sleep(2)
        by_status = {}
        for aid, (st, msg) in res.items():
            by_status.setdefault(st, []).append((aid, msg))

    ok = len(by_status.get("OK") or [])
    bad = len(res) - ok
    print("-" * 66)
    print("确认 OK %d 条，需人工确认 %d 条" % (ok, bad))
    if bad:
        print("⚠ 带 --case <id> 逐条重刷；连续失败大概率是服务端限流或该草稿已损坏。")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "fresh_seconds": args.fresh_seconds,
                "since": args.since or None,
                "result": {a: {"status": s, "detail": m,
                              "case": expect[a]["case"]}
                           for a, (s, m) in res.items()},
            }, f, ensure_ascii=False, indent=2)
        print("结果已写 -> %s" % args.out)
    return 0 if bad == 0 else 2



if __name__ == "__main__":
    sys.exit(main())
