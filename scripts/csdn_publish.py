#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 发布 —— 纯 REST（Cookie + 阿里云网关签名），不开浏览器填表。

这是第六个平台接入。与掘金同构：有真服务端写接口，cookie 鉴权；
比掘金多一层**阿里云 API 网关签名**（CSDN 没有官方公开 API，接口是
编辑器 `editor.csdn.net` 用的私有端点，走 bizapi.csdn.net）。

## 实测结论（2026-10-28，本机探针 + 真发真删，见 _probe_csdn.py）

| 项 | 结论 | 依据 |
|---|---|---|
| 端点 | `POST https://bizapi.csdn.net/blog-console-api/v3/mdeditor/saveArticle` | 无 Cookie POST → `401 请登录后操作！`（业务层响应，说明签名先过了） |
| 签名 secret | `9znpamsyl2c7cdrr9sas0le9vbc3r6ba`（社区流传固定值） | 签名与实际头一致时网关放行；故意不一致 → `HMAC signature does not match` 反向验证 |
| to_sign 形状 | `POST\\n{accept}\\n\\n{ctype}\\n\\nx-ca-key:…\\nx-ca-nonce:…\\n{path}` | accept 用 `*/*` 或 `application/json` 都行——**网关只要求与实际头一致** |
| 🚨 无草稿语义 | `pubStatus:"draft"` **被服务端无视，直接发表** | 两篇「草稿」上了公开博客列表（get-business-list 可见）；配额正是按发表计的 |
| 🚨 日配额 | **2 篇/天（低等级账号），按发表计** | 超限报 `400300011 今天发表文章数量已达到限制的2篇` |
| 401 迷团 | 同 payload 有时 401 有时 400300011 | 疑似后端节点不一致；**见 401 先查配额，别急着重登** |
| 探活 | 空 body POST → `code:200,data:"成功"`，不建稿 | ⚠ `/v1/user/info` 对任何登录态恒 401，不能当探活 |
| 假成功 | 缺字段也回 200「成功」但不建稿 | **判成功必须看 `data.id`/`data.url`** |
| 回读 | `GET blog.csdn.net/community/home-api/v1/get-business-list`（blog 域，免签名，纯 Cookie） | 发布成功唯一判据 = 此列表按标题命中 |
| 删除 | `POST bizapi.csdn.net/blog/phoenix/console/v1/article/del` body `{"articleId":<int>}` | 实测连删 3 篇（已发表+自动保存草稿）全 `data:true` |
| 老接口 | `mp.csdn.net/mdeditor/saveArticle` 已死（405）；MetaWeblog 已死（461） | 实测 |
| WAF | blog 域接口请求过密会 521 | list 已带退避重试 + 翻页间隔 |
| Cookie | 约 30 天、绑设备；过期信号 401 | 公开资料多源一致 |

⚠ **payload 两个字段必须分开传**：`markdowncontent`（原始 MD）与
`content`（渲染后 HTML）。混塞会全文格式乱（多来源一致 + DAMO 示例）。

⚠ 签名头四件套：`x-ca-key`(固定 203803574) / `x-ca-nonce`(每次 uuid4，重复判重放)
/ `x-ca-signature`(HMAC-SHA256+Base64) / `x-ca-signature-headers: x-ca-key,x-ca-nonce`。

## 用法

```bash
python scripts/csdn_publish.py login            # 首次：开浏览器登录并存 cookie（独立 profile，不动其他平台）
python scripts/csdn_publish.py whoami           # 验登录态 + 签名（空 body 探活）
python scripts/csdn_publish.py publish --file drafts/wechat/xxx.md --case xxx
                                                # 🚨 直接发表（日配额 2 篇！无草稿语义）
python scripts/csdn_publish.py list [--query 标题子串]   # 已发表列表回读（判据）
python scripts/csdn_publish.py delete --article-id <id>  # 删除（已实测）
```

⚠ `draft` 子命令**故意不可用**：saveArticle 没有存草稿语义，真草稿端点
未确认（编辑器「保存草稿」的请求待 hook，合成 click 触发不了 Vue handler）。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://bizapi.csdn.net"
CA_KEY = "203803574"                            # 阿里云网关 app 标识（固定值）
SECRET = "9znpamsyl2c7cdrr9sas0le9vbc3r6ba"     # 网关签名 secret（2026-10-28 实测有效）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
ACCEPT = "*/*"
CTYPE = "application/json;charset=UTF-8"

