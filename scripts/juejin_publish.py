#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掘金（juejin.cn）发布 —— **纯 REST API，不点 DOM、不开浏览器填表**。

这是四平台里唯一「有真服务端写接口」的一家，跟另外三个的差别要写清楚，
免得后来者照着头条/B站那套 CDP 思路来找编辑器和按钮（根本不存在）：

| 平台 | 写入方式 | 正文格式 |
|---|---|---|
| 公众号 | CDP 填编辑器 | HTML |
| 小红书 | CDP 填编辑器（草稿在 IndexedDB） | 图片 + 文本 |
| 今日头条 | CDP + `article/publish` API | 富文本 |
| B站专栏 | `draft/add` API + CDP 点发布 | HTML |
| **掘金** | **纯 REST，cookie 鉴权** | **Markdown** |

## 实测结论（2026-10-25，本机探测，非照抄网文）

鉴权只要 cookie（`Cookie:` 头），无签名、无 csrf、无 WBI。

八个候选路由逐个打过，靠 `err_no` 区分「路由不存在」与「需登录」：

| 路由 | 判定 | 依据 |
|---|---|---|
| `POST /content_api/v1/article_draft/create` | ✅ 存在 | `403 must login` |
| `POST /content_api/v1/article_draft/update` | ✅ 存在 | `403 must login` |
| `POST /content_api/v1/article_draft/delete` | ✅ 存在 | `403 must login` |
| `POST /content_api/v1/article_draft/query_list` | ✅ 存在 | `403 must login` |
| `POST /content_api/v1/article_draft/list_by_user` | ✅ 存在 | `403 must login` |
| `POST /content_api/v1/article/publish` | ✅ 存在 | `403 must bind phone`（比 must login 更深一层） |
| `POST /content_api/v1/article_draft/publish` | ❌ **不存在** | `err_no:2 请求路由不存在` |
| `GET  /user_api/v1/user/get` | ✅ 存在 | `err_no:0`（未登录时 data=null） |

⚠ **网上流传的 `article_draft/publish` 是错的**（CSDN 那篇多平台稿里写的），
真发布走 `article/publish` + `draft_id`。照抄会得到 `err_no:2`，而 `err_no:2`
和「参数不对」长得一样，很容易误判成自己参数写错。

⚠ **探测写接口别用空体**：搜索结果里那篇实测用 `{}` 试探 `create`，
结果是 `err_no:0` —— 真的建出一份空草稿，还得靠 delete 擦屁股。
无登录态时写接口一律 403 是安全的；**一旦登录了，探测就只能用只读接口**
（`whoami` / `list` / `categories`）。

分类与标签接口（`tag_api/v1/query_*`）**免登录可用**，所以分类 ID 能直接
拉真值写死在下面，不用靠猜。

## 用法

```bash
python scripts/juejin_publish.py login                 # 首次：开浏览器登录并存 cookie
python scripts/juejin_publish.py whoami                # 验登录态
python scripts/juejin_publish.py categories            # 八大分类真实 ID
python scripts/juejin_publish.py draft --file out/articles/mcdpoints.md      # 独立稿存草稿
python scripts/juejin_publish.py draft --case mcdpoints                       # 案例稿存草稿
python scripts/juejin_publish.py list                  # 草稿箱
python scripts/juejin_publish.py publish --draft-id <id>                      # 真发布
```

