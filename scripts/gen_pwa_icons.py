# -*- coding: utf-8 -*-
"""生成 PWA 图标（manifest + apple-touch-icon）。

为什么不用 Pillow：本项目是「stdlib 零依赖」路线，一个图标不值得引入
重量级依赖，而且脚本要能在任何机器上重跑（改品牌色/改造型时）。
PNG 编码只需要 zlib + struct，四十行搞定。

造型（2026-10-14 定稿）：
    品牌蓝渐变底 + 中央白色圆角卡片 + 卡片内三条蓝色横线。
    - 白卡片 = 「一条案例」，三条横线 = 拆解后的条目，和站内信息架构同形；
    - 所有内容收在中央 40% 半径内，因此同一个文件可以同时声明
      purpose="any maskable"（maskable 的安全圈是直径 80%）；
    - 桌面侧栏品牌块是同一个蓝色的圆角方块，图标和界面能认亲。

用法：
    python scripts/gen_pwa_icons.py            # 写 static/icons/*.png
    python scripts/gen_pwa_icons.py --check    # 只校验已有文件，不生成
"""
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "static", "icons")

# 品牌色：取自 static/style.css 的 .brand-mark 渐变两端
C_TOP = (47, 111, 208)     # #2f6fd0
C_BOT = (29, 75, 143)      # #1d4b8f
C_CARD = (255, 255, 255)
C_LINE = (47, 111, 208)    # 卡片内的线用主品牌色，压住白底
C_LINE_DIM = (150, 180, 220)  # 第三条线淡一档，读出「次要信息」的层次

# 卡片几何（相对边长的比例）
CARD = 0.50      # 卡片边长
RAD = 0.100      # 圆角半径
LINE_LEN = 0.26  # 横线长度
LINE_TH = 0.038  # 横线粗细
LINE_GAP = 0.092  # 横线间距


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _inside_card(x, y, size):
    """圆角矩形判定。x/y 是像素中心（+0.5 后比较）。"""
    cx = size / 2.0
    half = CARD * size / 2.0
    rad = RAD * size
    dx = abs(x - cx)
    dy = abs(y - cx)
    if dx > half or dy > half:
        return False
    # 落在四角圆域：圆内算卡片内，圆外不算。
    # ⚠ 曾经把这行的比较写成「>」（返回"在圆外"的布尔值），结果四角圆外被
    # 涂成白色、圆内反而是背景色 —— 纯看字节数和 PNG 头完全发现不了。
    if dx > half - rad and dy > half - rad:
        return (dx - (half - rad)) ** 2 + (dy - (half - rad)) ** 2 <= rad ** 2
    return True


def _inside_bar(x, y, size, idx):
    """第 idx 条横线（0/1/2，自上而下）的判定。"""
    cx = size / 2.0
    cy = size / 2.0
    half_len = LINE_LEN * size / 2.0
    th = LINE_TH * size / 2.0
    cy_i = cy + (idx - 1) * LINE_GAP * size
    return abs(x - cx) <= half_len and abs(y - cy_i) <= th


def render_pixels(size):
    """逐像素算出整张图。size 不大（<=512），纯循环足够快。"""
    rows = []
    for y in range(size):
        row = bytearray()
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            # 对角渐变：左上偏亮、右下偏深
            t = (px + py) / (2.0 * size)
            rgb = _lerp(C_TOP, C_BOT, t)
            if _inside_card(px, py, size):
                rgb = C_CARD
                for i in range(3):
                    if _inside_bar(px, py, size, i):
                        rgb = C_LINE_DIM if i == 2 else C_LINE
                        break
            row += bytes(rgb)
        rows.append(bytes(row))
    return rows


def _chunk(tag, data):
    blob = tag + data
    return (struct.pack(">I", len(data)) + blob +
            struct.pack(">I", zlib.crc32(blob) & 0xFFFFFFFF))


def write_png(path, size, rows):
    raw = b"".join(b"\x00" + r for r in rows)  # 每行 filter type 0
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n" +
            _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(raw, 9)) +
            _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)


def probe(path):
    """读回 PNG 头部，确认尺寸与色彩类型，防止写出坏文件还不自知。"""
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return "不是合法 PNG"
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    if depth != 8 or ctype != 2:
        return "色深/类型异常 depth=%d ctype=%d" % (depth, ctype)
    if w != h:
        return "非正方形 %dx%d" % (w, h)
    return None


def main():
    check_only = "--check" in sys.argv
    sizes = (192, 512, 180)  # 192/512 给 manifest，180 给 apple-touch-icon
    if not check_only:
        os.makedirs(OUT_DIR, exist_ok=True)
    bad = 0
    for s in sizes:
        path = os.path.join(OUT_DIR, "icon-%d.png" % s)
        if check_only:
            if not os.path.isfile(path):
                print("  缺失 %s" % path)
                bad += 1
                continue
            err = probe(path)
            print("  %s %s" % ("OK " if not err else "BAD", path) +
                  ("" if not err else " -> " + err))
            if err:
                bad += 1
            continue
        rows = render_pixels(s)
        write_png(path, s, rows)
        err = probe(path)
        print("  %s %s (%d×%d, %d bytes)" %
              ("OK " if not err else "BAD", path, s, s, os.path.getsize(path)) +
              ("" if not err else " -> " + err))
        if err:
            bad += 1
    if bad:
        print("[!] %d 个文件有问题" % bad)
        return 1
    print("[OK] 图标就绪（%d 个）" % len(sizes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
