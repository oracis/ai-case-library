#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探测：头条「群发」（正式发表）是不是 save=1。

## 已拿到的静态证据（2026-10-18）

`publish.b8c90341ac.js` 里：

    o = { PUBLISH: 1, DRAFT: 0, PREVIEW: 0 }
    t({ save: o[r], entrance: r===PUBLISH ? "main" : "", ... })

⇒ 发表与存草稿是**同一个端点** `/mp/agw/article/publish`，差别只有
`save`（0→1）与 `entrance`（""→"main"）。没有独立的 published 接口。

## 为什么先用假 pgc_id

真发是不可逆的线上动作。先用一个**不存在的 pgc_id**（20 位数字）打 save=1：

  · 回「稿件不存在」类错误  ⇒ save=1 被受理，且没有创建任何东西 → 可以放心做真实验证
  · 回 code:0               ⇒ 危险：可能被当成新建并直接发表了 → 立刻查已发列表

## 用法

    python -X utf8 scripts/_oneoff/probe_tt_send.py            # 假 ID 试探（不发任何内容）
    python -X utf8 scripts/_oneoff/probe_tt_send.py --ghost 7399999999999999999
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import toutiao_publish as T  # noqa: E402
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_PORT", 9223))
GHOST = "7399999999999999999"


def post_publish(cdp, body, anti_token):
    """原样复用 save_draft_api 的 JS 构造（同 body、同三个头），只换 body。"""
    hdr = {"Accept": "application/json, text/plain, */*",
           "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
           "tt-anti-token": anti_token}
    js = ("(function(){"
          "return fetch(%s,{method:'POST',credentials:'include',"
          "headers:%s,body:%s})"
          ".then(function(r){return r.text();})"
          ".catch(function(e){return 'ERR:'+e.message;})})()"
          % (json.dumps(T._publish_api_url()), json.dumps(hdr),
             json.dumps(body)))
    return str(cdp.eval(js) or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghost", default=GHOST)
    ap.add_argument("--title", default="__tt_probe_send__")
    args = ap.parse_args()

    cdp = wp.CDP(PORT)
    t = cdp.new_target(T.TT_EDITOR)
    if not cdp.connect_target(t["id"]):
        print("✗ 连不上编辑器页")
        return 1
    try:
        time.sleep(10)
        print("页面：%s" % str(cdp.eval("location.href") or "")[:80], flush=True)
        print("取 tt-anti-token…", flush=True)
        t0 = time.time()
        tok = T.grab_anti_token(cdp, attempts=3, per_attempt=25)
        print("  token=%s len=%d（%.1fs）"
              % (("✅ " + tok[:10] + "…") if tok else "✗", len(tok or ""),
                 time.time() - t0), flush=True)
        if not tok:
            print("✗ 没取到 token，无法验证写操作")
            return 2

        body = T._publish_draft_body(args.title, "<p>probe</p>",
                                     pgc_id=args.ghost)
        pairs = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
        print("\n原始 save=%r entrance=%r pgc_id=%r"
              % (pairs.get("save"), pairs.get("entrance"), pairs.get("pgc_id")))
        pairs["save"] = "1"          # PUBLISH
        pairs["entrance"] = "main"   # 发表时前端传 main
        probe = urllib.parse.urlencode(pairs)
        print("改写 save=1 entrance=main，假 pgc_id=%s 提交…\n" % args.ghost,
              flush=True)

        out = post_publish(cdp, probe, tok)
        print("返回：%s" % out[:500])
        try:
            d = json.loads(out)
        except Exception:
            print("\n⚠ 非 JSON（多半是 argus 风控页）")
            return 3
        code = d.get("code")
        msg = d.get("message")
        print("\ncode=%s message=%s" % (code, msg))
        if code == 0:
            print("\n🚨 返回成功 —— 可能被当成新建并发表了！立刻去已发列表核对")
            print("   data=%s" % json.dumps(d.get("data"), ensure_ascii=False)[:300])
            return 4
        print("\n✓ save=1 被服务端受理并拒绝（%s）⇒ 没有创建任何内容，"
              "可以用测试稿做真实发布验证" % msg)
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
