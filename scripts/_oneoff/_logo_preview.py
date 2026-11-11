# -*- coding: utf-8 -*-
"""渲染 logo.svg 的多尺寸预览，用于目视验收（一次性脚本）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_logo as gl  # noqa: E402

SVG = gl.render_svg()

HTML = (
    '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><style>\n'
    'html,body{margin:0;padding:0;background:#0b0e14;}\n'
    '.wrap{display:flex;gap:24px;padding:24px;align-items:center;}\n'
    '.b{width:256px;height:256px;}\n'
    '.s{width:96px;height:96px;}\n'
    '.t{width:48px;height:48px;}\n'
    '.round{border-radius:50%;overflow:hidden;}\n'
    '.sq{border-radius:22%;overflow:hidden;}\n'
    '</style></head><body>\n'
    '<div class="wrap">\n'
    '  <div class="b">' + SVG + '</div>\n'
    '  <div class="s sq">' + SVG + '</div>\n'
    '  <div class="t">' + SVG + '</div>\n'
    '  <div class="b round">' + SVG + '</div>\n'
    '</div>\n</body></html>\n'
)


def main():
    out = os.path.join(ROOT, "tmp", "logo-render", "preview.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(HTML)
    print("写好了 " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
