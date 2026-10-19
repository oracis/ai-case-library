#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_both.py 的三平台别名入口（公众号 + 小红书 + 今日头条）。

历史原因主脚本叫 publish_both（最早只有公众号 + 小红书），现在默认三平台，
叫 all 更顺口。两个入口共用同一份实现：

    python scripts/publish_all.py              # 发最近 1 条还没发全的
    python scripts/publish_all.py --near 3     # 最近 3 条
    python scripts/publish_all.py --all        # 其余全部
    python scripts/publish_all.py --queue      # 只看计划（不连浏览器）
    python scripts/publish_all.py nitra --dry  # 只打印要跑的子命令
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_both import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