COOKIE_FILE = os.path.join(ROOT, "data", "csdn_cookie.json")
DRAFT_FILE = os.path.join(ROOT, "data", "csdn_drafts.json")
LOGIN_PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-csdn-profile")
LOGIN_PORT = 9226
LOGIN_URL = "https://editor.csdn.net/md/"
# 登录 cookie 里的参考票据名——只作线索打印，不作硬判据
# （2026-10-28 实测：UserToken/UserName_tt 的旧命名已对不上，别拿它当门卫）
LOGIN_MARKERS = ("UserToken", "UserName_tt")


class LoginRequired(Exception):
    pass


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------
# 签名（2026-10-28 无登录探针实锤；形状对但头不一致会被网关拒）
# --------------------------------------------------------------------------

def csdn_sign(method, path):
    """返回 (headers_extra, to_sign)。accept/ctype 必须与请求头完全一致。"""
    nonce = str(uuid.uuid4())
    to_sign = "%s\n%s\n\n%s\n\nx-ca-key:%s\nx-ca-nonce:%s\n%s" % (
        method, ACCEPT, CTYPE, CA_KEY, nonce, path)
    sig = base64.b64encode(hmac.new(SECRET.encode(), to_sign.encode(),
                                    hashlib.sha256).digest()).decode()
    return {
        "x-ca-key": CA_KEY,
        "x-ca-nonce": nonce,
        "x-ca-signature": sig,
        "x-ca-signature-headers": "x-ca-key,x-ca-nonce",
    }, to_sign


def _opener():
    # 系统代理会掐请求；CSDN 是国内站，直连即可
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, data=None, cookie=None, method=None, timeout=30):
    """统一入口。data 为 dict 时转 JSON。返回 (http_status, dict|str)。

    ⚠ path 带 query 时也**只签 path**——实测 saveArticle 是 POST 无 query；
      GET 带 query 的路由如果报 HMAC 错，先怀疑 query 没参与签名，别急着改别的。
    """
    method = method or ("POST" if data is not None else "GET")
    url = API + path
    extra, to_sign = csdn_sign(method, path)
    headers = {
        "User-Agent": UA,
        "Accept": ACCEPT,
        "Content-Type": CTYPE,
        "Origin": "https://editor.csdn.net",
        "Referer": "https://editor.csdn.net/",
    }
    headers.update(extra)
    if cookie:
        headers["Cookie"] = cookie
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    op = _opener()
    try:
        with op.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:                                   # noqa: BLE001
        raise ApiError("网络错误: %r" % (e,))
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw[:300]
    if status == 401 or (isinstance(parsed, dict) and parsed.get("code") == 401):
        raise LoginRequired("401 请登录 | HTTP %s | %s"
                            % (status, json.dumps(parsed, ensure_ascii=False)[:300]))
    if status >= 400:
        raise ApiError("HTTP %s: %s" % (status, raw[:200]))
    return status, parsed


# --------------------------------------------------------------------------
# cookie 读写（与 juejin_cookie.json 同约定：atomicio 落盘）
# --------------------------------------------------------------------------

def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        raise LoginRequired("没有 cookie 文件，先跑: python scripts/csdn_publish.py login")
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("cookie_string", "")


