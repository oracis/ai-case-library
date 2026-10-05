#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日头条适配器。

头条侧的既有记录分成两个文件（drafts / published），两个都算「发过」。
封面是头条的硬要求（无封面草稿在信息流里是空图），所以 `build` 之后还要
确认 `out/toutiao/<id>/cover.png` 存在，缺了就单独补一次 build。

⚠⚠ **头条正文抽自 `out/articles/<id>.md`，不是 `.html`**（`build_article()`
  读的是 `ARTICLES_DIR/<id>.md`）。`.md` 缺失时它**静默降级成空正文**：
  `md=""` → `chars=0 / blocks=0` → `article.html` 只剩一个h1 + 页脚
  （实测 393 字节 vs 正常 4438），而 `cmd_build` 只是打印
  「[!] 正文偏短，建议别发」**并继续**，publish 照发不误
  ⇒ 草稿箱里躺着一篇 113 字的空稿，却记成 `draft_saved`（2026-10-05 实测）。
  ⇒ 所以 build 必须先把 `.md` 补出来，且 publish 前按字数守门。
"""

import os

from .base import ROOT, PY, SCRIPTS, Result, Adapter, register
import subprocess

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
    # 头条正文下限。实测正常案例 1300 字起，113 字是空壳。
    BODY_MIN = 600

    def _load_done(self):
        import toutiao_publish as tp
        return set(tp._ids(tp.DRAFT_FILE)) | set(tp._ids(tp.PUB_FILE)) | {"voklit"}

    def _ensure_md(self, cid):
        """补`out/articles/<id>.md`（头条正文的唯一来源）。

        公众号母版是 `.html`，两边不是同一个文件——只跑过`make_article
        --format html` 的人永远补不上这一步。
        """
        import subprocess as _sp
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        _sp.run([PY, os.path.join(SCRIPTS, "make_article.py"),
                 "--id", cid, "--format", "md"],
                cwd=ROOT, capture_output=True, encoding="utf-8",
                errors="replace", env=env)

    def build(self, art, dry=False):
        r = super().build(art, dry)
        miss = self.missing_artifacts(art)
        if not miss and not dry:
            return Result(True, "draft_saved", r.note or "稿与封面齐")
        if dry:
            return Result(r.ok, r.state, r.note, r.rc)
        # ⚠ 顺序不能换：`.md` 是源，封面是渲染结果，都得在 build 之后。
        self._ensure_md(art.cid)
        r = super().build(art, dry)
        miss = self.missing_artifacts(art)
        note = r.note
        if miss:
            # 封面是独立子命令（要连 CDP 渲染），build 不会产出。
            import toutiao_publish as tp
            _env = dict(os.environ)
            _env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run(
                [PY, os.path.join(SCRIPTS, "toutiao_publish.py"), "covers",
                 "--case", art.cid],
                cwd=ROOT, capture_output=True, encoding="utf-8",
                errors="replace", env=_env, timeout=600)
            miss = self.missing_artifacts(art)
        return Result(r.ok and not miss, r.state,
                      (note + ("缺：" + ",".join(miss) if miss else "")).strip(" "),
                      r.rc)

    def publish(self, art, dry=False, replace=False, yes=False):
        # ⚠ 空正文守门：`chars` 是 build 抽出来的纯文本字数，
        #   低于 BODY_MIN 说明 `.md` 缺失或抽不出内容 —— 这种稿发出去
        #   就是空文。宁可 failed 让人来看一眼。
        import json
        mj = os.path.join(ROOT, "out", "toutiao", art.cid, "meta.json")
        try:
            with open(mj, encoding="utf-8") as f:
                chars = (json.load(f) or {}).get("chars") or 0
        except (OSError, ValueError):
            chars = 0
        if not dry and chars < self.BODY_MIN:
            return Result(False, "failed",
                           "正文只有 %d 字（<%d），多半是 out/articles/%s.md "
                           "缺失，先跑 make_article --format md"
                           % (chars, self.BODY_MIN, art.cid), 1)
        args = ["publish", "--case", art.cid]
        if yes:
            args.append("--yes")
        r = self.run(args, not dry, full=True)
        blob = r.out or ""
        if r.ok and ("没找到卡片图" in blob or "正文偏短" in blob):
            r = Result(False, "failed",
                       (blob.strip().splitlines() or ["空输出"])[-1][:200],
                       r.rc, blob)
        return r
