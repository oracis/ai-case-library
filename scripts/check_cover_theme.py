#!/usr/bin/env python
"""校验各平台封面图的底色是否等于 _cover_theme() 分配的 base。

零依赖（zlib 手解 PNG）。只解**前 12 行**像素流 —— 够拿到底色，不用把
3840x2160 全图解完（39 张要几分钟）。

## 采样策略：行众数，不是单点取样

单点取样踩过两次坑：
1. 公众号封面左边有强调竖条（CSS `width:12px`，但封面按 deviceScaleFactor
   约 2.5 倍渲染 → 实际约 38px 宽），采 x=5/x=20 都落在竖条里，读到的是
   强调色 `ac`（浅紫/浅橙）；
2. 小红书卡片有米白外框（`#f6f3ee`），左上角压根不是底色。

所以改成：在 y=SAMPLE_Y 这一行里跳过左右边缘，统计**出现次数最多的颜色**。

## ⚠ 只有头条能真判对错

| 平台 | 图片 | 底色 | 能否像素判定 |
|---|---|---|---|
| 头条 | `out/toutiao/<id>/cover.png` 3840x2160 | 纯 `base` 填充 | ✅ 能 |
| 公众号 | `tmp/covers/<id>.png` 2700x1150 | `linear-gradient(base 0%, band 63%)` **横向渐变** | ❌ 众数落在渐变中段 |
| 小红书 | `out/xhs/<id>/card-1.png` 2160x2880 | 米白外框 + 卡片内底色 | ❌ 众数是外框色 |

所以 `--wechat` / `--xhs` 只打印**读到的颜色**供人工比对，不判 OK/MISMATCH
（判了就是假阳性）。要确认这两个平台的配色，看 `wp._cover_theme()` 的返回
值即可 —— 那是唯一的配色来源，图片是它的渲染结果。

用法：
    python scripts/check_cover_theme.py            # 头条（可判定）
    python scripts/check_cover_theme.py voklit     # 只看某条
    python scripts/check_cover_theme.py --wechat   # 公众号（仅打印）
    python scripts/check_cover_theme.py --xhs      # 小红书（仅打印）
"""
import json
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp  # noqa: E402

MAX_ROWS = 12
# 采样策略：**行众数**，不是单点取样。
# 单点取样踩过两次坑：
#   1. 公众号左边有强调竖条（CSS 12px，实际渲染约 38px）→ 采 x=5/x=20 都
#      落在竖条里，读到强调色 `ac`（浅紫/浅橙）；
#   2. 公众号底色是 `linear-gradient(base 0%, band 63%)`，横向有渐变，
#      任何单点都只能采到某个中间值（实测 x=50 读到 #231c3e，base 是 #221b3d）。
# 小红书更极端：卡片有米白外框，左上角压根不是底色。
# 所以改成：在 y=SAMPLE_Y 这一行里，跳过左右边缘各 EDGE_SKIP 像素，
# 统计**出现次数最多的颜色**。底色占这一行的绝大多数，众数必然是它。
SAMPLE_Y = 5
EDGE_SKIP = 60      # 跳过左侧竖条与右侧装饰
MIN_ROW_PX = 200    # 这一行至少要有这么多像素才采信


def dominant_rgb(path, rows=MAX_ROWS, edge=EDGE_SKIP):
    """解前 rows 行，返回 (y=SAMPLE_Y 行的众数颜色, 宽度, 高度)。"""
    data = open(path, "rb").read()
    pos, idat, w, h, ct = 8, [], None, None, None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, _bd, ct = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    bpp = 4 if ct == 6 else 3
    stride = w * bpp
    dec = zlib.decompressobj()
    raw = dec.decompress(b"".join(idat), (stride + 1) * rows + 64)
    prev = bytearray(stride)
    i, y = 0, 0
    while y < rows and i + 1 + stride <= len(raw):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        prev = line
        if y == SAMPLE_Y:
            lo, hi = edge, max(edge + 1, w - edge)
            if hi - lo < MIN_ROW_PX:
                return None, w, h
            counts = {}
            for x in range(lo, hi, 4):        # 每 4px 采一次，够代表
                key = tuple(line[x * bpp:x * bpp + 3])
                counts[key] = counts.get(key, 0) + 1
            best = max(counts.items(), key=lambda kv: kv[1])[0]
            return "#%02x%02x%02x" % best, w, h
        y += 1
    return None, w, h


# 向后兼容旧名
top_left_rgb = dominant_rgb


def norm(c):
    c = (c or "").strip().lower()
    return c if c.startswith("#") else "#" + c


def main(argv):
    # 默认校验头条横版封面（唯一能真判对错的平台，见 docstring）。
    # 加 --wechat / --xhs 看另外两个平台的图（仅打印，不判对错）。
    args = [a for a in argv[1:] if not a.startswith("--")]
    if "--wechat" in argv:
        key, rel = "wechat", ("tmp", "covers", "%s.png")
    elif "--xhs" in argv:
        key, rel = "xhs", ("out", "xhs", "%s", "card-1.png")
    else:
        key, rel = "toutiao", ("out", "toutiao", "%s", "cover.png")
    # 只有头条是纯 base 填充，能像素判定；公众号是横向渐变、小红书是米白
    # 外框，判了就是假阳性 —— 那边只打印读到的颜色。
    judge = key == "toutiao"
    label = "--" + key

    cases = json.load(open(os.path.join(ROOT, "data", "cases.json"),
                           encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases") or cases.get("items") or []
    only = set(args)
    ids = [c["id"] for c in cases if not only or c["id"] in only]
    bad = []
    for cid in ids:
        exp = norm(wp._cover_theme(cid, key)[0])
        p = os.path.join(ROOT, *rel) % cid
        if not os.path.exists(p):
            print("%-24s 期望 %-9s 磁盘 MISSING" % (cid, exp))
            bad.append((cid, exp, "MISSING"))
            continue
        got, w, h = dominant_rgb(p)
        if not judge:
            print("%-24s 期望 %-9s 磁盘 %-9s %dx%d （渐变/外框，不判）"
                  % (cid, exp, got, w, h))
            continue
        ok = got == exp
        print("%-24s 期望 %-9s 磁盘 %-9s %dx%d %s"
              % (cid, exp, got, w, h, "OK" if ok else "MISMATCH"))
        if not ok:
            bad.append((cid, exp, got))
    print("---")
    if not judge:
        print("[%s] 共 %d 条（该平台底色是渐变/带外框，像素判定不适用，"
              "以上仅供人工比对）" % (label, len(ids)))
        return 0
    print("[%s] 共 %d 条，错 %d 条" % (label, len(ids), len(bad)))
    for b in bad:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
