#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知乎创作中心：不打开编辑器也能读草稿箱 / 发文章 / 删草稿。

2026-10-31 实测打通。三条关键事实（别再靠猜）：

1) **读草稿箱**：`GET https://www.zhihu.com/api/v4/articles/my_drafts`
   - **必须带 x-zse-96 签名**，免签名恒定 403 `code 10003 请升级客户端`。
   - 路径是 `my_drafts`，不是 `/api/articles/drafts`（后者直接 500，
     是「路径猜错」而不是「权限不够」）。
2) **发布**：`PUT https://zhuanlan.zhihu.com/api/articles/<id>/publish`，body `{}` 就行。
   - `column` 可以是 null —— **没有专栏也能发**（本号专栏数为 0，实测发成功）。
   - 错的路径长这样：`/api/v4/articles/<id>/publish` 两个域都返回 **HTML 404**
     （= 路由不存在）；对的路径返回 **JSON 404 `code 4041 文章未找到`**（= 路由在、
     只是 id 假）。**判断路由是否存在要看返回是 HTML 还是 JSON**，这是本脚本
     探接口时唯一可靠的判据。
3) **删除**：`DELETE /api/v4/articles/<id>`（www）与 `DELETE /api/articles/<id>`（zhuanlan）
   路由都在，同样是 JSON 404 表示路由存在。

⚠ 关于 `ERR_TICKET_NOT_EXIST`：**它常常只是「cookie 过期」的伪装**，不是缺什么
   creator ticket。2026-10-31 23:00 之前据此下了「纯 cookie 走不通」的错结论，
   扫码后同一条路径直接 200。**任何「接口不通」的结论，先复验 whoami。**

用法：
    python scripts/zhihu_creator.py cookies            # 从已登录的 CDP 浏览器取 cookie
    python scripts/zhihu_creator.py list               # 草稿箱
    python scripts/zhihu_creator.py articles           # 已发布
    python scripts/zhihu_creator.py publish --id 123   # 发布草稿
    python scripts/zhihu_creator.py delete  --id 123   # 删草稿
    python scripts/zhihu_creator.py check   --id 123   # 匿名访客视角核验是否已上线
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import zhihu_publish as Z          # noqa: E402  load_cookie / save_cookie / whoami
import zhihu_sign as S             # noqa: E402  sign()

WWW = "https://www.zhihu.com"
ZHUANLAN = "https://zhuanlan.zhihu.com"
DEFAULT_PORT = int(os.environ.get("CDP_ZHIHU_PORT") or 9227)


# ---------------------------------------------------------------- http

def _opener():
    # 直连：本机 HTTP_PROXY 会污染到 zhihu，历史上吃过这个亏
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ck(name, ck_dict=None):
    ck_dict = ck_dict or Z.load_cookie()
    return Z._ck(ck_dict, name)


