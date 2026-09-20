#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地兜底：本地内容比线上新就自动重新构建并部署，否则什么都不做。

为什么要它
----------
部署只有本地一个入口（CI 不再部署，见 README「部署只有一个入口」）。

发布案例时不会忘 —— 部署是 release.py 六步里的第六步，而且失败即停。
但**只改了文案 / 前端 / 站点配置**时没有那个链路兜着：改完忘了跑部署，
线上就一直停在旧版本，而且没有任何提示。

所以这里做一个幂等的检查：构建一份对外产物，和线上逐文件比内容，
不一致才部署。一致就退出 —— 重复跑没有副作用。

边界（重要）
------------
它**不能替代人工决定上线时机**。唯一的判断依据是「本地和线上不一样」，
它不知道你是不是还在改、改完没有。所以：

  · 适合当**每日兜底**，补上「忘了部署」
  · 不适合改成发布主路径 —— 那会让半成品有被自动推上去的机会

用法
----
    python scripts/auto_deploy.py --env-file .env              # 检查 + 需要就部署
    python scripts/auto_deploy.py --env-file .env --dry-run     # 只报告差异
    python scripts/auto_deploy.py --env-file .env --force       # 有差异就当，无差异也当

退出码：0 = 一致（无需部署）或部署成功；1 = 出错。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_static                        # noqa: E402
import deploy_oss                          # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PUBLIC = os.path.join(ROOT, "public")

# 这两个文件里嵌着构建时刻，两次构建必然不同 —— 但它们的内容（数据）是等价的。
# 归一化后再比，否则每天都会「发现差异」并空转部署一次。
# 实测依据：把同一份数据连续构建两次，39 个产物里只有这两个文件哈希不同。
_TIMESTAMPY = ("data.json", "data.js")


def normalize(rel, raw):
    """把「内容等价但字节不同」的部分抹平，返回可比较的字节。

    只抹 generated_at 的**值**和 stats.inbox 的**数值**，不碰正文 ——
    正文里出现日期是正常的（比如「2026-09-18」是采集日期），
    整个文件做正则替换会误伤。
    """
    if rel not in _TIMESTAMPY:
        return raw
    text = raw.decode("utf-8", "replace")
    text = re.sub(r'"generated_at"\s*:\s*"[^"]*"', '"generated_at":""', text)
    # stats.inbox 统计的是**源数据**里的采集条数，随每日采集变化。
    # 但采集队列本身不进对外产物（inbox 数组为空），所以这个数字变了
    # 也不代表读者看到的内容变了 —— 拿它当差异会导致每天空转部署一次。
    text = re.sub(r'"inbox"\s*:\s*\d+', '"inbox":0', text)
    return text.encode("utf-8")


def digest(rel, raw):
    return hashlib.sha256(normalize(rel, raw)).hexdigest()


def local_manifest(root):
    """本地产物清单：{相对路径: 哈希}。"""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            with open(p, "rb") as f:
                out[rel] = digest(rel, f.read())
    return out


