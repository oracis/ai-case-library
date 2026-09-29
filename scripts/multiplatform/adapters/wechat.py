#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号适配器。

公众号侧只能存草稿（个人订阅号没有群发 API 权限，脚本的 publish 就是存进
草稿箱再由人在后台点群发）。它的 build 是两段式：`make_article` 出母版 →
`wechat_publish build` 出发布就绪版，所以 build 步骤要串两个子命令。
"""

import os

from .base import ROOT, Adapter, register


@register
class Wechat(Adapter):
    key = "wechat"
    label = "公众号"
    script = "wechat_publish.py"
    title_from = "wechat"
    requires = ("out/articles/<id>.html",)
    supports_publish = False          # 只能存草稿
    supports_replace = False
    can_build = False                 # 走 make_article + build 两步

    def _load_done(self):
        import wechat_publish as wp
        d = wp.load_published() or {}
        if isinstance(d, dict):
            return set(d.keys())
        if isinstance(d, list):
            return {x_.get("id") for x_ in d
                    if isinstance(x_, dict) and x_.get("id")}
        return set()

    def build(self, art, dry=False):
        from .base import PY, SCRIPTS
        if dry:
            return super().build(art, dry=True)
        import subprocess
        r1 = subprocess.run(
            [PY, os.path.join(SCRIPTS, "make_article.py"),
             "--id", art.cid, "--format", "html"],
            cwd=ROOT, encoding="utf-8", errors="replace")
        r2 = super().build(art, dry=False)
        ok = r2.ok
        note = r2.note
        if r1.returncode != 0:
            note = ("make_article rc=%s  " % r1.returncode) + note
            ok = ok and art.ok            # make_article 失败但母版已在 → 放行
        from .base import Result
        return Result(ok, r2.state, note.strip(), r2.rc)

    def publish(self, art, dry=False, replace=False, yes=False):
        args = ["publish", "--case", art.cid]
        if dry:
            args.append("--dry")          # 公众号的 --dry 是「不打开浏览器」，安全
        return self.run(args, not dry)