**正文吃 Markdown**（`edit_type:10` + `mark_content`）—— 这跟公众号/头条/B站
都不一样，那三家都要 HTML。所以掘金的稿**不需要 HTML 母版**，`out/articles/<id>.md`
直接就能喂。
"""

import argparse
import base64
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://api.juejin.cn"
# 掘金所有接口都带这对 query，缺了会被当爬虫（spider=0 声明非爬虫）
QS = "?aid=2608&spider=0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

COOKIE_FILE = os.path.join(ROOT, "data", "juejin_cookie.json")
DRAFT_FILE = os.path.join(ROOT, "data", "juejin_drafts.json")
ARTICLES = os.path.join(ROOT, "out", "articles")

# 八大分类真实 ID（2026-10-25 从 tag_api/v1/query_category_list 拉到，免登录）
CATEGORIES = {
    "后端":     "6809637769959178254",
    "前端":     "6809637767543259144",
    "Android": "6809635626879549454",
    "iOS":     "6809635626661445640",
    "人工智能": "6809637773935378440",
    "开发工具": "6809637771511070734",
    "代码人生": "6809637776263217160",
    "阅读":     "6809637772874219534",
}
DEFAULT_CATEGORY = "后端"
# 后端分类下的真实标签 ID（同上，免登录接口拉到）
KNOWN_TAGS = {
    "GitHub": "6809640375880253447",
    "Git":    "6809640373774712840",
    "Go":     "6809640364677267469",
    "Node.js": "6809640361531539470",
    "Python": "6809640391805526030",
}


class LoginRequired(Exception):
    pass


class ApiError(Exception):
    pass


# ---- cookie --------------------------------------------------------------
def load_cookie():
    """三通道取 cookie：环境变量 > 落盘文件 > 无。

    为什么不做「每次走 CDP 现取」：掘金 cookie 是普通会话 cookie，
    不像头条的 `tt-anti-token` 那样必须现算 —— 存下来能用很久。
    CDP 只在 `login` 那一步用。
    """
    c = os.environ.get("JUEJIN_COOKIE", "").strip()
    if c:
        return c
    if os.path.isfile(COOKIE_FILE):
        try:
            d = json.load(open(COOKIE_FILE, encoding="utf-8"))
            return (d or {}).get("cookie", "").strip()
        except (OSError, ValueError):
            return ""
    return ""


def save_cookie(cookie):
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    import atomicio
    atomicio.write_json(COOKIE_FILE, {
        "cookie": cookie,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })


def cookie_header(cookie):
    """把各种输入形态归一成 Cookie 头的值。

    允许直接粘浏览器的整条 `Cookie:` 头，也允许只粘 `sessionid=xxx` 一段。
    """
    c = (cookie or "").strip()
    c = re.sub(r"^cookie:\s*", "", c, flags=re.I).strip()
    return c


# ---- HTTP ----------------------------------------------------------------
def _opener():
    # 127.0.0.1 要绕代理；外网走系统代理（本机出口是境内，掘金可直连）
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, data=None, cookie=None, method=None, timeout=30):
    """打一个掘金接口。返回 data（dict/list）。失败抛 ApiError。"""
    cookie = cookie_header(cookie or load_cookie())
    url = API + path + QS
    method = method or ("POST" if data is not None else "GET")
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/json")
    req.add_header("Referer", "https://juejin.cn/")
    req.add_header("Origin", "https://juejin.cn")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        r = _opener().open(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    try:
        j = json.loads(raw)
    except ValueError:
        raise ApiError("非 JSON 响应：%s" % raw[:200])
    eno, emsg = j.get("err_no"), j.get("err_msg", "")
    if eno == 0:
        return j.get("data")
    if eno == 403 or "must login" in emsg:
        raise LoginRequired("未登录或 cookie 过期（%s）" % emsg)
    raise ApiError("err_no=%s %s" % (eno, emsg))


def whoami(cookie=None):
    return api("/user_api/v1/user/get", None, cookie)


# ---- 正文 ----------------------------------------------------------------
def read_md(path):
    if not os.path.isfile(path):
        raise SystemExit("✗ Markdown 不存在：%s" % path)
    return open(path, encoding="utf-8").read()


def split_md(md, default_title=""):
    """分出 (title, body)。

    掘金有独立标题字段，正文里的首个 H1 要剥掉，否则页面上会出现两个大标题。
    顺带把 `# ` 降级保留后续层级（h2/h3 在掘金正文里是正常的小标题）。
    """
    title = default_title
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        if not title:
            title = lines[0].lstrip()[2:].strip()
        lines = lines[1:]
    body = "\n".join(lines).strip()
    return (title or "未命名"), body


def brief_of(body, limit=100):
    """摘要。掘金建议 50–100 字，取正文首个自然语言段落。"""
    for p in body.splitlines():
        p = p.strip()
        if not p or p.startswith(("#", ">", "|", "-", "*", "```")):
            continue
        p = re.sub(r"\*\*|`|\[|\]\([^)]*\)", "", p).strip()
        if len(p) >= 20:
            return p[:limit]
    return body[:limit]


# ---- 草稿 ----------------------------------------------------------------
def create_draft(title, body, brief="", category=None, tags=None,
                 cover="", cookie=None):
    """存一份草稿。返回 draft_id。"""
    cat = CATEGORIES.get(category or DEFAULT_CATEGORY)
    if not cat:
        raise SystemExit("✗ 未知分类：%s（可选 %s）"
                         % (category, "/".join(CATEGORIES)))
    tag_ids = []
    for t in (tags or []):
        tid = KNOWN_TAGS.get(t, t if re.fullmatch(r"\d{10,}", t or "") else "")
        if not tid:
            print("  [warn] 标签 %r 查不到 ID，跳过（用 tags 子命令查真值）" % t)
            continue
        tag_ids.append(tid)
    # ⚠ 普通账号只能带 1 个标签，多传会被截断或报错
    if len(tag_ids) > 1:
        print("  [warn] 掘金普通账号只接受 1 个标签，已取第一个：%s"
              % (tags or [])[0])
        tag_ids = tag_ids[:1]

    payload = {
        "category_id": cat,
        "tag_ids": tag_ids,
        "title": title[:100],
        "brief_content": brief[:100],
        "edit_type": 10,              # 10 = Markdown（别改，改了正文当 HTML 解析）
        "mark_content": body,
        "cover_image": cover or "",
        "link_url": "",
        "theme_ids": [],
        "html_content": "deprecated",  # 前端就这么传，留着避免字段缺失
    }
    data = api("/content_api/v1/article_draft/create", payload, cookie)
    did = (data or {}).get("id") or (data or {}).get("draft_id")
    if not did:
        raise ApiError("建了草稿但拿不到 id：%s" % json.dumps(data)[:200])
    return did


def list_drafts(cookie=None, limit=20):
    """草稿箱列表。

    ⚠ `user_id` 必须是 int64 或干脆不传：传空字符串会得到
      `err_no=2 invalid use of ,string struct tag, trying to unmarshal "" into int64`
      —— 这个报错长得像「路由/参数类型不对」，很容易误判成路由不存在。
    """
    data = api("/content_api/v1/article_draft/query_list",
               {"cursor": "0", "limit": limit, "sort_type": 0},
               cookie)
    return data or []


def publish_draft(draft_id, cookie=None):
    """真发布。走的不是 draft/publish（那个路由不存在），是 article/publish。"""
    return api("/content_api/v1/article/publish",
               {"draft_id": str(draft_id), "sync_to_org": False,
                "column_ids": [], "theme_ids": []}, cookie)


def delete_draft(draft_id, cookie=None):
    return api("/content_api/v1/article_draft/delete",
               {"id": str(draft_id)}, cookie)


def remember(slug, draft_id, title):
    """本地台账，供适配器判幂等（掘金草稿只在远端，本地没记录就会重发）。"""
    db = {}
    if os.path.isfile(DRAFT_FILE):
        try:
            db = json.load(open(DRAFT_FILE, encoding="utf-8")) or {}
        except (OSError, ValueError):
            db = {}
    db[str(slug)] = {
        "draft_id": str(draft_id),
        "title": title,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": "https://juejin.cn/editor/drafts/%s" % draft_id,
    }
    import atomicio
    atomicio.write_json(DRAFT_FILE, db)


def known_ids():
    if not os.path.isfile(DRAFT_FILE):
        return set(), {}
    try:
        db = json.load(open(DRAFT_FILE, encoding="utf-8")) or {}
    except (OSError, ValueError):
        return set(), {}
    return set(db.keys()), db


# ---- 登录（唯一用到 CDP 的地方）------------------------------------------
class _WS:
    """极简 WebSocket 客户端（stdlib）。

    项目是零第三方依赖风格，不装 websocket-client。只实现够 CDP 用的部分：
    文本帧收发 + 126/127 长度分支。**服务端帧不 mask**，别按客户端帧解。
    """

    def __init__(self, url, timeout=20):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)$", url)
        if not m:
            raise RuntimeError("bad ws url: %s" % url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        hs = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
              "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
              "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(hs.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)

    def _recv_frame(self):
        def rd(n):
            out = b""
            while len(out) < n:
                c = self.sock.recv(n - len(out))
                if not c:
                    raise ConnectionError("ws closed")
                out += c
            return out
        h = rd(2)
        op = h[0] & 0x0F
        n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", rd(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", rd(8))[0]
        return op, rd(n) if n else b""

    def recv_text(self):
        """收一条完整文本消息（跳过 ping/pong/close 等控制帧）。"""
        while True:
            op, payload = self._recv_frame()
            if op == 0x1:
                return payload.decode("utf-8", "replace")
            if op == 0x8:
                raise ConnectionError("ws closed by peer")

    def send_text(self, text):
        data = text.encode("utf-8")
        n = len(data)
        hdr = bytes([0x81])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(hdr + masked)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _CDP:
    """够用的 CDP：开一个页面、eval 一段 JS、取全部 cookie。"""

    def __init__(self, port):
        self.port = port
        req = urllib.request.Request("http://127.0.0.1:%d/json/version" % port)
        # 本机 127.0.0.1 必须绕过系统代理，否则连调试端口会被掐
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ver = json.loads(op.open(req, timeout=10).read().decode())
        self.bws = _WS(ver["webSocketDebuggerUrl"])
        self._id = 0
        self.ws = None

    def _http(self, path):
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        r = op.open("http://127.0.0.1:%d%s" % (self.port, path), timeout=10)
        return json.loads(r.read().decode())

    def send_browser(self, method, params=None):
        self._id += 1
        self.bws.send_text(json.dumps({"id": self._id, "method": method,
                                       "params": params or {}}))
        while True:
            r = json.loads(self.bws.recv_text())
            if r.get("id") == self._id:
                return r

    def open(self, url):
        r = self.send_browser("Target.createTarget", {"url": url})
        tid = r["result"]["targetId"]
        for _ in range(20):
            for t in self._http("/json"):
                if t.get("id") == tid and t.get("webSocketDebuggerUrl"):
                    self.ws = _WS(t["webSocketDebuggerUrl"])
                    return tid
            time.sleep(0.5)
        raise RuntimeError("target 未就绪")

    def send(self, method, params=None):
        self._id += 1
        self.ws.send_text(json.dumps({"id": self._id, "method": method,
                                      "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv_text())
            if r.get("id") == self._id:
                return r

    def eval(self, js):
        r = self.send("Runtime.evaluate",
                      {"expression": js, "returnByValue": True,
                       "awaitPromise": True})
        res = r.get("result", {}).get("result", {})
        return res.get("value")

    def cookies(self):
        """用 CDP 的 Network.getAllCookies。

        ⚠ 不能读 `document.cookie`：掘金的登录票据是 HttpOnly，
          页面 JS 根本看不到，读出来会是空的。
        """
        self.send("Network.enable")
        r = self.send("Network.getAllCookies")
        items = (r.get("result") or {}).get("cookies") or []
        keep = [c for c in items if "juejin" in (c.get("domain") or "")]
        if not keep:
            keep = items
        return "; ".join("%s=%s" % (c.get("name"), c.get("value"))
                         for c in keep if c.get("name"))

    def close(self):
        for w in (self.ws, self.bws):
            if w:
                try:
                    w.close()
                except Exception:
                    pass


def cmd_login(args):
    """开浏览器让用户登录一次，抓 cookie 落盘。

    **轮询而不是干等**：登录是人工动作，快则 10 秒、慢则几分钟。写死睡够
    `wait` 秒再抓，要么让人白等，要么还没登录就抓了个空。
    所以每 5 秒抓一次 cookie 并打 `whoami` 验真 —— 验通即停。
    """
    cdp = _CDP(args.port)
    try:
        cdp.open(args.url)
        print("已打开 %s —— 请在浏览器里完成登录（扫码/手机号）。" % args.url)
        print("脚本每 5 秒自动验一次，登录成功即停（最多等 %d 秒）。"
              % args.wait)
        deadline = time.time() + args.wait
        seen = ""
        while time.time() < deadline:
            time.sleep(5)
            try:
                cookie = cdp.cookies()
            except Exception:                              # noqa: BLE001
                continue                     # 页面还在跳，下次再试
            if not cookie or cookie == seen:
                continue
            seen = cookie
            try:
                u = whoami(cookie)
            except (LoginRequired, ApiError):
                continue                     # cookie 有了但还没生效
            if u and u.get("user_id"):
                save_cookie(cookie)
                print("✓ 登录成功：%s（user_id=%s）"
                      % (u.get("user_name"), u.get("user_id")))
                print("✓ cookie 落盘：%s" % os.path.relpath(COOKIE_FILE, ROOT))
                return 0
        raise SystemExit("✗ 等了 %d 秒仍未检测到登录。确认在 %d 端口那个浏览器里"
                         "登录了掘金，再重跑一次。" % (args.wait, args.port))
    finally:
        cdp.close()


def _normalize_cookie(raw):
    """把「手机端弄到的任何形态」归一成 Cookie 头的值。

    三种常见输入都能吃：
      1. 抓包 App / 浏览器复制出来的整条请求头（可能带 `Cookie: ` 前缀、换行）
      2. 只粘一段 `sessionid=xxx; sid_tt=yyy`
      3. CDP `Network.getAllCookies` 导出的 JSON 数组（[{"name":..,"value":..}]）
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            return "; ".join("%s=%s" % (c.get("name"), c.get("value"))
                             for c in arr if c.get("name"))
        except (ValueError, AttributeError):
            pass
    out = []
    for line in raw.splitlines():
        line = re.sub(r"^cookie://s*", "", line.strip(), flags=re.I).strip()
        if "=" in line:
            out.append(line)
    return "; ".join(out)


