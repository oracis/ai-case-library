#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按实测语义做增量同步 / 断点续传（PRD #98）。

⛔ 这里**没有**只按 Content-Length 跳过的分支，也没有 `--resume`。
默认入口仍然是 `deploy_oss.py` 的全量重传；本脚本是**显式调用**的独立入口，
且自身默认干跑，要 `--yes` 才真的写。

判据（全部来自 2026-11-07 只读 + 最小化 PUT 实测，见 PRD #96 / #98）：
  · OSS 普通 PUT 的 ETag == 对象内容 MD5 的 32 位 hex（去引号、大小写归一）。
  · ListObjectsV2 的 <ETag> 与 GET/HEAD 同值；482/482 线上对象 == 本地 MD5 hex。
  · multipart 的 ETag 是 "<hex>-<N>"、Type=Multipart，**不等于**内容 MD5。
  · ListObjectsV2 分页：`list-type` / `max-keys` 走 query（不进签名），
    `continuation-token` **必须**走 subresource（进 CanonicalizedResource），
    否则第二页 403 SignatureDoesNotMatch（实测）。

⇒ 只有当「远端存在 + ETag 是普通 PUT 的 32-hex 形态 + ETag == 本地 MD5 hex
   + Type=Normal」四条同时成立才跳过。缺一条就重传，走 `put_verified()`
   的 PUT Content-MD5 + 响应 ETag 硬比对。`Size` 只用于打印，⛔ 绝不参与判定。

半更新撕裂态在这里会自愈：已传好的对象 ETag 匹配 ⇒ 跳过；旧对象（哪怕
与本地同尺寸）ETag 不匹配 ⇒ 重传。清单拿不全就整批退化为全量重传，
绝不拿「半份清单」当完整清单，也绝不把「远端没有」当成可以跳过。

用法：
    # 只列计划（默认，不写任何东西）
    python scripts/deploy_incremental.py --bucket ai-case-library \
           --region cn-hongkong --dir public --env-file .env

    # 真传 + 末尾独立回读复核
    python scripts/deploy_incremental.py --bucket ai-case-library \
           --region cn-hongkong --dir public --env-file .env --yes --verify

    # 清空单失败就硬失败（默认退化为全量重传，安全但慢）
    python scripts/deploy_incremental.py ... --yes --strict