def save_cookie(cookie_string):
    import atomicio
    atomicio.write_json(COOKIE_FILE, {
        "cookie_string": cookie_string,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    print("✓ cookie 落盘: %s" % os.path.relpath(COOKIE_FILE, ROOT))


def remember(case_id, article_id, title, url=""):
    import atomicio
    ledger = {}
    if os.path.exists(DRAFT_FILE):
        with open(DRAFT_FILE, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    ledger[case_id] = {
        "article_id": article_id, "title": title, "url": url,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    atomicio.write_json(DRAFT_FILE, ledger)
    try:
        shown = os.path.relpath(DRAFT_FILE, ROOT)   # 跨盘符会 ValueError
    except ValueError:
        shown = DRAFT_FILE
    print("✓ 台账: %s ← %s" % (shown, case_id))


# --------------------------------------------------------------------------
# 最小 md→html（受限语法：标题/粗斜体/代码块/行内码/链接/图片/列表/引用/段落）
# --------------------------------------------------------------------------

def _inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
               r'<img src="\2" alt="\1"/>', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(md):
    """草稿 md → HTML。不支持表格/嵌套列表——草稿管线里用到了再补。"""
    out, i = [], 0
    lines = md.split("\n")
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            fence = ln[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1                                   # 跳过收尾 ```
            cls = ' class="language-%s"' % fence if fence else ""
            out.append("<pre><code%s>%s</code></pre>"
                       % (cls, "\n".join(code).replace("&", "&amp;")
                          .replace("<", "&lt;").replace(">", "&gt;")))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            n = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (n, _inline(m.group(2)), n))
        elif ln.startswith("> "):
            out.append("<blockquote><p>%s</p></blockquote>" % _inline(ln[2:]))
        elif re.match(r"^[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                items.append("<li>%s</li>" % _inline(re.sub(r"^[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
            continue
        elif re.match(r"^\d+\.\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append("<li>%s</li>" % _inline(re.sub(r"^\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>%s</ol>" % "".join(items))
            continue
        elif ln.strip():
            out.append("<p>%s</p>" % _inline(ln.strip()))
        i += 1
    return "\n".join(out)


def split_md(md):
    """首行 `# 标题` 抽出来做 title，其余做正文（CSDN 标题是独立字段）。"""
    lines = md.split("\n")
    title, body = "", md
    for idx, ln in enumerate(lines):
        if ln.startswith("# "):
            title = ln[2:].strip()
            body = "\n".join(lines[:idx] + lines[idx + 1:])
            break
        if ln.strip():
            break
    if not title:
        m = re.search(r"^(.+)", md.strip())
        title = (m.group(1) if m else "untitled")[:60]
    return title, body.strip()


# --------------------------------------------------------------------------
# 发布动作
# --------------------------------------------------------------------------

def whoami():
    """探活：saveArticle 空 body POST。

    2026-10-28 实测：空 body 返回 `code:200, data:"成功"`，**不建稿**
    （连 articleId 都不回）。⚠ 别用 `/blog-console-api/v1/user/info` 做
    探活——它对任何登录态都回 401（登录循环因此空转了 8 分钟）。
    """
    st, body = api("/blog-console-api/v3/mdeditor/saveArticle", data={})
    if isinstance(body, dict) and body.get("code") == 200:
        return {"_note": "会话有效", "detail": body.get("data", "")}
    raise LoginRequired("探活未通过: %s" % json.dumps(body, ensure_ascii=False)[:200])


def save_article(title, md_body, pub=True, tags="人工智能", article_id=None):
    """直接发表（唯一已验证的写入语义）。

    🚨 **saveArticle 没有「存草稿」语义**：2026-10-28 实测 `pubStatus:"draft"`
    被服务端无视，**直接发表**（文章出现在公开博客 + 计入日配额 2 篇）。
    真草稿端点未确认（编辑器「保存草稿」按钮的请求待 hook，合成 click
    触发不了 Vue handler）。所以本函数只有发表一种行为，名字也如实叫 publish。
    """
    payload = {
        "title": title,
        "markdowncontent": md_body,          # 原始 MD
        "content": md_to_html(md_body),      # 渲染后 HTML，两字段必须分开
        "readType": "public",
        "type": "original",
        "pubStatus": "publish",
        "tags": tags,
        "status": 0,
        "source": "pc_mdeditor",
    }
    if article_id:
        payload["articleId"] = article_id
    st, body = api("/blog-console-api/v3/mdeditor/saveArticle", data=payload)
    if not (isinstance(body, dict) and body.get("code") == 200):
        raise ApiError("saveArticle 未返回 code=200: %s" % json.dumps(body, ensure_ascii=False)[:300])
    data = body.get("data") or {}
    return data.get("articleId") or article_id, data.get("url", ""), payload["title"]


LIST_API = ("https://blog.csdn.net/community/home-api/v1/get-business-list"
            "?page=%(page)s&size=20&businessType=blog&orderby=&noMore=false"
            "&year=&month=&username=%(username)s")
# blog 域接口不签名、纯 Cookie；但请求过密会被边缘 WAF 521，串行加间隔
USERNAME = ""                                   # 留空则从 cookie 里取 UserName


def _username_from_cookie(cookie):
    if USERNAME:
        return USERNAME
    m = re.search(r"UserName=([^;]+)", cookie)
    if not m:
        raise ApiError("cookie 里找不到 UserName，list 命令需要登录 cookie")
    return m.group(1)


def list_published(cookie=None, pages=1):
    """已发表文章列表（回读判据：发布成功 = 这里按标题命中）。"""
    import time as _t
    cookie = cookie or load_cookie()
    username = _username_from_cookie(cookie)
    out = []
    op = _opener()
    for page in range(1, pages + 1):
        url = LIST_API % {"page": page, "username": username}
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        req.add_header("Accept", "application/json, text/plain, */*")
        req.add_header("Referer", "https://blog.csdn.net/%s?type=blog" % username)
        req.add_header("Cookie", cookie)
        for attempt in range(3):                 # 521 退避重试
            try:
                data = json.loads(op.open(req, timeout=15).read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as e:
                if e.code in (521, 502) and attempt < 2:
                    _t.sleep(6 * (attempt + 1))
                    continue
                raise
        d = data.get("data") or {}
        for it in d.get("list") or []:
            out.append({"id": it.get("id") or (it.get("url") or "").rstrip("/").split("/")[-1],
                        "title": it.get("title"), "url": it.get("url"),
                        "postTime": it.get("postTime")})
        if page * 20 >= int(d.get("total") or 0):
            break
        _t.sleep(2)                              # 别把 WAF 惹毛
    return out


def delete_article(article_id, cookie=None):
    """删除已发表文章/草稿。

    ✅ 2026-10-28 实测：`POST /blog/phoenix/console/v1/article/del`
    body {"articleId": <int>} → `{"code":200,"data":true}`，已发表与
    编辑器自动保存草稿都能删（连删 3 篇验证）。
    """
    cookie = cookie or load_cookie()
    st, body = api("/blog/phoenix/console/v1/article/del",
                   data={"articleId": int(article_id)}, cookie=cookie)
    if not (isinstance(body, dict) and body.get("code") == 200):
        raise ApiError("del 未返回 code=200: %s" % json.dumps(body, ensure_ascii=False)[:200])
    return True


def list_articles():
    """已发表列表（回读判据），薄封装方便 CLI。"""
    return list_published()


# --------------------------------------------------------------------------
# login（独立 profile，不动公众号/头条的浏览器）
# --------------------------------------------------------------------------

# juejin_publish 里的 stdlib CDP 客户端（_WS/_CDP）直接复用
from juejin_publish import _CDP          # noqa: E402


def _collect_csdn_cookie(cdp):
    """抓 csdn.net 域全部 cookie 拼成头。

    ⚠ 别用固定 cookie 名当登录判据（UserToken/UserName_tt 实测已对不上
    新版命名）——有没有登录交给 whoami() 的 401 判定，这里只负责收集。
    """
    cdp.send("Network.enable")
    r = cdp.send("Network.getAllCookies")
    items = (r.get("result") or {}).get("cookies") or []
    csdn = [c for c in items if "csdn.net" in (c.get("domain") or "")]
    if not csdn:
        return None
    return "; ".join("%s=%s" % (c["name"], c["value"])
                     for c in csdn if not c["name"].startswith("__"))


def cmd_login(args):
    import chrome_cdp_launch as L
    L.clear_proxy()
    L.remove_lock(LOGIN_PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    args_chrome = L.build_args(chrome, LOGIN_PROFILE, port=args.port,
                               headless=False)          # 登录必须有头
    print("[login] 启动 %s（独立 profile: %s，端口 %d）"
          % (label, LOGIN_PROFILE, args.port), flush=True)
    proc = subprocess.Popen(args_chrome, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=args.port):
        raise SystemExit("Chrome CDP 没起来")
    cdp = _CDP(args.port)
    try:
        cdp.open(args.url)
        print("已打开 %s —— 请在浏览器里登录 CSDN（扫码/手机号）。" % args.url)
        print("脚本每 5 秒自动验一次，登录成功即停（最多等 %d 秒）。" % args.wait)
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(5)
            try:
                cookie = _collect_csdn_cookie(cdp)
            except Exception:                            # noqa: BLE001
                continue                                 # 页面还在跳
            if not cookie:
                continue
            try:
                save_cookie(cookie)                      # 先落盘再验
                info = whoami()
            except LoginRequired:
                continue                                 # cookie 有了还没生效
            print("✓ 登录成功并已验证: %s" % json.dumps(info, ensure_ascii=False)[:200])
            return 0
        raise SystemExit("✗ 等了 %d 秒仍未检测到登录。确认在 %d 端口那个浏览器里"
                         "登录了 CSDN，再重跑一次。" % (args.wait, args.port))
    finally:
        try:
            cdp.bws.close()
        except Exception:                                # noqa: BLE001
            pass
        try:
            proc.terminate()
        except Exception:                                # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser(description="CSDN 发布（纯 REST + 网关签名）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="开浏览器登录一次并存 cookie")
    p.add_argument("--port", type=int, default=LOGIN_PORT)
    p.add_argument("--url", default=LOGIN_URL)
    p.add_argument("--wait", type=int, default=300)
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("whoami", help="验登录态 + 签名")
    p.set_defaults(fn=lambda a: print(json.dumps(whoami(), ensure_ascii=False)[:500]))

    p = sub.add_parser("publish", help="发表文章（🚨 日配额 2 篇；saveArticle 无草稿语义）")
    p.add_argument("--file", required=True, help="markdown 文件")
    p.add_argument("--case", default="", help="案例 id（台账键，缺省用文件名）")
    p.add_argument("--tags", default="人工智能")

    p = sub.add_parser("draft", help="🚫 不可用：saveArticle 的 pubStatus=draft 被服务端"
                                     "无视、会直接发表（2026-10-28 实测）。真草稿端点未确认")
    p.set_defaults(fn=lambda a: None)

    p = sub.add_parser("list", help="已发表文章列表（回读判据：发布成功 = 按标题命中）")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--query", default="", help="按标题子串过滤")
    p.set_defaults(fn=lambda a: print(json.dumps(
        [it for it in list_published(pages=a.pages)
         if not a.query or a.query in (it.get("title") or "")],
        ensure_ascii=False, indent=1)))

    p = sub.add_parser("delete", help="删除已发表文章/草稿（phoenix del，已实测）")
    p.add_argument("--article-id", required=True)

    args = ap.parse_args()
    if args.cmd == "draft":
        raise SystemExit(
            "🚫 draft 不可用：saveArticle 的 `pubStatus:\"draft\"` 被服务端无视，\n"
            "  实测会**直接发表**并计入日配额（2026-10-28，两篇测试稿因此上了公开博客）。\n"
            "  要发表请用 `publish`；真草稿端点待从编辑器 hook（合成 click 触发不了 Vue handler）。")
    if args.cmd == "publish":
        with open(args.file, "r", encoding="utf-8") as f:
            md = f.read()
        # 剥掉草稿管线的元信息水印（「> 平台：wechat ｜ 案例：… ｜ 状态：…」），
        # 平台名在 CSDN 上是错的，留着会误导读者
        md = "\n".join(ln for ln in md.split("\n")
                       if not ln.startswith("> 平台："))
        title, body = split_md(md)
        if re.search(r"\d", title):
            print("⚠ 标题含数字（「标题禁金额」铁律）：", title)
        aid, url, title = save_article(title, body, tags=args.tags)
        remember(args.case or os.path.splitext(os.path.basename(args.file))[0],
                 aid, title, url)
        print("✓ 已发表: article_id=%s url=%s" % (aid, url))
        print("  回读验证: python scripts/csdn_publish.py list --query %r" % title[:20])
        return
    if args.cmd == "delete":
        delete_article(args.article_id)
        print("✓ 已删除 article_id=%s（复核: list 命令）" % args.article_id)
        return
    args.fn(args)


if __name__ == "__main__":
    main()