def call(method, path, host=WWW, body=None, ck=None, sign=True, timeout=25):
    """发一个知乎请求。返回 (status, 文本)。

    `path` 要**带 query**——签名是对「路径+查询串」整体算的，少一段就 403。
    """
    ck = ck or Z.load_cookie()
    headers = {
        "User-Agent": Z.UA,
        "accept": "*/*",
        "x-requested-with": "fetch",
        "Cookie": ck["cookie_string"],
        "Referer": host + "/",
    }
    if sign:
        headers["x-zse-93"] = S.X_ZSE_93
        headers["x-zse-96"] = S.sign(path, _ck("d_c0", ck))
    data = None
    if method != "GET":
        data = json.dumps(body if body is not None else {}).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["x-xsrftoken"] = _ck("_xsrf", ck)
        headers["Origin"] = host
    req = urllib.request.Request(host + path, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        r = _opener().open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                     # noqa: BLE001
        return 0, repr(e)


def call_json(method, path, host=WWW, body=None, ck=None):
    st, raw = call(method, path, host=host, body=body, ck=ck)
    try:
        return st, json.loads(raw)
    except Exception:                                          # noqa: BLE001
        return st, {"_raw": raw}


def _err(st, d):
    """把知乎的错误结构拍成一行，方便打印。"""
    e = (d or {}).get("error") or {}
    if e:
        return "%s %s(code=%s)" % (st, e.get("message") or "", e.get("code") or "")
    raw = str((d or {}).get("_raw") or "")[:120]
    return "%s %s" % (st, raw)


# ---------------------------------------------------------------- cookies

def cmd_cookies(args):
    """从已登录的 CDP 浏览器整份取 cookie 落盘。

    ⚠ `document.cookie` 读不到 z_c0（HttpOnly），必须走 `Storage.getCookies`。
      这也是「浏览器明明登着、脚本却 401」最常见的原因。
    """
    import wechat_publish as wp

    cdp = wp.CDP(args.port)
    resp = cdp.send_browser("Storage.getCookies", {})
    cookies = (resp.get("result") or {}).get("cookies") or []
    if not cookies:
        raise SystemExit("浏览器里没有 cookie —— 端口 %s 是不是没登录？" % args.port)

    want = ("zhuanlan.zhihu.com", ".zhuanlan.zhihu.com", ".zhihu.com")
    rank = {"zhuanlan.zhihu.com": 0, ".zhuanlan.zhihu.com": 1, ".zhihu.com": 2}
    best = {}
    for c in cookies:
        dom = c.get("domain")
        if dom not in want:
            continue
        name, val = c.get("name"), c.get("value") or ""
        if name not in best or rank[dom] < rank[best[name][0]]:
            best[name] = (dom, val)
    pairs = [[n, v] for n, (d, v) in sorted(best.items())]
    if not any(n == "z_c0" for n, _ in pairs):
        raise SystemExit("取到 %d 个 cookie 但没有 z_c0 ⇒ 这个浏览器没登录" % len(pairs))
    Z.save_cookie(pairs)
    print("  条数 %d（含 z_c0 / d_c0 / _xsrf）" % len(pairs))
    try:
        me = Z.whoami()
        print("  whoami: %s" % (me.get("name") or me.get("id")))
    except Exception as e:                                     # noqa: BLE001
        print("  ⚠ whoami 仍失败：%s" % str(e)[:100])


# ---------------------------------------------------------------- read

def cmd_list(args):
    st, d = call_json("GET", "/api/v4/articles/my_drafts?offset=0&limit=%d" % args.limit)
    if st != 200:
        raise SystemExit("读草稿箱失败：" + _err(st, d))
    items = d.get("data") or []
    total = (d.get("paging") or {}).get("totals")
    print("草稿箱 %s 条（本次取 %d）" % (total, len(items)))
    for it in items:
        print("  %-22s %-6s %s" % (it.get("id"),
                                   "%s字" % it.get("content_words") if it.get("content_words") else "-",
                                   (it.get("title") or "(无标题)")[:44]))
    return items


def cmd_articles(args):
    token = args.token
    if not token:
        st, me = call_json("GET", "/api/v4/me")
        if st != 200:
            raise SystemExit("读不出账号信息，用 --token 指定 url_token：" + _err(st, me))
        token = me.get("url_token")
    path = ("/api/v4/members/%s/articles?limit=%d&offset=0&sort_by=created"
            % (token, args.limit))
    st, d = call_json("GET", path)
    if st != 200:
        raise SystemExit("读已发表失败：" + _err(st, d))
    items = d.get("data") or []
    print("已发表（%s）%s 条" % (token, (d.get("paging") or {}).get("totals")))
    for it in items:
        print("  %-22s %s" % (it.get("id"),
                              (it.get("title") or "")[:44]))
        print("      https://zhuanlan.zhihu.com/p/%s" % it.get("id"))
    return items


def cmd_check(args):
    """匿名访客视角：只带 d_c0（签名用），不带 z_c0（登录态）。"""
    ck = Z.load_cookie()
    d_c0 = _ck("d_c0", ck)
    path = "/api/v4/articles/%s" % args.id
    headers = {"User-Agent": Z.UA, "accept": "*/*", "x-requested-with": "fetch",
               "x-zse-93": S.X_ZSE_93, "x-zse-96": S.sign(path, d_c0),
               "Cookie": "d_c0=%s" % d_c0}
    req = urllib.request.Request(WWW + path)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        r = _opener().open(req, timeout=25)
        st, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        st, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                     # noqa: BLE001
        st, raw = 0, repr(e)
    print("匿名读取 -> %s" % st)
    if st != 200:
        print("  " + raw[:200])
        return False
    d = json.loads(raw)
    print("  标题  : %s" % d.get("title"))
    print("  状态  : %s / %s" % (d.get("state"), d.get("type")))
    print("  作者  : %s" % ((d.get("author") or {}).get("name")))
    print("  链接  : https://zhuanlan.zhihu.com/p/%s" % d.get("id"))
    print("  专栏  : %s" % (d.get("column") or "（无，允许）"))
    return d.get("state") == "published"


# ---------------------------------------------------------------- write

def cmd_publish(args):
    aid = str(args.id)
    # 先确认它确实在草稿箱 —— 避免把已发布的再发一次
    st, d = call_json("GET", "/api/v4/articles/my_drafts?offset=0&limit=50")
    in_box = st == 200 and any(str(x.get("id")) == aid for x in (d.get("data") or []))
    if not in_box and not args.force:
        print("⚠ %s 不在草稿箱前 50 条里，可能已发布或 id 写错；确认无误加 --force" % aid)
        return False
    st, d = call_json("PUT", "/api/articles/%s/publish" % aid, host=ZHUANLAN, body={})
    if st != 200:
        print("发布失败：" + _err(st, d))
        return False
    print("已发布：%s" % (d.get("title") or ""))
    print("  https://zhuanlan.zhihu.com/p/%s" % aid)
    return cmd_check(argparse.Namespace(id=aid))


def cmd_delete(args):
    aid = str(args.id)
    if not args.yes:
        st, d = call_json("GET", "/api/v4/articles/my_drafts?offset=0&limit=50")
        hit = next((x for x in (d.get("data") or []) if str(x.get("id")) == aid), None) \
            if st == 200 else None
        if not hit:
            print("⚠ 草稿箱前 50 条里没有 %s（已发布稿要 --yes 才删）" % aid)
            return False
        print("将删除草稿：%s | %s" % (aid, hit.get("title")))
        if not args.yes and not args.dry:
            raise SystemExit("确认删除请加 --yes")
    if args.dry:
        print("干跑：不实际删除")
        return True
    st, d = call_json("DELETE", "/api/v4/articles/%s" % aid)
    if st not in (200, 204):
        print("删除失败：" + _err(st, d))
        return False
    print("已删除 %s" % aid)
    return True


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="知乎创作中心 API 通道")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="CDP 端口（默认 %d，或环境变量 CDP_ZHIHU_PORT）" % DEFAULT_PORT)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("cookies", help="从已登录的 CDP 浏览器取 cookie 落盘")

    p = sub.add_parser("list", help="列草稿箱")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("articles", help="列已发表")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--token", help="url_token，不填自动取 /api/v4/me")

    p = sub.add_parser("publish", help="发布草稿")
    p.add_argument("--id", required=True, help="文章 id")
    p.add_argument("--force", action="store_true", help="不在草稿箱也强行发")

    p = sub.add_parser("delete", help="删草稿")
    p.add_argument("--id", required=True, help="文章 id")
    p.add_argument("--yes", action="store_true", help="真的删（默认只预览）")
    p.add_argument("--dry", action="store_true", help="只看不删")

    p = sub.add_parser("check", help="匿名访客视角核验")
    p.add_argument("--id", required=True)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0

    fn = globals()["cmd_" + args.cmd]
    r = fn(args)
    return 1 if r is False else 0


if __name__ == "__main__":
    sys.exit(main())
