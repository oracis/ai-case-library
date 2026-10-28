#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""博客园发布 —— 官方 MetaWeblog XML-RPC API，零逆向零签名，真·草稿语义。

第七个平台接入，也是**最干净的一个**：博客园官方支持 MetaWeblog 协议
（2002 年与 WordPress 一起诞生的标准），后台自助开 token，无风控对抗。
Python 标准库 `xmlrpc.client` 直发，十几行核心。

## 要点（2026-10-28 桌面调研，多实测帖交叉验证；待本机实测定稿）

| 项 | 结论 |
|---|---|
| 端点 | `https://rpc.cnblogs.com/metaweblog/<博客名>`（博客名 = 个人主页 cnblogs.com/<博客名> 那段） |
| 鉴权 | username + **MetaWeblog 访问令牌**（管理→设置→其他设置→启用并复制；不是登录密码） |
| 建/发文 | `metaWeblog.newPost(blogid, username, token, struct, publish_bool)` → 返回 postid |
| 🚨 **真草稿** | `publish=False` 就是真草稿（与 CSDN 的假草稿天壤之别）——攒稿后人工/事后放行 |
| 编辑 | `metaWeblog.editPost(postid, username, token, struct, publish)` |
| 删除 | `blogger.deletePost(appkey, postid, username, token)`（appkey 任意字符串） |
| 回读 | `metaWeblog.getRecentPosts(blogid, username, token, N)` —— 发布成功判据 = 这里按标题命中 |
| 🚨 Markdown | categories 必须含 **`[Markdown]`**，否则正文被当 HTML 解析；description 传 MD 原文 |
| 图片 | newMediaObject 可传图，但草稿管线图片全是外链，暂不需要 |

## 用法

```bash
# 一次性配置（token 从博客园后台拿，见上表）
python scripts/cnblogs_publish.py login --username <u> --blog-name <b> --token <t>
python scripts/cnblogs_publish.py whoami                       # 验 token
python scripts/cnblogs_publish.py draft --file drafts/wechat/x.md --case x
python scripts/cnblogs_publish.py publish --file ... --case x  # 直接发表
python scripts/cnblogs_publish.py list [--query 标题子串]       # 回读判据
python scripts/cnblogs_publish.py delete --post-id <id>
```
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import xmlrpc.client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from csdn_publish import split_md, md_to_html          # noqa: E402  复用纯函数

CONFIG_FILE = os.path.join(ROOT, "data", "cnblogs_config.json")
DRAFT_FILE = os.path.join(ROOT, "data", "cnblogs_drafts.json")
APPKEY = "case-library"                                 # MetaWeblog 老协议字段，博客园不校验
TIMEOUT = 30


class ConfigError(Exception):
    pass


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------
# XML-RPC 传输（超时 + TLS 校验失败自动降级重试）
# --------------------------------------------------------------------------

class _Transport(xmlrpc.client.SafeTransport):
    """带超时的 https transport（SafeTransport 的 context 参数 py3.6+）。"""

    def __init__(self, context):
        super().__init__(context=context)
        self._timeout = TIMEOUT

    def make_connection(self, host):
        conn = super().make_connection(host)
        try:
            conn.timeout = self._timeout
        except Exception:                                # noqa: BLE001
            pass
        return conn


def _client(cfg, lax_tls=False):
    from urllib.parse import quote
    blog_name = quote(str(cfg.get("blog_name") or cfg.get("username") or ""),
                      safe="")     # 中文登录名/博客名必须 URL 编码
    url = "https://rpc.cnblogs.com/metaweblog/%s" % blog_name
    if lax_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context()
    return xmlrpc.client.ServerProxy(url, transport=_Transport(ctx),
                                     allow_none=True)


