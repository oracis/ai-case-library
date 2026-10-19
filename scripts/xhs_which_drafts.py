#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐个 profile 启动 Chrome，打开小红书创作者中心，报告「哪个账号 + 草稿几篇」。

为什么需要这个（2026-10-02 用户反馈）
--------------------------------------
用户打开 creator.xiaohongshu.com，右上角是「小红薯6474A1BD」，草稿箱(0)，
但 `data/xhs_drafts.json` 记着 37 条。**账号或profile 不对**。

磁盘上查不出来（cookie 值是 App-Bound 加密的，`value` 列全是空的，
Local Storage 里也只有 `_renderInfo` 这类无关键）。所以只能**真打开一次看**。

⚠ 本脚本会**启动/复用两个不同 profile 的 Chrome**，分别用9223 / 9224 端口，
避免和公众号的 9222 互抢。**它只读不写**：不点发布、不删草稿、不改任何设置，
只在草稿箱页读标题条数。

判据（唯一可靠的那种）：
    右上角账号名 + 草稿箱页的条目数
    —— 不是 cookie 条数，不是 Cookies 文件大小。

用法：
    python -X utf8 scripts/xhs_which_drafts.py            # 只查第一个
    python -X utf8 scripts/xhs_which_drafts.py --all      # 两个都查
    python -X utf8 scripts/xhs_which_drafts.py --all --keep# 查完不关，留着给你看
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chrome_cdp_launch as L          # noqa: E402