def cmd_set_cookie(args):
    """不在这台电脑前时的登录通道：把别处拿到的 Cookie 直接灌进来。

    场景：人在外面只有手机，`login` 那条 CDP 路走不了（需要本机 Chrome）。
    手机上登录完掘金，把 juejin.cn 的 Cookie 头粘过来即可 —— 之后 draft /
    publish 全部照常工作，不用等回电脑。
    """
    raw = args.cookie
    if not raw or raw == "-":
        raw = sys.stdin.read()
    cookie = _normalize_cookie(raw)
    if not cookie:
        raise SystemExit("✗ 没解析出任何 name=value 片段，检查一下粘贴内容")
    low = cookie.lower()
    miss = [k for k in ("sessionid", "sid_tt", "uid_tt") if k not in low]
    if miss:
        print("⚠ 没看到 %s，可能只是游客 cookie —— 先照样验一次" % "/".join(miss))
    if args.dry:
        print("dry：%d 字节，%d 项" % (len(cookie), cookie.count("=")))
        return 0
    save_cookie(cookie)
    print("✓ 已写入 %s" % os.path.relpath(COOKIE_FILE, ROOT))
    try:
        u = whoami(cookie)
    except (LoginRequired, ApiError) as e:
        print("✗ 校验失败：%s" % e)
        print("  多半是复制时截断了，或只抓到非登录 cookie。")
        return 1
    if not u:
        print("✗ 接口通但 data 为 null —— 这不是登录态（游客 cookie）")
        return 1
    print("✓ 登录态有效：%s（user_id=%s）" % (u.get("user_name"), u.get("user_id")))
    return 0