def _call(cfg, method, *args):
    """统一调用：TLS 校验失败降级重试一次；Fault/ProtocolError 归一成 ApiError。"""
    for lax in (False, True):
        try:
            return getattr(_client(cfg, lax_tls=lax), method)(*args)
        except ssl.SSLCertVerificationError:
            if lax:
                raise ApiError("TLS 校验失败且降级仍失败")
            continue                                    # 降级重试
        except xmlrpc.client.Fault as e:
            raise ApiError("XML-RPC Fault %s: %s" % (e.faultCode, e.faultString))
        except xmlrpc.client.ProtocolError as e:
            if e.errcode in (401, 403):
                raise ConfigError("鉴权失败 HTTP %s —— token/用户名不对，或令牌被重置"
                                  % e.errcode)
            raise ApiError("ProtocolError %s: %s" % (e.errcode, e.errmsg))
        except OSError as e:
            raise ApiError("网络错误: %r" % (e,))
    raise ApiError("unreachable")


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise ConfigError(
            "没有配置文件。先拿 token：博客园后台 → 管理 → 设置 → 其他设置 → "
            "「启用 MetaWeblog 访问令牌」→ 复制。然后跑:\n"
            "  python scripts/cnblogs_publish.py login --username <用户名> "
            "--blog-name <博客名> --token <令牌>")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("username", "blog_name", "token"):
        if not cfg.get(k):
            raise ConfigError(
                "配置缺字段 %s。token 获取：博客园后台 → 管理 → 设置 → 其他设置 → "
                "「启用 MetaWeblog 访问令牌」→ 复制；然后重跑 login（或补齐 %s）"
                % (k, CONFIG_FILE))
    return cfg


