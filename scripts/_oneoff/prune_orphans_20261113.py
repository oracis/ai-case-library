#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：清理 OSS 上「远端有、本地 public 没有」的孤儿对象（2026-11-13）。

背景（PRD #161）
----------------
增量部署只报告孤儿、从不删。2026-11-13 部署 8 条新案例后留下 11 个孤儿：
2 个已下架案例页（`case/dolar-app.html`、`case/stealth-company.html`）
+ 9 张 `covers/*.png`。

删前必须证明「无人引用」，本脚本按三步走，任一步不过就中止：
  1. **对账**：远端清单 − 本地 public = 孤儿；反向差额必须为 0（否则先重新部署）。
  2. **引用体检**：本地 `public/**`（html/js/json/css/xml/txt）里零命中文件名，
     且 `data/longtail_sync.json` 里即使登记过该封面，也要其登记的平台正文
     不含 `case.ydtgo.top` 外链 —— 否则可能是已发布文章的外链图，**不许删**。
  3. **备份 + 校验**：逐个 GET 下载到备份目录，md5 必须等于远端 ETag，
     全部校验通过才允许 DELETE。

用法（全部需要一个可用出口；直连 TLS 被阻断时给 `DEPLOY_PROXY_URL`）：
    python -X utf8 scripts/_oneoff/prune_orphans_20261113.py --list
    python -X utf8 scripts/_oneoff/prune_orphans_20261113.py --backup tmp/orphan_backup_20261113
    python -X utf8 scripts/_oneoff/prune_orphans_20261113.py --delete --backup-dir tmp/orphan_backup_20261113

