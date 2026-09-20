#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 dist/ 静态产物上传到阿里云 OSS。零依赖 —— 只用 Python 标准库自己实现 OSS 签名，
不需要 pip install oss2，也不要求本机装 ossutil。

用法：
    # 先验证凭证对不对（只读，不写任何东西）
    python scripts/deploy_oss.py --check --region cn-hongkong --env-file <你的.env>

    # 看看会传哪些文件（不真传）
    python scripts/deploy_oss.py --bucket my-bucket --region cn-hongkong --dry-run

    # 真正上传（对外发布务必用 --dir public，不要用带 inbox 的 dist）
    python scripts/deploy_oss.py --bucket my-bucket --region cn-hongkong --dir public

本项目实际用的（Bucket 在 cn-hongkong，港澳台及海外，无需国内域名备案）：
    python scripts/deploy_oss.py --bucket ai-case-library --region cn-hongkong \
           --dir public --verify-public

地域是必填的，没有兜底值：
    曾经兜底 cn-hangzhou，但本项目所有 Bucket 都在 cn-hongkong，
    「什么都不填」等于必然指向错的 endpoint，而 OSS 的回话是
    「must be addressed using the specified endpoint」—— 看不出是地域问题。
    所以改成不填就停下来说清楚。可用 --region 或环境变量 OSS_REGION 给。

    为什么用香港而不是国内：国内地域绑自定义域名需要域名备案，
    这个项目没有备案，改走香港地域直绑 OSS（代价是没 CDN 加速）。

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
import json
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
# 按 basename 认不出来的那些：预渲染的案例页和搜索引擎要读的两个文件。
# case/ 下的页面正文随数据更新（改了数字就得立刻生效），所以不缓存。
CACHE_BY_PREFIX = (("case/", CACHE_NO),)
CACHE_BY_NAME = ("sitemap.xml", "robots.txt")


def cache_for(key):
    """给一个 OSS 对象 key 选缓存策略。"""
    for prefix, rule in CACHE_BY_PREFIX:
        if key.startswith(prefix):
            return rule
    name = key.split("/")[-1]
    if name in CACHE_BY_NAME:
        return CACHE_NO
    return CACHE_RULES.get(name, CACHE_SHORT)


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
        self.ak = ak
        self.sk = sk
        self.region = region
        self.endpoint = "https://oss-%s.aliyuncs.com" % region
        self.verbose = verbose
        # 本机代理会炸 TLS，显式绕开（和 harvest.py 一个处理）
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _host_for(self, bucket):
        """选 host：列账号下的所有 Bucket 走二级域名；任何带 Bucket 的请求
        必须走三级域名 <bucket>.oss-<region>.aliyuncs.com，否则 OSS 会回
        SecondLevelDomainForbidden。这是 OSS 的强制约束，不是什么优化。"""
        if not bucket:
            return self.endpoint
        return "https://%s.oss-%s.aliyuncs.com" % (bucket, self.region)

    def request(self, method, bucket="", key="", body=None, headers=None,
                subresource="", query=""):
        hdrs = dict(headers or {})
        hdrs.setdefault("Date", formatdate(usegmt=True))
        hdrs["Authorization"] = sign(self.ak, self.sk, method, bucket, key,
                                     hdrs, subresource)

        # /bucket/key[?subresource]；列 Bucket 时是 "/"
        # host 二级 / 三级域名由 _host_for 决定，路径不再含 bucket
        url = self._host_for(bucket) + "/"
        if key:
            url += urllib.parse.quote(key)
        if subresource:
            url += "?" + subresource
        if query:
            url += ("?" if not subresource else "&") + query

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


def create_bucket(oss, bucket, acl="public-read"):
    """PUT /<bucket> —— 新建 Bucket。

    Bucket 名全局唯一，被别人占了会返回 409，那种情况只能换名字。
    acl 用 public-read：静态站点的文件要能被匿名读到，否则访问会 403。
    """
    st, body, _ = oss.request("PUT", bucket, headers={
        "x-oss-acl": acl,
        "Content-Length": "0",
    })
    return st, body.decode("utf-8", "replace")[:400]


