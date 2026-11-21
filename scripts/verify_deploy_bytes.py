#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署后的**独立匿名 GET** 全量回读：逐对象比对 ETag + 字节（PRD #97）。

为什么需要它
------------
`deploy_oss.py` / `deploy_incremental.py` 的「成功 N · ETag 通过 N」是**写入侧
自报**，不是部署成功的证据；`verify_deploy.py` 是语义验收（明确不看字节数），
案例数不变但正文被换的撕裂态它抓不到（PRD #95/#97）。唯一可信判据是：
部署完成后，用**无签名、无登录态、绕本机代理**的匿名 GET 把每个对象取回来，
比对 HTTP ETag（OSS 普通 PUT 形态 == 内容 MD5 hex）与实收字节的 MD5。

判据（全部来自 PRD #96/#98 实测）：
  · 匿名 GET 200，响应 ETag 去引号后是 32-hex；multipart 的 "<hex>-N" 形态判失败。
  · 远端 ETag == 本地文件 MD5 hex（大小写归一）；实收字节 MD5 == 远端 ETag。
  · 任一项不符累计 FAIL，末尾 rc≠0 —— 绝不「报成功但留半更新」。

只读：只发匿名 GET，不写任何本地文件。

用法：
    python scripts/verify_deploy_bytes.py                    # 默认 base 读 data/site.json
    python scripts/verify_deploy_bytes.py --base https://case.ydtgo.top
    python scripts/verify_deploy_bytes.py --workers 8 --limit 20   # 抽验
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "public")
DEFAULT_BASE = "https://case.ydtgo.top"


def site_base():
    p = os.path.join(ROOT, "data", "site.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d.get("url") or DEFAULT_BASE
    except Exception:
        return DEFAULT_BASE


def norm_etag(v):
    return (v or "").strip().strip('"').lower()


def plain_md5_etag(v):
    e = norm_etag(v)
    return len(e) == 32 and all(c in "0123456789abcdef" for c in e)


def make_opener():
    # 绕本机 127.0.0.1 代理污染；公开站匿名 GET 不需要任何凭证。
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_objects():
    """public/ 下所有相对路径（POSIX 分隔），排除隐藏文件。"""
    out = []
    for root, dirs, files in os.walk(PUBLIC):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, PUBLIC).replace(os.sep, "/")
            out.append((rel, full))
    return sorted(out)


def verify_one(opener, base, rel, full):
    data = open(full, "rb").read()
    local_md5 = hashlib.md5(data).hexdigest()
    url = base.rstrip("/") + "/" + urllib.request.quote(rel)
    try:
        with opener.open(urllib.request.Request(
                url, headers={"User-Agent": "verify-deploy-bytes"}),
                timeout=45) as r:
            if r.status != 200:
                return (rel, "HTTP %s" % r.status)
            etag = norm_etag(r.headers.get("ETag"))
            body = r.read()
    except urllib.error.HTTPError as e:
        return (rel, "HTTP %s" % e.code)
    except Exception as e:
        return (rel, "ERR %s" % str(e)[:60])
    if not plain_md5_etag(etag):
        return (rel, "ETag 非普通 PUT 形态: %r" % etag)
    if etag != local_md5:
        return (rel, "ETag 不符: 远端=%s 本地=%s" % (etag, local_md5))
    body_md5 = hashlib.md5(body).hexdigest()
    if body_md5 != etag:
        return (rel, "字节不符: body=%s ETag=%s" % (body_md5, etag))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=None, help="站点根地址")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="只抽前 N 个（0=全部）")
    args = ap.parse_args()

    base = args.base or site_base()
    objs = local_objects()
    if args.limit:
        objs = objs[:args.limit]
    print("[verify_bytes] base=%s  对象=%d  匿名 GET 回读…" % (base, len(objs)))

    opener = make_opener()
    bad = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(verify_one, opener, base, rel, full): rel
                for rel, full in objs}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            done += 1
            if r:
                bad.append(r)
            if done % 50 == 0:
                print("  %d/%d" % (done, len(objs)))
            if args.workers == 1 and r:
                print("  [FAIL]", r[0], r[1])

    bad.sort()
    print("\n[verify_bytes] 回读 %d 个：一致 %d · 不一致 %d"
          % (len(objs), len(objs) - len(bad), len(bad)))
    for rel, why in bad[:20]:
        print("  [FAIL] %s  %s" % (rel, why))
    if len(bad) > 20:
        print("  … 另有 %d 个不一致" % (len(bad) - 20))
    if bad:
        print("[verify_bytes] FAIL —— 部署不完整，不许当作成功")
        return 1
    print("[verify_bytes] OK —— 全量匿名 GET 的 ETag + 字节与本地逐对象一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
