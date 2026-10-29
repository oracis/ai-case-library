#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知乎发布 —— Web API v4（x-zse-96 签名见 zhihu_sign.py），CDP 登录一次。

## 现状（2026-10-28 本机实测）

| 项 | 结论 |
|---|---|
| 签名 | ✅ `zhihu_sign.py` 匿名实测通过：带签名 POST /udid 成功领 d_c0；|
|    | 只读端点 404/401（业务层响应）证明请求过了签名层 |
| 风控 | ⚠ 匿名 d_c0 打数据端点（feeds/followers）会 403 40362「请求异常」——登录 cookie 后大概率缓解 |
| 建稿 | `POST /api/v4/articles`（title + content[HTML]），需登录 cookie + x-xsrftoken |
| 发布 | `POST /api/v4/articles/{id}/publish`（⚠ 2026-05-30 后无公开实测成功案例，先建稿验证再上） |
| 回读 | 待定稿：发布成功判据 = 文章页 URL 按标题命中（/p/<id>） |

## 用法

```bash
python scripts/zhihu_publish.py login --wait 480   # 开浏览器登录一次（独立 profile）
python scripts/zhihu_publish.py whoami             # 验 cookie + 签名（GET /api/v4/me）
python scripts/zhihu_publish.py draft --file drafts/wechat/x.md --case x
```
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import zhihu_sign as Z                    # noqa: E402
from csdn_publish import split_md, md_to_html   # noqa: E402  复用纯函数
from juejin_publish import _CDP           # noqa: E402  stdlib CDP 客户端

COOKIE_FILE = os.path.join(ROOT, "data", "zhihu_cookie.json")
DRAFT_FILE = os.path.join(ROOT, "data", "zhihu_drafts.json")
LOGIN_PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-zhihu-profile")
LOGIN_PORT = 9227
LOGIN_URL = "https://www.zhihu.com/signin"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


class LoginRequired(Exception):
    pass


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------
# cookie 读写
# --------------------------------------------------------------------------