def no_proxy_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_remote(base, rel, opener, timeout=45):
    """取线上一个文件，返回归一化后的哈希；取不到返回 None。"""
    url = base.rstrip("/") + "/" + rel
    req = urllib.request.Request(url, headers={"User-Agent": "auto-deploy/1.0"})
    try:
        with opener.open(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return digest(rel, r.read())
    except Exception:                                          # noqa: BLE001
        return None


def diff_manifest(local, remote):
    """比两份清单。返回 (changed, missing, extra)。

    changed 线上内容与本地不同 —— 需要部署
    missing 线上没有这个文件 —— 需要部署（首次上线会全落这里）
    extra   线上多出来的、本地没有 —— **不触发部署**，部署也不会删它，
            所以单独报出来给人判断（多半是历史残留）
    """
    changed, missing, extra = [], [], []
    for rel in sorted(local):
        r = remote.get(rel)
        if r is None:
            missing.append(rel)
        elif r != local[rel]:
            changed.append(rel)
    for rel in sorted(remote):
        if rel not in local:
            extra.append(rel)
    return changed, missing, extra


def site_base():
    """线上地址：优先 data/site.json 的 url。"""
    try:
        with open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8") as f:
            return (json.load(f).get("url") or "").rstrip("/")
    except Exception:                                          # noqa: BLE001
        return ""


def main():
    ap = argparse.ArgumentParser(description="本地兜底：内容比线上新就重新部署")
    ap.add_argument("--base", help="线上站点地址（默认读 data/site.json 的 url）")
    ap.add_argument("--bucket", help="OSS Bucket")
    ap.add_argument("--region", help="OSS 地域（如 cn-hongkong）")
    ap.add_argument("--env-file", help="从 .env 读 bucket / region")
    ap.add_argument("--force", action="store_true", help="即使无差异也部署")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不部署")
    args = ap.parse_args()

    # 和 release.py 同源：先在本地把 .env 读进来，再取 bucket / region。
    if args.env_file:
        if not deploy_oss.load_env_file(args.env_file):
            print("[!] 读不到 --env-file：%s" % args.env_file)
            return 1
    args.bucket = args.bucket or os.environ.get("OSS_BUCKET")
    args.region = args.region or os.environ.get("OSS_REGION")

    base = (args.base or site_base()).rstrip("/")
    if not base:
        print("[!] 不知道线上地址。用 --base 给一个，或把域名写进 data/site.json 的 url。")
        return 1

    print("=" * 66)
    print("  兜底检查：本地内容 vs 线上")
    print("=" * 66)
    print("  线上：%s" % base)
    print("  产物：public（--no-inbox，对外版）")
    print()

    print("构建中…")
    build_static.build(PUBLIC, include_inbox=False)
    local = local_manifest(PUBLIC)
    print("  本地 %d 个文件" % len(local))

    opener = no_proxy_opener()
    print("  比对线上（逐文件 GET，约 %d 个请求）…" % len(local))
    remote = {rel: fetch_remote(base, rel, opener) for rel in local}

    unreachable = [r for r, h in remote.items() if h is None]
    if len(unreachable) >= len(local):
        print()
        print("[!] 线上一个文件都取不到 —— 大概率不是「内容不同」，而是网络或域名不对。")
        print("    先手工确认一次：curl -I %s/index.html" % base)
        print("    （把它当成「有差异」去部署是危险的判断，所以这里直接停下。）")
        return 1

    changed, missing, extra = diff_manifest(local, remote)

    print()
    if not changed and not missing and not extra and not args.force:
        print("  线上与本地一致 —— 不需要部署。")
        return 0

    if changed:
        print("  内容不同（%d 个）：" % len(changed))
        for rel in changed[:12]:
            print("    ~ %s" % rel)
        if len(changed) > 12:
            print("    … 另有 %d 个" % (len(changed) - 12))
    if missing:
        print("  线上缺失（%d 个）：" % len(missing))
        for rel in missing[:12]:
            print("    + %s" % rel)
        if len(missing) > 12:
            print("    … 另有 %d 个" % (len(missing) - 12))
    if extra:
        # 部署不会删这些文件，所以单独说清楚，别让人以为「重新部署就干净了」。
        print("  线上多出、本地没有（%d 个，**部署不会删它们**）：" % len(extra))
        for rel in extra[:12]:
            print("    ? %s" % rel)
        if len(extra) > 12:
            print("    … 另有 %d 个" % (len(extra) - 12))
        print("    要清掉得去 OSS 控制台手动删（或跑一次带删除的同步，本脚本不做）。")
    if unreachable:
        print("  取不到（按「有差异」处理，共 %d 个）：%s"
              % (len(unreachable), "、".join(unreachable[:6])))

    if args.dry_run:
        print()
        print("（--dry-run：只报告，未部署）")
        return 0

    if not args.bucket or not args.region:
        print()
        print("[!] 需要部署，但没给 bucket / region（试过 --env-file 与环境变量）。")
        print("    补上：--bucket <名> --region cn-hongkong，或用 --env-file .env")
        return 1

    print()
    print("=" * 66)
    print("  开始部署")
    print("=" * 66)
    rc = deploy_oss.main([
        "deploy_oss.py",
        "--bucket", args.bucket,
        "--region", args.region,
        "--dir", "public",
        "--verify-public",
    ])
    if rc:
        print("\n[!] 部署失败，退出码 %s" % rc)
        return 1

    print()
    print("  已上线。想确认内容真的对了：python scripts/verify_deploy.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