def cmd_whoami(args):
    try:
        u = whoami()
    except LoginRequired as e:
        print("✗ %s" % e)
        print("  先跑：python scripts/juejin_publish.py login")
        return 1
    if not u:
        print("✗ 未登录（接口通，但 data 为 null）")
        return 1
    print("✓ %s（user_id=%s，level=%s）"
          % (u.get("user_name"), u.get("user_id"), u.get("level")))
    return 0


def cmd_categories(args):
    data = api("/tag_api/v1/query_category_list", {})
    for c in data or []:
        cc = c.get("category", {})
        print("  %-10s %s" % (cc.get("category_name"), cc.get("category_id")))
    return 0


def cmd_tags(args):
    cat = CATEGORIES.get(args.category)
    if not cat:
        raise SystemExit("✗ 未知分类：%s" % args.category)
    data = api("/tag_api/v1/query_tag_list",
               {"cursor": "0", "limit": args.limit, "sort_type": 0,
                "category_id": cat})
    for t in data or []:
        tg = t.get("tag", {})
        print("  %-24s %s" % (tg.get("tag_name"), tg.get("tag_id")))
    return 0


def cmd_draft(args):
    """存草稿。掘金正文吃 Markdown，所以独立稿直接给 .md 就行，不需要 HTML。"""
    if args.file:
        md_path = args.file
        slug = os.path.splitext(os.path.basename(md_path))[0]
    elif args.case:
        md_path = os.path.join(ARTICLES, args.case + ".md")
        slug = args.case
    else:
        raise SystemExit("✗ 给 --file 或 --case 之一")

    md = read_md(md_path)
    title, body = split_md(md, args.title or "")
    if not body:
        raise SystemExit("✗ 正文是空的：%s" % md_path)
    brief = args.brief or brief_of(body)

    print("掘金草稿：%s" % os.path.relpath(md_path, ROOT))
    print("  标题   = %s（%d 字）" % (title, len(title)))
    print("  正文   = %d 字（Markdown）" % len(body))
    print("  摘要   = %s" % brief[:60])
    print("  分类   = %s" % (args.category or DEFAULT_CATEGORY))
    print("  标签   = %s" % (", ".join(args.tags or []) or "（无）"))

    _, db = known_ids()
    if slug in db and not args.replace:
        print("✓ 已存过（draft_id=%s），跳过。要覆盖加 --replace" %
              db[slug]["draft_id"])
        return 0
    if args.dry:
        print("  [dry] 不调接口")
        return 0

    try:
        did = create_draft(title, body, brief, args.category, args.tags,
                           args.cover)
    except LoginRequired as e:
        print("✗ %s" % e)
        print("  先跑：python scripts/juejin_publish.py login")
        return 1
    remember(slug, did, title)
    print("✓ 已存草稿 draft_id=%s" % did)
    print("  编辑器：https://juejin.cn/editor/drafts/%s" % did)
    print("  发布：python scripts/juejin_publish.py publish --draft-id %s" % did)
    return 0


