# -*- coding: utf-8 -*-
"""gen_logo.py 的几何与幂等测试。

背景（2026-10-15）：品牌 logo 从「白卡片三横线」换成「两张卡片错位叠放」
（读作「拆解」）。造型改了以后有两类退化是**肉眼看小图发现不了的**：

1. **错位丢了** —— 上下两张卡片如果对齐了，就退化成两张一样的卡片，
   「拆解」这层意思整个没了，但图看着还挺正常。所以必须钉死
   「上卡左边缘 ≠ 下卡左边缘」。
2. **两张卡片粘在一起** —— 中间那道缝本来是镂空（露底色），如果哪天
   计算写错把两卡连成一片，缩小后就是一根白条，同样看不出问题。

另外两个性质用于保证「重跑不捣乱」：
- **确定性**：同一尺寸两次渲染必须逐字节相同，否则每次部署图标都在变。
- **安全圈**：内容收在 maskable 安全圈内，manifest 才能声明 maskable。

不联网。用法：python scripts/test_gen_logo.py
"""
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import gen_logo as gl  # noqa: E402

passed = 0
fails = []


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def px(rows, size, fx, fy):
    """取渲染结果的相对坐标像素。"""
    x = int(fx * size)
    y = int(fy * size)
    o = x * 3
    r = rows[y]
    return (r[o], r[o + 1], r[o + 2])


def is_white(p):
    return sum(p) > 720


def solid_rows(size, rgb):
    row = bytes(rgb) * size
    return [row for _ in range(size)]


def test_geometry():
    size = 64
    rows = gl.render_pixels(size)
    # 探针点由模块导出的几何推导 —— 不要硬编码坐标，
    # 否则设计一改（或缩放系数变）测试就假红。
    tx0, ty0, tx1, ty1 = gl.TOP
    bx0, by0, bx1, by1 = gl.BOT

    cy_top = (ty0 + ty1) / 2.0
    cy_bot = (by0 + by1) / 2.0
    chk("上卡内部为白", is_white(px(rows, size, 0.50, cy_top)))
    chk("下卡内部为白", is_white(px(rows, size, 0.50, cy_bot)))

    # 两卡之间的缝必须是底色（蓝），证明是镂空不是连成一片
    gap_y = (ty1 + by0) / 2.0
    gap = px(rows, size, 0.50, gap_y)
    chk("两卡之间是镂空（非白）", not is_white(gap), str(gap))

    # 错位：上卡左边缘比下卡更靠左
    probe_x = (tx0 + bx0) / 2.0          # 落在两卡左边缘之间
    top_left = px(rows, size, probe_x, cy_top)
    bot_left = px(rows, size, probe_x, cy_bot)
    chk("错位成立（上卡左伸、下卡内缩）",
        is_white(top_left) and not is_white(bot_left),
        "x=%.3f top=%s bot=%s" % (probe_x, top_left, bot_left))

    # 右侧同理：下卡比上卡右伸
    probe_r = (tx1 + bx1) / 2.0
    top_right = px(rows, size, probe_r, cy_top)
    bot_right = px(rows, size, probe_r, cy_bot)
    chk("错位成立（上卡右缩、下卡右伸）",
        not is_white(top_right) and is_white(bot_right),
        "x=%.3f top=%s bot=%s" % (probe_r, top_right, bot_right))

    # 四角必须是底色：内容不能贴边
    for fx, fy, what in [(0.04, 0.04, "左上"), (0.96, 0.04, "右上"),
                         (0.04, 0.96, "左下"), (0.96, 0.96, "右下")]:
        chk("角落留白（%s）" % what, not is_white(px(rows, size, fx, fy)))


def test_maskable_safe_circle():
    """内容须收在中心 80% 直径内，manifest 才能声明 maskable。"""
    size = 96
    rows = gl.render_pixels(size)
    cx = cy = size / 2.0
    limit = 0.40 * size          # 安全圈半径 = 80% 直径 / 2
    worst = None
    for y in range(size):
        for x in range(size):
            o = x * 3
            if is_white((rows[y][o], rows[y][o + 1], rows[y][o + 2])):
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                if d > limit:
                    worst = (x, y, round(d, 2))
                    break
        if worst:
            break
    chk("白色内容不越 maskable 安全圈", worst is None,
        "" if worst is None else "越界点 %s" % (worst,))

    # 也不能缩太小 —— 内容应占到安全圈的 7 成以上，否则图标显得空
    maxr = gl._max_radius([gl.TOP, gl.BOT])
    chk("造型利用率合理（占安全圈 70%+）", maxr >= 0.28,
        "最远角半径 %.3f" % maxr)


def test_determinism():
    a = gl.render_pixels(48)
    b = gl.render_pixels(48)
    chk("同尺寸两次渲染逐字节一致", a == b)


def test_probe_catches_blank():
    """probe 必须能拦住「一整块纯蓝」这种废图。"""
    import tempfile
    size = 64
    with tempfile.TemporaryDirectory() as td:
        blank = os.path.join(td, "blank.png")
        gl.write_png(blank, size, solid_rows(size, gl.C_TOP))
        err = gl.probe(blank)
        chk("probe 拦下纯色废图", err is not None, str(err))

        # 正常图必须通过
        good = os.path.join(td, "good.png")
        gl.write_png(good, size, gl.render_pixels(size))
        chk("probe 放行正常图", gl.probe(good) is None, str(gl.probe(good)))


def test_png_header():
    import tempfile
    size = 32
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "h.png")
        gl.write_png(p, size, gl.render_pixels(size))
        with open(p, "rb") as f:
            head = f.read(33)
    chk("PNG 魔数正确", head[:8] == b"\x89PNG\r\n\x1a\n")
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    chk("正方形 8bit RGB", w == h == size and depth == 8 and ctype == 2,
        "%dx%d depth=%d ctype=%d" % (w, h, depth, ctype))


def test_targets_declared():
    names = [n for n, _ in gl.TARGETS]
    chk("含网站三枚图标",
        all(n in names for n in ("icon-192.png", "icon-512.png", "icon-180.png")),
        str(names))
    chk("含小程序头像 144", "logo-144.png" in names)
    big = dict(gl.TARGETS)["icon-512.png"]
    chk("512 图标尺寸正确", big == 512)


def test_wrapper_delegates():
    """gen_pwa_icons 不再自己画图，必须和 gen_logo 造型一致。"""
    import gen_pwa_icons as gp
    chk("gen_pwa_icons 委托 gen_logo",
        getattr(gp, "gen_logo", None) is gl)


def main():
    print("== 几何 ==")
    test_geometry()
    print("== 安全圈 ==")
    test_maskable_safe_circle()
    print("== 幂等 ==")
    test_determinism()
    print("== 产物校验 ==")
    test_probe_catches_blank()
    test_png_header()
    test_targets_declared()
    test_wrapper_delegates()
    print("\n[%s] %d 通过, %d 失败" %
          ("OK" if not fails else "FAIL", passed, len(fails)))
    if fails:
        for n in fails:
            print("  - " + n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