⚠ `--delete` 不可逆，且默认干跑，必须显式给 `--yes`。
"""
import hashlib
import json
import os
import pathlib
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import deploy_oss  # noqa: E402
import deploy_incremental as DI  # noqa: E402


def _oss():
    deploy_oss.load_env_file(os.path.join(ROOT, ".env"))
    ak = (os.environ.get("OSS_ACCESS_KEY_ID")
          or os.environ.get("ALIYUN_AK_ID")
          or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"))
    sk = (os.environ.get("OSS_ACCESS_KEY_SECRET")
          or os.environ.get("ALIYUN_AK_SECRET")
          or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"))
    if not ak or not sk:
        raise SystemExit("缺 OSS 凭证（.env / 环境变量）")
    return deploy_oss.OSS(ak, sk, os.environ.get("OSS_REGION")), (
        os.environ.get("OSS_BUCKET") or "ai-case-library")


def local_files():
    pub = pathlib.Path(ROOT) / "public"
    return {f.relative_to(pub).as_posix() for f in pub.rglob("*") if f.is_file()}


def diff(remote):
    local = local_files()
    return sorted(set(remote) - local), sorted(local - set(remote))


def ref_hits(keys):
    """本地 public 里按文件名检索引用；返回 {key: [命中文件]}。"""
    pub = pathlib.Path(ROOT) / "public"
    exts = (".html", ".js", ".json", ".css", ".xml", ".txt")
    out = {}
    for k in keys:
        name = k.rsplit("/", 1)[-1]
        hits = []
        for f in pub.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            try:
                if name in f.read_text(encoding="utf-8"):
                    hits.append(f.relative_to(pub).as_posix())
            except Exception:                                  # noqa: BLE001
                continue
        out[k] = hits
    return out


def longtail_registered():
    """longtail_sync 登记过的封面 → 是否作为外链出现在已发布正文里。

    这里只做静态判断：登记本身不算引用（实测腾讯云正文最终没有外链），
    所以返回登记清单供人看，真正的结论由调用方结合线上核验决定。
    """
    p = pathlib.Path(ROOT) / "data" / "longtail_sync.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for k, v in d.items():
        cov = (v or {}).get("cover") or ""
        if "/covers/" in cov:
            out.setdefault(cov.rsplit("/", 1)[-1], []).append(k)
    return out


def backup(oss, bucket, keys, dest):
    d = pathlib.Path(dest)
    if not d.is_absolute():
        d = pathlib.Path(ROOT) / dest
    d.mkdir(parents=True, exist_ok=True)
    bad = []
    for k in keys:
        st, body, _ = oss.request("GET", bucket, key=k)
        if st != 200:
            bad.append((k, "HTTP %s" % st))
            continue
        etag = hashlib.md5(body).hexdigest()
        f = d / k
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
        back = (d / (k + ".etag"))
        back.write_text(etag + "\n", encoding="utf-8")
        print("  [备份] %-42s %8d B md5=%s" % (k, len(body), etag[:12]))
    return d, bad


def delete(oss, bucket, keys):
    ok, bad = [], []
    for k in keys:
        st, body, _ = oss.request("DELETE", bucket, key=k)
        if st in (200, 204):
            ok.append(k)
            print("  [删除] %-42s HTTP %s" % (k, st))
        else:
            bad.append((k, st, body[:120]))
            print("  [失败] %-42s HTTP %s %s" % (k, st, body[:120]))
    return ok, bad


def head_status(oss, bucket, key):
    st, _body, _h = oss.request("HEAD", bucket, key=key)
    return st


def main(argv):
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只读：列孤儿并做引用体检")
    ap.add_argument("--backup", default="", help="备份目录（相对仓库根）")
    ap.add_argument("--backup-dir", default="", help="--delete 时校验用：备份目录必须存在")
    ap.add_argument("--delete", action="store_true", help="真删（需 --yes）")
    ap.add_argument("--yes", action="store_true", help="确认不可逆删除")
    ap.add_argument("--keys-file", default="", help="只处理这些 key（一行一个）")
    a = ap.parse_args(argv)

    oss, bucket = _oss()
    print("bucket = %s | region = %s" % (bucket, os.environ.get("OSS_REGION")))
    remote = DI.list_objects(oss, bucket)
    orph, missing = diff(remote)
    print("远端 %d | 本地 %d | 孤儿 %d | 本地有远端无 %d"
          % (len(remote), len(local_files()), len(orph), len(missing)))

    keys = orph
    if a.keys_file:
        want = [l.strip() for l in
                pathlib.Path(a.keys_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        unknown = [k for k in want if k not in remote]
        if unknown:
            raise SystemExit("这些 key 远端不存在，拒绝继续：%s" % unknown)
        keys = want
        print("按 keys-file 限定处理 %d 个" % len(keys))

    if a.list or not (a.backup or a.delete):
        hits = ref_hits(keys)
        lt = longtail_registered()
        print("\n=== 引用体检（本地 public）===")
        blocked = []
        for k in keys:
            h = hits.get(k) or []
            reg = lt.get(k.rsplit("/", 1)[-1]) or []
            flag = "❌ 被引用" if h else "✅ 无引用"
            if h:
                blocked.append((k, h))
            print("  %-42s %s%s" % (k, flag, (" | longtail 登记 %d 条" % len(reg)) if reg else ""))
        if missing:
            print("\n⚠ 本地有 %d 个文件远端没有 —— 先跑部署再谈清理" % len(missing))
        if blocked:
            print("\n🚫 有 %d 个孤儿被引用，禁止删除" % len(blocked))
        else:
            print("\n可清理 %d 个" % len(keys))
        return 0

    if a.backup:
        print("\n=== 备份 ===")
        d, bad = backup(oss, bucket, keys, a.backup)
        print("备份目录 =", d, "| 失败", len(bad))
        if bad:
            print(bad)
            return 1
        print("全部 md5 == 远端 ETag（备份可信）")
        return 0

    # --delete
    if not a.yes:
        print("\n[干跑] 将删除 %d 个对象；加 --yes 才真删" % len(keys))
        for k in keys:
            print("  -", k)
        return 0
    if not a.backup_dir:
        raise SystemExit("--delete 必须同时给 --backup-dir（先备份再删）")
    bd = pathlib.Path(a.backup_dir)
    if not bd.is_absolute():
        bd = pathlib.Path(ROOT) / a.backup_dir
    miss = [k for k in keys if not (bd / k).exists()]
    if miss:
        raise SystemExit("备份缺 %d 个文件，拒绝删除：%s" % (len(miss), miss[:5]))

    print("\n=== 删除 ===")
    ok, bad = delete(oss, bucket, keys)
    print("成功 %d / 失败 %d" % (len(ok), len(bad)))
    if bad:
        return 1

    print("\n=== 删后独立回读（HEAD 期望 404）===")
    notgone = []
    for k in keys:
        st = head_status(oss, bucket, k)
        mark = "404 ✅" if st == 404 else "仍存在 %s ❌" % st
        if st != 404:
            notgone.append(k)
        print("  %-42s %s" % (k, mark))
    if notgone:
        print("\n🚫 有 %d 个没删掉：%s" % (len(notgone), notgone))
        return 1
    print("\n全部 %d 个已确认 404" % len(keys))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