def save_config(cfg):
    import atomicio
    cfg = dict(cfg)
    cfg["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    atomicio.write_json(CONFIG_FILE, cfg)
    print("✓ 配置落盘: %s" % os.path.relpath(CONFIG_FILE, ROOT))


def remember(case_id, post_id, title, url="", status="draft"):
    import atomicio
    ledger = {}
    if os.path.exists(DRAFT_FILE):
        with open(DRAFT_FILE, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    ledger[case_id] = {
        "post_id": post_id, "title": title, "url": url, "status": status,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    atomicio.write_json(DRAFT_FILE, ledger)
    print("✓ 台账: %s ← %s (%s)" % (os.path.basename(DRAFT_FILE), case_id, status))


# --------------------------------------------------------------------------
# 动作
# --------------------------------------------------------------------------

def verify(cfg):
    """getUsersBlogs：验 token + 自动补 blogid。返回博客信息列表。"""
    blogs = _call(cfg, "blogger.getUsersBlogs", APPKEY, cfg["username"], cfg["token"])
    if not blogs:
        raise ConfigError("token 有效但该账号没有博客——先去 cnblogs 开通博客")
    if not cfg.get("blogid"):
        cfg["blogid"] = str(blogs[0].get("blogid") or "")
        save_config(cfg)
    return blogs


def _post_struct(title, body, as_html=False, categories=None):
    cats = list(categories or [])
    if as_html:
        description = md_to_html(body)
    else:
        description = body                       # [Markdown] 分类下博客园自己渲染
        if "[Markdown]" not in cats:
            cats.insert(0, "[Markdown]")
    return {
        "title": title,
        "description": description,
        "categories": cats,
        "mt_keywords": "AI,独立开发,产品拆解",   # 标签，按需覆盖
    }


def new_post(cfg, title, body, publish, as_html=False):
    st = _post_struct(title, body, as_html=as_html)
    post_id = _call(cfg, "metaWeblog.newPost",
                    cfg.get("blogid") or "", cfg["username"], cfg["token"],
                    st, publish)
    return str(post_id)


def recent_posts(cfg, count=20):
    """回读判据：发布成功 = 这里按标题命中。"""
    items = _call(cfg, "metaWeblog.getRecentPosts",
                  cfg.get("blogid") or "", cfg["username"], cfg["token"], int(count))
    out = []
    for it in items or []:
        out.append({
            "post_id": str(it.get("postid") or ""),
            "title": it.get("title") or "",
            "url": it.get("link") or it.get("permalink") or "",
            "dateCreated": str(it.get("dateCreated") or ""),
            "categories": list(it.get("categories") or []),
        })
    return out


def delete_post(cfg, post_id):
    ok = _call(cfg, "blogger.deletePost", APPKEY, str(post_id),
               cfg["username"], cfg["token"])
    if not ok:
        raise ApiError("deletePost 返回非真值")
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_login(args):
    cfg = {"username": args.username, "blog_name": args.blog_name,
           "token": args.token, "blogid": ""}
    blogs = verify(cfg)
    for b in blogs:
        print("  博客: id=%s | %s | %s" % (b.get("blogid"), b.get("blogName"), b.get("url")))
    print("✓ token 验证通过，配置已落盘")


def _load_md_strip_watermark(path):
    with open(path, "r", encoding="utf-8") as f:
        md = f.read()
    # 剥掉草稿管线的元信息水印（「> 平台：wechat ｜ 案例：… ｜ 状态：…」）
    return "\n".join(ln for ln in md.split("\n") if not ln.startswith("> 平台："))


def _do_post(args, publish):
    cfg = load_config()
    md = _load_md_strip_watermark(args.file)
    title, body = split_md(md)
    if re.search(r"\d", title):
        print("⚠ 标题含数字（「标题禁金额」铁律）：", title)
    post_id = new_post(cfg, title, body, publish=publish, as_html=args.html)
    case_id = args.case or os.path.splitext(os.path.basename(args.file))[0]
    url = ""
    if publish:                                     # 发表后从回读里带出链接
        time.sleep(2)
        for it in recent_posts(cfg, count=10):
            if it["post_id"] == post_id:
                url = it["url"]
                break
    remember(case_id, post_id, title, url=url,
             status="published" if publish else "draft")
    print("✓ %s: post_id=%s %s" % ("已发表" if publish else "草稿", post_id, url))
    if not publish:
        print("  放行：后台人工点发布，或 editPost（待接）")


def main():
    ap = argparse.ArgumentParser(description="博客园发布（官方 MetaWeblog XML-RPC）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="一次性配置：验证 token 并落盘")
    p.add_argument("--username", required=True)
    p.add_argument("--blog-name", required=True, help="cnblogs.com/<博客名> 那段")
    p.add_argument("--token", required=True, help="MetaWeblog 访问令牌（后台设置里拿）")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("whoami", help="验证 token（getUsersBlogs）")
    p.set_defaults(fn=lambda a: print(json.dumps(
        verify(load_config()), ensure_ascii=False, indent=1, default=str)))

    p = sub.add_parser("draft", help="存草稿（publish=False，真草稿语义）")
    p.add_argument("--file", required=True)
    p.add_argument("--case", default="")
    p.add_argument("--html", action="store_true",
                   help="正文转 HTML 发送（默认发 MD 原文 + [Markdown] 分类）")
    p.set_defaults(fn=lambda a: _do_post(a, publish=False))

    p = sub.add_parser("publish", help="直接发表（publish=True）")
    p.add_argument("--file", required=True)
    p.add_argument("--case", default="")
    p.add_argument("--html", action="store_true")
    p.set_defaults(fn=lambda a: _do_post(a, publish=True))

    p = sub.add_parser("list", help="最近文章回读（发布成功 = 按标题命中）")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--query", default="")
    p.set_defaults(fn=lambda a: print(json.dumps(
        [it for it in recent_posts(load_config(), count=a.n)
         if not a.query or a.query in it["title"]],
        ensure_ascii=False, indent=1, default=str)))

    p = sub.add_parser("delete", help="删除文章/草稿")
    p.add_argument("--post-id", required=True)
    p.set_defaults(fn=lambda a: (delete_post(load_config(), a.post_id),
                                 print("✓ 已删除 post_id=%s" % a.post_id)))

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
