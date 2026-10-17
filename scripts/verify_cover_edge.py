# -*- coding: utf-8 -*-
"""封面「白边」体检：检测截图 clip 超出视口被白填充的脏封面。

背景（2026-10-17 实测踩坑）：headless 视口 ~780px < 封面画布 1080px 时，
`Page.captureScreenshot` 的 clip 被裁到视口内，右侧 ~300px 变成白边
（连同页面滚动条滑轨），而产物尺寸仍是 2160x920 —— 肉眼/尺寸检查
完全发现不了，最终读者看到的就是「设计挤左侧 + 右侧白边 + tag 丢失」。

判据（对 47 张线上封面实测校准）：
  右缘 4.5% 区域平均亮度 > 200 ⇒ 脏（白边）
  右缘 4.5% 区域平均亮度 < 200 ⇒ 净（设计背景实测 47~75）
PROSP 修复前后对照：修前 edgeL=255（脏），修后 edgeL=66（净）。

用法：
  python verify_cover_edge.py --dir tmp/covers/audit          # 目录下所有 jpg/png
  python verify_cover_edge.py --url <封面URL> [--url ...]      # 直接检 URL
  python verify_cover_edge.py --json meta.json                # {id: url|path} 映射
退出码：0=全净；1=有脏；2=用法/连接错误。
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["no_proxy"] = "*"

EDGE_THRESHOLD = 200      # 右缘 4.5% 平均亮度阈值
EDGE_FRACTION = 0.045     # 右缘占比
BATCH = 4                 # 每批并发处理的图片数（dataURL 太大，分批注入）


def collect(args):
    """返回 [(id, kind, src)]，kind ∈ local/http。"""
    items = []
    if args.json:
        meta = json.load(open(args.json, encoding="utf-8"))
        for k, v in meta.items():
            src = v if isinstance(v, str) else v.get("cover") or v.get("url")
            if not src:
                continue
            kind = "local" if os.path.exists(src) else "http"
            items.append((k, kind, src))
    for d in args.dir or []:
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith((".jpg", ".jpeg", ".png")):
                items.append((os.path.splitext(fn)[0], "local",
                              os.path.join(d, fn)))
    for u in args.url or []:
        items.append((u.rsplit("/", 1)[-1][:24] or u, "http", u))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append",
                    help="封面图目录（可多次）；jpg/png 全量检测")
    ap.add_argument("--url", action="append", help="封面 URL（可多次）")
    ap.add_argument("--json", help="JSON：{id: 本地路径或URL}")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--out", help="把结果写成 JSON 文件")
    args = ap.parse_args()

    items = collect(args)
    if not items:
        print("[!] 没有待检图片")
        return 2

    # 本地文件先读成 bytes；http 的下载
    payload = []
    for _id, kind, src in items:
        try:
            if kind == "local":
                with open(src, "rb") as f:
                    data = f.read()
            else:
                req = urllib.request.Request(
                    src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = r.read()
            if len(data) < 1000:
                raise ValueError("文件过小(%dB)" % len(data))
            payload.append((_id, base64.b64encode(data).decode("ascii")))
        except Exception as e:                            # noqa: BLE001
            print("[warn] %s 读取失败: %s" % (_id, e))

    import wechat_publish as wp
    cdp = wp.CDP(args.port)
    t = cdp.new_target("about:blank")
    if not cdp.connect_target(t["targetId"]):
        print("[X] CDP 连接失败（port %d）" % args.port)
        return 2
    cdp.send("Page.navigate", {"url": "about:blank"})
    time.sleep(0.8)

    js_tpl = r"""
    (async () => {
      const out = [];
      const q = window.__BATCH.map((it, i) => ({i, id: it[0], b64: it[1]}));
      async function work() {
        while (q.length) {
          const it = q.shift();
          const img = new Image();
          await new Promise((res, rej) => {
            img.onload = res;
            img.onerror = () => rej(new Error("load fail"));
            img.src = "data:image/jpeg;base64," + it.b64;
          });
          const w = img.naturalWidth, h = img.naturalHeight;
          const c = document.createElement("canvas");
          c.width = w; c.height = h;
          const ctx = c.getContext("2d", {willReadFrequently: true});
          ctx.drawImage(img, 0, 0);
          const d = ctx.getImageData(0, 0, w, h).data;
          let s = 0, n = 0;
          for (let x = Math.floor(w * (1 - %s)); x < w; x++) {
            for (let y = 0; y < h; y++) {
              const i = (y * w + x) * 4;
              s += 0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2]; n++;
            }
          }
          out.push({id: it.id, w: w, h: h, edgeLum: s / n});
        }
      }
      await Promise.all([work(), work(), work(), work()]);
      return JSON.stringify(out);
    })()
    """ % EDGE_FRACTION

    results = []
    for s in range(0, len(payload), BATCH):
        batch = payload[s:s + BATCH]
        cdp.send("Runtime.evaluate", {"expression":
                 "window.__BATCH=" + json.dumps(batch) + ";",
                 "returnByValue": True})
        r = cdp.send("Runtime.evaluate",
                     {"expression": js_tpl, "awaitPromise": True,
                      "returnByValue": True, "timeout": 60000})
        val = (r.get("result", {}).get("result", {}) or {}).get("value")
        if not val:
            print("[warn] batch %d 无返回" % s)
            continue
        results.extend(json.loads(val))

    dirty = []
    print("%-28s %6s %6s %8s  %s" % ("id", "w", "h", "edgeL", "判定"))
    for r in sorted(results, key=lambda x: x["id"]):
        bad = r["edgeLum"] > EDGE_THRESHOLD
        if bad:
            dirty.append(r["id"])
        print("%-28s %6d %6d %8.1f  %s" % (
            r["id"][:28], r["w"], r["h"], r["edgeLum"],
            "脏(白边)" if bad else "净"))
    print("\n共 %d 张，脏 %d 张%s" % (
        len(results), len(dirty), "：" + " ".join(dirty) if dirty else ""))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"results": results, "dirty": dirty}, f,
                      ensure_ascii=False, indent=1)
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
