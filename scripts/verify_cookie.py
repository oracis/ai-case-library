#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用自签 cookie 跑 ai_verify 的核验流水线（免明文密码）。

为什么需要这个文件
------------------
`ai_verify.py --password` 只认明文密码。而唯一能拿明文密码的办法是
`auth.py --reset`，那会换掉 secret ⇒ 浏览器里已登录的后台会话当场失效
（记忆里的铁律：别擅自改后台口令）。

`auth.py` 的 secret 就在 data/admin.json 里，`make_token()` 是纯函数，
所以可以自己铸一个合法 token 传给 `Admin(base, cookie=...)` ——
身份合法、别人的登录态一条都不受影响。

用法
----
    python scripts/verify_cookie.py --id draftly
    python scripts/verify_cookie.py --id draftly --id harperai --publish
    python scripts/verify_cookie.py --all

⚠ 代理：连 127.0.0.1 必须绕开系统代理，否则 502（见 no_proxy 处理）。
   外网检索仍走代理，所以只把本地地址加进 no_proxy，不要整体清空。
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth  # noqa: E402
import ai_verify as AV  # noqa: E402

ADMIN_BASE = os.environ.get("CASE_LIB_ADMIN_BASE", "http://127.0.0.1:5053")


def _no_proxy_env():
    """只把本地地址排除出代理，其他（外网检索）照旧走代理。"""
    local = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = local
    os.environ["NO_PROXY"] = local


def make_admin():
    """铸一个合法 cookie，不碰明文密码。"""
    cfg = auth.load_admin()
    if not cfg or not cfg.get("secret"):
        raise SystemExit("[!] data/admin.json 没有 secret，先跑 scripts/auth.py --reset")
    token = auth.make_token(cfg["secret"])
    return AV.Admin(ADMIN_BASE, cookie=auth.cookie_header(token))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="用自签 cookie 跑 AI 核验（不动后台口令）")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--include-small", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--ai-base", default="https://opencode.ai/zen/v1")
    ap.add_argument("--ai-model", default="space-bunny-free")
    args = ap.parse_args()

    _no_proxy_env()
    # 免 key 端点：显式传空串清 key（真值判据会沿用旧 key ⇒ 必 401）
    AV.configure(api_key="", base=args.ai_base, model=args.ai_model)
    if not AV.ai_ready():
        raise SystemExit("[!] LLM 不可用")

    cands = AV.load_json("candidates")
    index = AV.triage_index()
    selected = AV.pick_candidates(
        cands, limit=args.limit, ids=args.id or None,
        include_small=args.include_small, index=index)
    if args.all:
        selected = AV.pick_candidates(
            cands, limit=None, ids=None,
            include_small=args.include_small, index=index)
    if not selected:
        print("没有可处理的候选。")
        return 0

    admin = make_admin()
    print("LLM：%s @ %s｜后台：%s（自签 cookie，未改口令）｜%s\n" % (
        AV.AI_MODEL, AV.AI_BASE, ADMIN_BASE,
        "核完直接发布" if args.publish else "只存草稿"))

    done = published = failed = 0
    for c in selected:
        try:
            r = AV.verify_one(c, admin, publish=args.publish,
                               min_score=args.min_score)
            done += 1
            if r.get("published"):
                published += 1
            elif not r.get("ok", True):
                failed += 1
        except Exception as e:                                # noqa: BLE001
            failed += 1
            print("== %s  [失败] %s" % (c.get("id"), e))
        print("")

    print("=" * 56)
    print("处理 %d 条｜已发布 %d 条｜未成 %d 条" % (done, published, failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())