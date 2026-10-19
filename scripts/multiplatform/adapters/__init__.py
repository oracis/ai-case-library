#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台适配器：把统一的 Article 变成「某个平台存草稿」这一步。

刻意做成**薄壳**：所有 CDP 选择器、封面上传、草稿箱点击都留在既有的
`toutiao_publish.py` / `bilibili_publish.py` / `xhs_publish.py` /
`wechat_publish.py` 里（那是几个月的踩坑沉淀，不能为了架构好看重写）。
适配器只负责四件事：

1. 声明自己是谁（key、显示名、需要什么产物、标题去哪个平台取精修版）；
2. 跑 build（缺稿才造，幂等）；
3. 跑 publish（存草稿 or 真发）；
4. 把子进程结果翻译成清单里的状态。

这正是 Wechatsync 的平台适配器思路，只是执行引擎从「扩展里的浏览器」
换成本项目的「CDP + 已登录 Chrome」。
"""

from . import toutiao, bilibili, xiaohongshu, wechat       # noqa: F401
from .base import REGISTRY, Adapter, get, all_adapters     # noqa: F401
