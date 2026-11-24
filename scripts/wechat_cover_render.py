#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""非案例稿（独立稿）封面渲染：只出封面 PNG，不连公众号、不需要登录。

为什么单独开一个：`wechat_publish publish --file` 那条路要走 CDP + 已登录的
公众号后台，才能顺手把封面截出来；改一句封面文案就得连一次后台，成本太高。
本脚本复用同一套版式（`wechat_publish.cover_html`，铁律：封面文案单一真理），
只把「HTML → PNG」那一步换成无头 Chrome 的 `--screenshot`，全程离线。

    python scripts/wechat_cover_render.py \
        --name "80 个样本，0 命中" \
        --desc "第三方估算能当选题，不能当证据" \
        --kicker "DATA CHECK" --tag "实测 · 2026-11" \
        --seed tool-site-teardown \
        --out out/wechat/cover_tool_site.png

产物固定 2160x920（1080x460 画布 × deviceScaleFactor=2），和 `_gen_cover`
的输出尺寸一致 —— 微信会自己降到 1080x460，走它的降采样比我们先缩更锐。
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wechat_publish as W                                   # noqa: E402

CHROME_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome for Testing"
                       r"\chrome-win64\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application"
                       r"\chrome.exe"),
]


def pick_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    raise SystemExit("✗ 找不到 Chrome，检查 CHROME_CANDIDATES：%s"
                     % CHROME_CANDIDATES)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("✗ 不是 PNG：%s" % path)
    return struct.unpack(">II", head[16:24])


def render(name, desc, kicker=None, tag=None, seed="standalone",
           out="cover.png", keep_html=False):
    """渲染一张封面 PNG，返回 (路径, 宽, 高)。"""
    W._COVER_TAG = tag or "万物解释者"
    W._COVER_KICKER = (kicker or "").strip() or None
    c = {"id": seed, "_cover_name": name, "cover_desc": desc}
    html = W.cover_html(c, 0, W.COVER_W, W.COVER_H)

    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    html_path = (os.path.splitext(out)[0] + ".html") if keep_html else \
        os.path.join(tempfile.mkdtemp(prefix="cover_"), "cover.html")
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    chrome = pick_chrome()
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=2",
           "--window-size=%d,%d" % (W.COVER_W, W.COVER_H),
           "--screenshot=" + out,
           "file:///" + html_path.replace("\\", "/").lstrip("/")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.isfile(out):
        raise SystemExit("✗ Chrome 没出图：%s\n%s"
                         % (r.returncode, (r.stderr or "")[-400:]))
    if not keep_html:
        try:
            os.remove(html_path)
            os.rmdir(os.path.dirname(html_path))
        except OSError:
            pass
    w, h = png_size(out)
    return out, w, h


def main():
    ap = argparse.ArgumentParser(description="独立稿公众号封面渲染（离线）")
    ap.add_argument("--name", required=True, help="封面第一行（大字号）")
    ap.add_argument("--desc", default="", help="封面第二行（建议 ≤20 字，留空则整行不渲染）")
    ap.add_argument("--kicker", default="", help="左上角英文角标（默认 CASE STUDY）")
    ap.add_argument("--tag", default="", help="右下角角标（默认「万物解释者」）")
    ap.add_argument("--seed", default="standalone",
                    help="主题配色种子（同 wechat_publish 的 id 取色，决定底色）")
    ap.add_argument("--out", default="out/wechat/cover.png")
    ap.add_argument("--keep-html", action="store_true", help="保留源 HTML 便于手改")
    a = ap.parse_args()

    path, w, h = render(a.name, a.desc, a.kicker, a.tag, a.seed,
                        a.out, a.keep_html)
    em = W._text_em_width(a.name)
    f = W._fit_font_size(a.name, W._COVER_TEXT_W, [68, 62, 56, 50, 46,
                                                  42, 38, 34, 30])
    print("✓ %s" % os.path.relpath(path))
    print("  %dx%d（%.3f:1）｜第一行 %dpx 占 %.0f/%dpx"
          % (w, h, w / h, f, em * f, W._COVER_TEXT_W))
    return 0


if __name__ == "__main__":
    sys.exit(main())
