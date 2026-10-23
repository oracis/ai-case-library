# -*- coding: utf-8 -*-
"""把 zero-dep-icon-gen 生成的多尺寸 PNG 打包成 Windows .ico（纯标准库）。

PyInstaller 的 --icon 要 .ico；skill 的 gen_icons.py 只产 PNG，且一次一个
尺寸，所以这里做第二半：读入各尺寸 PNG → 打包成单文件 ICO。

条目格式默认 **PNG 原样内嵌**（Vista+ 认，体积只有几 KB，且保留 skill 的
超采样抗锯齿）；`--bmp` 则解回 32bpp BMP 条目（老加载器兼容向，256 尺寸
单条目约 270KB）。

用法：
    python -X utf8 gui/png2ico.py -o gui/htm-increment-gui.ico \
        tmp/icon_png/htm-increment-gui-16.png tmp/icon_png/htm-increment-gui-32.png \
        tmp/icon_png/htm-increment-gui-48.png tmp/icon_png/htm-increment-gui-64.png \
        tmp/icon_png/htm-increment-gui-256.png
    # 或通配：python -X utf8 gui/png2ico.py -o out.ico tmp/icon_png/htm-increment-gui-*.png
"""

import argparse
import os
import struct
import sys
import zlib
from array import array


def decode_png(path):
    """解 8bit RGBA（color type 6）非隔行 PNG，返回 (w, h, rows[list[bytes]])。"""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s 不是 PNG" % path)
    pos = 8
    w = h = None
    idat = bytearray()
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype != 6:
                raise ValueError("%s 只支持 8bit RGBA（depth=%d ctype=%d）"
                                 % (path, depth, ctype))
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    stride = w * 4
    rows = []
    prev = bytearray(stride)
    off = 0
    for _ in range(h):
        f = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        if f == 0:
            pass
        elif f == 1:  # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif f == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:  # Average
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:  # Paeth
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ValueError("%s 未知行滤波 %d" % (path, f))
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def bmp_entry(rows, w, h):
    """32bpp BMP 条目（BGRA 底向上 + 全零 AND 掩码）。

    坑：PNG 行是 RGBA，BMP 像素序是 BGRA——直接拷贝会把红蓝对调
    （靛蓝变三文鱼，2026-10-23 实测踩过）。这里按 32bit 字交换 R/B。
    """
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        words = array("I", rows[y])   # 小端字：R | G<<8 | B<<16 | A<<24
        xor += array("I", [(v & 0xFF00FF00) | ((v & 0xFF) << 16)
                           | ((v >> 16) & 0xFF) for v in words]).tobytes()
    and_row = ((w + 31) // 32) * 4
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                      len(xor) + and_row * h, 0, 0, 0, 0)
    return bytes(hdr) + bytes(xor) + bytes(and_row * h)


def write_ico(path, entries):
    """entries: [(size, bytes)]，按尺寸升序。"""
    entries = sorted(entries, key=lambda e: e[0])
    count = len(entries)
    offset = 6 + 16 * count
    dir_b = b""
    blob = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size
        dir_b += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blob += data
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, count))
        f.write(dir_b)
        f.write(blob)


def size_from_name(path):
    """从文件名尾部提取尺寸（htm-increment-gui-256.png → 256）。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    tail = stem.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="多尺寸 PNG → 单文件 ICO")
    ap.add_argument("-o", "--out", required=True, help="输出 .ico 路径")
    ap.add_argument("--bmp", action="store_true",
                    help="用 32bpp BMP 条目而非 PNG 内嵌（默认 PNG）")
    ap.add_argument("pngs", nargs="+", help="各尺寸 PNG 路径")
    args = ap.parse_args(argv)

    entries = []
    for p in args.pngs:
        size = size_from_name(p)
        if size is None:
            print("[!] %s 文件名尾部没有尺寸数字，跳过" % p)
            continue
        if args.bmp:
            w, h, rows = decode_png(p)
            if (w, h) != (size, size):
                print("[!] %s 实际 %dx%d 与文件名尺寸 %d 不符，跳过" % (p, w, h, size))
                continue
            entries.append((size, bmp_entry(rows, w, h)))
        else:
            entries.append((size, open(p, "rb").read()))
    if not entries:
        print("[!] 没有可用条目")
        return 1
    write_ico(args.out, entries)
    total = sum(len(d) for _, d in entries)
    print("[OK] %s" % args.out)
    print("     条目 %s，共 %.1f KB（%s）"
          % ("/".join(str(s) for s, _ in sorted(entries)), total / 1024,
             "BMP" if args.bmp else "PNG"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
