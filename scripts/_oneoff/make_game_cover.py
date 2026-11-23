#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给游戏稿（游戏仓 ext-ep-NNN-*）渲染一张头条式横版封面。

为什么需要它（2026-11-24）
--------------------------
`publish_one` 的 cover 闸对 `bilibili2` 是**强制**的，而封面查找链里
`out/articles/<cid>-cover.png`、`out/xhs/<cid>/card-1.png` 都没有游戏稿的图；
游戏仓 `content/<slug>/cover.png` 是 1–2 期时代的手工图，从第 12 期起就不再
提供（ep-012/013/014 目录下都没有任何图片文件）。

那 1–14 期的封面是哪来的？**头条发布时现渲染的**：`toutiao_publish.render_tt_cover()`
用 CDP 打开一段 HTML、按 1920×1080 视口截图，落到 `out/toutiao/<cid>/cover.png`；
B站一号/二号直接复用同一张（实测 ep-014 的一号目录与 `-b2` 目录里那张
cover.png 字节完全相同，85298B）。所以这里只做一件事：**把同一个渲染器
对着游戏稿调用一次**，不另造设计。

用法（9223 必须已起，见 `scripts/_launch_9223.py --hold`）：
    python -X utf8 scripts/_oneoff/make_game_cover.py --case ext-ep-015-openrct2 \
        --name OpenRCT2 --desc "开源游乐园经营重制"

⚠ 本脚本只写 `out/toutiao/<cid>/cover.png`（工作产物，不是台账）。
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def png_size(path):
    """读 PNG 的 IHDR 拿宽高（不引 Pillow）。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="产物 id，如 ext-ep-015-openrct2")
    ap.add_argument("--name", required=True, help="封面主视觉的项目名，如 OpenRCT2")
    ap.add_argument("--desc", default="", help="封面第二行：一句话介绍，不含金额")
    ap.add_argument("--port", type=int, default=9223, help="CDP 端口（默认 9223）")
    args = ap.parse_args()

    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % args.port, timeout=3).read(64)
    except Exception:
        raise SystemExit("CDP %d 未就绪：先跑 python scripts/_launch_9223.py --hold 600"
                         % args.port)

    import toutiao_publish as tp

    c = {"id": args.case, "name": args.name}
    if args.desc:
        # cover_desc 是 cover_desc() 的第一优先级，写在 desc 之前能压住
        # 兜底取值（one_liner / what_it_does 那些在游戏稿里都没有）。
        c["cover_desc"] = args.desc
    p = tp.render_tt_cover(args.port, c)
    if not p or not os.path.isfile(p):
        raise SystemExit("渲染失败：render_tt_cover 返回 %r" % (p,))
    wh = png_size(p)
    print("✓ 封面 %s" % p)
    print("  尺寸 %sx%s ｜ %.1f KB" % (wh[0], wh[1], os.path.getsize(p) / 1024.0) if wh
          else "  尺寸未知 ｜ %.1f KB" % (os.path.getsize(p) / 1024.0))


if __name__ == "__main__":
    raise SystemExit(main())
