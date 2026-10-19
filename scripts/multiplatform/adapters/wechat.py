#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号适配器。

公众号侧只能存草稿（个人订阅号没有群发 API 权限，脚本的 publish 就是存进
草稿箱再由人在后台点群发）。它的 build 是两段式：`make_article` 出母版 →
`wechat_publish build` 出发布就绪版，所以 build 步骤要串两个子命令。
"""

import os

from .base import ROOT, Adapter, Result, register


@register
class Wechat(Adapter):
    key = "wechat"
    label = "公众号"
    script = "wechat_publish.py"
    title_from = "wechat"
    requires = ("out/articles/<id>.html",)
    supports_publish = False          # 只能存草稿
    supports_replace = False
    # ⚠⚠ 这里**必须**是 True，否则 runner 认定公众号没有 build 能力，
    # `missing_artifacts` 报缺时也不会去补，根目录的
    # `公众号-<id>拆解.html` 永远生成不出来。
    # （原值False 属遗留：以为公众号走「母版即产物」，实测2026-10-05
    #   两段式的事实被漏掉，直接导致假绿。）
    can_build = True

    def _load_done(self):
        import wechat_publish as wp
        d = wp.load_published() or {}
        if isinstance(d, dict):
            return set(d.keys())
        if isinstance(d, list):
            return {x_.get("id") for x_ in d
                    if isinstance(x_, dict) and x_.get("id")}
        return set()

    def missing_artifacts(self, art):
        """真实产物是仓库根的 `公众号-<id>拆解.html`，**不是**母版。

        ⚠⚠ 不能用基类的 `requires = ("out/articles/<id>.html",)`：
        母版由 `make_article` 生成，对**新案例本来就存在**，于是判据恒为空
        → runner 认为「产物齐了，不必 build」→ 直接 publish
        → `wechat_publish.cmd_publish` 的 `find_article()` 在根目录找不到
        公众号-*.html，打印「先跑 build」后 **`return`（rc=0）**
        → runner 判`r.ok=True` → 台账写`draft_saved`
        ⇒ **草稿压根没进后台，却记成成功**（2026-10-05 实测踩中）。

        所以这里必须查根目录的精修产物，且要用 `find_article` 同一套
        大小写不敏感 + name兜底的匹配，否则 id 大小写不同又会漏。
        """
        import wechat_publish as wp
        if wp.find_article(art.cid, art.name):
            return []
        return ["公众号-%s拆解.html" % art.cid]

    def publish(self, art, dry=False, replace=False, yes=False):
        args = ["publish", "--case", art.cid]
        if dry:
            args.append("--dry")          # 公众号的 --dry是「不打开浏览器」，安全
        r = self.run(args, not dry, full=True)
        # ⚠ `cmd_publish` 的提前 return 走的是 rc=0，光看返回码会把
        # 「什么都没发」判成成功。这里补一道 stdout 判据兜底。
        #⚠ `r.out` 可能为 None（超时/启动失败分支不填 out），
        #   必须 `(r.out or "")`，否则 `"x" in None` 直接抛 TypeError
        #   —— 而这个崩溃发生在**草稿已存成功之后**，等于把成功变失败
        #   （2026-10-05 实测：easymix 已存 appmsgid=100000399，
        #   却在判据这行崩掉，整轮被判failed）。
        blob = r.out or ""
        if r.ok and ("未找到" in blob or "先跑 build" in blob):
            r = Result(False, "failed",
                       (blob.strip().splitlines() or ["空输出"])[-1][:200],
                       r.rc, blob)
        return r

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
        return Result(ok, r2.state, note.strip(), r2.rc)