def set_bucket_acl(oss, bucket, acl="public-read"):
    """占位：阿里云不允许通过 API 把 Bucket ACL 设为 public。
    公共访问必须改用 Bucket Policy（见 set_bucket_policy）。
    保留这个函数只是为了让旧代码不报错。"""
    return 403, ("Put public bucket acl is not allowed by Aliyun policy; "
                 "use Bucket Policy instead")


def get_bucket_acl(oss, bucket):
    """读一次当前 ACL，做诊断用。阿里云对公共访问做了策略收紧，
    主账号经常看不到 ACL 返回值，需要看 get_bucket_policy 才能确认状态。"""
    st, body, _ = oss.request("GET", bucket, subresource="acl")
    if st != 200:
        return None, body.decode("utf-8", "replace")[:200]
    import re
    m = re.search(r"<Grant>(.*?)</Grant>", body.decode("utf-8", "replace"), re.S)
    return (m.group(1) if m else "<no Grant>"), ""


# 允许匿名 GET 所有对象的最小策略。阿里云 OSS 公共访问的推荐方式。
PUBLIC_READ_POLICY = """{
  "Version": "1",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["oss:GetObject"],
    "Principal": ["*"],
    "Resource": ["acs:oss:*:%s/*"]
  }]
}"""


def set_bucket_policy(oss, bucket, policy=None):
    """PUT /?policy —— 设置 Bucket Policy。

    阿里云 OSS 不允许通过 API 设 ACL=public-read，但允许设 Bucket Policy。
    这条策略允许匿名 GET 所有对象，覆盖静态站点的全部需求（PUT/DELETE
    仍受 AccessKey 保护，所以安全上和「公共读 ACL」等价，但策略更灵活）。
    """
    if policy is None:
        policy = PUBLIC_READ_POLICY % bucket
    body = policy.encode("utf-8")
    st, resp, _ = oss.request("PUT", bucket, subresource="policy", body=body,
                              headers={
                                  "Content-Type": "application/json",
                                  "Content-Length": str(len(body)),
                              })
    return st, resp.decode("utf-8", "replace")[:300]


def get_bucket_policy(oss, bucket):
    """读回当前策略，做诊断用（OSS 会在 PUT 成功但读回时报错时回 404）。"""
    st, body, _ = oss.request("GET", bucket, subresource="policy")
    return st, body.decode("utf-8", "replace")[:400]


