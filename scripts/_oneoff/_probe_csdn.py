#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探针：CSDN 三条发布路线的无登录探活（2026-10-28）。

原理：阿里云 API 网关先验签名、再验登录。所以**不带 Cookie** 也能区分：
  - 签名错   → 网关层拒绝（403 / mark 错误码）
  - 签名对   → 网关放行，业务层报「未登录」（401/200+code!=200）
据此不用浏览器就能钉死 secret 与签名字符串形状。

三条路线：
  A. bizapi.csdn.net/blog-console-api/v3/mdeditor/saveArticle（2026 年新接口）
     to_sign 形状有 A1/A2/A3 三个候选（Accept 行值说法不一），逐个试。
  B. mp.csdn.net/mdeditor/saveArticle（2020-2022 老接口，预期已死）
  C. write.blog.csdn.net/xmlrpc/index（MetaWeblog，预期对多数账号关闭）

无登录态 + 空 body ⇒ 不会真的建草稿。用完即删（_ 前缀约定）。
"""
import base64
import hashlib
import hmac
import json
import socket
import ssl
import sys
import time
import urllib.request
import urllib.error
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CA_KEY = "203803574"
SECRET = "9znpamsyl2c7cdrr9sas0le9vbc3r6ba"
PATH = "/blog-console-api/v3/mdeditor/saveArticle"
URL = "https://bizapi.csdn.net" + PATH
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def csdn_sign(method, accept, content_type, nonce, path=PATH):
    """按候选形状拼签名字符串。accept/content_type 必须与实际头一致。"""
    to_sign = (
        method + "\n"
        + accept + "\n"
        + "\n"                      # date 空行
        + content_type + "\n"
        + "\n"                      # 其他 header 空
        + "x-ca-key:" + CA_KEY + "\n"
        + "x-ca-nonce:" + nonce + "\n"
        + path
    )
    sig = base64.b64encode(
        hmac.new(SECRET.encode(), to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    return to_sign, sig


def post_json(op, url, headers, body=b""):
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with op.open(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return None, repr(e)


def probe_a():
    print("=" * 70)
    print("A. bizapi 新接口：三个 to_sign 候选（无 Cookie）")
    op = opener()
    variants = [
        ("A1 accept=*/*",        "*/*", "application/json;charset=UTF-8"),
        ("A2 accept=json",       "application/json", "application/json;charset=UTF-8"),
        ("A3 accept=空",          "", "application/json;charset=UTF-8"),
    ]
    for label, accept, ctype in variants:
        nonce = str(uuid.uuid4())
        to_sign, sig = csdn_sign("POST", accept, ctype, nonce)
        headers = {
            "User-Agent": UA,
            "Accept": accept or "*/*",
            "Content-Type": ctype,
            "x-ca-key": CA_KEY,
            "x-ca-nonce": nonce,
            "x-ca-signature": sig,
            "x-ca-signature-headers": "x-ca-key,x-ca-nonce",
            "Origin": "https://editor.csdn.net",
            "Referer": "https://editor.csdn.net/",
        }
        code, text = post_json(op, URL, headers, b"{}")
        print("\n[%s]" % label)
        print("  to_sign = %r" % to_sign)
        print("  -> HTTP %s | %s" % (code, text.replace("\n", " ")))


def probe_b():
    print("=" * 70)
    print("B. mp.csdn.net 老接口（预期已死/重定向）")
    op = opener()
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    code, text = post_json(op, "https://mp.csdn.net/mdeditor/saveArticle",
                           headers, b"title=t&content=c")
    print("  -> HTTP %s | %s" % (code, text.replace("\n", " ")[:200]))


def probe_c():
    print("=" * 70)
    print("C. MetaWeblog XML-RPC（预期对多数账号关闭，先看端点是否活着）")
    # 只发一个 getUsersBlogs 的 XML（无真实账密）—— 只看端点响应形状
    body = ('<?xml version="1.0"?><methodCall><methodName>blogger.getUsersBlogs</methodName>'
            '<params><param><value><string></string></value></param></params></methodCall>').encode()
    req = urllib.request.Request(
        "https://write.blog.csdn.net/xmlrpc/index", data=body, method="POST")
    req.add_header("Content-Type", "text/xml")
    req.add_header("User-Agent", UA)
    op = opener()
    try:
        with op.open(req, timeout=15) as r:
            print("  -> HTTP %s | %s" % (r.status, r.read().decode("utf-8", "replace")[:300]))
    except urllib.error.HTTPError as e:
        print("  -> HTTP %s | %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))
    except Exception as e:
        print("  -> 连接失败: %r" % (e,))


if __name__ == "__main__":
    probe_a()
    probe_b()
    probe_c()
    print("=" * 70)
    print("探针完成（全程无 Cookie，不可能建出草稿）")
