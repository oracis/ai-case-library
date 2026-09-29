#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站专栏适配器。

与其它平台四点不同，写在这里免得每次重新踩：

1. **没有状态文件**。草稿只存在 B站草稿箱里，本地无从判断哪条发过。
   所以清单（`data/publish_manifest.json`）是唯一的幂等依据 —— 这也是
   必须有 state 层的原因，不是为了架构好看。
2. **publish 不支持 --yes**：脚本只点「保存为草稿」，公开发布留给人。
3. **LV1–3 每天只能发 1 篇**，LV4–6 5 篇。批量前留意草稿箱别一天清空。
4. **封面要单独一步**（`bilibili_publish.py cover`）。它复用头条的
   `out/toutiao/<id>/cover.png`，走「发布设置 → 自定义封面」开关 →
   hidden input + DataTransfer → 裁剪弹窗确定 → 存草稿。
   远端判据是 `image_urls/origin_image_urls`，**不是 `banner_url`**
   （后者在草稿接口里恒空，详见 bilibili_publish._cover_of）。
"""

import os

from .base import ROOT, Adapter, register


@register
class Bilibili(Adapter):
    key = "bilibili"
    label = "B站专栏"
    script = "bilibili_publish.py"
    title_from = "bili"
    requires = ("out/bili/<id>/article.html", "out/toutiao/<id>/cover.png")
    supports_publish = False          # 只存草稿，脚本无 --yes
    supports_replace = True
    supports_cover = True
    has_state_file = False

    def done_ids(self):
        """B站侧只能靠清单；这里刻意返回空，让 runner 用 manifest 判幂等。"""
        return set()

    def cover(self, art, dry=False, force=False):
        """给已存草稿补封面。幂等：远端已有封面的会自己跳过。"""
        args = ["cover", "--case", art.cid]
        if force:
            args.append("--force")
        return self.run(args, not dry, timeout=600)
