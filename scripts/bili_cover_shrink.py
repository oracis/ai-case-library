# -*- coding: utf-8 -*-
"""把超大封面压成 B 站能收的 JPG（纯 CDP，不装 PIL/opencv）。

为什么需要
----------
Agnes 生成的 4K 封面是 6~9 MB PNG，而 B站 `draft/add` 的封面上限是
**5 MB**（`bili_draft_api.upload_cover` 直接 raise）⇒ 建草稿整条失败。
本机没有 Pillow / opencv / ImageMagick（`convert` 是 Windows 自带的
磁盘转换工具，不是 ImageMagick），唯一可用的重采样器是 **Chrome 的 canvas**。

为什么起一个本地 HTTP 服务喂图（而不是把 base64 塞进 evaluate）
--------------------------------------------------------------
`file://` 页面里 drawImage 一张 `file://` 图片会把 canvas 标脏，
`toDataURL()` 直接抛 SecurityError —— 所以不能用 file://。
改走 data: URL 虽然不脏，但 8 MB 图 = 11 MB base64，得分片塞进
Runtime.evaluate，**每片都要一个 CDP 往返**：实测 28 片跑 137 秒/张
（首版就是这个毛病，CDP 对大帧非常慢）。

现在改成：进程内起一个只读的本地 HTTP 服务，页面同源取图 ⇒
每张图**只花一次 CDP 往返**（只把 toDataURL 的 jpeg base64 传回来）。

用法
----
    python -X utf8 scripts/bili_cover_shrink.py --dry content/*/cover.png
    python -X utf8 scripts/bili_cover_shrink.py content/*/cover.png
    # 默认写同目录 <stem>-bili.jpg；--force 覆盖已存在的
"""

import argparse
import base64
import glob
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 连 127.0.0.1 必须绕开系统代理，否则 CDP 的 WebSocket 被系统代理掐断
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_publish as wp  # noqa: E402

PORT = 9223          # 9223 = 头条/B站/小红书 共用的 chrome-debug-profile
LIMIT = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# 一次性喂图服务（只读、只服务内存里的当前这张）
# ---------------------------------------------------------------------------
class _Feed(BaseHTTPRequestHandler):
    png = b""            # 当前待处理的 PNG 字节

    def do_GET(self):                                    # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body, ctype = (b"<!doctype html><meta charset=utf-8>"
                           b"<title>cover-shrink</title>"), "text/html"
        elif path == "/img.png":
            body, ctype = self.png, "image/png"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # 静音访问日志
        pass


def serve(handle):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def shrink(cdp, base, src, dst, width=1920, quality=0.92):
    """取 /img.png → canvas 重采样到 width → 写 dst(jpg)。返回 (字节数, w, h)。"""
    raw = open(src, "rb").read()
    _Feed.png = raw
    js = """(async () => {
      const img = new Image();
      img.src = '/img.png?t=' + Date.now();
      await img.decode();
      const W = %d, H = Math.round(img.naturalHeight * W / img.naturalWidth);
      const c = document.createElement('canvas');
      c.width = W; c.height = H;
      const x = c.getContext('2d');
      x.imageSmoothingEnabled = true;
      x.imageSmoothingQuality = 'high';
      x.drawImage(img, 0, 0, W, H);
      return JSON.stringify({w: W, h: H,
                             url: c.toDataURL('image/jpeg', %s)});
    })()""" % (width, quality)
    out = cdp.eval(js)
    _Feed.png = b""
    if not out:
        raise RuntimeError("canvas 没回包")
    d = json.loads(out)
    url = d.get("url") or ""
    if not url.startswith("data:image/jpeg;base64,"):
        raise RuntimeError("toDataURL 返回异常：%r" % url[:60])
    data = base64.b64decode(url.split(",", 1)[1])
    tmp = dst + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)          # 原子替换，写一半不会留垃圾文件
    return len(data), d["w"], d["h"]


def main():
    ap = argparse.ArgumentParser(description="压缩封面到 B站可接受的 JPG")
    ap.add_argument("files", nargs="+", help="PNG 路径（可带通配）")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--quality", type=float, default=0.92)
    ap.add_argument("--suffix", default="-bili")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出")
    ap.add_argument("--dry", action="store_true", help="只报体积、不动手")
    a = ap.parse_args()

    paths = []
    for p in a.files:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        raise SystemExit("没有匹配到文件")

    todo = []
    for p in paths:
        stem, _ = os.path.splitext(os.path.basename(p))
        out_dir = a.out_dir or os.path.dirname(p)
        dst = os.path.join(out_dir, stem + a.suffix + ".jpg")
        size = os.path.getsize(p)
        if os.path.exists(dst) and not a.force:
            print("· 跳过（已存在）%s" % os.path.relpath(dst))
            continue
        todo.append((p, dst, size))
    if not todo:
        print("全部已存在，无需处理（要重压加 --force）")
        return 0

    print("待压 %d 个；目标宽 %d、质量 %.2f、上限 %.1f MB"
          % (len(todo), a.width, a.quality, LIMIT / 1048576))
    if a.dry:
        for p, dst, size in todo:
            flag = "超限" if size > LIMIT else "  可"
            print("  [%s] %7.1f MB  %s → %s"
                  % (flag, size / 1048576, os.path.basename(p),
                     os.path.basename(dst)))
        return 0

    srv = serve(_Feed)
    base = "http://127.0.0.1:%d/" % srv.server_address[1]
    cdp = wp.CDP(PORT)
    tid = cdp.new_target(base)["id"]
    cdp.connect_target(tid)
    time.sleep(1.5)
    bad = 0
    try:
        for p, dst, size in todo:
            t0 = time.time()
            try:
                n, w, h = shrink(cdp, base, p, dst, a.width, a.quality)
            except Exception as e:
                print("  ✗ %s：%s" % (os.path.basename(p), e))
                bad += 1
                continue
            if n > LIMIT:
                bad += 1
            print("  %s %7.1f MB → %5.2f MB (%dx%d) %.1fs  %s"
                  % ("✗ 仍超限" if n > LIMIT else "✓", size / 1048576,
                     n / 1048576, w, h, time.time() - t0,
                     os.path.relpath(dst)))
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass
        srv.shutdown()
    if bad:
        print("\n%d 个未达标：调小 --width（如 1600）或 --quality（如 0.88）重跑"
              % bad)
        return 1
    print("\n全部达标（≤ %.1f MB）" % (LIMIT / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
