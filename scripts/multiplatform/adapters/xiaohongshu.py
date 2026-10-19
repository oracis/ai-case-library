#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书适配器。

小红书侧脚本的 `--dry` 语义是「照常连浏览器填表，只是不点发布」—— 会真的
把表单填进编辑页（2026-09-26 实测污染了 bustem 的草稿）。所以适配器里
**必须拦掉 --dry**，只打印计划，绝不透传。
"""

import os

from .base import ROOT, Adapter, register


@register
class Xiaohongshu(Adapter):
    key = "xhs"
    label = "小红书"
    script = "xhs_publish.py"
    title_from = "xhs"
    requires = ("out/xhs/<id>/note.json",)
    supports_publish = True
    supports_replace = False

    def _load_done(self):
        import xhs_publish as x
        return set(x._drafted_ids()) | set(x._published_ids()) | {"voklit"}
