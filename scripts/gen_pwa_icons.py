# -*- coding: utf-8 -*-
"""生成 PWA 图标（manifest + apple-touch-icon）。

⚠ 2026-10-15 起造型已迁移到 scripts/gen_logo.py —— 那里是**唯一来源**，
  对外品牌 logo（小程序头像）和站内图标用同一张图，避免两处各画一版
  慢慢跑偏。这个文件保留为兼容入口（build_static.py 的提示文案还指向它），
  实际渲染全部委托给 gen_logo。

用法：
    python scripts/gen_pwa_icons.py            # 等价于 gen_logo.py
    python scripts/gen_pwa_icons.py --check    # 只校验已有文件
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_logo  # noqa: E402


def main():
    print("[i] 造型已统一到 gen_logo.py，这里转发执行。")
    return gen_logo.main()


if __name__ == "__main__":
    sys.exit(main())
