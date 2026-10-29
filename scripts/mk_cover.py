#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画「一份 Markdown 源稿 → 四个技术社区」的封面示意图（纯几何、零依赖）。

为什么固化进 scripts/（2026-10-29）
----------------------------------
这张图是阿里云《同一份 Markdown 分发到 4 个技术社区…》的封面 + 正文首图
（article/1776574），原脚本只活在 `tmp/` 里，而 `tmp/` 和 `out/` 都被
.gitignore 盖住 ⇒ 换台机器或清一次 out/ 就再也做不出同一张图，以后改版、
补发到别的平台、或要换尺寸重出都得从零再猜一遍配色和坐标。

为什么不是截图 / AI 生图
------------------------
· 1920 宽的编辑器截图缩到 200*120 后文字全糊，发出去是事故（10-29 实测过一版）。
· AI 生图一次 5-10 积分，而这只是 200*120 的栏目图，几何色块够用且可复算。

设计取舍
--------
纯色块 + 直角 ⇒ 任何缩放档位都不出彩边；背景用浅灰（238,242,247）不去碰近黑区，
缩略图里不会糊成一团黑。5:3（600*360）同时满足腾讯云 336*200（1.68:1）与
阿里云不限定尺寸的要求。

依赖
----
只用标准库 `zlib` + `struct` 手写 8 位 RGB PNG，不装 Pillow/OpenCV。
原脚本 import 了本机 skill 路径下的 imgtool，别人克隆跑不了，故重写编码部分。

用法
----
    python scripts/mk_cover.py                      # 默认输出到 out/longtail/
    python scripts/mk_cover.py --out D:/x/cover.png
"""
from __future__ import annotations

import argparse
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 画布与配色：改尺寸就改这四个常量（布局按 600*360 手工量的坐标，等比缩放需重排）
W, H = 600, 360
BG = (238, 242, 247)
CARD_BG = (255, 255, 255)
LINE = (201, 211, 224)
DARK = (78, 94, 116)
ACCENT = (22, 93, 255)
# 四个色条对应四个社区（B站蓝 / 掘金绿 / 头条橙 / 小红书红），只作视觉区分
PLATFORMS = [(22, 93, 255), (0, 180, 42), (255, 125, 0), (245, 63, 63)]


def write_png(path, w, h, rgb):
    """写 8 位真彩 PNG（color type 2），每行 filter=None。

    rgb 是 length == w*h*3 的 bytes，按 R,G,B 顺序紧密排列。
    """
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                       # 行过滤器 0（None）即可，色块图不需要优化
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", ihdr) +
           chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def new_canvas(w, h, rgb):
    return bytearray(bytes(rgb) * (w * h))


def fill(buf, w, h, x0, y0, x1, y1, rgb):
    """闭区间矩形填色（自动裁到画布内）。"""
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w - 1, x1), min(h - 1, y1)
    if x1 < x0 or y1 < y0:
        return
    row = bytes(rgb) * (x1 - x0 + 1)
    for y in range(y0, y1 + 1):
        off = (y * w + x0) * 3
        buf[off:off + len(row)] = row


def draw(buf, w, h):
    """三段式构图：左边的源稿 → 中间主干分叉 → 右边四个目标卡片。"""
    # 左：一份 Markdown 文档（标题条 + 6 行正文线 + 一段“代码”）
    fill(buf, w, h, 56, 66, 214, 294, CARD_BG)
    fill(buf, w, h, 56, 66, 214, 86, ACCENT)
    for i in range(6):
        y = 108 + i * 30
        line_w = 130 if i % 3 else 92
        fill(buf, w, h, 78, y, 78 + line_w, y + 9, LINE)
    fill(buf, w, h, 78, 246, 120, 258, ACCENT)

    # 中：主干 + 文档到主干的一根横线
    fill(buf, w, h, 258, 90, 261, 270, DARK)
    fill(buf, w, h, 214, 178, 258, 181, DARK)

    # 右：四张卡片（左侧色条 + 两行“文字” + 从主干引出的分支）
    for i, color in enumerate(PLATFORMS):
        top = 56 + i * 66
        fill(buf, w, h, 344, top, 544, top + 50, CARD_BG)
        fill(buf, w, h, 344, top, 352, top + 50, color)
        fill(buf, w, h, 366, top + 14, 470, top + 21, LINE)
        fill(buf, w, h, 366, top + 30, 430, top + 37, LINE)
        mid = top + 25
        fill(buf, w, h, 261, mid, 344, mid + 2, DARK)


def main():
    ap = argparse.ArgumentParser(description="生成长尾技术稿的封面示意图")
    ap.add_argument("--out", default=os.path.join(
        ROOT, "out", "longtail", "markdown-structure-cover.png"),
        help="输出路径（默认 out/longtail/markdown-structure-cover.png）")
    args = ap.parse_args()

    buf = new_canvas(W, H, BG)
    draw(buf, W, H)
    out = os.path.abspath(args.out)
    parent = os.path.dirname(out)
    if not os.path.isdir(parent):
        raise SystemExit("输出目录不存在: %s" % parent)
    write_png(out, W, H, bytes(buf))
    print("written %s (%dx%d, %.1f KB)" % (out, W, H, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