def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        raise LoginRequired("没有 cookie，先跑: python scripts/zhihu_publish.py login")
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cookie(pairs):
    import atomicio
    atomicio.write_json(COOKIE_FILE, {
        "pairs": pairs,                                # [(name, value), ...]
        "cookie_string": "; ".join("%s=%s" % kv for kv in pairs),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    print("✓ cookie 落盘: %s" % os.path.relpath(COOKIE_FILE, ROOT))


def _ck(cookie_dict, name):
    for k, v in cookie_dict.get("pairs") or []:
        if k == name:
            return v
    return ""


def remember(case_id, article_id, title, url="", status="draft"):
    import atomicio
    ledger = {}
    if os.path.exists(DRAFT_FILE):
        with open(DRAFT_FILE, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    ledger[case_id] = {
        "article_id": article_id, "title": title, "url": url, "status": status,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    atomicio.write_json(DRAFT_FILE, ledger)
    print("✓ 台账: %s ← %s (%s)" % (os.path.basename(DRAFT_FILE), case_id, status))


# --------------------------------------------------------------------------
# API 封装（签名在 zhihu_sign）
# --------------------------------------------------------------------------

def call(path_query, method="GET", data=None):
    ck = load_cookie()
    d_c0 = _ck(ck, "d_c0")
    if not d_c0:
        raise LoginRequired("cookie 里没有 d_c0，重跑 login")
    xsrf = _ck(ck, "XSRF-TOKEN")
    st, body = Z.api(path_query, d_c0, cookie=ck["cookie_string"],
                     method=method, data=data, xsrf=xsrf)
    if st == 401:
        raise LoginRequired("401（cookie 过期或签名被拒）: %s"
                            % json.dumps(body, ensure_ascii=False)[:200])
    if st >= 400:
        raise ApiError("HTTP %s: %s" % (st, json.dumps(body, ensure_ascii=False)[:300]))
    return body


def _call_zhuanlan(method, path, payload):
    """zhuanlan.zhihu.com/api/articles/* —— 实测免 x-zse-96 签名，
    只要 cookie + x-xsrftoken（注意 xsrf cookie 名是 _xsrf，www 域是 XSRF-TOKEN）。"""
    ck = load_cookie()
    xsrf = _ck(ck, "_xsrf")
    import urllib.request
    import urllib.error
    req = urllib.request.Request("https://zhuanlan.zhihu.com" + path,
                                 data=json.dumps(payload).encode(), method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("x-xsrftoken", xsrf)
    req.add_header("x-requested-with", "fetch")
    req.add_header("Referer", "https://zhuanlan.zhihu.com/write")
    req.add_header("User-Agent", UA)
    req.add_header("Cookie", ck["cookie_string"])
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = op.open(req, timeout=30)
        raw = r.read().decode("utf-8", "replace")
        st = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        st = e.code
    except urllib.error.URLError as e:
        raise ApiError("网络错误: %r" % e)
    if st == 401 or st == 403:
        raise LoginRequired("HTTP %s（cookie 过期？重跑 login）: %s" % (st, raw[:150]))
    if st >= 400:
        raise ApiError("HTTP %s: %s" % (st, raw[:300]))
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"_raw": raw}


def whoami():
    """GET /api/v4/me：验登录态 + 签名 + cookie 完整性。"""
    return call("/api/v4/me?include=account_status")


def create_draft(title, html_body):
    """建草稿（2026-10-28 autosave hook 实测的两段式）：
    ① POST /api/articles/drafts  {title, delta_time, can_reward} -> 草稿对象含 id
    ② PATCH /api/articles/{id}/draft  {content: HTML, table_of_contents, ...}
    ⚠ 只建稿不发表。"""
    d = _call_zhuanlan("POST", "/api/articles/drafts",
                       {"title": title, "delta_time": 0, "can_reward": False})
    aid = str(d.get("id") or "")
    if not aid:
        raise ApiError("建稿响应里没有 id: %s" % json.dumps(d, ensure_ascii=False)[:300])
    _call_zhuanlan("PATCH", "/api/articles/%s/draft" % aid,
                   {"content": html_body, "table_of_contents": False,
                    "delta_time": 0, "can_reward": False})
    return aid, "https://zhuanlan.zhihu.com/p/%s" % aid


# --------------------------------------------------------------------------
# login（CDP，独立 profile）
# --------------------------------------------------------------------------

def _collect_zhihu_cookies(cdp):
    cdp.send("Network.enable")
    r = cdp.send("Network.getAllCookies")
    items = (r.get("result") or {}).get("cookies") or []
    zhihu = [c for c in items if "zhihu.com" in (c.get("domain") or "")]
    if not zhihu:
        return None
    names = {c["name"]: c["value"] for c in zhihu}
    if not names.get("z_c0"):                 # z_c0 = 登录票据，唯一硬判据
        return None
    return sorted((c["name"], c["value"]) for c in zhihu
                  if not c["name"].startswith("__"))


def cmd_login(args):
    import chrome_cdp_launch as L
    L.clear_proxy()
    L.remove_lock(LOGIN_PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    proc = subprocess.Popen(L.build_args(chrome, LOGIN_PROFILE, port=args.port,
                                         headless=False),   # 登录必须有头
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=args.port):
        raise SystemExit("Chrome CDP 没起来")
    cdp = _CDP(args.port)
    try:
        cdp.open(args.url)
        print("已打开知乎登录页 —— 请登录（扫码/手机号/密码均可）。")
        print("脚本每 5 秒自动检测 z_c0 登录票据，最多等 %d 秒。" % args.wait)
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(5)
            try:
                pairs = _collect_zhihu_cookies(cdp)
            except Exception:                            # noqa: BLE001
                continue
            if not pairs:
                continue
            save_cookie(pairs)
            try:
                info = whoami()
            except (LoginRequired, ApiError) as e:
                print("  cookie 已收但验证未过（%s），继续等…" % str(e)[:80])
                continue
            print("✓ 登录成功并已验证: %s" % json.dumps(info, ensure_ascii=False)[:200])
            return 0
        raise SystemExit("✗ 超时未检测到登录。")
    finally:
        try:
            cdp.bws.close()
        except Exception:                                # noqa: BLE001
            pass
        try:
            proc.terminate()
        except Exception:                                # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="知乎发布（Web API v4 + x-zse-96）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="开浏览器登录一次并存 cookie")
    p.add_argument("--port", type=int, default=LOGIN_PORT)
    p.add_argument("--url", default=LOGIN_URL)
    p.add_argument("--wait", type=int, default=480)
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("whoami", help="验登录态 + 签名")
    p.set_defaults(fn=lambda a: print(json.dumps(whoami(), ensure_ascii=False)[:500]))

    p = sub.add_parser("draft", help="建草稿（只建稿不发表）")
    p.add_argument("--file", required=True, help="markdown 文件")
    p.add_argument("--case", default="")

    p = sub.add_parser("publish", help="🚫 未开放：publish 端点 2026-05-30 后无公开实测，"
                                        "先跑通 draft 再评估")
    p.set_defaults(fn=lambda a: None)

    args = ap.parse_args()
    if args.cmd == "publish":
        raise SystemExit("🚫 publish 未开放：`POST /api/v4/articles/{id}/publish` 在 "
                         "2026-05-30 后无公开实测成功案例。先跑 draft，建稿稳定后再评估。")
    if args.cmd == "draft":
        with open(args.file, "r", encoding="utf-8") as f:
            md = f.read()
        md = "\n".join(ln for ln in md.split("\n")
                       if not ln.startswith("> 平台："))
        title, body_md = split_md(md)
        if re.search(r"\d", title):
            print("⚠ 标题含数字（「标题禁金额」铁律）：", title)
        aid, url = create_draft(title, md_to_html(body_md))
        case_id = args.case or os.path.splitext(os.path.basename(args.file))[0]
        remember(case_id, aid, title, url=url, status="draft")
        print("✓ 草稿: article_id=%s url=%s" % (aid, url))
        return
    args.fn(args)


if __name__ == "__main__":
    main()
