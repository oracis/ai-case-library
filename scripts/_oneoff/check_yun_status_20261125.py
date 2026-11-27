#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两朵云「审核中 / 已驳回 / 已发布」登录态复查（只读 DOM，不点任何按钮）。

为什么要这个：`yun_state.judge()` 只看**匿名公开页**，404 只能证明「此刻没公开」，
分不出「还在审核」和「已驳回」。区分二者必须进创作中心的文章管理列表看卡片文案
（阿里云 #109 的口径：卡片写「你的文章已驳回,原因:…」才是 rejected）。

用法:
    python scripts/_oneoff/check_yun_status_20261125.py            # 检查两篇
    python scripts/_oneoff/check_yun_status_20261125.py --keys easyMix 解析创作者

红线：只 eval DOM + 截图，不 dispatch 任何鼠标事件，不碰发布/删除按钮。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chrome_cdp_launch as L                     # noqa: E402
from juejin_publish import _CDP                   # noqa: E402

PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-yun-profile")
PORT = 9228
SHOT_DIR = os.path.join(ROOT, "tmp")

PAGES = [
    ("腾讯云 创作中心-文章管理", "https://cloud.tencent.com/developer/creator/article"),
    ("阿里云 我的-内容管理", "https://developer.aliyun.com/my"),
]

# 状态词：命中的句子打出来，供人判读（不做自动裁定，避免误判）
STATUS_WORDS = ["审核中", "审核通过", "已发布", "发布中", "已驳回", "驳回",
                "未通过", "草稿", "已下线", "未公开"]

DUMP_JS = r"""
JSON.stringify({
  url: location.href,
  title: document.title,
  nick: (() => {
    const el = document.querySelector('[class*=nick],[class*=avatar],[class*=user-name],.user-name');
    return el ? (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30) : '';
  })(),
  body: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 9000),
  links: [...document.querySelectorAll('a[href]')]
    .map(a => a.getAttribute('href') || '')
    .filter(h => /article|manage|draft|creator|content|my/i.test(h))
    .slice(0, 60)
})"""


def shot(cdp, path):
    try:
        r = cdp.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["result"]["data"]))
        print("  📷 %s" % os.path.relpath(path, ROOT))
    except Exception as e:                                     # noqa: BLE001
        print("  (截图失败: %s)" % str(e)[:80])


def launch(port):
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, _ = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    proc = subprocess.Popen(
        L.build_args(chrome, PROFILE, port=port, headless=False),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=port):
        raise SystemExit("Chrome CDP 没起来")
    return proc, _CDP(port)


def probe(cdp, name, url, keys, settle=9.0):
    print("\n=== %s ===" % name)
    tid = cdp.open(url)
    time.sleep(settle)
    raw = cdp.eval(DUMP_JS) or "{}"
    try:
        info = json.loads(raw)
    except Exception:                                          # noqa: BLE001
        print("!! dump 解析失败: %s" % str(raw)[:200])
        return
    print("  final: %s" % info.get("url"))
    print("  title: %s | 昵称痕迹: %r" % (info.get("title"), info.get("nick")))
    body = info.get("body") or ""
    print("  正文字数: %d" % len(body))

    # 1) 标题命中
    for k in keys:
        idx = body.find(k)
        if idx >= 0:
            print("  ✓ 命中关键词 %r：…%s…" % (k, body[max(0, idx - 120):idx + 200]))
        else:
            print("  ✗ 未命中 %r" % k)

    # 2) 状态词
    hits = []
    for w in STATUS_WORDS:
        for m in re.finditer(re.escape(w), body):
            hits.append((m.start(), w))
    hits.sort()
    print("  状态词命中 %d 处:" % len(hits))
    seen = set()
    for pos, w in hits[:20]:
        seg = body[max(0, pos - 70):pos + 70]
        if seg in seen:
            continue
        seen.add(seg)
        print("    [%s] …%s…" % (w, seg))

    # 3) 列表链接（找「文章管理」入口）
    links = [h for h in (info.get("links") or []) if h]
    if links:
        print("  链接样本 ×%d: %s" % (len(links), " | ".join(dict.fromkeys(links))[:300]))

    shot(cdp, os.path.join(SHOT_DIR, "yun_check_%s.png"
                           % re.sub(r"[^\w]+", "_", name)[:20]))
    try:
        cdp.send_browser("Target.closeTarget", {"targetId": tid})
    except Exception:                                          # noqa: BLE001
        pass


def probe_ali_list(cdp, keys, settle=8.0):
    """阿里云个人中心是 hash SPA：先开 /my 等框架起来，再改 hash 进「我的文章」。

    直接开 `/my#/article` 有概率拿到未渲染的空壳（实测 1203 字），必须两步走。
    """
    print("\n=== 阿里云 我的文章（完整列表）===")
    tid = cdp.open("https://developer.aliyun.com/my")
    time.sleep(settle)
    cdp.eval("location.hash = '#/article'; 1")
    time.sleep(6.0)
    raw = cdp.eval(DUMP_JS) or "{}"
    try:
        info = json.loads(raw)
    except Exception:                                          # noqa: BLE001
        print("!! dump 解析失败: %s" % str(raw)[:200])
        return
    print("  final: %s | 字数 %d" % (info.get("url"),
                                     len(info.get("body") or "")))
    body = info.get("body") or ""
    print("---- innerText ----")
    print(body)
    print("---- /innerText ----")
    for k in keys:
        print("  关键词 %r: %s" % (k, "命中" if k in body else "未命中"))
    shot(cdp, os.path.join(SHOT_DIR, "yun_check_ali_list.png"))
    try:
        cdp.send_browser("Target.closeTarget", {"targetId": tid})
    except Exception:                                          # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keys", nargs="*",
                    default=["easyMix", "混音和母带", "解析创作者平台收入数据", "stan"])
    ap.add_argument("--url", nargs="*", default=None,
                    help="覆盖默认两个页面（成对 name=url）")
    ap.add_argument("--full", action="store_true", help="打印整页 innerText")
    ap.add_argument("--ali-list", action="store_true",
                    help="只走阿里云「我的文章」两步导航并打印完整列表")
    args = ap.parse_args()

    if not os.path.isdir(SHOT_DIR):
        os.makedirs(SHOT_DIR)
    pages = PAGES
    if args.url:
        pages = [tuple(u.split("=", 1)) for u in args.url]

    proc, cdp = launch(args.port)
    try:
        if args.ali_list:
            probe_ali_list(cdp, args.keys)
        else:
            for name, url in pages:
                probe(cdp, name, url, args.keys)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()
        print("\n(chrome 已关)")


if __name__ == "__main__":
    main()
