# -*- coding: utf-8 -*-
"""生成品牌 logo（网站图标 + 微信小程序头像）。

和 gen_pwa_icons.py 的关系：
    这里是**造型的唯一来源**，gen_pwa_icons.py 会从这里复用渲染器。
    对外 logo（小程序头像）和站内图标用同一张图，避免「网站一个样、
    小程序另一个样」。

造型（2026-10-15 定稿，替换旧的「白卡片三横线」）：
    品牌蓝渐变底 + 两张白色圆角卡片错位叠放，各留一道镂空缝。
    - 上方卡片左对齐、下方卡片右移，中间错开 —— 读作「一张卡片
      正在被拆开」，正是站点的动作（拆解海外软件生意）；
    - 缝是**真的镂空**（露出底色而不是画一条蓝线），缩放时不糊；
    - 造型按设计意图书写，渲染前自动等比缩放居中到 maskable 安全圈内
      （最远角半径 0.385 < 0.40），因此同一个文件可以同时声明
      purpose="any maskable"，小程序把图裁成圆角也不会切到内容。

为什么不用 Pillow：本项目是「stdlib 零依赖」路线（见 gen_pwa_icons.py），
图标脚本要在任何机器上能重跑，一个图标不值得引入重量级依赖。
PNG 编码只需 zlib + struct。

用法：
    python scripts/gen_logo.py            # 写 static/icons/*.png
    python scripts/gen_logo.py --check    # 只校验已有文件，不生成
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

# 几何：写的是**设计意图**（相对边长的比例），渲染前会整体缩放居中到
# 安全圈内（见 _resolved_boxes），所以这里改大了也不会画到边上去。
# 上卡左对齐、下卡右移，形成错位 —— 错位幅度是「拆解」这层意思的全部来源。
RAW_TOP = (0.215, 0.185, 0.785, 0.472)   # x0, y0, x1, y1
RAW_BOT = (0.255, 0.528, 0.825, 0.815)
RAW_CARD_R = 0.082                        # 卡片圆角
RAW_SLOT_TOP = (0.300, 0.275, 0.520, 0.303)   # 上卡的镂空缝
RAW_SLOT_BOT = (0.460, 0.618, 0.720, 0.646)
RAW_SLOT_R = 0.014

# maskable 安全圈：直径 80% ⇒ 半径 0.40。留 0.015 余量。
SAFE_R = 0.385

SS = 2  # 超采样倍数：2x 渲染后盒式降采样，圆角边才不锯齿


def _corners(box):
    x0, y0, x1, y1 = box
    return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]


def _max_radius(boxes):
    """所有矩形的角离画面中心的最远距离。"""
    worst = 0.0
    for b in boxes:
        for cx, cy in _corners(b):
            d = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5
            worst = max(worst, d)
    return worst


def _resolved_boxes():
    """把设计意图等比缩放 + 居中，保证最远角落在 SAFE_R 内。

    为什么要自动缩放而不是手填坐标：手填时「内容是否出安全圈」全靠人算，
    实际第一次就写错了（下卡右下角到 0.4526，超出 0.40 一大截），而图上
    完全看不出来 —— 只有小程序把图裁成圆时才会发现被切。这里让代码兜住。
    返回 (top, bot, slot_top, slot_bot, card_r, slot_r, scale)。
    """
    raw = [RAW_TOP, RAW_BOT]
    k = SAFE_R / _max_radius(raw)

    def xf(b):
        return tuple(0.5 + (v - 0.5) * k for v in b)

    return (xf(RAW_TOP), xf(RAW_BOT), xf(RAW_SLOT_TOP), xf(RAW_SLOT_BOT),
            RAW_CARD_R * k, RAW_SLOT_R * k, k)


(TOP, BOT, SLOT_TOP, SLOT_BOT, CARD_R, SLOT_R, SCALE) = _resolved_boxes()


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _in_rrect(x, y, box, r, size):
    """点 (x,y) 是否落在圆角矩形 box 内。x/y 是像素中心。"""
    x0, y0, x1, y1 = (v * size for v in box)
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    rr = r * size
    # 四个角：落在角圆域内才算
    cx = x0 + rr if x < x0 + rr else (x1 - rr if x > x1 - rr else x)
    cy = y0 + rr if y < y0 + rr else (y1 - rr if y > y1 - rr else y)
    if cx == x and cy == y:
        return True
    return (x - cx) ** 2 + (y - cy) ** 2 <= rr ** 2


def _render_hires(size):
    """逐像素算一遍（size 已是超采样后尺寸）。返回行列表 bytes。"""
    rows = []
    for y in range(size):
        row = bytearray()
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            t = (px + py) / (2.0 * size)          # 对角渐变，与 CSS 135deg 同向
            rgb = _lerp(C_TOP, C_BOT, t)
            in_card = (_in_rrect(px, py, TOP, CARD_R, size) or
                       _in_rrect(px, py, BOT, CARD_R, size))
            if in_card:
                # 缝是真镂空：落进缝里就不涂白，保留底色
                if not (_in_rrect(px, py, SLOT_TOP, SLOT_R, size) or
                        _in_rrect(px, py, SLOT_BOT, SLOT_R, size)):
                    rgb = C_CARD
            row += bytes(rgb)
        rows.append(bytes(row))
    return rows


def render_pixels(size):
    """2x 超采样 + 盒式降采样，去掉圆角锯齿。"""
    big = _render_hires(size * SS)
    out = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0]
            for dy in range(SS):
                r = big[y * SS + dy]
                for dx in range(SS):
                    o = (x * SS + dx) * 3
                    acc[0] += r[o]; acc[1] += r[o + 1]; acc[2] += r[o + 2]
            n = SS * SS
            row += bytes((acc[0] // n, acc[1] // n, acc[2] // n))
        out.append(bytes(row))
    return out


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def render_svg():
    """输出同一造型的 SVG（矢量，给网页侧栏品牌块用）。

    和 PNG 共用 TOP/BOT/SLOT_* 这组解析后的几何 —— 网页上的小图标和
    小程序头像永远是同一个图形，不会因为改了一处忘了另一处而跑偏。
    缝用 mask 挖（真镂空），和 PNG 的逐像素判定等价。
    """
    def rrect(box, r, fill):
        x0, y0, x1, y1 = (v * 100 for v in box)
        return ('<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" rx="%.4f" '
                'fill="%s"/>' % (x0, y0, x1 - x0, y1 - y0, r * 100, fill))

    cards = rrect(TOP, CARD_R, "#fff") + rrect(BOT, CARD_R, "#fff")
    # mask：黑=透明，白=保留。先铺白，再用黑把两道缝挖掉。
    cut = ('<rect width="100" height="100" fill="#fff"/>' +
           rrect(SLOT_TOP, SLOT_R, "#000") + rrect(SLOT_BOT, SLOT_R, "#000"))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'role="img" aria-label="拆解海外">'
        '<defs>'
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="%s"/>'
        '<stop offset="1" stop-color="%s"/>'
        '</linearGradient>'
        '<mask id="cut">%s</mask>'
        '</defs>'
        '<rect width="100" height="100" fill="url(#bg)"/>'
        '<g mask="url(#cut)">%s</g>'
        '</svg>' % (_hex(C_TOP), _hex(C_BOT), cut, cards)
    )


def write_svg(path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_svg())


def _chunk(tag, data):
    blob = tag + data
    return (struct.pack(">I", len(data)) + blob +
            struct.pack(">I", zlib.crc32(blob) & 0xFFFFFFFF))


def write_png(path, size, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n" +
            _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(raw, 9)) +
            _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(blob)


def _sample_px(path, fx, fy):
    """读 PNG 里相对坐标 (fx,fy) 的像素。需要解码，只看 IDAT。"""
    with open(path, "rb") as f:
        data = f.read()
    w, h = struct.unpack(">II", data[16:24])
    # 顺序拼接所有 IDAT
    idat = b""
    i = 8
    while i < len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        if tag == b"IDAT":
            idat += data[i + 8:i + 8 + ln]
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 3
    x = int(fx * w); y = int(fy * h)
    off = y * (stride + 1) + 1 + x * 3  # +1 是行 filter 字节
    return tuple(raw[off:off + 3])


def probe(path):
    """校验 PNG 结构，并抽查特征点，防止写出「一块纯蓝」还不自知。

    采样点从几何推导（不硬编码）—— 造型或缩放系数一变，硬编码的点
    可能落到卡片外面，测试就会毫无道理地红，反而掩盖真问题。
    """
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return "不是合法 PNG"
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    if depth != 8 or ctype != 2:
        return "色深/类型异常 depth=%d ctype=%d" % (depth, ctype)
    if w != h:
        return "非正方形 %dx%d" % (w, h)

    tx0, ty0, tx1, ty1 = TOP
    bx0, by0, _, by1 = BOT
    checks = [
        ((tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0, True, "上卡中心"),
        (tx0 + 0.02, (ty0 + ty1) / 2.0, True, "上卡左缘内侧"),
        (0.5, (ty1 + by0) / 2.0, False, "两卡之间"),
        (0.5, by0 + 0.02, True, "下卡上缘内侧"),
        (bx0 + 0.02, (by0 + by1) / 2.0, True, "下卡左缘内侧"),
    ]
    for fx, fy, want_white, what in checks:
        px = _sample_px(path, fx, fy)
        is_white = sum(px) > 720
        if is_white != want_white:
            return "%s 像素异常 %s（期望%s）" % (what, px,
                                            "白" if want_white else "蓝")
    return None


def probe_svg(path):
    """SVG 结构自检：必须有渐变底、mask、两张卡片。"""
    with open(path, encoding="utf-8") as f:
        s = f.read()
    if not s.startswith("<svg") or not s.rstrip().endswith("</svg>"):
        return "不是完整 SVG"
    for token, what in [("<linearGradient", "渐变底"), ('mask id="cut"', "镂空 mask"),
                        ('mask="url(#cut)"', "mask 引用")]:
        if token not in s:
            return "缺少%s" % what
    if s.count("<rect") < 5:   # 底1 + 卡2 + 缝2
        return "rect 数量异常（%d）" % s.count("<rect")
    return None


# (文件名, 尺寸) —— 192/512/180 给网站，144 给小程序头像
TARGETS = [
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("icon-180.png", 180),
    ("logo-144.png", 144),
]

SVG_NAME = "logo.svg"


def main():
    check_only = "--check" in sys.argv
    if not check_only:
        os.makedirs(OUT_DIR, exist_ok=True)
    bad = 0
    for name, s in TARGETS:
        path = os.path.join(OUT_DIR, name)
        if check_only:
            if not os.path.isfile(path):
                print("  缺失 %s" % path)
                bad += 1
                continue
            err = probe(path)
            print("  %s %s%s" % ("OK " if not err else "BAD", path,
                                 "" if not err else " -> " + err))
            if err:
                bad += 1
            continue
        write_png(path, s, render_pixels(s))
        err = probe(path)
        print("  %s %s (%d×%d, %d bytes)%s" % (
            "OK " if not err else "BAD", path, s, s, os.path.getsize(path),
            "" if not err else " -> " + err))
        if err:
            bad += 1

    # SVG：网页侧栏品牌块用，和上面几张 PNG 同造型
    svg_path = os.path.join(OUT_DIR, SVG_NAME)
    if check_only:
        if not os.path.isfile(svg_path):
            print("  缺失 %s" % svg_path)
            bad += 1
        else:
            err = probe_svg(svg_path)
            print("  %s %s%s" % ("OK " if not err else "BAD", svg_path,
                                 "" if not err else " -> " + err))
            if err:
                bad += 1
    else:
        write_svg(svg_path)
        err = probe_svg(svg_path)
        print("  %s %s (%d bytes)%s" % (
            "OK " if not err else "BAD", svg_path, os.path.getsize(svg_path),
            "" if not err else " -> " + err))
        if err:
            bad += 1

    total = len(TARGETS) + 1
    if bad:
        print("[!] %d 个文件有问题" % bad)
        return 1
    print("[OK] 品牌 logo 就绪（%d 个）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
