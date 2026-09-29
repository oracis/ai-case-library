#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日头条适配器。

头条侧的既有记录分成两个文件（drafts / published），两个都算「发过」。
封面是头条的硬要求（无封面草稿在信息流里是空图），所以 `build` 之后还要
确认 `out/toutiao/<id>/cover.png` 存在，缺了就单独补一次 build。
"""

import os

from .base import ROOT, Result, Adapter, register

DRAFT = os.path.join(ROOT, "data", "toutiao_drafts.json")
PUB = os.path.join(ROOT, "data", "toutiao_published.json")


@register
class Toutiao(Adapter):
    key = "toutiao"
    label = "今日头条"
    script = "toutiao_publish.py"
    title_from = "toutiao"
    requires = ("out/toutiao/<id>/meta.json", "out/toutiao/<id>/cover.png")
    supports_publish = True          # 有 --yes 才真发
    supports_replace = False

    def _load_done(self):
        import toutiao_publish as tp
        return set(tp._ids(tp.DRAFT_FILE)) | set(tp._ids(tp.PUB_FILE)) | {"voklit"}

    def build(self, art, dry=False):
        r = super().build(art, dry)
        miss = self.missing_artifacts(art)
        if not miss and not dry:
            return Result(True, "draft_saved", r.note or "稿与封面齐")
        return Result(r.ok, r.state, r.note,
                      r.rc) if not miss else Result(
            r.ok, r.state, (r.note + " 缺：" + ",".join(miss)).strip(" "), r.rc)

    def publish(self, art, dry=False, replace=False, yes=False):
        args = ["publish", "--case", art.cid]
        if yes:
            args.append("--yes")
        return self.run(args, not dry)