"""

import argparse
import hashlib
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import deploy_oss as D  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_PAGES = 50
PAGE_SIZE = 1000


class IncompleteList(RuntimeError):
    """远端清单没拿全。调用方必须退化为全量重传，不许拿半份清单做跳过判定。"""


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def _children(node, name):
    return [c for c in node if _localname(c.tag) == name]


def _text(node, name, default=""):
    for c in node:
        if _localname(c.tag) == name:
            return c.text or default
    return default


def list_objects(oss, bucket, page_size=PAGE_SIZE):
    """列全量对象，返回 {key: {"etag","type","size"}}。分页不完整直接抛异常。

    ⚠ 签名坑（实测）：`continuation-token` 必须作为 subresource，
    `list-type=2&max-keys=N` 必须放 query。两者混放或全放 query 都 403。
    """
    out = {}
    token = None
    seen_tokens = set()
    for page in range(1, MAX_PAGES + 1):
        kw = {"query": "list-type=2&max-keys=%d" % page_size}
        if token:
            kw["subresource"] = "continuation-token=" + urllib.parse.quote(token, safe="")
        st, body, _ = oss.request("GET", bucket, **kw)
        if st != 200:
            raise IncompleteList("第 %d 页 HTTP %s：%s" %
                                 (page, st, body.decode("utf-8", "replace")[:200]))
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise IncompleteList("第 %d 页 XML 解析失败：%s" % (page, e)) from e

        contents = _children(root, "Contents")
        if not contents:
            raise IncompleteList("第 %d 页没有 Contents（疑似空转）" % page)
        for c in contents:
            key = _text(c, "Key")
            if not key:
                raise IncompleteList("第 %d 页有 Contents 缺 Key" % page)
            out[key] = {
                "etag": _text(c, "ETag"),
                "type": _text(c, "Type"),
                "size": int(_text(c, "Size", "0") or 0),
            }

        truncated = _text(root, "IsTruncated", "false").strip().lower() == "true"
        if not truncated:
            return out
        token = _text(root, "NextContinuationToken")
        if not token:
            raise IncompleteList("第 %d 页 IsTruncated=true 但没有 NextContinuationToken" % page)
        if token in seen_tokens:
            raise IncompleteList("第 %d 页返回重复的 continuation-token（死循环）" % page)
        seen_tokens.add(token)
    raise IncompleteList("超过 %d 页仍未列完" % MAX_PAGES)


def can_skip(remote, md5_hex):
    """远端对象能否跳过 —— 只看 ETag 内容与对象类型，⛔ 不看 Size / Content-Length。

    multipart 的 ETag 形如 "<hex>-1"，即使前半段碰巧等于本地 MD5 也不能跳：
    实测它的值与内容 MD5 无关，拿它当证据就是 #94 那种「判据量错对象」。
    """
    if not remote:
        return False
    etag = D.norm_etag(remote.get("etag"))
    if not D.etag_is_plain_md5(etag):
        return False
    if etag != md5_hex:
        return False
    typ = (remote.get("type") or "").strip()
    if typ and typ != "Normal":          # Type 缺省时（V1 响应）仅靠 ETag 判定
        return False
    return True


def plan(files, remote):
    """返回 [(local_path, key, md5_hex, action, remote_size)]，action ∈ skip/put。"""
    rows = []
    for full, key in files:
        with open(full, "rb") as f:
            data = f.read()
        md5_hex = hashlib.md5(data).hexdigest()
        r = remote.get(key)
        rows.append((full, key, md5_hex, "skip" if can_skip(r, md5_hex) else "put",
                     (r or {}).get("size")))
    return rows


def sync(oss, bucket, src_dir, prefix="", dry_run=True, strict=False, verify=False,
         page_size=PAGE_SIZE):
    """返回退出码。dry_run=True 时只读（但仍会列清单，便于核对计划）。"""
    files = D.collect_files(src_dir, prefix)
    if not files:
        print("[!] %s 里没有文件，先跑 python scripts/build_static.py" % src_dir)
        return 1

    inbox_n = D.inbox_count(os.path.join(src_dir, "data.json"))
    if inbox_n is None:
        print("[!] 拒绝上传：读不出 %s/data.json 的 inbox 字段。" % src_dir)
        return 1
    if inbox_n:
        print("[!] 拒绝上传：产物带 %d 条未核实队列，请用 --no-inbox 重建 public/。" % inbox_n)
        return 1

    print("源目录：%s" % D.safe_relpath(src_dir, ROOT))
    print("目标：oss://%s/%s" % (bucket, prefix.strip("/") + "/" if prefix else ""))

    def _scoped(all_objects):
        """列全量清单后按 prefix 取子集，避免把别的路径对象误报成孤儿。"""
        if not prefix:
            return all_objects
        p = prefix.strip("/") + "/"
        return {k: v for k, v in all_objects.items() if k.startswith(p)}

    degraded = False
    try:
        remote = _scoped(list_objects(oss, bucket, page_size=page_size))
        print("[i] 远端清单：%d 个对象（分页完整，本次 prefix 范围）" % len(remote))
    except IncompleteList as e:
        if strict:
            print("[FAIL] 远端清单不完整：%s" % e)
            print("       去掉 --strict 可退化为全量重传；或直接用 deploy_oss.py。")
            return 1
        print("[WARN] 远端清单不完整：%s" % e)
        print("       ⇒ 退化为**全量重传**：本次不跳过任何对象（不拿半份清单做判定）。")
        remote = {}
        degraded = True

    rows = plan(files, remote)
    orphans = sorted(set(remote) - {key for _, key in files}) if remote else []
    skips = [r for r in rows if r[3] == "skip"]
    puts = [r for r in rows if r[3] == "put"]

    print()
    print("  %-24s %10s  %-8s %s" % ("对象", "大小", "动作", "说明"))
    print("  " + "-" * 76)
    for full, key, md5_hex, action, rsize in rows:
        size = os.path.getsize(full)
        why = ("ETag 一致（内容已确认）" if action == "skip"
               else ("远端缺失" if rsize is None else "ETag 不一致 / 形态不合格"))
        print("  %-24s %10s  %-8s %s" % (key, D.human(size), action.upper(), why))
    if orphans:
        print()
        print("  [i] 远端有、本地没有的对象 %d 个（仅报告，⛔ 不自动删除）：" % len(orphans))
        for k in orphans[:10]:
            print("      %s" % k)
        if len(orphans) > 10:
            print("      ... 其余 %d 个" % (len(orphans) - 10))

    print("  " + "-" * 76)
    print("  计划：总计 %d · 跳过 %d · 需上传 %d · 孤儿 %d%s"
          % (len(rows), len(skips), len(puts), len(orphans),
             " · 清单降级=全量重传" if degraded else ""))
    if dry_run:
        print("  dry-run：未上传（加 --yes 才真写）")
        return 0

    ok = fail = 0
    total_bytes = 0
    for full, key, _md5, action, _rsize in puts:
        with open(full, "rb") as f:
            data = f.read()
        ctype = D.MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        good, why, detail = D.put_verified(oss, bucket, key, data, ctype, D.cache_for(key))
        total_bytes += len(data)
        if good:
            ok += 1
        else:
            fail += 1
            if why == "FAIL":
                st, msg = detail
                print("  [FAIL] %s HTTP %s %s" % (key, st, msg))
            else:
                expect, got = detail
                print("  [%s] %s 本地 %s / 远端 %s"
                      % (why, key, expect[:12], got[:12] or "(无)"))

    print()
    print("  上传完成：成功 %d · 失败 %d · 跳过 %d · 合计 %s"
          % (ok, fail, len(skips), D.human(total_bytes)))
    if fail:
        print("  [FAIL] 有对象没写对，别当成功；重跑本脚本即可只补这些。")
        return 1

    if verify:
        print()
        print("独立回读：重新列一次远端清单，逐对象复核 ETag == 本地 MD5 …")
        try:
            after = _scoped(list_objects(oss, bucket, page_size=page_size))
        except IncompleteList as e:
            print("  [FAIL] 回读清单不完整：%s" % e)
            return 1
        bad = []
        for _full, key, md5_hex, _action, _rsize in rows:
            r = after.get(key)
            if not can_skip(r, md5_hex):
                bad.append((key, (r or {}).get("etag"), md5_hex))
        print("  回读：%d/%d 对象 ETag 与本地 MD5 一致，异常 %d"
              % (len(rows) - len(bad), len(rows), len(bad)))
        for k, e, m in bad[:10]:
            print("      [BAD] %s 远端 %s / 本地 %s" % (k, e, m[:12]))
        if bad:
            return 1

    print("  这只是逐对象字节一致的确认；上线语义仍要再跑 scripts/verify_deploy.py。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="按 ETag 实测语义做增量同步（默认干跑）")
    ap.add_argument("--bucket", help="目标 Bucket 名")
    ap.add_argument("--region", default=None, help="地域，如 cn-hongkong（必填）")
    ap.add_argument("--dir", default="public", help="本地产物目录（默认 public）")
    ap.add_argument("--prefix", default="", help="OSS 子路径，如 ai-cases")
    ap.add_argument("--env-file", help="从 .env 读凭证")
    ap.add_argument("--yes", action="store_true", help="真写（缺省只列计划）")
    ap.add_argument("--verify", action="store_true", help="传完再独立列一次清单复核 ETag")
    ap.add_argument("--strict", action="store_true",
                    help="清单不完整就硬失败（缺省退化为全量重传，不跳过任何对象）")
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE, help="ListObjects 每页条数")
    args = ap.parse_args(argv)

    if args.env_file:
        if not D.load_env_file(args.env_file):
            print("[!] 读不到 --env-file 指定的文件：%s" % args.env_file)
            return 1
        print("[i] 已从 %s 载入凭证" % args.env_file)
    region = args.region or os.environ.get("OSS_REGION")
    if not region:
        print("[!] 没指定地域（--region 或 OSS_REGION）。不设兜底 —— 猜错只会 403。")
        return 1
    ak, sk = D.resolve_credentials()
    if not ak or not sk:
        print("[!] 没找到 AccessKey（--env-file 或环境变量）。")
        return 1
    bucket = args.bucket or os.environ.get("OSS_BUCKET")
    if not bucket:
        print("[!] 请用 --bucket 指定目标 Bucket")
        return 1

    oss = D.OSS(ak, sk, region)
    return sync(oss, bucket, args.dir, prefix=args.prefix,
                dry_run=not args.yes, strict=args.strict, verify=args.verify,
                page_size=args.page_size)


if __name__ == "__main__":
    sys.exit(main())