HOMES = [
    ("项目 xhs 用的（xhs_publish.py PUB_PORT=9222）",
     r"C:\Users\DELL\chrome-debug-profile", 9223),
    ("公众号 autostart 用的（%LOCALAPPDATA%）",
     os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP"), 9224),
]

HOME_URL = "https://creator.xiaohongshu.com/new/home"
DRAFT_URL = "https://creator.xiaohongshu.com/publish/publish?target=draft"


def log(m):
    print(m, flush=True)


def cdp_for(port, timeout=2):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return json.loads(op.open(
            "http://127.0.0.1:%d/json/version" % port, timeout=timeout).read())
    except Exception:
        return None


def cdp_class(port):
    """复用项目 CDP 类（走Target.createTarget 建target 再连 WebSocket）。

    ⚠ 新版 Chrome 已禁 HTTP `/json/new`（GET/POST 都 405），
    必须用浏览器级 WS 发 `Target.createTarget`，这就是项目 CDP 里的
    `new_target()` / `connect_target()`。
    ⚠ **漏了这两步会报「未连接页面 target」** —— CDP 类构造时并不自动
    附着到任何页面（踩过）。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wechat_publish as W
    W.CDP_PORT = port
    return W.CDP(port)


def open_page(cdp, url):
    """新建 target 并连上，返回 target id。

    ⚠ `Page.enable` **必须放在 connect_target 之后** —— CDP 类的 `send()`
    在没附着页面时直接抛「未连接页面 target」。踩过。
    """
    t = cdp.new_target(url)
    tid = t.get("id") or t.get("targetId")
    if not cdp.connect_target(tid):
        raise RuntimeError("connect_target 失败 target=%s" % tid)
    cdp.send("Page.enable")
    return tid


def body_text(cdp, limit=4000):
    try:
        return cdp.eval("(document.body.innerText||'').slice(0,%d)" % limit,
                        refresh_context=True) or ""
    except Exception:
        return ""


def probe(cdp, tok_unused=None):
    """打开首页 + 草稿箱页，抓账号名与草稿条目数。返回 dict。"""
    import re
    out = {}

    # --- 首页：右上角账号名 ---
    open_page(cdp, HOME_URL)
    txt = ""
    for _ in range(30):
        time.sleep(1.5)
        txt = body_text(cdp)
        if "小红薯" in txt or "创作" in txt:
            break
    out["home_loaded"] = bool(txt)
    m = re.search(r"小红薯\s*([0-9A-Za-z]{4,})", txt)
    out["account"] = ("小红薯" + m.group(1)) if m else None

    # --- 草稿箱 ---
    open_page(cdp, DRAFT_URL)
    dtxt = ""
    for _ in range(25):
        time.sleep(1.5)
        dtxt = body_text(cdp)
        if dtxt.strip():
            break
    out["draft_page_text_len"] = len(dtxt)
    # ⚠ 判据要打「侧边栏那个真实数字」，不要信 DOM 计数，也不要信
    # 「页面上看不见卡片」—— 草稿列表是懒加载的，页面文本里往往只有
    # 侧边栏的 `草稿箱(N)`。2026-10-02 实测：`DOM 计数=0` 但
    # `草稿箱(36)` 真实存在，两个判据**指向相反结论**，必须以侧边栏为准。
    m2 = re.search(r"草稿箱\s*\(?\s*(\d+)\s*\)?", dtxt)
    out["draft_count_parsed"] = int(m2.group(1)) if m2 else None
    n = body_text_js = None
    try:
        n = cdp.eval(
            "document.querySelectorAll('.draft-item,.note-item,"
            "[class*=\"draft\"] [class*=\"item\"],[data-draft-id]').length",
            refresh_context=True)
    except Exception:
        n = None
    try:
        out["draft_items"] = int(n or 0)
    except Exception:
        out["draft_items"] = None
    out["draft_page_head"] = dtxt[:600].replace("\n", " | ")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="两个 profile 都查")
    ap.add_argument("--keep", action="store_true", help="查完不关 Chrome")
    args = ap.parse_args()

    L.clear_proxy()
    todo = HOMES if args.all else HOMES[:1]

    for label, prof, port in todo:
        log("\n" + "=" * 74)
        log("■ %s" % label)
        log("  端口 %d · profile %s" % (port, prof))
        log("=" * 74)

        if not os.path.isdir(prof):
            log("  ✗ profile 不存在")
            continue

        ver = cdp_for(port)
        proc = None
        if ver is None:
            L.remove_lock(prof)
            chrome, _label = L.pick_chrome()
            if chrome is None:
                log("  ✗ 找不到 Chrome")
                continue
            argslist = [
                chrome,
                "--remote-debugging-port=%d" % port,
                "--user-data-dir=%s" % prof,
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
            ]
            proc = subprocess.Popen(argslist,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            L.CDP_PORT = port
            ok = False
            for _ in range(30):
                time.sleep(1.5)
                if cdp_for(port):
                    ok = True
                    break
                if proc.poll() is not None:
                    log("  ✗ Chrome 退出 code=%s" % proc.poll())
                    break
            if not ok:
                log("  ✗ CDP 未开")
                continue
            log("  [启动] Chrome for Testing, --no-sandbox")
        else:
            log("  [复用] 端口已有实例")

        try:
            cdp = cdp_class(port)
            res = probe(cdp)
            log("  账号: %s" % (res.get("account") or "（页面上没识别到）"))
            log("  草稿箱(侧边栏): %s" % res.get("draft_count_parsed"))
            log("  草稿卡片 DOM 计数: %s（懒加载，可能为 0，不作判据）"
                % res.get("draft_items"))
            log("  草稿页文本(%d 字): %s"
                % (res.get("draft_page_text_len", 0),
                   res.get("draft_page_head", "")[:200]))
            verdict = res.get("draft_count_parsed")
            log("  => 判定: %s"
                % ("草稿箱有 %d 条（这个 profile 是对的）" % verdict
                   if verdict else "草稿箱为空 ⇒ 不是你要找的那份"))
        except Exception as e:
            log("  ✗ 探测失败: %s" % e)
            if os.environ.get("XHS_DIAG_TRACE"):
                import traceback
                log("".join(traceback.format_exc()))
        finally:
            if proc is not None and not args.keep:
                try:
                    proc.terminate()
                except Exception:
                    pass

    if args.keep:
        log("\n（--keep：Chrome 留着没关，可直接切窗口看）")


if __name__ == "__main__":
    main()