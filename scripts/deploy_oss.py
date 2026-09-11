#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 dist/ 静态产物上传到阿里云 OSS。零依赖 —— 只用 Python 标准库自己实现 OSS 签名，
不需要 pip install oss2，也不要求本机装 ossutil。

用法：
    # 先验证凭证对不对（只读，不写任何东西）
    python scripts/deploy_oss.py --check --env-file <你的.env>

    # 看看会传哪些文件（不真传）
    python scripts/deploy_oss.py --bucket my-bucket --dry-run --env-file <你的.env>

    # 真正上传
    python scripts/deploy_oss.py --bucket my-bucket --env-file <你的.env>

凭证来源（按优先级）：
    1. --env-file 指定的文件（KEY=VALUE 格式，支持 # 注释和引号）
    2. 环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET
    3. 环境变量 ALIYUN_AK_ID / ALIYUN_AK_SECRET   ← 与阿里云 CLI 的惯例一致
    4. 环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET

为什么自己实现签名而不用 ossutil / oss2：
    这个项目整体是「克隆下来就能跑」的零依赖风格，部署脚本不该是例外。
    OSS 的 V1 签名就是 HMAC-SHA1 拼一个字符串，几十行的事。

安全提醒：AccessKey 只从环境变量或 --env-file 读，脚本里不存任何密钥。
          .env 已在 .gitignore 里，别把它提交到公开仓库。
