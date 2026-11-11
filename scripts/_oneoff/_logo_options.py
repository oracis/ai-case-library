# -*- coding: utf-8 -*-
"""一次性脚本：渲染多个 logo 候选方案，供人工挑选。

造型全部沿用站内品牌蓝渐变（style.css .brand-mark），保证和网站认亲。
渲染用 4x 超采样再 LANCZOS 降采样，边缘才不锯齿（144 尺寸下尤其明显）。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "tmp", "logo-options")
S = 512
K = 4
F = float(S * K)

C_TOP = (47, 111, 208)
C_BOT = (29, 75, 143)
C_INK = (255, 255, 255)

FONT = r"C:\Windows\Fonts\msyhbd.ttc"


def gradient(size):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * (size - 1))
            px[x, y] = tuple(
                int(round(C_TOP[i] + (C_BOT[i] - C_TOP[i]) * t)) for i in range(3)
            )
    return img


BG = gradient(S)


def build(draw_marks):
    wm = Image.new("L", (S * K, S * K), 0)
    bm = Image.new("L", (S * K, S * K), 0)
    draw_marks(ImageDraw.Draw(wm), ImageDraw.Draw(bm), F)
    wm = wm.resize((S, S), Image.LANCZOS)
    bm = bm.resize((S, S), Image.LANCZOS)
    img = Image.composite(Image.new("RGB", (S, S), C_INK), BG, wm)
    return Image.composite(Image.new("RGB", (S, S), C_TOP), img, bm)


def rr(d, box, r, fill=255):
    d.rounded_rectangle([box[0] * F, box[1] * F, box[2] * F, box[3] * F],
                        radius=r * F, fill=fill)


def el(d, cx, cy, r, fill=255):
    d.ellipse([(cx - r) * F, (cy - r) * F, (cx + r) * F, (cy + r) * F], fill=fill)


def ln(d, p1, p2, w):
    d.line([p1[0] * F, p1[1] * F, p2[0] * F, p2[1] * F], fill=255, width=int(w * F))


def poly(d, pts):
    d.polygon([(x * F, y * F) for x, y in pts], fill=255)


# --- 0. 现状：案字 / 卡片图标 --------------------------------------------
def d_word(dw, db, f):
    font = ImageFont.truetype(FONT, int(0.60 * S * K))
    l, t, r, b = font.getbbox("案")
    dw.text(((S * K - (r - l)) / 2.0 - l, (S * K - (b - t)) / 2.0 - t),
            "案", font=font, fill=255)


def d_card(dw, db, f):
    rr(dw, (0.25, 0.25, 0.75, 0.75), 0.10)
    for i, (y, dim) in enumerate([(0.375, False), (0.475, False), (0.575, True)]):
        tgt = db if not dim else db
        tgt.rounded_rectangle(
            [0.37 * F, (y - 0.019) * F, 0.63 * F, (y + 0.019) * F],
            radius=0.019 * F, fill=255)


# --- 1. 拆解：卡片被拆成两片，错位露出缝隙 --------------------------------
def d_teardown(dw, db, f):
    rr(dw, (0.215, 0.185, 0.785, 0.472), 0.082)
    rr(dw, (0.255, 0.528, 0.825, 0.815), 0.082)
    # 两片各留一道蓝缝，读出「被拆开、还没拼回去」
    db.rounded_rectangle([0.30 * F, 0.275 * F, 0.52 * F, 0.303 * F],
                         radius=0.014 * F, fill=255)
    db.rounded_rectangle([0.46 * F, 0.618 * F, 0.72 * F, 0.646 * F],
                         radius=0.014 * F, fill=255)


# --- 2. 双轴评分：站点招牌（能不能一个人做 × 能不能搬回国内）--------------
def d_score(dw, db, f):
    t = 0.072
    dw.rectangle([0.245 * F, 0.195 * F, (0.245 + t) * F, 0.778 * F], fill=255)
    dw.rectangle([0.245 * F, (0.778 - t) * F, 0.798 * F, 0.778 * F], fill=255)
    for cx, cy, r in [(0.405, 0.620, 0.033), (0.550, 0.505, 0.033),
                      (0.700, 0.330, 0.043)]:
        el(dw, cx, cy, r)


# --- 3. 出海：地球 + 穿出的箭头 -----------------------------------------
def d_globe(dw, db, f):
    cx, cy, r = 0.430, 0.552, 0.238
    dw.ellipse([(cx - r) * F, (cy - r) * F, (cx + r) * F, (cy + r) * F],
               outline=255, width=int(0.056 * F))
    dw.rectangle([(cx - r) * F, (cy - 0.017) * F, (cx + r) * F, (cy + 0.017) * F],
                 fill=255)
    dw.ellipse([(cx - 0.104) * F, (cy - r) * F, (cx + 0.104) * F, (cy + r) * F],
               outline=255, width=int(0.028 * F))
    ln(dw, (0.520, 0.480), (0.760, 0.245), 0.062)
    poly(dw, [(0.845, 0.160), (0.712, 0.208), (0.792, 0.288)])


# --- 4. 核实：放大镜 + 文档 ---------------------------------------------
def d_magnifier(dw, db, f):
    cx, cy, r = 0.452, 0.432, 0.212
    el(dw, cx, cy, r)
    db.rounded_rectangle([(cx - 0.112) * F, (cy - 0.072) * F,
                          (cx + 0.112) * F, (cy - 0.034) * F],
                         radius=0.019 * F, fill=255)
    db.rounded_rectangle([(cx - 0.112) * F, (cy + 0.006) * F,
                          (cx + 0.112) * F, (cy + 0.044) * F],
                         radius=0.019 * F, fill=255)
    ln(dw, (0.598, 0.582), (0.788, 0.775), 0.074)


# --- 5. 增长：三级柱状 + 上行箭头 ---------------------------------------
def d_bars(dw, db, f):
    base, wd = 0.790, 0.112
    for x, h in [(0.215, 0.170), (0.383, 0.262), (0.551, 0.372)]:
        rr(dw, (x, base - h, x + wd, base), 0.032)
    ln(dw, (0.625, 0.415), (0.795, 0.235), 0.052)
    poly(dw, [(0.845, 0.180), (0.735, 0.205), (0.802, 0.272)])


OPTIONS = [
    ("00-现状-案字", d_word),
    ("01-拆解-错位卡片", d_teardown),
    ("02-双轴评分", d_score),
    ("03-出海-地球箭头", d_globe),
    ("04-核实-放大镜", d_magnifier),
    ("05-增长-柱状箭头", d_bars),
    ("06-现状-卡片图标", d_card),
]


def label_font(sz):
    return ImageFont.truetype(FONT, sz)


def main():
    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for name, fn in OPTIONS:
        img = build(fn)
        img.save(os.path.join(OUT, name + ".png"), "PNG", optimize=True)
        img.resize((144, 144), Image.LANCZOS).save(
            os.path.join(OUT, name + "-144.png"), "PNG", optimize=True)
        tiles.append((name, img))

    # 拼对照表：深色底更接近站点/IDE，白蓝图标在上面更跳
    cols, tile, gap, lab = 4, 240, 26, 40
    rows = (len(tiles) + cols - 1) // cols
    W = cols * tile + (cols + 1) * gap
    H = rows * (tile + lab) + (rows + 1) * gap
    sheet = Image.new("RGB", (W, H), (11, 14, 20))
    sd = ImageDraw.Draw(sheet)
    LF = label_font(17)
    for i, (name, img) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = gap + c * (tile + gap)
        y = gap + r * (tile + lab + gap)
        sheet.paste(img.resize((tile, tile), Image.LANCZOS), (x, y))
        sd.text((x + 2, y + tile + 8), name, font=LF, fill=(220, 226, 236))
    sheet.save(os.path.join(OUT, "_sheet.png"), "PNG", optimize=True)
    print("[OK] %d 个方案 + 对照表 -> %s" % (len(tiles), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