def cmd_list(args):
    try:
        items = list_drafts(limit=args.limit)
    except LoginRequired as e:
        print("✗ %s" % e)
        return 1
    print("草稿 %d 条：" % len(items))
    for d in items:
        print("  %-20s %s" % (d.get("id"), (d.get("title") or "")[:40]))
    return 0


def cmd_publish(args):
    _, db = known_ids()
    did = args.draft_id
    if not did and args.case:
        rec = db.get(args.case) or {}
        did = rec.get("draft_id")
        if not did:
            raise SystemExit("✗ 本地没有 %s 的草稿记录，先跑 draft" % args.case)
    if not did:
        raise SystemExit("✗ 给 --draft-id 或 --case")
    if args.dry:
        print("  [dry] 将发布 draft_id=%s" % did)
        return 0
    try:
        data = publish_draft(did)
    except LoginRequired as e:
        print("✗ %s" % e)
        return 1
    print("✓ 已发布 draft_id=%s" % did)
    if isinstance(data, dict) and data.get("article_id"):
        print("  https://juejin.cn/post/%s" % data["article_id"])
    return 0


def cmd_delete(args):
    delete_draft(args.draft_id)
    print("✓ 已删草稿 %s" % args.draft_id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="掘金发布（纯 API，正文吃 Markdown）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="开浏览器登录一次并存 cookie（轮询到成功为止）")
    p.add_argument("--port", type=int, default=9223, help="调试 Chrome 端口")
    p.add_argument("--wait", type=int, default=600, help="最长等待秒数")
    p.add_argument("--url", default="https://juejin.cn/",
                   help="打开哪个页面（默认首页）")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("set-cookie",
                       help="不在这台电脑前：直接粘贴 Cookie 完成登录（手机/抓包场景）")
    p.add_argument("cookie", nargs="?",
                   help="Cookie 字符串；省略或给 - 则从 stdin 读")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_set_cookie)

    p = sub.add_parser("whoami", help="验登录态")
    p.set_defaults(fn=cmd_whoami)

    p = sub.add_parser("categories", help="八大分类真实 ID")
    p.set_defaults(fn=cmd_categories)

    p = sub.add_parser("tags", help="某分类下的标签 ID")
    p.add_argument("--category", default=DEFAULT_CATEGORY)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_tags)

    p = sub.add_parser("draft", help="存草稿（默认；不公开发布）")
    p.add_argument("--file", help="独立稿 Markdown 路径（不走 cases.json）")
    p.add_argument("--case", help="案例 id（读 out/articles/<id>.md）")
    p.add_argument("--title", help="覆盖标题（默认取 md 首个 # ）")
    p.add_argument("--brief", help="摘要（默认取正文首段，100 字内）")
    p.add_argument("--category", default=DEFAULT_CATEGORY,
                   choices=list(CATEGORIES))
    p.add_argument("--tags", nargs="*", default=[], help="标签名或标签 ID")
    p.add_argument("--cover", default="", help="封面图 URL（可空）")
    p.add_argument("--replace", action="store_true", help="已存过也重建")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("list", help="草稿箱")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("publish", help="真发布（走 article/publish）")
    p.add_argument("--draft-id")
    p.add_argument("--case")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("delete", help="删草稿")
    p.add_argument("--draft-id", required=True)
    p.set_defaults(fn=cmd_delete)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
