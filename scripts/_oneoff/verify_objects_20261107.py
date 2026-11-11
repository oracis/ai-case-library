# -*- coding: utf-8 -*-
"""独立部署回读：匿名 GET 全部线上对象，逐对象比对 ETag 与字节。

不依赖 deploy_oss.upload() 的自报结果，也不依赖退出码。
判据：HTTP 200 且 响应 ETag(去引号/大小写归一) == 本地 MD5 hex
      且 响应体 MD5 == 本地 MD5 hex 且 字节数 == 本地字节数。
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "https://case.ydtgo.top"
MANIFEST = os.path.join(ROOT, "tmp", "deploy_after_manifest.json")
OUT = os.path.join(ROOT, "tmp", "deploy_readback_20261107.json")
WORKERS = 8


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(rel, expect_md5, expect_size, retries=2):
    url = BASE + "/" + urllib.parse.quote(rel, safe="/")
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-readback/1.0",
                                                        "Cache-Control": "no-cache"})
            with opener().open(req, timeout=45) as r:
                raw = r.read()
                etag = (r.headers.get("ETag") or "").strip('"').strip("'").lower()
                return {
                    "rel": rel, "http": r.status,
                    "etag": etag, "remote_md5": hashlib.md5(raw).hexdigest(),
                    "size": len(raw),
                    "etag_ok": etag == expect_md5,
                    "bytes_ok": hashlib.md5(raw).hexdigest() == expect_md5 and len(raw) == expect_size,
                }
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
    return {"rel": rel, "http": 0, "error": last, "etag_ok": False, "bytes_ok": False}


def main():
    local = json.load(open(MANIFEST, encoding="utf-8"))
    rels = sorted(local)
    print("独立回读：%d 个对象 <- %s" % (len(rels), MANIFEST), flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fetch, rel, local[rel]["md5"], local[rel]["size"]) for rel in rels]
        for i, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if i % 100 == 0:
                print("  ... %d/%d" % (i, len(rels)), flush=True)
    bad = [r for r in results if not (r["http"] == 200 and r["etag_ok"] and r["bytes_ok"])]
    print("=" * 66)
    print("  独立回读：总计 %d · ETag 一致 %d · 字节一致 %d · 异常 %d"
          % (len(results), sum(1 for r in results if r.get("etag_ok")),
             sum(1 for r in results if r.get("bytes_ok")), len(bad)))
    for r in bad[:20]:
        print("   [BAD]", r)
    print("=" * 66)
    json.dump({"results": results, "bad": bad}, open(OUT, "w", encoding="utf-8"),
              indent=1, ensure_ascii=False, sort_keys=True)
    print("明细 -> %s" % OUT)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