"""

import argparse
import base64
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 各扩展名的 Content-Type。OSS 不会自己猜，传错了浏览器行为会很怪。
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".woff2": "font/woff2",
}

# 缓存策略：内容会变的一律 no-cache（每次都回源校验，改完立刻生效）；
# 静态资源给 5 分钟，省点回源，改了也最多晚 5 分钟生效。
CACHE_NO = "no-cache"
CACHE_SHORT = "public, max-age=300"
CACHE_RULES = {
    "index.html": CACHE_NO,
    "404.html": CACHE_NO,
    "data.js": CACHE_NO,      # 数据每天变
    "data.json": CACHE_NO,
    "app.js": CACHE_SHORT,
    "style.css": CACHE_SHORT,
}


def load_env_file(path):
    """读 KEY=VALUE 格式的 .env。已存在的环境变量优先，不被覆盖。"""
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    return True


def resolve_credentials():
    ak = (os.environ.get("OSS_ACCESS_KEY_ID")
          or os.environ.get("ALIYUN_AK_ID")
          or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"))
    sk = (os.environ.get("OSS_ACCESS_KEY_SECRET")
          or os.environ.get("ALIYUN_AK_SECRET")
          or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"))
    return ak, sk


def sign(ak, sk, method, bucket, key, headers, subresource=""):
    """OSS V1 签名。把 OSS 的签名算法照文档拼一遍，不难，但一个字都不能错。

    StringToSign =
        VERB + "\\n" + Content-MD5 + "\\n" + Content-Type + "\\n" + Date + "\\n"
        + CanonicalizedOSSHeaders + CanonicalizedResource
    """
    # 所有 x-oss-* 头，按名字字典序，形如 "k:v\n"
    oss_hdrs = sorted(
        (k.lower(), " ".join(str(v).split()))
        for k, v in headers.items() if k.lower().startswith("x-oss-")
    )
    canon_oss = "".join("%s:%s\n" % (k, v) for k, v in oss_hdrs)

    # /bucket/key[?subresource]；列 Bucket 时是 "/"
    resource = "/" + (bucket or "")
    if key:
        resource += "/" + key
    elif bucket:
        resource += "/"
    if subresource:
        resource += "?" + subresource

    sts = "\n".join([
        method,
        headers.get("Content-MD5", ""),
        headers.get("Content-Type", ""),
        headers.get("Date", ""),
    ]) + "\n" + canon_oss + resource

    digest = hmac.new(sk.encode("utf-8"), sts.encode("utf-8"), hashlib.sha1).digest()
    return "OSS %s:%s" % (ak, base64.b64encode(digest).decode("ascii"))


class OSS(object):
    def __init__(self, ak, sk, region, verbose=False):
        self.ak, self.sk, self.region = ak, sk, region
        self.endpoint = "https://oss-%s.aliyuncs.com" % region
        self.verbose = verbose
        # 本机代理会炸 TLS，显式绕开（和 harvest.py 一个处理）
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, bucket="", key="", body=None, headers=None,
                subresource="", query=""):
        hdrs = dict(headers or {})
        hdrs.setdefault("Date", formatdate(usegmt=True))
        hdrs["Authorization"] = sign(self.ak, self.sk, method, bucket, key,
                                     hdrs, subresource)

        url = self.endpoint + "/"
        if bucket:
            url += bucket + "/"
        if key:
            url += urllib.parse.quote(key)
        if query:
            url += "?" + query

        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers or {})
        except Exception as e:                                # noqa: BLE001
            return -1, str(e).encode("utf-8"), {}


def list_buckets(oss):
    """GET / —— 列出账号下的所有 Bucket。只读，用来验证凭证。"""
    st, body, _ = oss.request("GET")
    if st != 200:
        return None, body.decode("utf-8", "replace")[:400]
    import re
    return re.findall(r"<Name>([^<]+)</Name>", body.decode("utf-8", "replace")), ""


def ensure_website(oss, bucket):
    """设置静态网站托管：首页 index.html、错误页 404.html。
    不设这个，访问域名根路径会返回一个 XML 列表而不是首页。"""
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           "<WebsiteConfiguration>"
           "<IndexDocument><Suffix>index.html</Suffix></IndexDocument>"
           "<ErrorDocument><Key>404.html</Key></ErrorDocument>"
           "</WebsiteConfiguration>").encode("utf-8")
    st, body, _ = oss.request("PUT", bucket, headers={
        "Content-Type": "application/xml",
        "Content-Length": str(len(xml)),
    }, subresource="website", body=xml)
    return st, body.decode("utf-8", "replace")[:300]


def upload(oss, bucket, src_dir, prefix="", dry_run=False):
    files = []
    for root, _dirs, names in os.walk(src_dir):
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            key = (prefix.strip("/") + "/" + rel).lstrip("/") if prefix else rel
            files.append((full, key))
    files.sort(key=lambda x: x[1])

    if not files:
        print("[!] %s 里没有文件，先跑 python scripts/build_static.py" % src_dir)
        return 1

    print("源目录：%s" % os.path.relpath(src_dir, ROOT).replace(os.sep, "/"))
    print("目标：oss://%s/%s" % (bucket, prefix.strip("/") + "/" if prefix else ""))
    print()
    print("  %-22s %10s  %-10s %s" % ("对象", "大小", "缓存", "Content-Type"))
    print("  " + "-" * 74)

    ok = skip = fail = 0
    total_bytes = 0
    for full, key in files:
        with open(full, "rb") as f:
            data = f.read()
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        cache = CACHE_RULES.get(key.split("/")[-1], CACHE_SHORT)
        total_bytes += len(data)

        if dry_run:
            print("  %-22s %10s  %-10s %s" % (key, human(len(data)), cache.split(",")[0], ctype))
            ok += 1
            continue

        st, body, _ = oss.request("PUT", bucket, key, body=data, headers={
            "Content-Type": ctype,
            "Cache-Control": cache,
            "Content-Length": str(len(data)),
        })
        if st == 200:
            print("  %-22s %10s  %-10s %s" % (key, human(len(data)), cache.split(",")[0], ctype))
            ok += 1
        else:
            msg = body.decode("utf-8", "replace")[:200].replace("\n", " ")
            print("  %-22s %10s  [FAIL] HTTP %s  %s" % (key, human(len(data)), st, msg))
            fail += 1

    print("  " + "-" * 74)
    if dry_run:
        print("  dry-run：共 %d 个文件 / %s，未上传" % (ok, human(total_bytes)))
    else:
        print("  上传完成：成功 %d · 失败 %d · 合计 %s" % (ok, fail, human(total_bytes)))
    return 1 if fail else 0


def human(n):
    return ("%d B" % n) if n < 1024 else ("%.1f KB" % (n / 1024.0))


def main():
    ap = argparse.ArgumentParser(description="上传静态产物到阿里云 OSS")
    ap.add_argument("--bucket", help="目标 Bucket 名")
    ap.add_argument("--region", default=None,
                    help="Bucket 地域，如 cn-hongkong（默认取环境变量 OSS_REGION，兜底 cn-hangzhou）")
    ap.add_argument("--dir", default="dist", help="要上传的本地目录（默认 dist）")
    ap.add_argument("--prefix", default="", help="传到 Bucket 下的子路径，如 ai-cases")
    ap.add_argument("--env-file", help="从指定的 .env 读凭证")
    ap.add_argument("--check", action="store_true", help="只验证凭证并列出 Bucket，不写任何东西")
    ap.add_argument("--dry-run", action="store_true", help="只列出会传的文件，不真传")
    ap.add_argument("--setup-website", action="store_true",
                    help="顺便设置静态网站托管（首页 index.html / 404 页 404.html）")
    args = ap.parse_args()

    if args.env_file:
        if load_env_file(args.env_file):
            print("[i] 已从 %s 载入凭证" % args.env_file)
        else:
            print("[!] 读不到 --env-file 指定的文件：%s" % args.env_file)
            return 1

    # 地域：命令行优先，其次环境变量（--env-file 载入的也算），最后兜底杭州。
    # 注意必须在 load_env_file 之后再取，否则读不到 .env 里的 OSS_REGION。
    if not args.region:
        args.region = os.environ.get("OSS_REGION", "cn-hangzhou")

    ak, sk = resolve_credentials()
    if not ak or not sk:
        print("[!] 没找到 AccessKey。三种给法，任选一种：")
        print("      1. --env-file <你的.env>")
        print("      2. 环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET")
        print("      3. 环境变量 ALIYUN_AK_ID / ALIYUN_AK_SECRET")
        return 1
    print("[i] AccessKey 已就绪（ID 长度 %d），地域 %s" % (len(ak), args.region))

    oss = OSS(ak, sk, args.region)

    # ---- 只读验证
    print()
    print("验证凭证（GET /，只读）…")
    names, err = list_buckets(oss)
    if names is None:
        print("[FAIL] 凭证或网络有问题：%s" % err)
        return 1
    print("[OK] 凭证可用，账号下共 %d 个 Bucket" % len(names))
    for n in names:
        mark = ""
        if args.bucket and n == args.bucket:
            mark = "   ← 本次目标"
        print("      %s%s" % (n, mark))

    if args.check:
        print()
        print("--check 模式，未做任何写操作。")
        if args.bucket and args.bucket not in names:
            print("[!] 注意：--bucket 指定的 %s 不在上面这个列表里" % args.bucket)
            return 1
        return 0

    if not args.bucket:
        print()
        print("[!] 请用 --bucket 指定目标 Bucket")
        return 1
    if args.bucket not in names:
        print()
        print("[!] 账号里没有名为 %s 的 Bucket" % args.bucket)
        return 1

    src = os.path.join(ROOT, args.dir)
    if not os.path.isdir(src):
        print()
        print("[!] 找不到 %s，先跑 python scripts/build_static.py" % args.dir)
        return 1

    # ---- 上传
    print()
    rc = upload(oss, args.bucket, src, prefix=args.prefix, dry_run=args.dry_run)
    if rc or args.dry_run:
        return rc

    # ---- 静态网站托管
    if args.setup_website:
        print()
        print("设置静态网站托管（首页 index.html / 404 页 404.html）…")
        st, msg = ensure_website(oss, args.bucket)
        if st == 200:
            print("[OK] 已设置")
        else:
            print("[!] 设置失败 HTTP %s：%s" % (st, msg))
            print("    不影响已上传的文件，也可以去控制台手动设置。")

    print()
    print("=" * 66)
    print("  部署完成")
    print("=" * 66)
    print("  OSS 默认域名访问：")
    print("    https://%s.oss-%s.aliyuncs.com/%s"
          % (args.bucket, args.region, args.prefix.strip("/") + "/" if args.prefix else ""))
    print()
    print("  如果绑了 CDN 自定义域名，记得刷一次 CDN 缓存，否则可能还是旧的。")
    print("  控制台路径：CDN → 域名管理 → 选域名 → 刷新预热 → 刷新缓存 → 选「目录」填 /")
    return 0


if __name__ == "__main__":
    sys.exit(main())
