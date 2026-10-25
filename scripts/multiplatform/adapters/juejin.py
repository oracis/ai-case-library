#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掘金适配器。

跟另外四个适配器最不一样的两点，先说清楚，免得照抄：

**一、正文源是 `out/articles/<id>.md`，不是 `.html`。**
掘金的 `mark_content` 吃 Markdown（`edit_type:10`）。而统一 `Article` 模型的
`ok` 属性只认 `.html` 母版 —— 一篇只有 .md 的 case 会被 runner 判成「缺母版」
直接 mark_failed。所以本适配器把 `.md` 声明进 `requires`，并在 `build()` 里
现补（`make_article --format md`），不等 runner 的 `_ensure_master`（那个只补 html）。

**二、这是唯一「不用开浏览器」的平台。**
`juejin_publish.py` 全程 REST，连 CDP 都不建立。所以：
  · 单条很快（一次 HTTP，秒级），不像 B站那样 70s/条；
  · 但**依赖 cookie**——没登录时全部接口 403，脚本会明确报
    `未登录或 cookie 过期` 而不是假装成功。
  · 并发上它本来就不抢 Chrome，但 runner 是串行的，保持现状即可。

**三、幂等只认本地台账。**
掘金草稿列表能查（`article_draft/query_list`），但用它判重要多一次网络往返，
且草稿标题会被人工改动而对不上。本地 `data/juejin_drafts.json` 记了
`slug → draft_id`，够用。
"""

import json
import os
import subprocess

from .base import ROOT, PY, SCRIPTS, Result, Adapter, register

DRAFT = os.path.join(ROOT, "data", "juejin_drafts.json")


@register
class Juejin(Adapter):
    key = "juejin"
    label = "掘金"
    script = "juejin_publish.py"
    title_from = "juejin"
    # ⚠ 是 .md 不是 .html —— 见模块 docstring 第一条
    requires = ("out/articles/<id>.md",)
    has_state_file = True
    can_build = True                 # build 的语义是「补 .md」
    supports_publish = True          # publish 子命令能真发
    supports_replace = True          # draft 子命令有 --replace
    supports_cover = False           # 封面是可选 URL，没有独立上传步骤

    BODY_MIN = 300                   # 低于此值多半是空稿

    def _load_done(self):
        if not os.path.isfile(DRAFT):
            return set()
        try:
            with open(DRAFT, encoding="utf-8") as f:
                return set((json.load(f) or {}).keys())
        except (OSError, ValueError):
            return set()

    def build(self, art, dry=False):
        """补 `out/articles/<id>.md`。

        掘金不需要 HTML 母版，所以这里**只补 md**，不碰 make_article 的 html
        产物（那是公众号的事，别为掘金给 37 篇都重生成一遍 html）。
        """
        md = os.path.join(ROOT, "out", "articles", art.cid + ".md")
        if os.path.isfile(md) and os.path.getsize(md) > self.BODY_MIN:
            return Result(True, "draft_saved", "md 已在（%d 字节）"
                          % os.path.getsize(md))
        if dry:
            return Result(True, "pending", "[dry] 会补 %s" % os.path.relpath(md, ROOT))
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUTF8", "1")
        p = subprocess.run(
            [PY, os.path.join(SCRIPTS, "make_article.py"),
             "--id", art.cid, "--format", "md"],
            cwd=ROOT, capture_output=True, encoding="utf-8",
            errors="replace", env=env, timeout=300)
        ok = os.path.isfile(md) and os.path.getsize(md) > self.BODY_MIN
        return Result(ok, "draft_saved" if ok else "failed",
                      ("md %d 字节" % os.path.getsize(md)) if os.path.isfile(md)
                      else "make_article 没产出 md：%s"
                      % (p.stderr or "")[-120:], p.returncode)

    def publish(self, art, dry=False, replace=False, yes=False):
        """yes=False → `draft`（只存草稿）；yes=True → `publish`（真发）。

        ⚠ 两条子命令不是「同一件事的两个开关」，是**两个不同的接口**：
          draft   → POST /content_api/v1/article_draft/create
          publish → POST /content_api/v1/article/publish（带 draft_id）
        网传的 `article_draft/publish` 路由不存在（实测 err_no:2）。
        """
        if yes:
            args = ["publish", "--case", art.cid]
        else:
            args = ["draft", "--case", art.cid]
            if replace and self.supports_replace:
                args.append("--replace")
        r = self.run(args, not dry, full=True)
        blob = r.out or ""
        # 未登录是**环境问题**，不是这条稿的问题。整轮都会失败，
        # 报出来让人去 login，别记成 failed 然后重试三次。
        if (not r.ok) and ("未登录" in blob or "cookie" in blob.lower()):
            return Result(False, "failed",
                          "掘金未登录/ cookie 过期，先跑 juejin_publish.py login",
                          r.rc, blob)
        return r

    def verify(self, art):
        """远端回读：草稿箱里有没有这一条的 draft_id。"""
        if not os.path.isfile(DRAFT):
            return None
        try:
            with open(DRAFT, encoding="utf-8") as f:
                rec = (json.load(f) or {}).get(art.cid)
        except (OSError, ValueError):
            return None
        if not rec:
            return None
        try:
            import juejin_publish as jp
            ids = {str(d.get("id")) for d in jp.list_drafts()}
            hit = str(rec.get("draft_id")) in ids
        except Exception:                                   # noqa: BLE001
            return None
        return Result(hit, "draft_saved" if hit else "failed",
                      "draft_id=%s %s" % (rec.get("draft_id"),
                                          "在草稿箱" if hit else "不在草稿箱"))