def verify_public(bucket, region, prefix=""):
    """匿名 GET 一次首页，确认「真能从公网打开」。

    这一步不能省：Bucket 建成 private 时上传照样成功，
    但访问会 403 —— 只有真的匿名请求过才知道站是活的。
    """
    key = (prefix.strip("/") + "/" if prefix else "") + "index.html"
    url = "https://%s.oss-%s.aliyuncs.com/%s" % (bucket, region, urllib.parse.quote(key))
    # 关键：不带任何签名头，模拟一个陌生访客
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (verify)"})
    try:
        with opener.open(req, timeout=30) as r:
            # 读全量，而不是 read(400) 探一小段——只探一小段的话日志里的字节数
            # 会被误读成「首页只有这么大」，实际那只是探针长度。
            body = r.read()
        return r.status, len(body), url, ""
    except urllib.error.HTTPError as e:
        return e.code, 0, url, e.read().decode("utf-8", "replace")[:220]
    except Exception as e:                                     # noqa: BLE001
        return -1, 0, url, str(e)[:220]


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
        cache = cache_for(key)
        total_bytes += len(data)

        if dry_run:
            print("  %-22s %10s  %-10s %s" % (key, human(len(data)), cache.split(",")[0], ctype))
            ok += 1
            continue

        st, body, _ = oss.request("PUT", bucket, key, body=data, headers={
            "Content-Type": ctype,
            "Cache-Control": cache,
            "Content-Length": str(len(data)),
            # 对象本身也要 public-read。Bucket 是 public-read 不代表对象自动继承，
            # 必须显式带 x-oss-acl，否则 OSS 给的 ACL 是 "default"，匿名 GET 会 403。
            "x-oss-acl": "public-read",
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


def inbox_count(data_json_path):
    """产物里未核实队列的条数。返回 None 表示**读不出来**（与「确实是 0」区分开）。

    dist 是本地预览版（带 inbox），public 是对外版（inbox 恒 0）。
    两者的区别只在这一个数字上，所以拿它当对外发布的闸门。

    为什么区分 None 和 0：第一版把「读不到」也返回 0，于是漏了 `import json`
    这个真 bug —— 抛异常被吞成「看起来一切正常」，闸门永远是开的。
    调用方现在必须同时处理三种状态：0（干净）/ 正数（脏）/ None（读不出来）。
    """
    try:
        with open(data_json_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:                                   # noqa: BLE001
        return None
    if not isinstance(data, dict) or "inbox" not in data:
        return None
    return len(data.get("inbox") or [])


def main(argv=None):
    """argv=None 时读 sys.argv（命令行）；传列表则可被别的脚本直接调用。

    别的地方（release.py / auto_deploy.py）要复用这套参数解析，
    给个 argv 入口比让它们各自替换 sys.argv 干净。
    """
    ap = argparse.ArgumentParser(description="上传静态产物到阿里云 OSS")
    ap.add_argument("--bucket", help="目标 Bucket 名")
    ap.add_argument("--region", default=None,
                    help="Bucket 地域，如 cn-hongkong（必填：或给环境变量 OSS_REGION。"
                         "不设兜底 —— 猜错只会 403）")
    ap.add_argument("--dir", default="dist", help="要上传的本地目录（默认 dist）")
    ap.add_argument("--allow-inbox", action="store_true",
                    help="允许上传带未核实队列的预览版（dist）。对外发布不要加这个")
    ap.add_argument("--prefix", default="", help="传到 Bucket 下的子路径，如 ai-cases")
    ap.add_argument("--env-file", help="从指定的 .env 读凭证")
    ap.add_argument("--check", action="store_true", help="只验证凭证并列出 Bucket，不写任何东西")
    ap.add_argument("--dry-run", action="store_true", help="只列出会传的文件，不真传")
    ap.add_argument("--setup-website", action="store_true",
                    help="顺便设置静态网站托管（首页 index.html / 404 页 404.html）")
    ap.add_argument("--create", action="store_true",
                    help="当 Bucket 不存在时新建（public-read ACL）。Bucket 名全局唯一，已被占用会返回 409")
    ap.add_argument("--verify-public", action="store_true",
                    help="部署后匿名 GET 一次首页，确认「真能从公网打开」。Bucket 是私有时这一步会 403")
    args = ap.parse_args(argv)

    if args.env_file:
        if load_env_file(args.env_file):
            print("[i] 已从 %s 载入凭证" % args.env_file)
        else:
            print("[!] 读不到 --env-file 指定的文件：%s" % args.env_file)
            return 1

    # 地域：命令行优先，其次环境变量（--env-file 载入的也算）。
    #
    # **不设兜底地域。** 这里原本兜底 cn-hangzhou —— 那是个有害的默认值：
    # 本项目所有 Bucket 都在 cn-hongkong（港澳台及海外，不需要国内域名备案），
    # 兜底杭州意味着「你什么都不填」就必然指向错的 endpoint。
    # 而猜错的失败方式不是清晰报错，是 OSS 回一句
    # 「The bucket you are attempting to access must be addressed using the
    # specified endpoint」403 —— 看不出是地域错了，只看到一堆上传失败。
    #
    # 宁可在这里停下来说清楚要什么，也不猜一个大概率错的值。
    # 注意必须在 load_env_file 之后再取，否则读不到 .env 里的 OSS_REGION。
    if not args.region:
        args.region = os.environ.get("OSS_REGION")
    if not args.region:
        print("[!] 没指定地域。Bucket 的地域必须显式给出 —— 猜错会 403 且看不出原因。")
        print("    （不设兜底是有意的：本项目 Bucket 在 cn-hongkong，兜底杭州必错）")
        print("    三种给法，任选一种：")
        print("      1. 命令行 --region cn-hongkong")
        print("      2. 环境变量 OSS_REGION=cn-hongkong")
        print("      3. --env-file <你的.env> 里写 OSS_REGION=cn-hongkong")
        print("    查地域：阿里云控制台 → OSS → Bucket 概览 → 访问端口/地域")
        return 1

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
        if args.create:
            print("    （开了 --create，下面会尝试新建）")
        else:
            print("    想自动新建就加 --create；Bucket 名是全局唯一的，长度 3-63、小写字母数字短横线")
            return 1
        # --dry-run 到此为止：桶都不存在，后面的策略设置和上传都没有意义。
        if args.dry_run:
            print("    （--dry-run：跳过建桶 —— 建桶是写操作）")
            return 0
        st, msg = create_bucket(oss, args.bucket)
        if st != 200:
            print("[FAIL] 建桶 HTTP %s：%s" % (st, msg))
            print("    多半是名字已被别人占了（409）或格式不合法。换一个名字重试。")
            return 1
        print("[OK] Bucket 已创建：%s（ACL=public-read）" % args.bucket)
        names.append(args.bucket)

    # 不论新建的还是既有的，都显式确认/纠正一次 ACL 与 Bucket Policy。
    # 阿里云不允许通过 API 设 ACL=public，但允许设 Bucket Policy——
    # Policy 是阿里云推荐的方式，效用上等价于「公共读 ACL」，
    # 但策略更精细（可以限定前缀、动作、IP 等）。
    #
    # **这一步是 PUT，--dry-run 必须跳过。** 它曾经被无条件执行：
    # dry_run 只保护了 upload 那一段，于是「只列出会传的文件，不真传」
    # 的承诺是假的 —— 权限策略照样真的被重设。而 release.py 的 --dry-run
    # 对外说的是「全链路预演，不写盘不上传」，会跟着一起骗人。
    print()
    if args.dry_run:
        print("（--dry-run：跳过 Bucket Policy 设置 —— 那是写操作，不是只读检查）")
    else:
        print("设置 Bucket Policy（允许匿名 GetObject）…")
        st, msg = set_bucket_policy(oss, args.bucket)
        if st == 200:
            print("[OK] 已设置匿名 GET 策略")
        else:
            print("[!] 设置失败 HTTP %s：%s" % (st, msg))
            print("    后续匿名访问几乎肯定 403，但文件仍然会上传。")
            print("    解决：去阿里云控制台 → OSS → 这个 Bucket → 权限管理 → Bucket 策略，")
            print("          加一条允许 Principal=* GetObject 的策略。")

    src = os.path.join(ROOT, args.dir)
    if not os.path.isdir(src):
        print()
        print("[!] 找不到 %s，先跑 python scripts/build_static.py" % args.dir)
        return 1

    # ---- 闸门：对外产物不许带未核实队列
    #
    # `--dir` 默认是 dist，而 dist 是**本地预览版**（带 inbox 441 条未核实素材）；
    # 对外发布必须用 `build_static.py --no-inbox --out public`。
    # 2026-09-20 实际踩过：默认参数直接把 441 条未核实素材推上了公网，
    # 语义校验报 `inbox = 0` 才发现的 —— 那之前它已经在线上了。
    #
    # 这类错误的代价不对称：多传几百条未核实数据不会报错，只会静静被搜索引擎收录。
    # 所以做成硬闸门而不是提示 —— 真要传预览版得显式说 --allow-inbox。
    inbox_n = inbox_count(os.path.join(src, "data.json"))
    if inbox_n is None:
        print()
        print("[!] 拒绝上传：读不出 %s/data.json 的 inbox 字段，无法确认产物是否干净。" % args.dir)
        print("    先确认跑过 python scripts/build_static.py 生成了完整产物。")
        return 1
    if inbox_n and not args.allow_inbox:
        print()
        print("[!] 拒绝上传：%s/data.json 里带着 %d 条未核实队列。" % (args.dir, inbox_n))
        print("    对外发布请用公开版产物：")
        print("      python scripts/build_static.py --no-inbox --out public")
        print("      python scripts/deploy_oss.py --bucket %s --dir public" % (args.bucket or "…"))
        print("    确实要传预览版（一般只在测试环境）再加 --allow-inbox。")
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

    if args.verify_public:
        print()
        print("匿名 GET 首页确认可访问…")
        st, size, url, err = verify_public(args.bucket, args.region, prefix=args.prefix)
        if st == 200:
            print("[OK] HTTP %s  %d 字节  %s" % (st, size, url))
        else:
            print("[FAIL] HTTP %s  %s" % (st, err or url))
            print("    多半是 Bucket 不是 public-read，或刚上传完 CDN 缓存还没刷新。")

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
    print()
    print("  这只是「能打开」的确认。要确认内容真的对了，再跑一遍语义校验：")
    print("    python scripts/verify_deploy.py --base <你的域名>")
    print("  （别用字节数验收：数据每天都在涨，对不上分不清是没部署还是数据变了。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
