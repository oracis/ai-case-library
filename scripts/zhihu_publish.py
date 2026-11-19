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
| 发布 | ✅ `PUT zhuanlan/api/articles/{id}/publish` body `{}`（2026-11-19 实测成功：mindustry 2105588389919580273，免 x-zse-96 只要 cookie+x-xsrftoken；2026-05-30 的「无公开实测」疑虑解除） |
| 回读 | 发布成功判据 = **登录态已发表列表命中 + 草稿箱移出**（`zhihu_creator.py articles/list`）；匿名公开页被知乎反爬全挡（API 404 / curl 403 / 无头渲染 40362），不可当回读通道 |

## 发布规则（2026-11-19 用户裁定，推翻 PRD #25「终点=草稿」）

知乎终点 = **发表**。每天一篇消化积压：轮到知乎时优先发表台账（platform_posts
zhihu state=draft）里**最久**的一条；草稿积压 0 时按当日档期新写，**新写即发表**
（`draft` 命令建稿后自动 PUT publish；`--draft-only` 是逃生口）。入口收口在
`publish_one.py --platform zhihu`。

## 用法

```bash
python scripts/zhihu_publish.py login --wait 480   # 开浏览器登录一次（独立 profile）
python scripts/zhihu_publish.py whoami             # 验 cookie + 签名（GET /api/v4/me）
python scripts/zhihu_publish.py draft --file x.md --case x   # 新写并发表
python scripts/zhihu_publish.py publish --id <aid> --case x  # 发表已有草稿（补发/积压）
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
import db  # noqa: E402  SQLite 统一台账：知乎发布记录写 platform_posts (platform='zhihu')

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
        # 2026-11-05 修：`"%s=%s" % kv` 只对 tuple 拆包，list 会 TypeError
        # （zhihu_creator.cmd_cookies 传的是 [n, v] 列表）。显式 tuple() 兼容两者。
        "cookie_string": "; ".join("%s=%s" % tuple(kv[:2]) for kv in pairs),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    print("✓ cookie 落盘: %s" % os.path.relpath(COOKIE_FILE, ROOT))


def _ck(cookie_dict, name):
    for k, v in cookie_dict.get("pairs") or []:
        if k == name:
            return v
    return ""


def remember(case_id, article_id, title, url="", status="draft"):
    """记账：知乎 draft/published。优先写 SQLite（platform_posts, platform='zhihu'），
    同时双写 JSON 快照（供 publish_slot / zhihu_ledger_sync 等旧脚本只读兼容）。

    ⚠ db 写仅当 `DRAFT_FILE` 是默认路径时生效（测试注入临时文件时不碰真库，
    与 `Manifest._db_write` 同一先例）。
    """
    if DRAFT_FILE == os.path.join(ROOT, "data", "zhihu_drafts.json"):
        conn = db.get_conn()
        try:
            db.upsert_post(conn, case_id, "zhihu", {
                "state": status,
                "remote_id": str(article_id) if article_id else "",
                "url": url or "",
                "title": title or "",
                "draft_id": str(article_id) if article_id else "",
                "attempt": 0,
                # 🚨 published_at 只属于 published 行（PRD #92）：
                #   draft 不许带时点，否则档期把草稿算成「今日已发」假满。
                #   （remember 原来恒填 now，绕过了 upsert_post 2026-11-07
                #   的「published_at not in row 才按 state 决定」修法。）
                "published_at": (time.strftime("%Y-%m-%d %H:%M:%S")
                                 if status == "published" else None),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "status=" + status,
            })
        finally:
            db.close_conn()

    # 双写 JSON 快照（保持原格式，供旧脚本只读兼容）
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


def publish_draft(article_id):
    """发表已有草稿（2026-11-19 用户裁定：知乎终点=发表，固定本规则）。

    `PUT zhuanlan.zhihu.com/api/articles/<id>/publish` body `{}` —— 与
    `zhihu_creator.py` 同一路径，2026-11-19 实测成功（mindustry 2105588389919580273）。
    ⚠ 判据：登录态已发表列表命中 + 草稿箱移出；匿名公开页被知乎反爬全挡
    （API 404 / curl 403 / 无头渲染 40362），不能当回读通道。
    """
    return _call_zhuanlan("PUT", "/api/articles/%s/publish" % article_id, {})


def publish_existing(article_id, case_id=""):
    """发发表前先落台账的已有草稿（积压消化路径的记账口）。

    case_id 缺省时从 zhihu_drafts.json 按 article_id 反查；发表成功后按
    status=published 记账（状态只前进，published 盖 draft 由 upsert_post 仲裁）。
    """
    aid = str(article_id)
    d = publish_draft(aid)
    if not case_id:
        try:
            with open(DRAFT_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            case_id = next((k for k, v in (ledger or {}).items()
                            if isinstance(v, dict)
                            and str(v.get("article_id")) == aid), "")
        except (OSError, ValueError):
            case_id = ""
    if case_id:
        remember(case_id, aid, (d.get("title") or ""), status="published")
    else:
        print("⚠ 发表成功但反查不到 case_id（article %s）⇒ 台账未记，"
              "请人工按 --case 补记" % aid)
    return d





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

    p = sub.add_parser("draft", help="建草稿并**自动发表**（2026-11-19 规则固定："
                                     "新写稿 → 发表；--draft-only 才只建稿）")
    p.add_argument("--file", required=True, help="markdown 文件")
    p.add_argument("--case", default="")
    p.add_argument("--draft-only", action="store_true",
                   help="只建稿不发表（逃生口，默认发表）")

    p = sub.add_parser("publish", help="发表已有草稿（PUT publish，2026-11-19 实测可用；"
                                       "知乎积压消化的主通道）")
    p.add_argument("--id", required=True, help="article id（草稿或已建未发的）")
    p.add_argument("--case", default="",
                   help="case id；缺省从 zhihu_drafts.json 按 article id 反查")

    args = ap.parse_args()
    if args.cmd == "draft":
        with open(args.file, "r", encoding="utf-8") as f:
            md = f.read()
        md = "\n".join(ln for ln in md.split("\n")
                       if not ln.startswith("> 平台："))
        title, body_md = split_md(md)
        if re.search(r"[$¥€£]\s*\d|\d[\d,.]*\s*(?:美元|美金|美刀|元|万|亿|RMB|USD|MRR|ARR)",
                     title, re.I):
            print("⚠ 标题疑似含金额（「标题禁金额」铁律）：", title)
        aid, url = create_draft(title, md_to_html(body_md))
        case_id = args.case or os.path.splitext(os.path.basename(args.file))[0]
        if args.draft_only:
            remember(case_id, aid, title, url=url, status="draft")
            print("✓ 草稿（未发表）: article_id=%s url=%s" % (aid, url))
            print("  补发：python scripts/zhihu_publish.py publish --id %s --case %s"
                  % (aid, case_id))
            return
        # 2026-11-19 规则固定：新写稿 → 直接发表（终点=published，推翻 PRD #25
        # 「终点=草稿」。发表失败时台账落 draft，可再跑 publish --id 补发）
        try:
            publish_draft(aid)
        except (LoginRequired, ApiError) as e:
            remember(case_id, aid, title, url=url, status="draft")
            print("✓ 草稿已建但发表失败: %s" % str(e)[:200])
            print("  补发：python scripts/zhihu_publish.py publish --id %s --case %s"
                  % (aid, case_id))
            raise SystemExit(1)
        remember(case_id, aid, title, url=url, status="published")
        print("✓ 已发表: article_id=%s url=%s" % (aid, url))
        print("  复核：登录态已发表列表命中 + 草稿箱移出"
              "（zhihu_creator.py articles）；匿名公开页被反爬挡，不可用")
        return
    if args.cmd == "publish":
        publish_existing(args.id, case_id=args.case)
        return
    args.fn(args)


if __name__ == "__main__":
    main()
