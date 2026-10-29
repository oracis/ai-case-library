#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google CSE 通道自检：凭据有没有、走哪条出口能到、配额还剩多少。

为什么单独做一个脚本
--------------------
这条通道断过三次，每次症状都一样（「补源补不到」），原因却各不同：
  1. 没配 key/cx —— 通道静默停用，日志里什么都不说
  2. 配了 key 但本机到 googleapis 不通 —— 表现为超时，容易被当成「网站挂了」
  3. key 配在 secrets.json，而某条通道只认环境变量 —— 一路通一路不通

所以把「凭据 / 出口 / 实查」三件事一次查完，退出码非 0 就是不能用。

用法
----
    python scripts/check_google_cse.py              # 凭据 + 出口诊断
    python scripts/check_google_cse.py --q lapsula  # 再发一次真查询
    python scripts/check_google_cse.py --proxy http://127.0.0.1:10808   # 临时试某个代理

出口优先级：--proxy 参数 > secrets/env 的 google_proxy > 系统 HTTPS_PROXY > 直连。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A                                        # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
CSE = "https://www.googleapis.com/customsearch/v1"
PROBE = CSE + "?key=INVALID_KEY&cx=INVALID_CX&q=ping"


def opener(proxy):
    if not proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def hit(url, proxy, timeout=20, tries=2):
    """返回 (状态, 说明)。状态：ok / http / fail。

    `tries` 默认 2 是有原因的：10808 到 googleapis 的成功率实测 2/5，
    失败形态是 TLS 握手失败/远端断连。只试一次会把「节点抖了一下」
    误判成「这条出口不通」—— 那和 2026-10-21 那次误判（以为 10808 全废）
    是同一类错误。
    """
    last = "fail", ""
    for i in range(max(1, tries)):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        t = time.time()
        try:
            with opener(proxy).open(req, timeout=timeout) as r:
                return "ok", "HTTP %d（%.1fs）" % (r.status, time.time() - t)
        except urllib.error.HTTPError as e:
            # CSE 对无效 key 回 400 并带官方 JSON —— 这恰恰说明路是通的
            body = e.read().decode("utf-8", "ignore")[:200]
            return "http", "HTTP %d（%.1fs）%s" % (e.code, time.time() - t, body[:90])
        except Exception as e:                               # noqa: BLE001
            last = ("fail", "%s: %s" % (type(e).__name__, str(e)[:60]))
            if i + 1 < tries:
                time.sleep(1.5)
    return last


def main():
    ap = argparse.ArgumentParser(description="Google CSE 通道自检")
    ap.add_argument("--q", help="发一次真查询验证配额与结果")
    ap.add_argument("--proxy", help="临时指定代理，覆盖配置里的 google_proxy")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    key, cx, cfg_proxy = A._google_cfg()
    print("=" * 68)
    print("Google CSE 自检")
    print("=" * 68)

    # 1) 凭据
    def mask(v):
        return (v[:6] + "…" + v[-4:]) if v and len(v) > 12 else (v or "（空）")
    print("[1/3] 凭据")
    print("   key : %s" % mask(key))
    print("   cx  : %s" % (cx or "（空）"))
    if not (key and cx):
        print("   ⇒ 通道停用：缺 key 或 cx。两样齐了才会发请求（不会发半截）。")
        print("     填 data/secrets.json 的 google_key / google_cx，或设环境变量")
        print("     CASE_LIB_GOOGLE_KEY / CASE_LIB_GOOGLE_CX。")
    else:
        print("   ⇒ 凭据齐备")

    # 2) 出口
    print()
    print("[2/3] 出口（打 CSE 端点，看哪条路能到 Google）")
    cands = []
    if args.proxy:
        cands.append(("--proxy 参数", args.proxy))
    if cfg_proxy:
        cands.append(("配置的 google_proxy", cfg_proxy))
    for name, val in (("系统 HTTPS_PROXY", os.environ.get("HTTPS_PROXY")),
                      ("系统 http_proxy", os.environ.get("http_proxy"))):
        if val and val not in [c[1] for c in cands]:
            cands.append((name, val))
    cands.append(("直连", None))
    # 本机常见的 Shadowsocks 端口，没配也试一下，省得用户自己找
    for p in ("http://127.0.0.1:10808", "http://127.0.0.1:10809"):
        if p not in [c[1] for c in cands]:
            cands.append(("本机常见端口", p))

    reachable = []
    for name, proxy in cands:
        st, desc = hit(PROBE, proxy, timeout=min(args.timeout, 8))
        flag = {"ok": "✅", "http": "✅", "fail": "❌"}[st]
        print("   %s %-18s %-22s %s" % (flag, name, proxy or "（无代理）", desc[:60]))
        if st in ("ok", "http"):
            reachable.append((name, proxy))

    if not reachable:
        print()
        print("   ⇒ 没有一条出口能到 googleapis。key 配齐了也只会超时，")
        print("     先把能出 Google 的代理写进 secrets.json 的 google_proxy。")
        return 2

    best_name, best_proxy = reachable[0]
    print()
    print("   ⇒ 可用出口：%s（%s）" % (best_name, best_proxy or "直连"))
    if best_proxy and best_proxy != cfg_proxy:
        print("     ⚠ 当前 google_proxy 配置是「%s」，与可用出口不一致 —— "
              "写进 secrets.json 才会被 picked up。" % (cfg_proxy or "（未配）"))

    # 3) 实查
    print()
    print("[3/3] 实查")
    if not (key and cx):
        print("   （跳过：没有凭据。上面出口已验证可用，填上 key/cx 即可）")
        return 1
    if not args.q:
        print("   （跳过：加 --q <关键词> 才会发真查询，省配额）")
        return 0
    url = "%s?%s" % (CSE, urllib.parse.urlencode(
        {"key": key, "cx": cx, "q": args.q, "num": 5}))
    st, desc = hit(url, best_proxy or cfg_proxy, timeout=args.timeout)
    print("   %s" % desc[:400])
    if st == "http":
        try:
            j = json.loads(desc.split("）", 1)[-1])
            print("   错误：%s" % (j.get("error", {}).get("message") or "")[:200])
        except Exception:                                    # noqa: BLE001
            pass
        return 3
    try:
        with opener(best_proxy or cfg_proxy).open(
                urllib.request.Request(url, headers={"User-Agent": UA}),
                timeout=args.timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        items = data.get("items") or []
        print("   命中 %s 条（总结果约 %s）" % (
            len(items), (data.get("searchInformation") or {}).get("totalResults")))
        for it in items[:5]:
            print("     - %s" % (it.get("link") or "")[:90])
    except Exception as e:                                   # noqa: BLE001
        print("   解析失败：%s" % e)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
